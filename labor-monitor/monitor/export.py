"""대시보드가 읽을 data.json 생성.

대시보드는 정적 파일이므로 백엔드 없이 이 JSON만 있으면 동작합니다.
나중에 API 서버를 붙이면 이 함수의 출력 형태를 그대로 응답하면 됩니다.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .store import Store


def build(store: Store, today: date, issue: int) -> dict:
    rows = store.archive()
    items = [
        {
            "uid": r["uid"],
            "kind": r["kind"],
            "title": r["title"],
            "summary": r["summary"],
            "scope": r["scope"],
            "effective_date": r["effective_date"] or "",
            "published": r["published"] or "",
            "action_level": r["action_level"],
            "status": r["status"],
            "assignee": r["assignee"] or "",
            "source": r["source"],
            "link": r["link"],
            "reason": r["reason"],
            "diff": json.loads(r["diff"]) if r["diff"] else [],
            "diff_source": r["diff_source"] or "",
        }
        for r in rows
    ]
    return {
        "generated_at": today.isoformat(),
        "issue": issue,
        "stats": store.stats(),
        "items": items,
    }


def write(store: Store, today: date, issue: int, path: str | Path = "dashboard/data.json") -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(build(store, today, issue), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return p
