from datetime import datetime, timezone
from uuid import uuid4

from rahband.domain.events import OwnerAccess, PassageDirection, PassageEvent
from rahband.domain.plate import Plate
from rahband.services.outbox import OutboxReplayer, SqlitePassageOutbox


def event() -> PassageEvent:
    return PassageEvent(
        id=uuid4(), log_id=uuid4(), plate=Plate.from_detection("29d69112"),
        occurred_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        direction=PassageDirection.ENTRY, camera_ip="In_L", plate_string="29d69112",
        owner=None,
    )


def test_outbox_survives_new_instance(tmp_path) -> None:
    path = tmp_path / "outbox.sqlite3"
    first = SqlitePassageOutbox(path)
    queued = event()
    first.enqueue(queued, "offline")
    second = SqlitePassageOutbox(path)
    assert second.count() == 1
    assert second.pending()[0] == queued


def test_replayer_enriches_owner_and_acknowledges(tmp_path) -> None:
    outbox = SqlitePassageOutbox(tmp_path / "outbox.sqlite3")
    queued = event()
    outbox.enqueue(queued, "offline")

    class Repository:
        inserted = []

        def find_owner(self, plate):
            return OwnerAccess(22, "مالک", "سراتو", "سفید", True, "12", 0)

        def insert_event(self, passage):
            self.inserted.append(passage)

    repository = Repository()
    assert OutboxReplayer(outbox, repository).replay_once() == 1
    assert outbox.count() == 0
    assert repository.inserted[0].owner.full_name == "مالک"
