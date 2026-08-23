from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from typing import Any
from uuid import UUID

from rahband.domain.events import OwnerAccess, PassageDirection, PassageEvent
from rahband.domain.plate import Plate


class SqlitePassageOutbox:
    """Durable local spool for SQL Server passage writes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS passage_outbox (
                    event_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_passage_outbox_created_at ON passage_outbox(created_at)"
            )
            connection.execute("PRAGMA optimize")

    def enqueue(self, event: PassageEvent, error: Exception | str) -> None:
        payload = json.dumps(self._serialize(event), ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO passage_outbox(event_id, payload, created_at, attempts, last_error)
                   VALUES (?, ?, ?, 0, ?)
                   ON CONFLICT(event_id) DO UPDATE SET last_error=excluded.last_error""",
                (str(event.id), payload, datetime.now().isoformat(), str(error)[:500]),
            )

    def pending(self, limit: int = 100) -> list[PassageEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM passage_outbox ORDER BY created_at LIMIT ?", (max(1, limit),)
            ).fetchall()
        return [self._deserialize(json.loads(row["payload"])) for row in rows]

    def acknowledge(self, event_id: UUID) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM passage_outbox WHERE event_id = ?", (str(event_id),))

    def failed(self, event_id: UUID, error: Exception | str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE passage_outbox SET attempts=attempts+1, last_error=? WHERE event_id=?",
                (str(error)[:500], str(event_id)),
            )

    def count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM passage_outbox").fetchone()[0])

    @staticmethod
    def _serialize(event: PassageEvent) -> dict[str, Any]:
        owner = event.owner
        return {
            "id": str(event.id), "log_id": str(event.log_id),
            "two": event.plate.two, "alpha": event.plate.alpha,
            "three": event.plate.three, "city": event.plate.city,
            "occurred_at": event.occurred_at.isoformat(), "direction": event.direction.value,
            "camera_ip": event.camera_ip, "plate_string": event.plate_string,
            "owner": None if owner is None else {
                "car_id": owner.car_id, "full_name": owner.full_name, "model": owner.model,
                "color": owner.color, "access": owner.access, "unit": owner.unit,
                "access_type": owner.access_type,
            },
        }

    @staticmethod
    def _deserialize(value: dict[str, Any]) -> PassageEvent:
        raw_owner = value["owner"]
        owner = OwnerAccess(**raw_owner) if raw_owner else None
        return PassageEvent(
            id=UUID(value["id"]), log_id=UUID(value["log_id"]),
            plate=Plate(value["two"], value["alpha"], value["three"], value["city"]),
            occurred_at=datetime.fromisoformat(value["occurred_at"]),
            direction=PassageDirection(value["direction"]), camera_ip=value["camera_ip"],
            plate_string=value["plate_string"], owner=owner,
        )


class OutboxReplayer(Thread):
    def __init__(self, outbox: SqlitePassageOutbox, repository: Any, retry_seconds: float = 5) -> None:
        super().__init__(name="sql-outbox-replayer", daemon=True)
        self._outbox = outbox
        self._repository = repository
        self._retry_seconds = retry_seconds
        self._stop_event = Event()
        self._logger = logging.getLogger(__name__)

    def stop(self) -> None:
        self._stop_event.set()

    def replay_once(self) -> int:
        completed = 0
        for event in self._outbox.pending():
            try:
                owner = self._repository.find_owner(event.plate)
                self._repository.insert_event(replace(event, owner=owner))
                self._outbox.acknowledge(event.id)
                completed += 1
            except Exception as error:
                self._outbox.failed(event.id, error)
                break
        return completed

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                completed = self.replay_once()
                if completed:
                    self._logger.info("replayed %s queued database event(s)", completed)
            except Exception as error:
                self._logger.warning("database outbox retry deferred: %s", error)
            self._stop_event.wait(self._retry_seconds)
