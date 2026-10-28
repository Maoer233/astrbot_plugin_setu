"""Fortune detail DTO.

Kept in the domain layer and free of infrastructure imports so the domain does
not depend on file-system loaders or optional libraries.  Infrastructure builds
instances of this type and hands them in.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FortuneDetail:
    """Every locally derived field shown on the fortune card."""

    yi: tuple[str, ...] = ()
    ji: tuple[str, ...] = ()
    lucky_color: str = ""
    lucky_color_hex: str = ""
    lucky_number: int | None = None
    lucky_direction: str = ""
    lucky_item: str = ""
    dims: tuple[tuple[str, str, int], ...] = ()
    auspicious_hour: str = ""
    auspicious_hour_window: str = ""
    blessing: str = ""
    sign_title: str = ""
    sign_type: str = ""
    sign_grade: str = ""
    sign_poetry: str = ""
    sign_meaning: str = ""
    sign_explanation: str = ""
    lunar_date: str = ""
    ganzhi: str = ""
    zodiac: str = ""
    solar_term: str = ""

    @property
    def has_any(self) -> bool:
        return bool(self.yi or self.dims or self.blessing or self.sign_title)
