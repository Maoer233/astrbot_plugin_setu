"""Optional lunar-calendar bridge.

``lunar-python`` is an optional dependency: when it is installed the card gains
a real lunar date, sexagenary cycle and solar term; when it is missing every
accessor returns ``None`` and the template simply omits that block.  Nothing
here performs network I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ...shared import get_logger

logger = get_logger()

try:  # pragma: no cover - exercised only when the optional dep is present
    from lunar_python import Solar as _Solar

    _LUNAR_AVAILABLE = True
except Exception:  # ImportError or a broken install
    _Solar = None
    _LUNAR_AVAILABLE = False


def lunar_available() -> bool:
    """Whether the optional lunar library is importable."""
    return _LUNAR_AVAILABLE


@dataclass(frozen=True)
class LunarInfo:
    """Lunar-calendar facts for one day."""

    lunar_date: str
    ganzhi: str
    zodiac: str
    solar_term: str

    @property
    def has_content(self) -> bool:
        return bool(self.lunar_date or self.ganzhi)


def get_lunar_info(target: date) -> LunarInfo | None:
    """Return lunar facts for ``target``, or ``None`` when unavailable.

    Never raises: a failure in the optional library must not break the card.
    """
    if not _LUNAR_AVAILABLE or _Solar is None:
        return None
    try:
        lunar = _Solar.fromYmd(target.year, target.month, target.day).getLunar()
        ganzhi = "".join(
            (
                lunar.getYearInGanZhi(),
                lunar.getMonthInGanZhi(),
                lunar.getDayInGanZhi(),
            )
        )
        return LunarInfo(
            lunar_date=lunar.toString(),
            ganzhi=ganzhi,
            zodiac=lunar.getYearShengXiao(),
            solar_term=lunar.getJieQi() or "",
        )
    except Exception as exc:  # defensive: optional dep, unknown failures
        logger.debug("[fortune] lunar lookup failed: %s", exc)
        return None
