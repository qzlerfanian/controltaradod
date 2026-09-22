from rahband.api.main import event_plate_key


def test_event_plate_key_uses_structured_database_columns() -> None:
    item = {
        "two": "52",
        "alpha": "ص",
        "three": "546",
        "city": "74",
        "plate_number": "52Sad54674",
    }

    assert event_plate_key(item) == "52x54674"


def test_event_plate_key_falls_back_to_plate_number() -> None:
    assert event_plate_key({"plate_number": "52Sad54674"}) == "52Sad54674"
