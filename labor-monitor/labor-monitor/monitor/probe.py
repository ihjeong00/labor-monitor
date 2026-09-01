"""target 값 확인기.

법제처 DRF API는 서비스마다 target 값이 다른데 활용가이드에서 값을 찾기가 번거롭습니다.
틀린 target 을 넣으면 에러가 아니라 **조용히 빈 결과**가 나와서 한참 못 알아챕니다.

이 명령은 config 의 target_candidates 를 순서대로 실제로 찔러보고,
결과가 나오는 값을 알려줍니다. 확인된 값을 config.yaml 에 적고 verified: true 로 바꾸세요.

  python run.py probe
"""
from __future__ import annotations

import logging

from .sources import _fetch_target, _law_request, _lookback_range

log = logging.getLogger("monitor.probe")

OK, EMPTY, FAIL = "\033[32m성공\033[0m", "\033[33m빈 결과\033[0m", "\033[31m실패\033[0m"


def _try(target: str, oc: str, org: str, spec: dict) -> tuple[str, int, str]:
    probe_spec = {**spec, "target": target, "display": 3, "use_lookback": False}
    records, err = _fetch_target(probe_spec, oc, org, "")
    if err:
        return FAIL, 0, err[:90]
    if not records:
        return EMPTY, 0, ""
    sample = records[0]
    fields = ", ".join(list(sample.keys())[:6])
    return OK, len(records), fields


def probe(cfg: dict, oc: str) -> None:
    if not oc:
        raise SystemExit("LAW_OC 가 설정되지 않았습니다.")

    api = cfg["sources"]["law_api"]
    org = api.get("org", "")
    print("\n법제처 DRF API target 확인\n" + "─" * 66)

    found: dict[str, str] = {}
    for spec in api.get("drf_sources", []):
        name = spec.get("name", spec["target"])
        print(f"\n▶ {name}")
        candidates = spec.get("target_candidates") or [spec["target"]]
        for target in candidates:
            status, n, note = _try(target, oc, org, spec)
            print(f"   target={target:<16} {status}" + (f"  {n}건" if n else ""))
            if note:
                print(f"      {note}")
            if status == OK:
                found[name] = target
                break

    # 신구법 비교
    diff = api.get("diff", {})
    for key, label in (("admrul", "행정규칙 신구법 비교"), ("law", "법령 신구법 비교")):
        sub = diff.get(key)
        if not sub:
            continue
        print(f"\n▶ {label}")
        for target in sub.get("list_candidates", [sub.get("list_target")]):
            try:
                records = _law_request(
                    {"OC": oc, "target": target, "type": "JSON", "display": 3}
                )
                status = OK if records else EMPTY
                print(f"   target={target:<16} {status}"
                      + (f"  {', '.join(list(records[0].keys())[:6])}" if records else ""))
                if records:
                    found[label] = target
                    break
            except Exception as exc:  # noqa: BLE001
                print(f"   target={target:<16} {FAIL}  {str(exc)[:80]}")

    print("\n" + "─" * 66)
    if found:
        print("\nconfig.yaml 에 아래 값을 반영하고 verified: true 로 바꾸세요.\n")
        for name, target in found.items():
            print(f"  {name:<34} target: \"{target}\"")
    else:
        print("\n확인된 target 이 없습니다. 확인할 것:")
        print("  · LAW_OC 값이 맞는지 (발급 이메일의 @ 앞부분)")
        print("  · 부가서비스 이용 권한이 있는지 (법령서비스 신청 시 자동 포함)")
        print("  · 활용가이드에서 정확한 target 확인: https://open.law.go.kr/LSO/openApi/guideList.do")
    print()
