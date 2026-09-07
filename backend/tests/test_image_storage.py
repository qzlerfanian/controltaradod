from uuid import UUID

from rahband.anpr.storage import ImageStore


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
