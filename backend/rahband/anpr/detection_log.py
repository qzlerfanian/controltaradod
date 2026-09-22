from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from rahband.config import CameraSettings

from .inference import Recognition


class DetectionAuditLog:
    """Thread-safe, bounded text log of raw and confirmed ANPR results."""

    def __init__(
        self,
        path: str | Path,
        *,
        enabled: bool = True,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
    ) -> None:
        self.path = Path(path).resolve()
        self.enabled = enabled
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._lock = Lock()
        self._logger = logging.getLogger(__name__)
        if enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, stage: str, camera: CameraSettings, recognition: Recognition) -> None:
        if not self.enabled:
            return
        scores = ",".join(f"{score:.3f}" for score in recognition.character_confidences)
        line = (
            f"{datetime.now(timezone.utc).isoformat(timespec='milliseconds')} "
            f"stage={stage} camera_id={camera.id} camera_ip={camera.ip} "
            f"database_camera={camera.database_name or ''} direction={camera.direction} "
            f"plate={recognition.plate.canonical} confidence={recognition.confidence:.3f} "
            f"quality={recognition.image_quality:.3f} sharpness={recognition.sharpness:.3f} "
            f"size={recognition.plate_width}x{recognition.plate_height} "
            f"characters=[{scores}]\n"
        )
        try:
            with self._lock:
                self._rotate_if_needed(len(line.encode("utf-8")))
                with self.path.open("a", encoding="utf-8", newline="") as stream:
                    stream.write(line)
        except OSError as error:
            self._logger.warning("detection audit log could not be written (%s)", error)

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if not self.path.exists() or self.path.stat().st_size + incoming_bytes <= self.max_bytes:
            return
        if self.backup_count <= 0:
            self.path.unlink(missing_ok=True)
            return
        oldest = self.path.with_name(f"{self.path.name}.{self.backup_count}")
        oldest.unlink(missing_ok=True)
        for number in range(self.backup_count - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{number}")
            if source.exists():
                source.replace(self.path.with_name(f"{self.path.name}.{number + 1}"))
        self.path.replace(self.path.with_name(f"{self.path.name}.1"))
