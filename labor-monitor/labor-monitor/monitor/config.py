"""설정 로딩."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load(path: str | Path | None = None) -> dict[str, Any]:
    path = Path(path) if path else ROOT / "config.yaml"
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("_root", str(ROOT))
    return cfg


def env(name: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.environ.get(name, default)
    if required and not val:
        raise SystemExit(
            f"환경변수 {name} 가 설정되지 않았습니다. README의 '환경변수' 절을 확인하세요."
        )
    return val


@dataclass
class Item:
    """수집 → 판정 → 저장을 통과하는 단일 항목."""

    uid: str                       # 중복 판정 키
    source: str = ""               # 어디서 왔는지 (표시용)
    kind: str = ""                 # 고시 / 법령 / 예고 / 공지 / 뉴스
    title: str = ""
    link: str = ""
    published: str = ""            # YYYY-MM-DD
    body: str = ""                 # 원문 일부 (판정 근거)

    # 아래는 LLM 판정 후 채워짐
    relevant: bool = False
    summary: str = ""
    effective_date: str = ""       # YYYY-MM-DD, 없으면 빈 문자열
    scope: str = ""                # 적용 대상
    action_level: str = "none"     # none / review / urgent
    reason: str = ""               # 판정 근거 한 줄

    # 신구조문 대비 (JSON 배열 문자열: [{article, old, new}, ...])
    diff: str = ""
    diff_source: str = ""

    # 운영 상태
    status: str = "신규"           # 신규 / 검토중 / 완료
    assignee: str = ""
    first_seen: str = ""
    notified: int = 0

    def to_row(self) -> dict:
        return asdict(self)
