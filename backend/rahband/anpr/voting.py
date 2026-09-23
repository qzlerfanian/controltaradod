from __future__ import annotations

from collections import defaultdict, deque
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
        anchor = recognition.plate.canonical
        cluster = [
            item.recognition for item in self._observations
            if self._distance(item.recognition.plate.canonical, anchor) <= 2
        ]
        if len(cluster) < self._minimum:
            return None
        if not self._ambiguous_digits_are_resolved(cluster):
            return None
        voted_value, voted_scores = self._vote_positions(cluster)
        from rahband.domain.plate import Plate

        try:
            voted_plate = Plate.from_detection(voted_value)
        except ValueError:
            return None
        selected = max(cluster, key=lambda item: (item.image_quality, item.confidence))
        self._observations.clear()
        return Recognition(
            plate=voted_plate,
            confidence=sum(voted_scores) / len(voted_scores),
            plate_image=selected.plate_image,
            vehicle_frame=selected.vehicle_frame,
            character_confidences=tuple(voted_scores),
            image_quality=selected.image_quality,
            sharpness=selected.sharpness,
            plate_width=selected.plate_width,
            plate_height=selected.plate_height,
            bbox_x1=selected.bbox_x1,
            bbox_y1=selected.bbox_y1,
            bbox_x2=selected.bbox_x2,
            bbox_y2=selected.bbox_y2,
            frame_width=selected.frame_width,
            frame_height=selected.frame_height,
        )

    @staticmethod
    def _distance(first: str, second: str) -> int:
        return sum(left != right for left, right in zip(first, second)) + abs(len(first) - len(second))

    @staticmethod
    def _vote_positions(recognitions: list[Recognition]) -> tuple[str, list[float]]:
        characters: list[str] = []
        scores: list[float] = []
        for position in range(8):
            totals: dict[str, float] = defaultdict(float)
            strongest: dict[str, float] = defaultdict(float)
            for recognition in recognitions:
                character = recognition.plate.canonical[position]
                weight = (
                    recognition.character_confidences[position]
                    if len(recognition.character_confidences) == 8
                    else recognition.confidence
                )
                quality_weighted = weight * (0.65 + 0.35 * recognition.image_quality)
                totals[character] += max(quality_weighted, 0.01)
                strongest[character] = max(strongest[character], weight)
            winner = max(totals, key=lambda character: (totals[character], strongest[character]))
            characters.append(winner)
            scores.append(strongest[winner])
        return "".join(characters), scores

    def _ambiguous_digits_are_resolved(self, recognitions: list[Recognition]) -> bool:
        confusing_pairs = ({"2", "3"}, {"4", "5"})
        for position in (0, 1, 3, 4, 5, 6, 7):
            present = {item.plate.canonical[position] for item in recognitions}
            if not any(pair.issuperset(present) and len(present) > 1 for pair in confusing_pairs):
                continue
            if len(recognitions) < max(3, self._minimum):
                return False
            totals: dict[str, float] = defaultdict(float)
            for item in recognitions:
                confidence = (
                    item.character_confidences[position]
                    if len(item.character_confidences) == 8 else item.confidence
                )
                totals[item.plate.canonical[position]] += confidence * (0.65 + 0.35 * item.image_quality)
            ranked = sorted(totals.values(), reverse=True)
            if len(ranked) > 1 and (ranked[0] - ranked[1]) / max(sum(ranked), 0.01) < 0.12:
                return False
        return True
