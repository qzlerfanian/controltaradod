from __future__ import annotations

import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread
from typing import Callable


class ImageRetentionService:
    def __init__(
        self, root: str | Path, retention_days: int = 60, max_disk_percent: float = 85,
        interval_seconds: float = 3600, disk_usage: Callable[[Path], object] = shutil.disk_usage,
    ) -> None:
        self.root = Path(root).resolve()
        self.retention_days = retention_days
        self.max_disk_percent = max_disk_percent
        self.interval_seconds = interval_seconds
        self._disk_usage = disk_usage

    def cleanup(self) -> dict[str, int | float]:
        files = sorted(
            (path for folder in (self.root / "plates", self.root / "vehicles") if folder.exists()
             for path in folder.glob("*.jpg") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
        )
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention_days)).timestamp()
        removed = 0
        for path in list(files):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                files.remove(path)
                removed += 1
        usage = self._disk_usage(self.root)
        percent = (usage.used / usage.total * 100) if usage.total else 0
        for path in files:
            if percent <= self.max_disk_percent:
                break
            path.unlink(missing_ok=True)
            removed += 1
            usage = self._disk_usage(self.root)
            percent = (usage.used / usage.total * 100) if usage.total else 0
        return {"removed": removed, "disk_percent": round(percent, 2)}

    def statistics(self) -> dict[str, int | float]:
        files = [path for folder in (self.root / "plates", self.root / "vehicles") if folder.exists()
                 for path in folder.glob("*.jpg") if path.is_file()]
        usage = self._disk_usage(self.root)
        return {
            "files": len(files), "bytes": sum(path.stat().st_size for path in files),
            "retention_days": self.retention_days, "max_disk_percent": self.max_disk_percent,
            "disk_total": usage.total, "disk_used": usage.used, "disk_free": usage.free,
            "disk_percent": round(usage.used / usage.total * 100, 2) if usage.total else 0,
        }


class ImageRetentionWorker(Thread):
    def __init__(self, service: ImageRetentionService) -> None:
        super().__init__(name="image-retention", daemon=True)
        self.service = service
        self._stop_event = Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.service.cleanup()
            except OSError:
                pass
            self._stop_event.wait(self.service.interval_seconds)

    def stop(self) -> None:
        self._stop_event.set()
