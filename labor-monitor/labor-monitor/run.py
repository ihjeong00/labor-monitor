#!/usr/bin/env python3
"""노동법 모니터 실행기.

  python run.py collect              수집 → 판정 → 저장
  python run.py collect --no-llm     AI 요약 없이 수집·필터만 (API 키 불필요)
  python run.py digest               미발송 항목으로 메일 발송
  python run.py digest --dry-run     발송하지 않고 data/preview.html 만 생성
  python run.py run                  collect + digest + export (매일 돌릴 명령)
  python run.py export               대시보드용 data.json 갱신
  python run.py probe                법제처 API target 값 확인 (최초 1회)
  python run.py demo                 샘플 데이터로 전체 흐름 확인 (API키·네트워크 불필요)
  python run.py status <uid> 검토중 --assignee 김담당
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from monitor import config as conf
from monitor import digest as dg
from monitor import export as ex
from monitor import probe as pb
from monitor import sources as src
from monitor.config import Item
from monitor.store import Store
from monitor.triage import passthrough, triage

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("monitor")


def issue_no(store: Store) -> int:
    """발행 호수 = 지금까지 발송 처리된 날 수 + 1 (표시용)."""
    return store.stats()["relevant"] // 10 + 1


# ── collect ───────────────────────────────────────────────────
def cmd_collect(cfg: dict, store: Store, args) -> None:
    items = src.collect_all(cfg, conf.env("LAW_OC"))
    fresh = store.unseen(items)
    log.info("신규 %d건 (기존 %d건 스킵)", len(fresh), len(items) - len(fresh))
    if not fresh:
        return

    kept, dropped = src.prefilter(fresh, cfg["keywords"])
    log.info("1차 키워드 필터: 통과 %d건 / 제외 %d건", len(kept), dropped)

    # 필터에서 걸러진 것도 저장합니다. 다시 수집·판정하지 않기 위해서입니다.
    store.upsert([i for i in fresh if i not in kept])

    if kept and args.no_llm:
        passthrough(kept)
        store.upsert(kept)
        log.info("저장 완료 (요약 없음). %s", store.stats())
        return

    if kept:
        # 신구조문 대비를 먼저 붙입니다. 판정 프롬프트에 들어가야 요약이 정확해집니다.
        n = src.enrich_diffs(kept, cfg["sources"]["law_api"].get("diff", {}), conf.env("LAW_OC"))
        if n:
            log.info("신구조문 대비 %d건 확보", n)

        api_key = conf.env("ANTHROPIC_API_KEY", required=True)
        triage(kept, cfg, api_key, date.today().isoformat())
        store.upsert(kept)
    log.info("저장 완료. %s", store.stats())


# ── digest ────────────────────────────────────────────────────
def cmd_digest(cfg: dict, store: Store, args) -> None:
    today = date.today()
    items = store.pending_digest()
    if not items and not cfg["digest"].get("send_when_empty"):
        log.info("발송할 신규 항목이 없습니다. 메일을 보내지 않습니다.")
        return

    issue = issue_no(store)
    html = dg.build_html(items, today, issue, conf.env("DASHBOARD_URL", "#"))
    text = dg.build_text(items, today)
    urgent = sum(1 for i in items if i["action_level"] == "urgent")
    subject = (
        f"{cfg['digest']['subject_prefix']} {today.strftime('%m/%d')} "
        f"신규 {len(items)}건" + (f" · 조치 필요 {urgent}건" if urgent else "")
    )

    if args.dry_run:
        p = dg.save_preview(html)
        print(f"\n미리보기 저장: {p}\n제목: {subject}\n")
        print(text)
        return

    dg.send(html, text, subject, cfg["digest"], {
        "host": conf.env("SMTP_HOST", required=True),
        "port": conf.env("SMTP_PORT", "587"),
        "user": conf.env("SMTP_USER"),
        "password": conf.env("SMTP_PASS"),
    })
    store.mark_notified([i["uid"] for i in items])


# ── export ────────────────────────────────────────────────────
def cmd_export(cfg: dict, store: Store, args) -> None:
    p = ex.write(store, date.today(), issue_no(store))
    log.info("대시보드 데이터 갱신: %s", p)


def cmd_run(cfg: dict, store: Store, args) -> None:
    cmd_collect(cfg, store, args)
    cmd_digest(cfg, store, args)
    cmd_export(cfg, store, args)


def cmd_probe(cfg: dict, store: Store, args) -> None:
    pb.probe(cfg, conf.env("LAW_OC", required=True))


def cmd_status(cfg: dict, store: Store, args) -> None:
    store.set_status(args.uid, args.value, args.assignee)
    log.info("상태 변경: %s → %s %s", args.uid, args.value, args.assignee)
    ex.write(store, date.today(), issue_no(store))


# ── demo ──────────────────────────────────────────────────────
def cmd_demo(cfg: dict, store: Store, args) -> None:
    """네트워크·API 없이 파이프라인 뒷단(저장→메일→export)만 검증합니다."""
    samples = [
        Item(uid="demo1", source="고용노동부 고시", kind="고시",
             title="[예시] 2027년 적용 최저임금 고시",
             link="https://www.moel.go.kr/", published="2026-08-05",
             relevant=True, effective_date="2027-01-01", action_level="urgent",
             summary="2027년 적용 최저임금액이 고시되었습니다. 업종별 구분 없이 전 사업장에 동일하게 적용됩니다.",
             scope="전 직원 · 수습·단시간 근로자 임금 재산정 및 근로계약서 갱신 필요",
             reason="전 사업장 적용 대상이며 시행일이 확정됨"),
        Item(uid="demo2", source="국가법령정보센터", kind="법령",
             title="[예시] 근로기준법 시행령 일부개정령",
             link="https://www.law.go.kr/", published="2026-08-20",
             relevant=True, effective_date="2026-10-01", action_level="urgent",
             summary="연차유급휴가 사용촉진 절차의 서면 통보 요건이 구체화되었습니다. 통보 시기와 기재사항이 명문화되었습니다.",
             scope="전 사업장 · 연차 촉진 통보 양식 및 HR 시스템 템플릿 수정",
             reason="사무직 사업장에도 그대로 적용되는 절차 규정",
             diff_source="https://www.law.go.kr/",
             diff=json.dumps([
                 {"article": "제7조(연차 유급휴가의 사용 촉진)",
                  "old": "사용자는 근로자별로 사용하지 아니한 휴가 일수를 알려주어야 한다.",
                  "new": "사용자는 근로자별로 사용하지 아니한 휴가 일수를 서면으로 알리고, "
                         "근로자가 그 사용 시기를 정하여 사용자에게 통보하도록 서면으로 촉구하여야 한다."},
                 {"article": "제7조제2항",
                  "old": "",
                  "new": "제1항에 따른 촉구는 휴가 사용 기간이 끝나기 6개월 전을 기준으로 "
                         "10일 이내에 하여야 한다."},
             ], ensure_ascii=False)),
        Item(uid="demo3", source="고용노동부 입법·행정예고", kind="예고",
             title="[예시] 직장 내 괴롭힘 대응 관련 고시 개정 행정예고",
             link="https://www.moel.go.kr/", published="2026-08-26",
             relevant=True, effective_date="2026-11-15", action_level="review",
             summary="조사 절차와 피해자 보호조치 기준을 정비하는 개정안이 행정예고되었습니다. 확정 전 단계입니다.",
             scope="확정 시 사내 규정 및 조사 매뉴얼 개정 예상",
             reason="예고 단계로 아직 확정되지 않음"),
        Item(uid="demo4", source="뉴스 · 통상임금", kind="뉴스",
             title="[예시] 통상임금 범위 관련 후속 실무 대응 논의",
             link="https://news.google.com/", published="2026-08-27",
             relevant=True, action_level="none",
             summary="판결 이후 기업들의 임금체계 개편 사례가 보도되고 있습니다. 법령 변경은 아닙니다.",
             scope="참고 · 임금체계 개편 검토 시 자료",
             reason="법령 변경이 아닌 언론 보도"),
    ]
    store.upsert(samples)
    log.info("샘플 %d건 저장", len(samples))
    args.dry_run = True
    cmd_digest(cfg, store, args)
    cmd_export(cfg, store, args)


CMDS = {
    "collect": cmd_collect, "digest": cmd_digest, "export": cmd_export,
    "run": cmd_run, "demo": cmd_demo, "status": cmd_status, "probe": cmd_probe,
}


def main() -> int:
    ap = argparse.ArgumentParser(description="노동법·고용노동부 고시 모니터")
    ap.add_argument("command", choices=CMDS.keys())
    ap.add_argument("uid", nargs="?", help="status 명령에서 대상 항목 uid")
    ap.add_argument("value", nargs="?", help="status 명령에서 바꿀 값 (신규/검토중/완료)")
    ap.add_argument("--assignee", default="", help="담당자")
    ap.add_argument("--dry-run", action="store_true", help="메일을 보내지 않고 미리보기만 생성")
    ap.add_argument("--no-llm", action="store_true",
                    help="AI 요약 없이 수집·키워드 필터까지만 (API 키 불필요)")
    ap.add_argument("--config", default=None)
    ap.add_argument("--db", default="data/monitor.db")
    args = ap.parse_args()

    cfg = conf.load(args.config)
    store = Store(args.db)
    try:
        CMDS[args.command](cfg, store, args)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("실행 실패: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
