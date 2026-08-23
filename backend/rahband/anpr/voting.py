from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .inference import Recognition


@dataclass(frozen=True, slots=True)
class Observation:
    seen_at: datetime
    recognition: Recognition


class TemporalPlateVoter:
    def __init__(self, window_seconds: float = 1.5, min_observations: int = 2) -> None:
        self._window = timedelta(seconds=window_seconds)
        self._minimum = min_observations
        self._observations: deque[Observation] = deque()

    def observe(self, recognition: Recognition, *, now: datetime | None = None) -> Recognition | None:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self._observations.append(Observation(current, recognition))
        while self._observations and current - self._observations[0].seen_at > self._window:
            self._observations.popleft()
        counts = Counter(item.recognition.plate.canonical for item in self._observations)
        winner, count = counts.most_common(1)[0]
        if count < self._minimum:
            return None
        candidates = [item.recognition for item in self._observations if item.recognition.plate.canonical == winner]
        selected = max(candidates, key=lambda item: item.confidence)
        self._observations.clear()
        return selected

