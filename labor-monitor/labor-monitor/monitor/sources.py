"""수집기.

각 함수는 Item 리스트를 돌려줍니다. 실패해도 예외를 밖으로 던지지 않고
빈 리스트 + 경고를 남깁니다. 소스 하나가 죽어도 나머지는 돌아야 하니까요.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.parse
from datetime import date, datetime, timedelta
from typing import Any, Iterable

import feedparser
import requests

from .config import Item

log = logging.getLogger("monitor.sources")

LAW_BASE = "https://www.law.go.kr"
UA = {"User-Agent": "labor-monitor/1.0 (+internal HR tool)"}
TIMEOUT = 20


def _uid(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _clean(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"&[a-z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _date(value: Any) -> str:
    """다양한 날짜 표기를 YYYY-MM-DD 로 정규화."""
    if not value:
        return ""
    s = str(value).strip()
    if re.fullmatch(r"\d{8}", s):                      # 20261001
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    m = re.search(r"(\d{4})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


# ── 고용노동부 RSS ────────────────────────────────────────────
def from_moel_rss(feeds: list[dict]) -> list[Item]:
    out: list[Item] = []
    for feed in feeds:
        try:
            parsed = feedparser.parse(feed["url"], agent=UA["User-Agent"])
            if parsed.bozo and not parsed.entries:
                log.warning("RSS 파싱 실패: %s (%s)", feed["name"], parsed.bozo_exception)
                continue
            for e in parsed.entries:
                link = getattr(e, "link", "")
                title = _clean(getattr(e, "title", ""))
                if not title:
                    continue
                out.append(
                    Item(
                        uid=_uid("moel", link or title),
                        source=feed["name"],
                        kind=feed.get("kind", "공지"),
                        title=title,
                        link=link,
                        published=_date(getattr(e, "published", "")),
                        body=_clean(getattr(e, "summary", ""))[:2000],
                    )
                )
            log.info("%s: %d건", feed["name"], len(parsed.entries))
        except Exception as exc:  # noqa: BLE001
            log.warning("RSS 수집 실패 %s: %s", feed["name"], exc)
    return out


# ── 법제처 국가법령정보 OPEN API ──────────────────────────────
def _find_records(payload: Any) -> list[dict]:
    """응답 JSON 구조가 target 마다 달라서, dict 리스트를 재귀로 찾아냅니다."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for value in payload.values():
            found = _find_records(value)
            if found and any(len(r) > 2 for r in found):
                return found
    return []


def _pick(rec: dict, *names: str) -> str:
    for n in names:
        for k, v in rec.items():
            if k.replace(" ", "") == n:
                return str(v).strip()
    return ""


def _law_request(params: dict) -> list[dict]:
    url = f"{LAW_BASE}/DRF/lawSearch.do"
    r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    try:
        return _find_records(r.json())
    except ValueError:
        log.warning("법제처 API가 JSON이 아닌 응답을 반환했습니다. OC 값을 확인하세요.")
        return []


def _lookback_range(days: int) -> str:
    """prmlYd 파라미터용 발령일자 기간 (YYYYMMDD~YYYYMMDD)."""
    end = date.today()
    start = end - timedelta(days=days)
    return f"{start:%Y%m%d}~{end:%Y%m%d}"


def _to_item(rec: dict, spec: dict, source_name: str) -> Item | None:
    """DRF 응답 레코드 하나를 Item 으로. 서비스마다 필드명이 달라 후보를 넓게 봅니다."""
    name = _pick(
        rec, "행정규칙명", "법령명한글", "법령명", "안건명", "사건명", "제목", "질의요지"
    )
    if not name:
        return None
    link = _pick(rec, "행정규칙상세링크", "법령상세링크", "상세링크", "본문링크")
    if link.startswith("/"):
        link = LAW_BASE + link
    eff = _date(_pick(rec, "시행일자"))
    pub = _date(_pick(rec, "발령일자", "공포일자", "결정일자", "회신일자", "의결일자"))
    serial = _pick(
        rec, "행정규칙ID", "법령일련번호", "법령ID", "안건번호", "일련번호", "사건번호"
    )
    revision = _pick(rec, "제개정구분명", "행정규칙종류", "결정구분", "종류")
    return Item(
        uid=_uid("law", spec["target"], serial or name, eff or pub),
        source=source_name,
        kind=spec.get("kind", "법령"),
        title=name,
        link=link,
        published=pub or eff,
        effective_date=eff,          # LLM이 못 찾아도 이 값이 남습니다
        body=" ".join(x for x in [revision, f"발령 {pub}" if pub else "",
                                  f"시행 {eff}" if eff else ""] if x),
    )


