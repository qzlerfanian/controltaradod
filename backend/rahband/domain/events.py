from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID

from .plate import Plate


class PassageDirection(str, Enum):
    ENTRY = "entry"
    EXIT = "exit"


@dataclass(frozen=True, slots=True)
class OwnerAccess:
    car_id: int
    full_name: str
    model: str | None
    color: str | None
    access: bool
    unit: str | None
    access_type: int | None


@dataclass(frozen=True, slots=True)
class PassageEvent:
    id: UUID
    log_id: UUID
    plate: Plate
    occurred_at: datetime
    direction: PassageDirection
    camera_ip: str
    plate_string: str | None
    owner: OwnerAccess | None

