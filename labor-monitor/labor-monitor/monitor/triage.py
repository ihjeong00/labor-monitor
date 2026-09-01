"""2차 판정 — 우리 회사에 해당되는가, 그리고 뭘 해야 하는가.

1차 키워드 필터를 통과한 항목만 여기로 옵니다.
회사 컨텍스트(config.yaml의 company 블록)가 판정의 기준입니다.
"""
from __future__ import annotations

import json
import logging
import re
import time

import requests

from .config import Item

log = logging.getLogger("monitor.triage")

API = "https://api.anthropic.com/v1/messages"
VALID_LEVELS = {"none", "review", "urgent"}

SYSTEM = """너는 한국 기업 인사팀의 노동법 모니터링 담당자다.
수집된 항목이 이 회사에 실제로 해당되는지 판정하고, 해당되면 인사 실무 관점에서 요약한다.

판정 원칙:
- 회사 정보와 무관한 업종·근로형태 대상 규정은 relevant=false로 분류한다.
- 확실하지 않으면 relevant=true, action_level="review"로 두고 reason에 불확실한 이유를 적는다.
  놓치는 것이 잘못 올리는 것보다 나쁘다.
- 원문에 없는 시행일이나 수치를 지어내지 않는다. 모르면 빈 문자열로 둔다.
- 신구조문 대비가 함께 제공된 항목은 반드시 그 조문을 근거로 요약한다.
  제목만 보고 추측하지 말고, 실제로 바뀐 문구가 무엇인지 요약에 담는다.
- 뉴스는 법령 변경이 아니므로 action_level을 "none" 또는 "review"까지만 준다.

action_level 기준:
- urgent: 시행일이 정해져 있고, 취업규칙·급여·계약서·사내 시스템 중 하나를 반드시 바꿔야 함
- review: 해당될 가능성이 있어 담당자 검토가 필요함. 확정 전 예고 단계도 여기.
- none: 참고용. 별도 조치 불필요.

출력은 JSON 배열만. 설명, 마크다운 코드펜스 없이."""

PROMPT = """[회사 정보]
{company}

[오늘 날짜]
{today}

[판정 대상 항목]
{items}

각 항목에 대해 아래 형식의 객체를 만들어 JSON 배열로 반환하라. 입력 순서와 개수를 그대로 지킨다.

{{
  "idx": <입력의 번호>,
  "relevant": true|false,
  "summary": "인사 담당자가 읽을 2~3문장 요약. 무엇이 어떻게 바뀌는지 중심으로.",
  "effective_date": "YYYY-MM-DD 또는 빈 문자열",
  "scope": "이 회사에서 누구에게 적용되고 무엇을 손봐야 하는지 한 문장",
  "action_level": "urgent"|"review"|"none",
  "reason": "이렇게 판정한 근거 한 문장"
}}"""


def _company_block(company: dict) -> str:
    lines = [
        f"회사명: {company.get('name','')}",
        f"업종: {company.get('industry','')}",
        f"상시 근로자 수: {company.get('headcount','')}명",
        f"근로형태: {', '.join(company.get('employment_types', []))}",
        f"교대제 운영: {'예' if company.get('shift_work') else '아니오'}",
        f"파견·도급 사용: {'예' if company.get('uses_dispatch') else '아니오'}",
        f"노동조합: {'있음' if company.get('has_union') else '없음'}",
        f"사업장 소재지: {', '.join(company.get('locations', []))}",
    ]
    if company.get("notes"):
        lines.append(f"특이사항: {company['notes'].strip()}")
    return "\n".join(lines)


def _diff_block(item: Item) -> str:
    """신구조문 대비가 있으면 프롬프트에 붙입니다. 요약 정확도가 크게 올라갑니다."""
    if not item.diff:
        return ""
    try:
        pairs = json.loads(item.diff)
    except json.JSONDecodeError:
        return ""
    lines = ["\n신구조문 대비:"]
    for pair in pairs[:8]:
        lines.append(f"  [{pair.get('article','조문')}]")
        lines.append(f"    개정 전: {(pair.get('old') or '(신설)')[:400]}")
        lines.append(f"    개정 후: {(pair.get('new') or '(삭제)')[:400]}")
    return "\n".join(lines)