def _fetch_target(spec: dict, oc: str, org: str, lookback: str,
                  query: str | None = None) -> tuple[list[dict], str | None]:
    """(레코드 목록, 오류메시지) 반환."""
    params = {"OC": oc, "target": spec["target"], "type": "JSON",
              "display": spec.get("display", 50)}
    params.update(spec.get("params", {}))
    if spec.get("use_org") and org:
        params["org"] = org
    if spec.get("use_lookback") and lookback:
        params["prmlYd"] = lookback
    if query:
        params["query"] = query
    try:
        return _law_request(params), None
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)


def from_law_api(cfg: dict, oc: str) -> list[Item]:
    """행정규칙·법령·법령해석·위원회 결정문을 config 의 drf_sources 대로 수집."""
    if not oc:
        log.warning("LAW_OC 미설정 — 법제처 API를 건너뜁니다.")
        return []

    org = cfg.get("org", "")
    lookback = _lookback_range(cfg.get("lookback_days", 14))
    out: list[Item] = []

    for spec in cfg.get("drf_sources", []):
        name = spec.get("name", spec["target"])
        queries = spec.get("queries") or [None]
        got = 0
        for q in queries:
            records, err = _fetch_target(spec, oc, org, lookback, q)
            if err:
                level = log.warning if spec.get("verified") else log.info
                level("%s 조회 실패 (target=%s): %s", name, spec["target"], err)
                continue
            for rec in records:
                item = _to_item(rec, spec, name)
                if item:
                    out.append(item)
                    got += 1
        if got == 0 and not spec.get("verified"):
            log.info(
                "%s: 결과 없음. target='%s' 가 맞는지 `python run.py probe` 로 확인하세요.",
                name, spec["target"],
            )
        else:
            log.info("%s: %d건", name, got)
    return out


# ── 신구조문 대비 ─────────────────────────────────────────────
_ARTICLE = re.compile(r"제\s*\d+\s*조(?:의\s*\d+)?")


def _diff_pairs(payload: Any, limit: int) -> list[dict]:
    """신구법 비교 본문 응답에서 (개정 전, 개정 후) 조문 쌍을 추출.

    서비스마다 필드명이 달라 '구'/'신' 계열 키를 넓게 훑습니다.
    """
    pairs: list[dict] = []

    def walk(node: Any) -> None:
        if len(pairs) >= limit:
            return
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            old = new = label = ""
            for k, v in node.items():
                if not isinstance(v, str):
                    continue
                key = k.replace(" ", "")
                if any(t in key for t in ("구조문", "구법", "개정전", "종전")):
                    old = _clean(v)
                elif any(t in key for t in ("신조문", "신법", "개정후", "현행")):
                    new = _clean(v)
                elif "조문" in key or "조번호" in key or "제목" in key:
                    label = label or _clean(v)
            if old or new:
                if not label:
                    m = _ARTICLE.search(new or old)
                    label = m.group(0) if m else ""
                pairs.append({"article": label, "old": old[:1200], "new": new[:1200]})
            for v in node.values():
                walk(v)

    walk(payload)
    return pairs[:limit]


