from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol


class RelayState(str, Enum):
    IDLE = "idle"
    HOLDING = "holding"


class RelayDriver(Protocol):
    def trigger(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RelayDecision:
    state: RelayState
    triggered: bool
    extended: bool
    deadline: datetime | None


class RelayCoordinator:
    """Triggers a pulse relay and tracks its expected hold window.

    The board owns the physical close operation. A new authorized passage sends
    another pulse so the board can renew its timer; no close command is sent.
    """

    def __init__(self, driver: RelayDriver, hold_seconds: float = 15) -> None:
        if hold_seconds <= 0:
            raise ValueError("hold_seconds must be positive")
        self._driver = driver
        self._hold = timedelta(seconds=hold_seconds)
        self._state = RelayState.IDLE
        self._deadline: datetime | None = None

    @property
    def state(self) -> RelayState:
        return self._state

    @property
    def deadline(self) -> datetime | None:
        return self._deadline

    def authorize(self, *, now: datetime | None = None) -> RelayDecision:
        current = self._utc(now)
        extended = self._state is RelayState.HOLDING and self._deadline is not None and current < self._deadline
        self._driver.trigger()
        self._state = RelayState.HOLDING
        self._deadline = current + self._hold
        return RelayDecision(self._state, True, extended, self._deadline)

    def tick(self, *, now: datetime | None = None) -> RelayDecision:
        current = self._utc(now)
        if self._state is RelayState.HOLDING and self._deadline is not None and current >= self._deadline:
            self._state = RelayState.IDLE
            self._deadline = None
        return RelayDecision(self._state, False, False, self._deadline)

    @staticmethod
    def _utc(value: datetime | None) -> datetime:
        result = value or datetime.now(timezone.utc)
        if result.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return result.astimezone(timezone.utc)

