from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from rahband.config import RoiSettings
from rahband.domain.plate import Plate


CHAR_ID_TO_CODE = {
    0: "0", 1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9",
    11: "b", 13: "T", 15: "j", 19: "d", 24: "s", 26: "x", 28: "t", 30: "A",
    33: "g", 35: "c", 36: "L", 37: "m", 38: "n", 39: "h", 40: "v", 41: "y", 42: "q",
}


@dataclass(frozen=True, slots=True)
class Recognition:
    plate: Plate
    confidence: float
    plate_image: Any
    vehicle_frame: Any


class OpenVinoAnprEngine:
    """One shared pair of OpenVINO-exported YOLO models for all cameras."""

    def __init__(
        self,
        plate_model_path: str,
        character_model_path: str,
        *,
        image_size: int = 256,
        plate_confidence: float = 0.04,
        character_confidence: float = 0.2,
    ) -> None:
        from ultralytics import YOLO

        plate_path = Path(plate_model_path).resolve()
        character_path = Path(character_model_path).resolve()
        if not plate_path.exists():
            raise FileNotFoundError(f"plate model not found: {plate_path}")
        if not character_path.exists():
            raise FileNotFoundError(f"character model not found: {character_path}")
        self._plate_model = YOLO(str(plate_path))
        self._character_model = YOLO(str(character_path))
        self._image_size = image_size
        self._plate_confidence = plate_confidence
        self._character_confidence = character_confidence
        self._lock = Lock()

    def recognize(self, frame: Any, roi: RoiSettings | None) -> list[Recognition]:
        view = self._crop_roi(frame, roi)
        if view.size == 0:
            return []
        with self._lock:
            results = self._plate_model.predict(
                view, imgsz=self._image_size, conf=self._plate_confidence, verbose=False,
            )
            recognitions: list[Recognition] = []
            for result in results:
                boxes = result.boxes
                for index in range(len(boxes)):
                    x1, y1, x2, y2 = (int(value) for value in boxes.xyxy[index].tolist())
                    plate_image = self._safe_crop(view, x1, y1, x2, y2)
                    if plate_image.size == 0:
                        continue
                    plate, char_confidence = self._read_characters(plate_image)
                    if plate is None:
                        continue
                    detector_confidence = float(boxes.conf[index])
                    recognitions.append(
                        Recognition(
                            plate=plate,
                            confidence=min(detector_confidence, char_confidence),
                            plate_image=plate_image.copy(),
                            vehicle_frame=frame.copy(),
                        )
                    )
            return sorted(recognitions, key=lambda item: item.confidence, reverse=True)

    def _read_characters(self, plate_image: Any) -> tuple[Plate | None, float]:
        results = self._character_model.predict(plate_image, imgsz=320, conf=self._character_confidence, verbose=False)
        if not results:
            return None, 0.0
        boxes = results[0].boxes
        ordered = sorted(
            ((float(boxes.xyxy[index][0]), int(boxes.cls[index]), float(boxes.conf[index])) for index in range(len(boxes))),
            key=lambda value: value[0],
        )
        characters = [(CHAR_ID_TO_CODE[class_id], confidence) for _, class_id, confidence in ordered if class_id in CHAR_ID_TO_CODE]
        value = "".join(character for character, _ in characters)
        try:
            plate = Plate.from_detection(value)
        except ValueError:
            return None, 0.0
        if plate.alpha_code.isdigit():
            return None, 0.0
        confidence = sum(score for _, score in characters) / len(characters)
        return plate, confidence

    @staticmethod
    def _crop_roi(frame: Any, roi: RoiSettings | None) -> Any:
        if roi is None:
            return frame
        height, width = frame.shape[:2]
        return frame[max(0, roi.y1):min(height, roi.y2), max(0, roi.x1):min(width, roi.x2)]

    @staticmethod
    def _safe_crop(image: Any, x1: int, y1: int, x2: int, y2: int) -> Any:
        height, width = image.shape[:2]
        pad_x = max(2, int((x2 - x1) * 0.04))
        pad_y = max(2, int((y2 - y1) * 0.08))
        return image[max(0, y1 - pad_y):min(height, y2 + pad_y), max(0, x1 - pad_x):min(width, x2 + pad_x)]

