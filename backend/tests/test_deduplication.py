from datetime import datetime, timedelta, timezone

from rahband.domain.deduplication import PassageDeduplicator


def test_same_plate_from_another_camera_is_suppressed() -> None:
    dedupe = PassageDeduplicator(window_seconds=20)
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    assert dedupe.should_accept("29d69112", now=now) is True
    assert dedupe.should_accept("29d69112", now=now + timedelta(seconds=2)) is False


def test_plate_is_accepted_after_window() -> None:
    dedupe = PassageDeduplicator(window_seconds=20)
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    assert dedupe.should_accept("29d69112", now=now) is True
    assert dedupe.should_accept("29d69112", now=now + timedelta(seconds=20)) is True

