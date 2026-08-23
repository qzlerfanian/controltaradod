from datetime import datetime, timedelta, timezone

import pytest

from rahband.domain.relay import RelayCoordinator, RelayState


class FakeRelay:
    def __init__(self) -> None:
        self.trigger_count = 0

    def trigger(self) -> None:
        self.trigger_count += 1


def test_authorized_plate_triggers_relay_for_fifteen_seconds() -> None:
    driver = FakeRelay()
    relay = RelayCoordinator(driver)
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    result = relay.authorize(now=now)
    assert result.triggered is True
    assert result.extended is False
    assert result.deadline == now + timedelta(seconds=15)
    assert driver.trigger_count == 1


def test_second_authorized_plate_retriggers_and_extends_board_timer() -> None:
    driver = FakeRelay()
    relay = RelayCoordinator(driver)
    start = datetime(2026, 8, 15, tzinfo=timezone.utc)
    relay.authorize(now=start)
    result = relay.authorize(now=start + timedelta(seconds=8))
    assert result.extended is True
    assert result.deadline == start + timedelta(seconds=23)
    assert driver.trigger_count == 2


def test_deadline_only_changes_logical_state_and_never_sends_close() -> None:
    driver = FakeRelay()
    relay = RelayCoordinator(driver)
    start = datetime(2026, 8, 15, tzinfo=timezone.utc)
    relay.authorize(now=start)
    assert relay.tick(now=start + timedelta(seconds=14)).state is RelayState.HOLDING
    assert relay.tick(now=start + timedelta(seconds=15)).state is RelayState.IDLE
    assert driver.trigger_count == 1


def test_naive_time_is_rejected() -> None:
    relay = RelayCoordinator(FakeRelay())
    with pytest.raises(ValueError, match="timezone-aware"):
        relay.authorize(now=datetime(2026, 8, 15))

