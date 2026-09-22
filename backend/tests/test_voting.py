from datetime import datetime, timedelta, timezone

import pytest

from rahband.anpr.inference import Recognition
from rahband.anpr.voting import TemporalPlateVoter
from rahband.domain.plate import Plate


def recognition(
    value: str,
    confidence: float = 0.8,
    character_confidences: tuple[float, ...] = (),
) -> Recognition:
    return Recognition(
        Plate.from_detection(value), confidence, object(), object(), character_confidences,
    )


def test_voter_requires_repeated_plate_in_time_window() -> None:
    voter = TemporalPlateVoter(window_seconds=1.5, min_observations=2)
    now = datetime(2026, 8, 16, tzinfo=timezone.utc)
    assert voter.observe(recognition("29d69112"), now=now) is None
    result = voter.observe(recognition("29d69112", 0.9), now=now + timedelta(milliseconds=300))
    assert result is not None
    assert result.plate.canonical == "29d69112"
    assert result.confidence == pytest.approx(0.9)


def test_old_observation_expires() -> None:
    voter = TemporalPlateVoter(window_seconds=1, min_observations=2)
    now = datetime(2026, 8, 16, tzinfo=timezone.utc)
    voter.observe(recognition("29d69112"), now=now)
    assert voter.observe(recognition("29d69112"), now=now + timedelta(seconds=2)) is None


def test_voter_combines_character_evidence_instead_of_requiring_exact_plate() -> None:
    voter = TemporalPlateVoter(window_seconds=1.5, min_observations=3)
    now = datetime(2026, 8, 16, tzinfo=timezone.utc)
    high = (0.95,) * 8
    low_second_digit = (0.95, 0.20, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95)
    low_first_digit = (0.20, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95)

    assert voter.observe(recognition("23d45612", character_confidences=high), now=now) is None
    assert voter.observe(
        recognition("22d45612", character_confidences=low_second_digit),
        now=now + timedelta(milliseconds=200),
    ) is None
    result = voter.observe(
        recognition("33d45612", character_confidences=low_first_digit),
        now=now + timedelta(milliseconds=400),
    )

    assert result is not None
    assert result.plate.canonical == "23d45612"


def test_conflicting_2_and_3_waits_for_a_third_observation() -> None:
    voter = TemporalPlateVoter(window_seconds=1.5, min_observations=2)
    now = datetime(2026, 8, 16, tzinfo=timezone.utc)
    scores = (0.9,) * 8
    assert voter.observe(recognition("22d45612", character_confidences=scores), now=now) is None
    assert voter.observe(
        recognition("23d45612", character_confidences=scores),
        now=now + timedelta(milliseconds=200),
    ) is None
    result = voter.observe(
        recognition("23d45612", character_confidences=scores),
        now=now + timedelta(milliseconds=400),
    )
    assert result is not None
    assert result.plate.canonical == "23d45612"
