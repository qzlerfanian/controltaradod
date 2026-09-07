from __future__ import annotations

from pathlib import Path
from uuid import UUID


class ImageStore:
    def __init__(self, root: str | Path, *, jpeg_quality: int = 90) -> None:
        self.root = Path(root).resolve()
        self.plates = self.root / "plates"
        self.vehicles = self.root / "vehicles"
        self.plates.mkdir(parents=True, exist_ok=True)
        self.vehicles.mkdir(parents=True, exist_ok=True)
        self._quality = jpeg_quality

    def save(self, log_id: UUID, plate_image: object, vehicle_frame: object) -> tuple[Path, Path]:
        plate_path = self.plates / f"{log_id}.jpg"
        vehicle_path = self.vehicles / f"{log_id}.jpg"
        self._write_jpeg(plate_path, plate_image)
        self._write_jpeg(vehicle_path, vehicle_frame)
        return plate_path, vehicle_path

    def path_for(self, kind: str, log_id: UUID) -> Path | None:
        directory = self.plates if kind == "plate" else self.vehicles if kind == "vehicle" else None
        if directory is None:
            return None
        # New images are JPEG/lowercase. PNG and uppercase UUID variants keep
        # compatibility with images copied from the legacy ANPR application.
        identifier = str(log_id)
        for stem in (identifier, identifier.upper()):
            for extension in (".jpg", ".jpeg", ".png"):
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