def fetch_diff(item: Item, cfg: dict, oc: str) -> bool:
    """항목에 신구조문 대비표를 채웁니다. 성공하면 True.

    없는 개정 건도 많으므로 실패는 정상 흐름으로 취급합니다.
    """
    if not oc or not cfg.get("enabled"):
        return False
    sub = cfg.get("admrul" if item.kind == "고시" else "law", {})
    if not sub:
        return False

    # 1) 목록 조회 — 항목명으로 찾아 비교 ID 확보
    try:
        records = _law_request({
            "OC": oc, "target": sub["list_target"], "type": "JSON",
            "query": item.title, "display": 5,
        })
    except Exception as exc:  # noqa: BLE001
        log.debug("신구법 목록 조회 실패 (%s): %s", item.title, exc)
        return False
    if not records:
        return False

    cmp_id = ""
    for rec in records:
        cmp_id = _pick(rec, "신구법비교ID", "비교ID", "ID", "일련번호",
                       "행정규칙ID", "법령일련번호")
        if cmp_id:
            break
    if not cmp_id:
        return False

    # 2) 본문 조회 — 조문 쌍 추출
    try:
        r = requests.get(
            f"{LAW_BASE}/DRF/lawService.do",
            params={"OC": oc, "target": sub["body_target"], "type": "JSON", "ID": cmp_id},
            headers=UA, timeout=TIMEOUT,
        )
        r.raise_for_status()
        pairs = _diff_pairs(r.json(), cfg.get("max_pairs", 12))
    except Exception as exc:  # noqa: BLE001
        log.debug("신구법 본문 조회 실패 (%s): %s", item.title, exc)
        return False

    if not pairs:
        return False
    item.diff = json.dumps(pairs, ensure_ascii=False)
    item.diff_source = f"{LAW_BASE}/DRF/lawService.do?target={sub['body_target']}&ID={cmp_id}"
    log.info("신구조문 대비 %d개 조문: %s", len(pairs), item.title[:40])
    return True


def enrich_diffs(items: list[Item], cfg: dict, oc: str) -> int:
    """개정으로 보이는 항목에만 대비표를 붙입니다 (호출 수 절약)."""
    if not cfg.get("enabled"):
        return 0
    targets = [
        i for i in items
        if i.kind in ("고시", "법령") and ("개정" in i.title or "개정" in i.body)
    ][: cfg.get("max_per_run", 10)]
    return sum(1 for i in targets if fetch_diff(i, cfg, oc))


# ── 뉴스 (구글 뉴스 RSS) ──────────────────────────────────────
def from_news(cfg: dict) -> list[Item]:
    if not cfg.get("enabled", True):
        return []
    out: list[Item] = []
    for q in cfg.get("queries", []):
        url = (
            "https://news.google.com/rss/search?"
            + urllib.parse.urlencode({"q": q, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
        )
        try:
            parsed = feedparser.parse(url, agent=UA["User-Agent"])
            for e in parsed.entries[: cfg.get("max_per_query", 10)]:
                title = _clean(getattr(e, "title", ""))
                link = getattr(e, "link", "")
                if not title:
                    continue
                out.append(
                    Item(
                        uid=_uid("news", link or title),
                        source=f"뉴스 · {q}",
                        kind="뉴스",
                        title=title,
                        link=link,
                        published=_date(getattr(e, "published", "")),
                        body=_clean(getattr(e, "summary", ""))[:1200],
                    )
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("뉴스 수집 실패 (%s): %s", q, exc)
    return out


# ── 통합 ─────────────────────────────────────────────────────
def collect_all(cfg: dict, law_oc: str | None) -> list[Item]:
    src = cfg["sources"]
    items: list[Item] = []
    items += from_moel_rss(src.get("moel_rss", []))
    if src.get("law_api", {}).get("enabled", True):
        items += from_law_api(src["law_api"], law_oc or "")
    items += from_news(src.get("news", {}))

    # 같은 실행 안에서의 중복 제거
    seen: set[str] = set()
    uniq = []
    for i in items:
        if i.uid in seen:
            continue
        seen.add(i.uid)
        uniq.append(i)
    log.info("수집 총 %d건 (중복 제거 후)", len(uniq))
    return uniq


def prefilter(items: Iterable[Item], keywords: dict) -> tuple[list[Item], int]:
    """1차 키워드 필터. LLM 호출 건수를 줄이는 게 목적입니다."""
    inc = [k for k in keywords.get("include", []) if k]
    exc = [k for k in keywords.get("exclude", []) if k]
    kept, dropped = [], 0
    for i in items:
        blob = f"{i.title} {i.body}"
        if exc and any(x in blob for x in exc):
            dropped += 1
            continue
        if inc and not any(x in blob for x in inc):
            dropped += 1
            continue
        kept.append(i)
    return kept, dropped
