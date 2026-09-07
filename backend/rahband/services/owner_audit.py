from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class OwnerAuditStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS owner_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT
                )
            """)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_owner_audit_owner_time "
                "ON owner_audit(owner_id, changed_at DESC)"
            )
            connection.execute("PRAGMA optimize")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def record(
        self, owner_id: int, action: str, *, actor: str = "system",
        before: dict[str, Any] | None = None, after: dict[str, Any] | None = None,
    ) -> None:
        serialize = lambda value: json.dumps(value, ensure_ascii=False, default=str) if value is not None else None
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO owner_audit(owner_id,action,actor,changed_at,before_json,after_json) VALUES(?,?,?,?,?,?)",
                (owner_id, action, actor, datetime.now(timezone.utc).isoformat(), serialize(before), serialize(after)),
            )

    def history(self, owner_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id,action,actor,changed_at,before_json,after_json FROM owner_audit "
                "WHERE owner_id=? ORDER BY changed_at DESC LIMIT ?", (owner_id, max(1, min(limit, 500))),
            ).fetchall()
        return [{
            "id": row[0], "action": row[1], "actor": row[2], "changed_at": row[3],
            "before": json.loads(row[4]) if row[4] else None,
            "after": json.loads(row[5]) if row[5] else None,
        } for row in rows]
