"""Local, offline data pools for the daily fortune feature.

Everything in this module is deterministic and network-free: the plugin used to
have exactly eight hard-coded sentences, so two users with the same star rating
saw identical text.  These pools give the card real variety without adding a
single HTTP request.

All loaders are tolerant: a missing or corrupt JSON file degrades to an empty
pool, and callers fall back to the legacy hard-coded values.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from ...shared import get_logger

logger = get_logger()

_DATA_DIR = Path(__file__).resolve().parents[3] / "templates" / "data"


def _load_json(name: str) -> Any:
    path = _DATA_DIR / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("[fortune] data file missing: %s", path)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[fortune] failed to load %s: %s", name, exc)
    return None


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


@dataclass(frozen=True)
class YiJiPool:
    """Fake-almanac "do / avoid" phrases, deliberately playful."""

    yi: tuple[str, ...] = ()
    ji: tuple[str, ...] = ()

    @classmethod
    def load(cls) -> YiJiPool:
        data = _load_json("yi_ji.json")
        if not isinstance(data, dict):
            return cls()
        return cls(yi=tuple(_as_str_list(data.get("yi"))), ji=tuple(_as_str_list(data.get("ji"))))

    @property
    def usable(self) -> bool:
        return len(self.yi) >= 3 and len(self.ji) >= 3


@dataclass(frozen=True)
class LuckyPool:
    """Lucky colour / direction / item / number pools."""

    colors: tuple[dict[str, str], ...] = ()
    directions: tuple[str, ...] = ()
    items: tuple[str, ...] = ()
    numbers: tuple[int, ...] = ()

    @classmethod
    def load(cls) -> LuckyPool:
        data = _load_json("lucky.json")
        if not isinstance(data, dict):
            return cls()

        colors: list[dict[str, str]] = []
        for entry in data.get("color") or []:
            if isinstance(entry, dict) and entry.get("name"):
                colors.append(
                    {
                        "name": str(entry.get("name", "")).strip(),
                        "hex": str(entry.get("hex", "")).strip(),
                    }
                )

        numbers = tuple(
            int(n) for n in (data.get("number_pool") or []) if isinstance(n, int)
        )
        return cls(
            colors=tuple(colors),
            directions=tuple(_as_str_list(data.get("direction"))),
            items=tuple(_as_str_list(data.get("item"))),
            numbers=numbers,
        )

    @property
    def usable(self) -> bool:
        return bool(self.colors and self.directions and self.items and self.numbers)


@dataclass(frozen=True)
class BlessingPool:
    """Replacement for the eight hard-coded fortune sentences."""

    blessings: tuple[str, ...] = ()

    @classmethod
    def load(cls) -> BlessingPool:
        data = _load_json("blessing.json")
        if not isinstance(data, dict):
            return cls()
        return BlessingPool(blessings=tuple(_as_str_list(data.get("blessings"))))

    @property
    def usable(self) -> bool:
        return len(self.blessings) >= 3


@dataclass(frozen=True)
class GuanyinSign:
    """One Guanyin lot (观音灵签)."""

    id: int
    title: str
    type: str
    poetry: str
    meaning: str
    explanation: str

    @property
    def grade(self) -> str:
        """Extract 上签 / 中签 / 下签 from the raw type string."""
        for grade in ("上签", "中签", "下签"):
            if self.type.startswith(grade):
                return grade
        return self.type[:2] if self.type else ""


@dataclass(frozen=True)
class SignPool:
    """100-entry Guanyin lot pool."""

    signs: tuple[GuanyinSign, ...] = ()

    @classmethod
    def load(cls) -> SignPool:
        data = _load_json("guanyin.json")
        if not isinstance(data, dict):
            return cls()

        signs: list[GuanyinSign] = []
        for entry in data.get("signs") or []:
            if not isinstance(entry, dict):
                continue
            try:
                sign_id = int(entry.get("id"))
            except (TypeError, ValueError):
                continue
            signs.append(
                GuanyinSign(
                    id=sign_id,
                    title=str(entry.get("title", "")).strip(),
                    type=str(entry.get("type", "")).strip(),
                    poetry=str(entry.get("poetry", "")).strip(),
                    meaning=str(entry.get("meaning", "")).strip(),
                    explanation=str(entry.get("explanation", "")).strip(),
                )
            )
        return cls(signs=tuple(signs))

    @property
    def usable(self) -> bool:
        return len(self.signs) >= 10


@dataclass(frozen=True)
class FortunePools:
    """Bundle of every local pool, loaded once per process."""

    yi_ji: YiJiPool = field(default_factory=YiJiPool)
    lucky: LuckyPool = field(default_factory=LuckyPool)
    blessing: BlessingPool = field(default_factory=BlessingPool)
    sign: SignPool = field(default_factory=SignPool)

    @classmethod
    def load(cls) -> FortunePools:
        return cls(
            yi_ji=YiJiPool.load(),
            lucky=LuckyPool.load(),
            blessing=BlessingPool.load(),
            sign=SignPool.load(),
        )


@lru_cache(maxsize=1)
def get_fortune_pools() -> FortunePools:
    """Load and cache every pool for the lifetime of the process."""
    pools = FortunePools.load()
    logger.info(
        "[fortune] pools loaded: yi=%d, ji=%d, colors=%d, items=%d, blessings=%d, signs=%d",
        len(pools.yi_ji.yi),
        len(pools.yi_ji.ji),
        len(pools.lucky.colors),
        len(pools.lucky.items),
        len(pools.blessing.blessings),
        len(pools.sign.signs),
    )
    return pools


def pick_many(rng: random.Random, pool: tuple[str, ...], count: int) -> list[str]:
    """Pick ``count`` distinct entries, or fewer when the pool is small."""
    if not pool:
        return []
    return rng.sample(list(pool), k=min(count, len(pool)))
