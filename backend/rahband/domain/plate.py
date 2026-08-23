from __future__ import annotations

from dataclasses import dataclass


PERSIAN_ALPHA_TO_CODE = {
    "ب": "b", "د": "d", "ق": "g", "ه": "h", "ج": "j", "ل": "L",
    "م": "m", "ن": "n", "س": "s", "ص": "x", "ط": "t", "و": "v",
    "ی": "y", "ي": "y", "ع": "A", "ت": "T", "ژ": "q", "گ": "c",
}
CODE_TO_PERSIAN_ALPHA = {value: key for key, value in PERSIAN_ALPHA_TO_CODE.items()}
CODE_TO_PERSIAN_ALPHA["y"] = "ی"


@dataclass(frozen=True, slots=True)
class Plate:
    two: str
    alpha: str
    three: str
    city: str

    def __post_init__(self) -> None:
        if not (self.two.isdigit() and len(self.two) == 2):
            raise ValueError("two must contain exactly 2 digits")
        if not (self.three.isdigit() and len(self.three) == 3):
            raise ValueError("three must contain exactly 3 digits")
        if not (self.city.isdigit() and len(self.city) == 2):
            raise ValueError("city must contain exactly 2 digits")
        if len(self.alpha.strip()) != 1:
            raise ValueError("alpha must contain exactly one character")

    @property
    def alpha_code(self) -> str:
        return PERSIAN_ALPHA_TO_CODE.get(self.alpha, self.alpha)

    @property
    def persian_alpha(self) -> str:
        return CODE_TO_PERSIAN_ALPHA.get(self.alpha, self.alpha)

    @property
    def canonical(self) -> str:
        return f"{self.two}{self.alpha_code}{self.three}{self.city}"

    @classmethod
    def from_detection(cls, value: str) -> "Plate":
        compact = "".join(value.split())
        if len(compact) != 8:
            raise ValueError("detected plate must contain 8 characters")
        return cls(compact[:2], compact[2], compact[3:6], compact[6:])

