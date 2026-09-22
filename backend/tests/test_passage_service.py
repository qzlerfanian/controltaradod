from datetime import datetime, timedelta, timezone

from rahband.domain.deduplication import PassageDeduplicator
from rahband.domain.events import OwnerAccess, PassageDirection
from rahband.domain.relay import RelayCoordinator
from rahband.services.passage import PassageService


class FakeRelay:
    def __init__(self) -> None:
        self.count = 0

    def trigger(self) -> None:
        self.count += 1


class FailingRelay:
    def trigger(self) -> None:
        raise TimeoutError("relay offline")


class FakeRepository:
    def __init__(self, access: bool) -> None:
        self.owner = OwnerAccess(22, "مالک", "سراتو", "سفید", access, None, 0)
        self.events = []

    def find_owner(self, plate):
        return self.owner

    def insert_event(self, event):
        self.events.append(event)


def test_authorized_passage_is_logged_and_opens_relay() -> None:
    repository = FakeRepository(access=True)
    driver = FakeRelay()
    service = PassageService(repository, RelayCoordinator(driver), PassageDeduplicator(20))
    event = service.process_detection(
        plate_value="29d69112", direction=PassageDirection.ENTRY,
        camera_ip="172.18.24.244", occurred_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )
    assert event is not None
    assert len(repository.events) == 1
    assert driver.count == 1


def test_cross_camera_duplicate_is_not_logged_or_retriggered() -> None:
    repository = FakeRepository(access=True)
    driver = FakeRelay()
    service = PassageService(repository, RelayCoordinator(driver), PassageDeduplicator(20))
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    service.process_detection(plate_value="29d69112", direction=PassageDirection.EXIT, camera_ip="172.18.24.241", occurred_at=now)
    duplicate = service.process_detection(plate_value="29d69112", direction=PassageDirection.ENTRY, camera_ip="172.18.24.244", occurred_at=now + timedelta(seconds=2))
    assert duplicate is None
    assert len(repository.events) == 1
    assert driver.count == 1


def test_denied_owner_is_logged_without_relay() -> None:
    repository = FakeRepository(access=False)
    driver = FakeRelay()
    service = PassageService(repository, RelayCoordinator(driver), PassageDeduplicator(20))
    service.process_detection(
        plate_value="73m36274", direction=PassageDirection.ENTRY,
        camera_ip="172.18.24.238", occurred_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )
    assert len(repository.events) == 1
    assert driver.count == 0


def test_shadow_mode_logs_authorized_owner_without_relay() -> None:
    repository = FakeRepository(access=True)
    driver = FakeRelay()
    service = PassageService(
        repository, RelayCoordinator(driver), PassageDeduplicator(20), relay_enabled=False,
    )
    service.process_detection(
        plate_value="29d69112", direction=PassageDirection.ENTRY,
        camera_ip="172.18.24.244", occurred_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )
    assert len(repository.events) == 1
    assert driver.count == 0


def test_database_failure_is_queued_and_returns_event() -> None:
    class OfflineRepository(FakeRepository):
        def find_owner(self, plate):
            raise ConnectionError("offline")

    class Outbox:
        events = []

        def enqueue(self, event, error):
            self.events.append(event)

    repository = OfflineRepository(access=True)
    outbox = Outbox()
    service = PassageService(
        repository, RelayCoordinator(FakeRelay()), PassageDeduplicator(20), outbox=outbox,
    )
    result = service.process_detection(
        plate_value="29d69112", direction=PassageDirection.ENTRY, camera_ip="In_L",
    )
    assert result is not None
    assert len(outbox.events) == 1


def test_relay_failure_does_not_discard_committed_event() -> None:
    repository = FakeRepository(access=True)
    service = PassageService(repository, RelayCoordinator(FailingRelay()), PassageDeduplicator(20))

    event = service.process_detection(
        plate_value="29d69112", direction=PassageDirection.ENTRY,
        camera_ip="In_L", occurred_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )

    assert event is not None
    assert repository.events == [event]
