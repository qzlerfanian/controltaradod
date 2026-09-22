from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from rahband.domain.events import PassageEvent


class ImageStore:
    def __init__(
        self,
        root: str | Path,
        *,
        jpeg_quality: int = 90,
        index_path: str | Path | None = None,
        database_utc_offset_minutes: int = 0,
    ) -> None:
        self.root = Path(root).resolve()
        self.plates = self.root / "plates"
        self.vehicles = self.root / "vehicles"
        self.plates.mkdir(parents=True, exist_ok=True)
        self.vehicles.mkdir(parents=True, exist_ok=True)
        self._quality = jpeg_quality
        self._database_timezone = timezone(timedelta(minutes=database_utc_offset_minutes))
        self.index_path = Path(index_path).resolve() if index_path else self.root / "image_index.sqlite3"
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_index()

    def save(
        self,
        event: PassageEvent,
        plate_image: object,
        vehicle_frame: object,
    ) -> tuple[Path, Path]:
        log_id = event.log_id
        plate_path = self.plates / f"{log_id}.jpg"
        vehicle_path = self.vehicles / f"{log_id}.jpg"
        self._write_jpeg(plate_path, plate_image)
        self._write_jpeg(vehicle_path, vehicle_frame)
        self._index_event(event)
        return plate_path, vehicle_path

    def recent_detections(self, limit: int = 20) -> list[dict[str, object]]:
        safe_limit = max(1, min(limit, 200))
        with sqlite3.connect(self.index_path, timeout=5) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT log_id, event_id, plate_number, camera_ip, occurred_at,
                       direction, two, alpha, three, city, full_name, model,
                       color, unit, access
                FROM image_events
                WHERE event_id IS NOT NULL
                ORDER BY occurred_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        items: list[dict[str, object]] = []
        for row in rows:
            try:
                log_id = UUID(str(row["log_id"]))
            except (TypeError, ValueError):
                continue
            items.append({
                "id": row["event_id"],
                "log_id": str(log_id),
                "plate_number": row["plate_number"],
                "plate_string": row["plate_number"],
                "camera_ip": row["camera_ip"],
                "trans_time": datetime.fromtimestamp(row["occurred_at"], timezone.utc),
                "direction": row["direction"],
                "two": row["two"],
                "alpha": row["alpha"],
                "three": row["three"],
                "city": row["city"],
                "full_name": row["full_name"],
                "model": row["model"],
                "color": row["color"],
                "unit": row["unit"],
                "access": bool(row["access"]),
                "plate_image_url": (
                    f"/api/v1/images/plate/{log_id}" if self.path_for("plate", log_id) else None
                ),
                "vehicle_image_url": (
                    f"/api/v1/images/vehicle/{log_id}" if self.path_for("vehicle", log_id) else None
                ),
            })
        return items

    def matching_log_id(
        self,
        *,
        plate_number: str | None,
        camera_ip: str | None,
        occurred_at: datetime | None,
        max_delta_seconds: float,
    ) -> UUID | None:
        if not plate_number or not camera_ip or occurred_at is None:
            return None
        plate_key = self._normalize_plate(plate_number)
        camera_key = self._normalize_camera(camera_ip)
        if not plate_key or not camera_key:
            return None
        event_time = self._timestamp(occurred_at, naive_timezone=self._database_timezone)
        with sqlite3.connect(self.index_path, timeout=5) as connection:
            rows = connection.execute(
                """
                SELECT log_id
                FROM image_events
                WHERE plate_key = ? AND camera_key = ?
                  AND occurred_at BETWEEN ? AND ?
                ORDER BY ABS(occurred_at - ?) ASC
                LIMIT 10
                """,
                (
                    plate_key,
                    camera_key,
                    event_time - max_delta_seconds,
                    event_time + max_delta_seconds,
                    event_time,
                ),
            ).fetchall()
        for row in rows:
            try:
                log_id = UUID(str(row[0]))
            except (TypeError, ValueError):
                continue
            if self.path_for("plate", log_id) or self.path_for("vehicle", log_id):
                return log_id
        return None

    def path_for(self, kind: str, log_id: UUID) -> Path | None:
        primary = self.plates if kind == "plate" else self.vehicles if kind == "vehicle" else None
        if primary is None:
            return None
        # Keep compatibility with images produced by previous installations:
        # UUIDs may be upper-case or compact and extensions/directories may
        # have a different case. Direct candidate checks keep this inexpensive
        # because it runs for every row in the passages page.
        directory_names = (
            ("plates", "plate", "Plate", "Plates", "plate_images", "PlateImages")
            if kind == "plate"
            else ("vehicles", "vehicle", "Vehicle", "Vehicles", "cars", "Cars", "vehicle_images", "VehicleImages")
        )
        directories = [primary, self.root, *(self.root / name for name in directory_names)]
        identifiers = (str(log_id), str(log_id).upper(), log_id.hex, log_id.hex.upper())
        extensions = (".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG")
        visited: set[Path] = set()
        for directory in directories:
            if directory in visited:
                continue
            visited.add(directory)
            for stem in identifiers:
                for extension in extensions:
                    path = directory / f"{stem}{extension}"
                    if path.is_file():
                        return path
        return None

    def _write_jpeg(self, path: Path, image: object) -> None:
        import cv2

        success, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self._quality])
        if not success:
            raise OSError(f"could not encode image: {path.name}")
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(encoded.tobytes())
        temporary.replace(path)

    def _initialize_index(self) -> None:
        with sqlite3.connect(self.index_path, timeout=5) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS image_events (
                    log_id TEXT PRIMARY KEY,
                    plate_key TEXT NOT NULL,
                    camera_key TEXT NOT NULL,
                    occurred_at REAL NOT NULL,
                    event_id TEXT,
                    plate_number TEXT,
                    camera_ip TEXT,
                    direction TEXT,
                    two TEXT,
                    alpha TEXT,
                    three TEXT,
                    city TEXT,
                    full_name TEXT,
                    model TEXT,
                    color TEXT,
                    unit TEXT,
                    access INTEGER
                )
                """
            )
            existing = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(image_events)")
            }
            additions = {
                "event_id": "TEXT", "plate_number": "TEXT", "camera_ip": "TEXT",
                "direction": "TEXT", "two": "TEXT", "alpha": "TEXT",
                "three": "TEXT", "city": "TEXT", "full_name": "TEXT",
                "model": "TEXT", "color": "TEXT", "unit": "TEXT", "access": "INTEGER",
            }
            for name, sql_type in additions.items():
                if name not in existing:
                    connection.execute(f"ALTER TABLE image_events ADD COLUMN {name} {sql_type}")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_image_events_match
                ON image_events (plate_key, camera_key, occurred_at)
                """
            )

    def _index_event(self, event: PassageEvent) -> None:
        owner = event.owner
        with sqlite3.connect(self.index_path, timeout=5) as connection:
            connection.execute(
                """
                INSERT INTO image_events (
                    log_id, plate_key, camera_key, occurred_at, event_id,
                    plate_number, camera_ip, direction, two, alpha, three, city,
                    full_name, model, color, unit, access
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(log_id) DO UPDATE SET
                    plate_key=excluded.plate_key,
                    camera_key=excluded.camera_key,
                    occurred_at=excluded.occurred_at,
                    event_id=excluded.event_id,
                    plate_number=excluded.plate_number,
                    camera_ip=excluded.camera_ip,
                    direction=excluded.direction,
                    two=excluded.two,
                    alpha=excluded.alpha,
                    three=excluded.three,
                    city=excluded.city,
                    full_name=excluded.full_name,
                    model=excluded.model,
                    color=excluded.color,
                    unit=excluded.unit,
                    access=excluded.access
                """,
                (
                    str(event.log_id),
                    self._normalize_plate(event.plate.canonical),
                    self._normalize_camera(event.camera_ip),
                    self._timestamp(event.occurred_at),
                    str(event.id), event.plate.canonical, event.camera_ip,
                    event.direction.value, event.plate.two, event.plate.persian_alpha,
                    event.plate.three, event.plate.city,
                    owner.full_name if owner else None,
                    owner.model if owner else None,
                    owner.color if owner else None,
                    owner.unit if owner else None,
                    1 if owner and owner.access else 0,
                ),
            )

    @staticmethod
    def _normalize_plate(value: str) -> str:
        return re.sub(r"[^0-9a-zA-Z\u0600-\u06ff]", "", value).casefold()

    @staticmethod
    def _normalize_camera(value: str) -> str:
        return value.strip().casefold()

    @staticmethod
    def _timestamp(
        value: datetime, *, naive_timezone: timezone = timezone.utc,
    ) -> float:
        # SQL Server DATETIME has no timezone information. Interpret a naive
        # value in the configured database/site timezone before comparing it
        # with the UTC timestamp stored in the local image index.
        if value.tzinfo is None:
            value = value.replace(tzinfo=naive_timezone)
        return value.timestamp()
