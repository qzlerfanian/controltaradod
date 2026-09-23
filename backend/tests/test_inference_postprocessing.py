import numpy as np

from rahband.anpr.inference import CharacterCandidate, OpenVinoAnprEngine, Recognition
from rahband.anpr.worker import CameraWorker
from rahband.config import RoiSettings
from rahband.domain.plate import Plate


def candidate(index: int, code: str, confidence: float = 0.9) -> CharacterCandidate:
    left = float(index * 12)
    return CharacterCandidate(left, 0.0, left + 10.0, 20.0, code, confidence)


def test_overlapping_character_box_with_lower_confidence_is_removed() -> None:
    original = candidate(0, "2", 0.95)
    duplicate = CharacterCandidate(1.0, 1.0, 9.0, 19.0, "3", 0.40)

    result = OpenVinoAnprEngine._suppress_overlaps([duplicate, original, candidate(1, "9")])

    assert [item.code for item in result] == ["2", "9"]


def test_best_valid_iranian_plate_layout_is_selected_from_extra_boxes() -> None:
    values = ["2", "9", "d", "6", "9", "1", "1", "2"]
    valid = [candidate(index, value) for index, value in enumerate(values)]
    extra_digit_in_letter_position = CharacterCandidate(25.0, 0.0, 31.0, 20.0, "3", 0.99)

    selected = OpenVinoAnprEngine._select_plate_characters(valid + [extra_digit_in_letter_position])

    assert selected is not None
    assert "".join(item.code for item in selected) == "29d69112"


def test_quality_rewards_sharp_and_sufficiently_large_plate_crop() -> None:
    small_blurred = OpenVinoAnprEngine._quality_score(90, 24, 0.15)
    large_sharp = OpenVinoAnprEngine._quality_score(240, 65, 0.85)
    assert large_sharp > small_blurred
    assert 0 <= small_blurred <= 1
    assert 0 <= large_sharp <= 1


def test_roi_is_scaled_from_reference_resolution_to_stream_resolution() -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    roi = RoiSettings(x1=1050, y1=450, x2=1650, y2=850)

    cropped = OpenVinoAnprEngine._crop_roi(frame, roi)

    assert cropped.shape[:2] == (267, 400)


def test_trigger_zone_accepts_only_plate_center_inside_box() -> None:
    zone = RoiSettings(x1=700, y1=500, x2=1600, y2=900)
    inside = Recognition(
        Plate.from_detection("29d69112"), 0.9, None, None,
        bbox_x1=700, bbox_y1=500, bbox_x2=900, bbox_y2=600,
        frame_width=1920, frame_height=1080,
    )
    outside = Recognition(
        Plate.from_detection("29d69112"), 0.9, None, None,
        bbox_x1=100, bbox_y1=100, bbox_x2=300, bbox_y2=200,
        frame_width=1920, frame_height=1080,
    )

    assert CameraWorker._inside_trigger_zone(inside, zone)
    assert not CameraWorker._inside_trigger_zone(outside, zone)


def test_trigger_zone_scales_with_stream_resolution() -> None:
    zone = RoiSettings(x1=960, y1=540, x2=1800, y2=1000)
    recognition = Recognition(
        Plate.from_detection("29d69112"), 0.9, None, None,
        bbox_x1=640, bbox_y1=360, bbox_x2=800, bbox_y2=450,
        frame_width=1280, frame_height=720,
    )

    assert CameraWorker._inside_trigger_zone(recognition, zone)
