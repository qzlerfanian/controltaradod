from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock


class PassageDeduplicator:
    """Suppresses the same plate across all cameras for a bounded window."""

    def __init__(self, window_seconds: float) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._window = timedelta(seconds=window_seconds)
        self._seen: dict[str, datetime] = {}
        self._lock = Lock()

    def should_accept(self, plate: str, *, now: datetime | None = None) -> bool:
        key = self._normalize(plate)
        if not key:
            raise ValueError("plate cannot be empty")
        current = self._utc(now)
        with self._lock:
            expired = [item for item, seen_at in self._seen.items() if current - seen_at >= self._window]
            for item in expired:
                del self._seen[item]
            last_seen = self._seen.get(key)
            if last_seen is not None and current - last_seen < self._window:
                return False
            self._seen[key] = current
            return True

    @staticmethod
    def _normalize(plate: str) -> str:
        return "".join(plate.split()).upper()

    @staticmethod
    def _utc(value: datetime | None) -> datetime:
        result = value or datetime.now(timezone.utc)
        if result.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return result.astimezone(timezone.utc)

