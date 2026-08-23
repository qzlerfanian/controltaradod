from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from rahband.domain.deduplication import PassageDeduplicator
from rahband.domain.events import PassageDirection, PassageEvent
from rahband.domain.plate import Plate
from rahband.domain.relay import RelayCoordinator
from rahband.repositories.sqlserver import SqlServerRepository


class PassageService:
    def __init__(self, repository: SqlServerRepository, relay: RelayCoordinator, deduplicator: PassageDeduplicator, mqtt: object | None = None, *, relay_enabled: bool = True, outbox: object | None = None) -> None:
        self._repository = repository
        self._relay = relay
        self._deduplicator = deduplicator
        self._mqtt = mqtt
        self._relay_enabled = relay_enabled
        self._outbox = outbox

    def process_detection(self, *, plate_value: str, direction: PassageDirection, camera_ip: str, plate_string: str | None = None, occurred_at: datetime | None = None) -> PassageEvent | None:
        now = occurred_at or datetime.now(timezone.utc)
        plate = Plate.from_detection(plate_value)
        if not self._deduplicator.should_accept(plate.canonical, now=now):
            return None
        database_available = True
        try:
            owner = self._repository.find_owner(plate)
        except Exception as error:
            owner = None
            database_available = False
            logging.getLogger(__name__).warning("owner lookup unavailable; event will be queued (%s)", error)
        event = PassageEvent(
            id=uuid4(), log_id=uuid4(), plate=plate, occurred_at=now,
            direction=direction, camera_ip=camera_ip, plate_string=plate_string, owner=owner,
        )
        try:
            if not database_available:
                raise ConnectionError("database unavailable during owner lookup")
            self._repository.insert_event(event)
        except Exception as error:
            if self._outbox is None:
                raise
            self._outbox.enqueue(event, error)
            logging.getLogger(__name__).warning("database event queued locally (%s)", error)
        if owner is not None and owner.access and self._relay_enabled:
            self._relay.authorize(now=now)
        if self._mqtt is not None:
            try:
                self._mqtt.publish(self.serialize(event))
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "MQTT unavailable; database event remains committed (%s)", error
                )
        return event

    @staticmethod
    def serialize(event: PassageEvent) -> dict[str, object]:
        return {
            "id": str(event.id), "log_id": str(event.log_id), "plate_number": event.plate.canonical,
            "two": event.plate.two, "three": event.plate.three, "city": event.plate.city,
            "alpha": event.plate.persian_alpha, "trans_time": event.occurred_at.isoformat(),
            "direction": event.direction.value, "camera_ip": event.camera_ip,
            "plate_string": event.plate_string, "access": bool(event.owner and event.owner.access),
            "full_name": event.owner.full_name if event.owner else None,
            "model": event.owner.model if event.owner else None,
            "color": event.owner.color if event.owner else None,
        }