def _parse_json(text: str) -> list[dict]:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            raise
        data = json.loads(m.group(0))
    return data if isinstance(data, list) else [data]


def _call(api_key: str, model: str, prompt: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            r = requests.post(
                API,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 4000,
                    "system": SYSTEM,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=120,
            )
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code} {r.text[:200]}")
            r.raise_for_status()
            return "".join(
                b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text"
            )
        except Exception as exc:  # noqa: BLE001
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt * 3
            log.warning("API 재시도 %d/%d (%ss): %s", attempt + 1, retries, wait, exc)
            time.sleep(wait)
    return ""


def passthrough(items: list[Item]) -> list[Item]:
    """--no-llm 모드. 판정 없이 1차 필터 통과분을 그대로 올립니다.

    '우리 회사에 해당되는가' 판단이 빠지므로 노이즈가 많습니다.
    키워드가 잘 잡히는지 눈으로 확인하는 용도입니다.
    """
    for it in items:
        it.relevant = True
        it.action_level = "review"
        it.summary = ""          # 요약 없음 — 메일에서 제목·출처만 표시
        it.reason = "키워드 필터만 통과 (자동 요약 미사용)"
    log.info("요약 없이 %d건 등록", len(items))
    return items


def triage(items: list[Item], cfg: dict, api_key: str, today: str) -> list[Item]:
    """판정 결과를 items 에 채워 넣고 그대로 돌려줍니다."""
    if not items:
        return []

    company = _company_block(cfg["company"])
    model = cfg["llm"]["model"]
    batch_size = cfg["llm"].get("max_items_per_call", 6)
    force = cfg["sources"].get("news", {}).get("force_action_level")

    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        listing = "\n\n".join(
            f"[{n}] 구분: {it.kind} / 출처: {it.source}\n"
            f"제목: {it.title}\n"
            f"수집 정보: {it.body[:900] or '(본문 없음)'}\n"
            f"알려진 시행일: {it.effective_date or '미상'}"
            f"{_diff_block(it)}"
            for n, it in enumerate(batch)
        )
        prompt = PROMPT.format(company=company, today=today, items=listing)

        try:
            verdicts = _parse_json(_call(api_key, model, prompt))
        except Exception as exc:  # noqa: BLE001
            # 판정 실패한 배치는 버리지 않고 검토 대상으로 올립니다.
            log.error("판정 실패 — 검토 대상으로 넘깁니다: %s", exc)
            for it in batch:
                it.relevant = True
                it.action_level = "review"
                it.summary = "자동 요약에 실패했습니다. 원문을 직접 확인해 주세요."
                it.reason = "요약 단계 오류"
            continue

        by_idx = {v.get("idx", n): v for n, v in enumerate(verdicts)}
        for n, it in enumerate(batch):
            v = by_idx.get(n, {})
            it.relevant = bool(v.get("relevant", True))
            it.summary = (v.get("summary") or "").strip()
            it.effective_date = (v.get("effective_date") or it.effective_date or "").strip()
            it.scope = (v.get("scope") or "").strip()
            level = (v.get("action_level") or "none").strip()
            it.action_level = level if level in VALID_LEVELS else "review"
            it.reason = (v.get("reason") or "").strip()
            if it.kind == "뉴스" and force in VALID_LEVELS:
                # 뉴스는 법령 변경이 아니므로 조치등급 상한을 강제합니다.
                order = ["none", "review", "urgent"]
                if order.index(it.action_level) > order.index(force):
                    it.action_level = force

        log.info("판정 %d~%d / %d", start + 1, start + len(batch), len(items))

    kept = [i for i in items if i.relevant]
    log.info("판정 결과: 관련 %d건 / 무관 %d건", len(kept), len(items) - len(kept))
    return items
