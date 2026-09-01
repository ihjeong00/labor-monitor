"""SQLite 저장소.

중복 발송을 막는 게 이 모듈의 핵심 역할입니다.
한 번 본 uid 는 다시 요약하지도, 다시 메일에 넣지도 않습니다.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from .config import Item

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    uid             TEXT PRIMARY KEY,
    source          TEXT,
    kind            TEXT,
    title           TEXT,
    link            TEXT,
    published       TEXT,
    body            TEXT,
    relevant        INTEGER DEFAULT 0,
    summary         TEXT,
    effective_date  TEXT,
    scope           TEXT,
    action_level    TEXT DEFAULT 'none',
    reason          TEXT,
    diff            TEXT DEFAULT '',
    diff_source     TEXT DEFAULT '',
    status          TEXT DEFAULT '신규',
    assignee        TEXT DEFAULT '',
    first_seen      TEXT,
    notified        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notified ON items(notified);
CREATE INDEX IF NOT EXISTS idx_effective ON items(effective_date);
CREATE INDEX IF NOT EXISTS idx_relevant ON items(relevant);
"""


class Store:
    def __init__(self, path: str | Path = "data/monitor.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            # 이전 버전 DB를 위한 마이그레이션
            have = {r["name"] for r in c.execute("PRAGMA table_info(items)")}
            for col in ("diff", "diff_source"):
                if col not in have:
                    c.execute(f"ALTER TABLE items ADD COLUMN {col} TEXT DEFAULT ''")

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── 중복 판정 ────────────────────────────────────────────
    def unseen(self, items: list[Item]) -> list[Item]:
        """아직 저장된 적 없는 항목만 반환."""
        if not items:
            return []
        uids = [i.uid for i in items]
        with self._conn() as c:
            q = ",".join("?" * len(uids))
            rows = c.execute(f"SELECT uid FROM items WHERE uid IN ({q})", uids).fetchall()
        known = {r["uid"] for r in rows}
        return [i for i in items if i.uid not in known]

    # ── 쓰기 ─────────────────────────────────────────────────
    def upsert(self, items: list[Item]) -> int:
        if not items:
            return 0
        today = date.today().isoformat()
        rows = []
        for i in items:
            d = i.to_row()
            d["first_seen"] = d["first_seen"] or today
            d["relevant"] = int(d["relevant"])
            rows.append(d)
        cols = list(rows[0].keys())
        with self._conn() as c:
            c.executemany(
                f"INSERT OR REPLACE INTO items ({','.join(cols)}) "
                f"VALUES ({','.join(':' + k for k in cols)})",
                rows,
            )
        return len(rows)

    def mark_notified(self, uids: list[str]) -> None:
        if not uids:
            return
        with self._conn() as c:
            c.executemany("UPDATE items SET notified=1 WHERE uid=?", [(u,) for u in uids])

    def set_status(self, uid: str, status: str, assignee: str = "") -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE items SET status=?, assignee=? WHERE uid=?", (status, assignee, uid)
            )

    # ── 읽기 ─────────────────────────────────────────────────
    def pending_digest(self) -> list[dict]:
        """관련 있고 아직 메일에 넣지 않은 항목."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM items WHERE relevant=1 AND notified=0 "
                "ORDER BY CASE action_level WHEN 'urgent' THEN 0 WHEN 'review' THEN 1 ELSE 2 END, "
                "COALESCE(NULLIF(effective_date,''),'9999-12-31')"
            ).fetchall()
        return [dict(r) for r in rows]

    def archive(self, limit: int = 500) -> list[dict]:
        """대시보드용. 관련 있다고 판정된 전체 항목."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM items WHERE relevant=1 "
                "ORDER BY COALESCE(NULLIF(effective_date,''), first_seen) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) n FROM items").fetchone()["n"]
            rel = c.execute("SELECT COUNT(*) n FROM items WHERE relevant=1").fetchone()["n"]
            open_ = c.execute(
                "SELECT COUNT(*) n FROM items WHERE relevant=1 AND action_level!='none' "
                "AND status!='완료'"
            ).fetchone()["n"]
        return {"total": total, "relevant": rel, "open_actions": open_}
