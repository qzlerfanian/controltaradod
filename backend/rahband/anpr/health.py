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
    disconnected_since: datetime | None = None


class CameraHealthRegistry:
    def __init__(self, camera_ids: list[str]) -> None:
        started_at = datetime.now(timezone.utc)
        self._items = {
            camera_id: CameraHealth(camera_id, disconnected_since=started_at)
            for camera_id in camera_ids
        }
        self._previews: dict[str, bytes] = {}
        self._lock = Lock()

    def connected(self, camera_id: str) -> None:
        with self._lock:
            item = self._items[camera_id]
            item.online = True
            item.error = None
            item.disconnected_since = None

    def frame(self, camera_id: str) -> None:
        with self._lock:
            item = self._items[camera_id]
            item.online = True
            item.last_frame_at = datetime.now(timezone.utc)
            item.error = None
            item.disconnected_since = None

    def detection(self, camera_id: str) -> None:
        with self._lock:
            self._items[camera_id].last_detection_at = datetime.now(timezone.utc)

    def preview(self, camera_id: str, jpeg: bytes) -> None:
        with self._lock:
            self._previews[camera_id] = jpeg

    def latest_preview(self, camera_id: str) -> bytes | None:
        with self._lock:
            return self._previews.get(camera_id)

    def disconnected(self, camera_id: str, error: str) -> None:
        with self._lock:
            item = self._items[camera_id]
            item.online = False
            item.reconnect_count += 1
            item.error = error[:200]
            if item.disconnected_since is None:
                item.disconnected_since = datetime.now(timezone.utc)

    def snapshot(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {camera_id: asdict(item) for camera_id, item in self._items.items()}
