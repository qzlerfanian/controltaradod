from dataclasses import dataclass
from enum import Enum


class CameraDirection(str, Enum):
    ENTRY = "entry"
    EXIT = "exit"


@dataclass(frozen=True, slots=True)
class Camera:
    id: str
    name: str
    direction: CameraDirection
    relay_id: str | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("camera id cannot be empty")
        if not self.name.strip():
            raise ValueError("camera name cannot be empty")
