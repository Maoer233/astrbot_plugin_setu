"""Tests for deterministic fortune derivation and offline pools."""

from __future__ import annotations

from astrbot_plugin_setu.src.domain.fortune.detail import (
    HOURS,
    build_detail,
    make_rng,
    make_seed,
)
from astrbot_plugin_setu.src.infrastructure.fortune.pools import get_fortune_pools


def _detail(**overrides):
    kwargs = {
        "user_id": "user-1",
        "date_str": "2026-10-27",
        "star_count": 4,
        "pools": get_fortune_pools(),
    }
    kwargs.update(overrides)
    return build_detail(**kwargs)


def test_same_identity_produces_identical_detail() -> None:
    """预生成与首次请求必须得到完全一致的卡片内容。"""
    assert _detail() == _detail()


def test_different_users_get_different_detail() -> None:
    assert _detail(user_id="user-1") != _detail(user_id="user-2")


def test_different_dates_get_different_detail() -> None:
    assert _detail(date_str="2026-10-27") != _detail(date_str="2026-10-28")


def test_nonce_reshuffles_the_same_day() -> None:
    assert _detail(nonce=0) != _detail(nonce=1)


def test_master_key_reshuffles_everybody() -> None:
    assert _detail(master_key="a") != _detail(master_key="b")


def test_seed_is_stable_and_isolated() -> None:
    assert make_seed("u", "2026-10-27") == make_seed("u", "2026-10-27")
    assert make_seed("u", "2026-10-27") != make_seed("v", "2026-10-27")
    # 两个 RNG 互不干扰
    a = make_rng("u", "2026-10-27")
    b = make_rng("u", "2026-10-27")
    assert [a.random() for _ in range(5)] == [b.random() for _ in range(5)]


def test_yi_ji_are_distinct_and_populated() -> None:
    detail = _detail()

    assert len(detail.yi) == 3
    assert len(detail.ji) == 3
    assert len(set(detail.yi)) == 3
    assert len(set(detail.ji)) == 3


def test_dimension_scores_stay_in_range() -> None:
    for star in range(8):
        detail = _detail(star_count=star)
        assert len(detail.dims) == 5
        for _key, _label, score in detail.dims:
            assert 1 <= score <= 100


def test_higher_star_tends_to_score_higher() -> None:
    """星级作为基线偏移，应整体抬升分项分数均值。"""
    low = sum(s for _, _, s in _detail(star_count=0).dims)
    high = sum(s for _, _, s in _detail(star_count=7).dims)
    assert high > low


def test_lucky_fields_and_hour_are_filled() -> None:
    detail = _detail()

    assert detail.lucky_color
    assert detail.lucky_direction
    assert detail.lucky_item
    assert detail.lucky_number is not None
    assert (detail.auspicious_hour, detail.auspicious_hour_window) in HOURS


def test_sign_is_drawn_from_the_100_lot_pool() -> None:
    detail = _detail()

    assert detail.sign_title
    assert detail.sign_poetry
    assert detail.sign_grade in {"上签", "中签", "下签"}


def test_blessing_comes_from_the_pool() -> None:
    pool = get_fortune_pools()

    assert _detail().blessing in set(pool.blessing.blessings)


def test_disabling_sections_empties_them() -> None:
    detail = build_detail(
        user_id="u",
        date_str="2026-10-27",
        star_count=4,
        pools=get_fortune_pools(),
        enable_yi_ji=False,
        enable_lucky=False,
        enable_dims=False,
        enable_hour=False,
        enable_blessing=False,
        enable_sign=False,
    )

    assert detail.yi == () and detail.ji == ()
    assert detail.lucky_color == ""
    assert detail.dims == ()
    assert detail.auspicious_hour == ""
    assert detail.blessing == ""
    assert detail.sign_title == ""


def test_missing_pools_degrades_without_raising() -> None:
    """数据文件损坏时不能连累整张卡。"""
    detail = build_detail(
        user_id="u", date_str="2026-10-27", star_count=4, pools=None
    )

    assert detail.yi == ()
    assert detail.lucky_color == ""
    # 分项与吉时是纯算法，不依赖数据文件
    assert len(detail.dims) == 5
    assert detail.auspicious_hour
