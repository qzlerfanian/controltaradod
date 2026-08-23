from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from threading import Lock


@dataclass(slots=True)
class CameraHealth:
    camera_id: str
    online: bool = False
    last_frame_at: datetime | None = None
    last_detection_at: datetime | None = None
    reconnect_count: int = 0
    error: str | None = None


class CameraHealthRegistry:
    def __init__(self, camera_ids: list[str]) -> None:
        self._items = {camera_id: CameraHealth(camera_id) for camera_id in camera_ids}
        self._lock = Lock()

    def connected(self, camera_id: str) -> None:
        with self._lock:
            item = self._items[camera_id]
            item.online = True
            item.error = None

    def frame(self, camera_id: str) -> None:
        with self._lock:
            item = self._items[camera_id]
            item.online = True
            item.last_frame_at = datetime.now(timezone.utc)
            item.error = None

    def detection(self, camera_id: str) -> None:
        with self._lock:
            self._items[camera_id].last_detection_at = datetime.now(timezone.utc)

    def disconnected(self, camera_id: str, error: str) -> None:
        with self._lock:
            item = self._items[camera_id]
            item.online = False
            item.reconnect_count += 1
            item.error = error[:200]

    def snapshot(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {camera_id: asdict(item) for camera_id, item in self._items.items()}

