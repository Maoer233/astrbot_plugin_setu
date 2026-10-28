"""Deterministic fortune derivation.

The original implementation called the global ``random`` module, so a fortune
generated during the nightly pre-generation pass and the same user's first
request of the day could disagree.  Deriving every field from a seeded
``random.Random`` makes the whole card reproducible:

* pre-generation and first read always agree;
* new fields need no extra database column, since they can be recomputed from
  ``(user_id, date, master_key, nonce)``;
* changing ``master_key`` reshuffles everybody at once, which is a cheap way to
  run an event.

This module stays free of infrastructure imports: data pools and lunar info are
passed in through :class:`FortunePoolsProtocol` / callables.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any, Protocol

from .detail_dto import FortuneDetail

DEFAULT_MASTER_KEY = "astrbot-setu"

# Five dimensions scored independently of the star rating.
DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("career", "事业"),
    ("study", "学业"),
    ("love", "爱情"),
    ("wealth", "财运"),
    ("health", "健康"),
)

# 12 时辰 with their clock windows.
HOURS: tuple[tuple[str, str], ...] = (
    ("子时", "23:00-01:00"),
    ("丑时", "01:00-03:00"),
    ("寅时", "03:00-05:00"),
    ("卯时", "05:00-07:00"),
    ("辰时", "07:00-09:00"),
    ("巳时", "09:00-11:00"),
    ("午时", "11:00-13:00"),
    ("未时", "13:00-15:00"),
    ("申时", "15:00-17:00"),
    ("酉时", "17:00-19:00"),
    ("戌时", "19:00-21:00"),
    ("亥时", "21:00-23:00"),
)


class FortunePoolsProtocol(Protocol):
    """Structural contract for whatever supplies the local word pools."""

    yi_ji: Any
    lucky: Any
    blessing: Any
    sign: Any


def make_seed(
    user_id: str,
    date_str: str,
    master_key: str = DEFAULT_MASTER_KEY,
    nonce: int = 0,
) -> int:
    """Derive a stable 128-bit seed from the fortune identity."""
    raw = f"{user_id}|{date_str}|{master_key}|{nonce}".encode()
    digest = hashlib.blake2b(raw, digest_size=16).digest()
    return int.from_bytes(digest, "big")


def make_rng(
    user_id: str,
    date_str: str,
    master_key: str = DEFAULT_MASTER_KEY,
    nonce: int = 0,
) -> random.Random:
    """Return an isolated RNG seeded for this user and day."""
    return random.Random(make_seed(user_id, date_str, master_key, nonce))


def _pick_many(rng: random.Random, pool: Any, count: int) -> list[str]:
    items = list(pool or ())
    if not items:
        return []
    return rng.sample(items, k=min(count, len(items)))


def build_detail(
    *,
    user_id: str,
    date_str: str,
    star_count: int,
    master_key: str = DEFAULT_MASTER_KEY,
    nonce: int = 0,
    pools: FortunePoolsProtocol | None = None,
    lunar_info: Any | None = None,
    enable_yi_ji: bool = True,
    enable_lucky: bool = True,
    enable_dims: bool = True,
    enable_hour: bool = True,
    enable_blessing: bool = True,
    enable_sign: bool = True,
    enable_lunar: bool = True,
    sign_count: int = 1,
    dim_count: int = 5,
) -> FortuneDetail:
    """Derive every card field deterministically from the fortune identity.

    ``star_count`` (0-7) biases the numeric scores upward but never fully
    determines them, so two four-star days still differ.
    """
    rng = make_rng(user_id, date_str, master_key, nonce)

    yi: tuple[str, ...] = ()
    ji: tuple[str, ...] = ()
    if enable_yi_ji and pools is not None and getattr(pools, "yi_ji", None):
        yi = tuple(_pick_many(rng, pools.yi_ji.yi, 3))
        ji = tuple(_pick_many(rng, pools.yi_ji.ji, 3))

    lucky_color = lucky_hex = lucky_direction = lucky_item = ""
    lucky_number: int | None = None
    lucky = getattr(pools, "lucky", None) if pools is not None else None
    if enable_lucky and lucky is not None and getattr(lucky, "usable", False):
        color = rng.choice(list(lucky.colors))
        lucky_color = color.get("name", "")
        lucky_hex = color.get("hex", "")
        lucky_direction = rng.choice(list(lucky.directions))
        lucky_item = rng.choice(list(lucky.items))
        lucky_number = rng.choice(list(lucky.numbers))

    dims: tuple[tuple[str, str, int], ...] = ()
    if enable_dims:
        # 星级作为基线偏移：0 星 ~28 分起，7 星 ~70 分起。
        base = 28 + star_count * 6
        scored: list[tuple[str, str, int]] = []
        for key, label in DIMENSIONS[: max(1, min(dim_count, len(DIMENSIONS)))]:
            jitter = rng.randint(-26, 26)
            score = max(1, min(100, base + jitter))
            scored.append((key, label, score))
        dims = tuple(scored)

    hour = window = ""
    if enable_hour:
        hour, window = rng.choice(list(HOURS))

    blessing = ""
    blessing_pool = getattr(pools, "blessing", None) if pools is not None else None
    if enable_blessing and blessing_pool is not None and blessing_pool.usable:
        blessing = rng.choice(list(blessing_pool.blessings))

    sign_title = sign_type = sign_grade = ""
    sign_poetry = sign_meaning = sign_explanation = ""
    sign_pool = getattr(pools, "sign", None) if pools is not None else None
    if enable_sign and sign_pool is not None and sign_pool.usable:
        chosen = rng.sample(
            list(sign_pool.signs), k=min(max(1, sign_count), len(sign_pool.signs))
        )
        primary = chosen[0]
        sign_title = primary.title
        sign_type = primary.type
        sign_grade = primary.grade
        sign_poetry = primary.poetry
        sign_meaning = primary.meaning
        sign_explanation = primary.explanation

    lunar = lunar_info if (enable_lunar and lunar_info is not None) else None

    return FortuneDetail(
        yi=yi,
        ji=ji,
        lucky_color=lucky_color,
        lucky_color_hex=lucky_hex,
        lucky_number=lucky_number,
        lucky_direction=lucky_direction,
        lucky_item=lucky_item,
        dims=dims,
        auspicious_hour=hour,
        auspicious_hour_window=window,
        blessing=blessing,
        sign_title=sign_title,
        sign_type=sign_type,
        sign_grade=sign_grade,
        sign_poetry=sign_poetry,
        sign_meaning=sign_meaning,
        sign_explanation=sign_explanation,
        lunar_date=getattr(lunar, "lunar_date", "") if lunar else "",
        ganzhi=getattr(lunar, "ganzhi", "") if lunar else "",
        zodiac=getattr(lunar, "zodiac", "") if lunar else "",
        solar_term=getattr(lunar, "solar_term", "") if lunar else "",
    )
