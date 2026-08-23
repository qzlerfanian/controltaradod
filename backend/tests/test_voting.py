from datetime import datetime, timedelta, timezone

from rahband.anpr.inference import Recognition
from rahband.anpr.voting import TemporalPlateVoter
from rahband.domain.plate import Plate


def recognition(value: str, confidence: float = 0.8) -> Recognition:
    return Recognition(Plate.from_detection(value), confidence, object(), object())


def test_voter_requires_repeated_plate_in_time_window() -> None:
    voter = TemporalPlateVoter(window_seconds=1.5, min_observations=2)
    now = datetime(2026, 8, 16, tzinfo=timezone.utc)
    assert voter.observe(recognition("29d69112"), now=now) is None
    result = voter.observe(recognition("29d69112", 0.9), now=now + timedelta(milliseconds=300))
    assert result is not None
    assert result.plate.canonical == "29d69112"
    assert result.confidence == 0.9


def test_old_observation_expires() -> None:
    voter = TemporalPlateVoter(window_seconds=1, min_observations=2)
    now = datetime(2026, 8, 16, tzinfo=timezone.utc)
    voter.observe(recognition("29d69112"), now=now)
    assert voter.observe(recognition("29d69112"), now=now + timedelta(seconds=2)) is None

