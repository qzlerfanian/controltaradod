import pytest

from rahband.domain.plate import Plate


def test_database_plate_and_detection_have_same_canonical_key() -> None:
    from_database = Plate(two="29", alpha="د", three="691", city="12")
    from_model = Plate.from_detection("29d69112")
    assert from_database.canonical == from_model.canonical == "29d69112"
    assert from_model.persian_alpha == "د"


def test_invalid_plate_is_rejected() -> None:
    with pytest.raises(ValueError):
        Plate.from_detection("29d69")

