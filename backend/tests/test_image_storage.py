from datetime import datetime, timedelta, timezone
import sqlite3
from uuid import UUID

from rahband.anpr.storage import ImageStore
from rahband.domain.events import OwnerAccess, PassageDirection, PassageEvent
from rahband.domain.plate import Plate


def event_for(
    log_id: UUID,
    plate: str,
    camera: str,
    occurred_at: datetime,
    owner: OwnerAccess | None = None,
) -> PassageEvent:
    return PassageEvent(
        id=UUID("11111111-1111-4111-8111-111111111111"),
        log_id=log_id,
        plate=Plate.from_detection(plate),
        occurred_at=occurred_at,
        direction=PassageDirection.ENTRY,
        camera_ip=camera,
        plate_string=plate,
        owner=owner,
    )


def test_image_store_finds_new_jpeg(tmp_path):
    store = ImageStore(tmp_path)
    log_id = UUID("b264b9e5-3c9c-41c5-be40-1230753204af")
    expected = store.plates / f"{log_id}.jpg"
    expected.write_bytes(b"jpeg")
    assert store.path_for("plate", log_id) == expected


def test_image_store_finds_legacy_uppercase_png(tmp_path):
    store = ImageStore(tmp_path)
    log_id = UUID("86fd529e-417f-4a8a-a5b5-7225c3fba804")
    expected = store.vehicles / f"{str(log_id).upper()}.png"
    expected.write_bytes(b"png")
    assert store.path_for("vehicle", log_id) == expected


def test_image_store_finds_compact_uuid_and_uppercase_extension(tmp_path):
    store = ImageStore(tmp_path)
    log_id = UUID("86fd529e-417f-4a8a-a5b5-7225c3fba804")
    expected = store.plates / f"{log_id.hex.upper()}.JPG"
    expected.write_bytes(b"jpeg")
    assert store.path_for("plate", log_id) == expected


def test_image_store_finds_legacy_singular_directory(tmp_path):
    store = ImageStore(tmp_path)
    log_id = UUID("86fd529e-417f-4a8a-a5b5-7225c3fba804")
    legacy = tmp_path / "plate"
    legacy.mkdir()
    expected = legacy / f"{log_id}.jpeg"
    expected.write_bytes(b"jpeg")
    assert store.path_for("plate", log_id) == expected


def test_image_store_finds_image_saved_directly_under_root(tmp_path):
    store = ImageStore(tmp_path)
    log_id = UUID("86fd529e-417f-4a8a-a5b5-7225c3fba804")
    expected = tmp_path / f"{log_id}.jpg"
    expected.write_bytes(b"jpeg")
    assert store.path_for("plate", log_id) == expected


def test_image_store_matches_different_database_log_id_by_metadata(tmp_path):
    store = ImageStore(tmp_path)
    image_log_id = UUID("b2a218f9-b115-4afe-b0cf-41ca9b6a3e77")
    occurred_at = datetime(2026, 9, 22, 7, 13, 17, tzinfo=timezone.utc)
    store.plates.joinpath(f"{image_log_id}.jpg").write_bytes(b"plate")
    store.vehicles.joinpath(f"{image_log_id}.jpg").write_bytes(b"vehicle")
    store._index_event(event_for(image_log_id, "49T45112", "In_L   ", occurred_at))

    matched = store.matching_log_id(
        plate_number="49T45112",
        camera_ip="in_l",
        occurred_at=occurred_at + timedelta(seconds=4),
        max_delta_seconds=15,
    )

    assert matched == image_log_id


def test_image_store_does_not_match_outside_time_window(tmp_path):
    store = ImageStore(tmp_path)
    image_log_id = UUID("b2a218f9-b115-4afe-b0cf-41ca9b6a3e77")
    occurred_at = datetime(2026, 9, 22, 7, 13, 17, tzinfo=timezone.utc)
    store.plates.joinpath(f"{image_log_id}.jpg").write_bytes(b"plate")
    store._index_event(event_for(image_log_id, "49T45112", "In_L", occurred_at))

    matched = store.matching_log_id(
        plate_number="49T45112",
        camera_ip="In_L",
        occurred_at=occurred_at + timedelta(seconds=16),
        max_delta_seconds=15,
    )

    assert matched is None


def test_image_store_interprets_naive_database_time_in_configured_timezone(tmp_path):
    store = ImageStore(tmp_path, database_utc_offset_minutes=210)
    image_log_id = UUID("b2a218f9-b115-4afe-b0cf-41ca9b6a3e77")
    utc_time = datetime(2026, 9, 22, 11, 41, 6, tzinfo=timezone.utc)
    database_time = datetime(2026, 9, 22, 15, 11, 6)
    store.plates.joinpath(f"{image_log_id}.jpg").write_bytes(b"plate")
    store._index_event(event_for(image_log_id, "52x54674", "In_L", utc_time))

    matched = store.matching_log_id(
        plate_number="52x54674",
        camera_ip="In_L      ",
        occurred_at=database_time,
        max_delta_seconds=15,
    )

    assert matched == image_log_id


def test_recent_detection_keeps_text_and_images_from_same_event(tmp_path):
    store = ImageStore(tmp_path)
    log_id = UUID("b2a218f9-b115-4afe-b0cf-41ca9b6a3e77")
    owner = OwnerAccess(22, "مالک", "سراتو", "سفید", True, "12", 0)
    event = event_for(
        log_id,
        "52x54674",
        "In_L",
        datetime(2026, 9, 22, 11, 41, 6, tzinfo=timezone.utc),
        owner,
    )
    store.plates.joinpath(f"{log_id}.jpg").write_bytes(b"plate")
    store.vehicles.joinpath(f"{log_id}.jpg").write_bytes(b"vehicle")
    store._index_event(event)

    item = store.recent_detections(1)[0]

    assert item["plate_number"] == "52x54674"
    assert item["full_name"] == "مالک"
    assert item["plate_image_url"] == f"/api/v1/images/plate/{log_id}"
    assert item["vehicle_image_url"] == f"/api/v1/images/vehicle/{log_id}"


def test_existing_minimal_index_is_migrated_without_deleting_rows(tmp_path):
    index_path = tmp_path / "image_index.sqlite3"
    with sqlite3.connect(index_path) as connection:
        connection.execute(
            """CREATE TABLE image_events (
                log_id TEXT PRIMARY KEY, plate_key TEXT NOT NULL,
                camera_key TEXT NOT NULL, occurred_at REAL NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO image_events VALUES (?, ?, ?, ?)",
            ("b2a218f9-b115-4afe-b0cf-41ca9b6a3e77", "52x54674", "in_l", 1.0),
        )

    ImageStore(tmp_path, index_path=index_path)

    with sqlite3.connect(index_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(image_events)")}
        count = connection.execute("SELECT COUNT(*) FROM image_events").fetchone()[0]
    assert {"event_id", "plate_number", "camera_ip", "direction", "access"} <= columns
    assert count == 1
