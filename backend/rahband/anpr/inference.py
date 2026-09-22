from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
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
    character_confidences: tuple[float, ...] = ()
    image_quality: float = 0.0
    sharpness: float = 0.0
    plate_width: int = 0
    plate_height: int = 0


@dataclass(frozen=True, slots=True)
class CharacterCandidate:
    x1: float
    y1: float
    x2: float
    y2: float
    code: str
    confidence: float


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
                    plate, character_scores = self._read_characters(plate_image)
                    if plate is None:
                        continue
                    char_confidence = sum(character_scores) / len(character_scores)
                    detector_confidence = float(boxes.conf[index])
                    plate_height, plate_width = plate_image.shape[:2]
                    sharpness = self._sharpness(plate_image)
                    image_quality = self._quality_score(plate_width, plate_height, sharpness)
                    recognitions.append(
                        Recognition(
                            plate=plate,
                            confidence=min(detector_confidence, char_confidence),
                            plate_image=plate_image.copy(),
                            vehicle_frame=frame.copy(),
                            character_confidences=character_scores,
                            image_quality=image_quality,
                            sharpness=sharpness,
                            plate_width=plate_width,
                            plate_height=plate_height,
                        )
                    )
            return sorted(recognitions, key=lambda item: item.confidence, reverse=True)

    def _read_characters(self, plate_image: Any) -> tuple[Plate | None, tuple[float, ...]]:
        import cv2

        results: list[tuple[Plate, tuple[float, ...]]] = []
        original = self._read_characters_once(plate_image)
        if original[0] is not None:
            results.append(original)  # type: ignore[arg-type]
            if sum(original[1]) / len(original[1]) >= 0.78:
                return original

        # Upscaling before OCR preserves thin strokes which commonly separate
        # 2/3 and 4/5 on compressed RTSP frames.
        height, width = plate_image.shape[:2]
        scale = max(2.0, min(3.0, 240.0 / max(width, 1)))
        upscaled = cv2.resize(
            plate_image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC,
        )
        for variant in (upscaled, self._enhance_plate(upscaled)):
            result = self._read_characters_once(variant)
            if result[0] is not None:
                results.append(result)  # type: ignore[arg-type]
        if not results:
            return None, ()
        return max(results, key=lambda item: (sum(item[1]) / len(item[1]), min(item[1])))

    def _read_characters_once(self, plate_image: Any) -> tuple[Plate | None, tuple[float, ...]]:
        results = self._character_model.predict(
            plate_image, imgsz=320, conf=self._character_confidence, verbose=False,
        )
        if not results:
            return None, ()
        boxes = results[0].boxes
        candidates = []
        for index in range(len(boxes)):
            class_id = int(boxes.cls[index])
            if class_id not in CHAR_ID_TO_CODE:
                continue
            x1, y1, x2, y2 = (float(value) for value in boxes.xyxy[index].tolist())
            candidates.append(CharacterCandidate(
                x1, y1, x2, y2, CHAR_ID_TO_CODE[class_id], float(boxes.conf[index]),
            ))
        characters = self._select_plate_characters(self._suppress_overlaps(candidates))
        if characters is None:
            return None, ()
        value = "".join(character.code for character in characters)
        try:
            plate = Plate.from_detection(value)
        except ValueError:
            return None, ()
        if plate.alpha_code.isdigit():
            return None, ()
        return plate, tuple(character.confidence for character in characters)

    @staticmethod
    def _suppress_overlaps(candidates: list[CharacterCandidate]) -> list[CharacterCandidate]:
        kept: list[CharacterCandidate] = []
        for candidate in sorted(candidates, key=lambda item: item.confidence, reverse=True):
            if any(OpenVinoAnprEngine._intersection_over_union(candidate, other) >= 0.45 for other in kept):
                continue
            kept.append(candidate)
        return sorted(kept, key=lambda item: (item.x1 + item.x2) / 2)

    @staticmethod
    def _intersection_over_union(first: CharacterCandidate, second: CharacterCandidate) -> float:
        width = max(0.0, min(first.x2, second.x2) - max(first.x1, second.x1))
        height = max(0.0, min(first.y2, second.y2) - max(first.y1, second.y1))
        intersection = width * height
        first_area = max(0.0, first.x2 - first.x1) * max(0.0, first.y2 - first.y1)
        second_area = max(0.0, second.x2 - second.x1) * max(0.0, second.y2 - second.y1)
        union = first_area + second_area - intersection
        return intersection / union if union else 0.0

    @staticmethod
    def _select_plate_characters(candidates: list[CharacterCandidate]) -> tuple[CharacterCandidate, ...] | None:
        if len(candidates) < 8:
            return None
        # Limit pathological detections before trying combinations.
        pool = sorted(sorted(candidates, key=lambda item: item.confidence, reverse=True)[:14], key=lambda item: item.x1)
        best: tuple[float, tuple[CharacterCandidate, ...]] | None = None
        for selected in combinations(pool, 8):
            if selected[2].code.isdigit() or any(not item.code.isdigit() for index, item in enumerate(selected) if index != 2):
                continue
            centers = [(item.x1 + item.x2) / 2 for item in selected]
            gaps = [right - left for left, right in zip(centers, centers[1:])]
            if not gaps or min(gaps) <= 0:
                continue
            mean_gap = sum(gaps) / len(gaps)
            irregularity = sum(abs(gap - mean_gap) for gap in gaps) / (len(gaps) * mean_gap)
            score = sum(item.confidence for item in selected) / 8 - min(irregularity, 2.0) * 0.06
            if best is None or score > best[0]:
                best = (score, selected)
        return best[1] if best else None

    @staticmethod
    def _enhance_plate(plate_image: Any) -> Any:
        import cv2

        lab = cv2.cvtColor(plate_image, cv2.COLOR_BGR2LAB)
        lightness, channel_a, channel_b = cv2.split(lab)
        lightness = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4)).apply(lightness)
        enhanced = cv2.cvtColor(cv2.merge((lightness, channel_a, channel_b)), cv2.COLOR_LAB2BGR)
        blurred = cv2.GaussianBlur(enhanced, (0, 0), 1.0)
        return cv2.addWeighted(enhanced, 1.35, blurred, -0.35, 0)

    @staticmethod
    def _sharpness(plate_image: Any) -> float:
        import cv2

        gray = cv2.cvtColor(plate_image, cv2.COLOR_BGR2GRAY)
        return min(float(cv2.Laplacian(gray, cv2.CV_64F).var()) / 500.0, 1.0)

    @staticmethod
    def _quality_score(width: int, height: int, sharpness: float) -> float:
        resolution = min(width / 220.0, height / 55.0, 1.0)
        return round(0.65 * sharpness + 0.35 * max(0.0, resolution), 4)

    @staticmethod
    def _crop_roi(frame: Any, roi: RoiSettings | None) -> Any:
        if roi is None:
            return frame
        height, width = frame.shape[:2]
        scale_x = width / roi.reference_width
        scale_y = height / roi.reference_height
        x1 = round(roi.x1 * scale_x)
        y1 = round(roi.y1 * scale_y)
        x2 = round(roi.x2 * scale_x)
        y2 = round(roi.y2 * scale_y)
        return frame[max(0, y1):min(height, y2), max(0, x1):min(width, x2)]

    @staticmethod
    def _safe_crop(image: Any, x1: int, y1: int, x2: int, y2: int) -> Any:
        height, width = image.shape[:2]
        pad_x = max(2, int((x2 - x1) * 0.04))
        pad_y = max(2, int((y2 - y1) * 0.08))
        return image[max(0, y1 - pad_y):min(height, y2 + pad_y), max(0, x1 - pad_x):min(width, x2 + pad_x)]
