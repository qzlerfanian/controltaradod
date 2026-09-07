from __future__ import annotations

import os
import time
from collections import namedtuple

from rahband.services.image_retention import ImageRetentionService
from rahband.services.owner_audit import OwnerAuditStore


def test_owner_audit_survives_owner_deletion(tmp_path):
    store = OwnerAuditStore(tmp_path / "audit.sqlite3")
    store.record(12, "created", after={"id": 12, "full_name": "مالک"})
    store.record(12, "deleted", before={"id": 12, "full_name": "مالک"})
    history = store.history(12)
    assert [item["action"] for item in history] == ["deleted", "created"]
    assert history[0]["before"]["full_name"] == "مالک"


def test_retention_removes_old_images_but_keeps_recent_images(tmp_path):
    plates, vehicles = tmp_path / "plates", tmp_path / "vehicles"
    plates.mkdir(); vehicles.mkdir()
    old_file, new_file = plates / "old.jpg", vehicles / "new.jpg"
    old_file.write_bytes(b"old"); new_file.write_bytes(b"new")
    old_timestamp = time.time() - 61 * 86400
    os.utime(old_file, (old_timestamp, old_timestamp))
    Usage = namedtuple("Usage", "total used free")
    service = ImageRetentionService(tmp_path, retention_days=60, disk_usage=lambda _: Usage(100, 50, 50))
    result = service.cleanup()
    assert result["removed"] == 1
    assert not old_file.exists()
    assert new_file.exists()
