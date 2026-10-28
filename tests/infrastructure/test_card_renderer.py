"""Tests for the local Pillow fortune card renderer."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from astrbot_plugin_setu.src.domain.fortune.detail_dto import FortuneDetail
from astrbot_plugin_setu.src.infrastructure.fortune.card_renderer import (
    HEIGHT,
    WIDTH,
    FortuneCardRenderer,
    _hex_to_rgb,
    _level,
)
from astrbot_plugin_setu.src.infrastructure.fortune.fonts import load_font


def _detail(**overrides) -> FortuneDetail:
    base = dict(
        yi=("早睡", "摸鱼", "喝奶茶"),
        ji=("熬夜", "立flag", "查余额"),
        lucky_color="月白",
        lucky_color_hex="#DCEBF0",
        lucky_number=42,
        lucky_direction="正东",
        lucky_item="钢笔",
        dims=(
            ("career", "事业", 80),
            ("study", "学业", 44),
            ("love", "爱情", 68),
            ("wealth", "财运", 52),
            ("health", "健康", 33),
        ),
        auspicious_hour="午时",
        auspicious_hour_window="11:00-13:00",
        blessing="慢一点也没关系，你已在路上",
        sign_grade="中签",
        sign_title="女娲氏炼石",
        sign_type="中签卯宫",
        sign_poetry="昔日行船失了针。今朝依旧海中寻。",
        sign_meaning="此卦海中寻针之象。",
        sign_explanation="福是缘基。",
        lunar_date="农历 二零二六 九月 十八日",
        ganzhi="丙午年戊戌月甲戌日",
    )
    base.update(overrides)
    return FortuneDetail(**base)


def _fortune(**overrides) -> dict:
    base = {
        "username": "测试用户",
        "date_str": "2026-10-27",
        "title": "大吉",
        "star_count": 6,
        "max_stars": 7,
        "theme_color": "theme-red",
        "detail": _detail(),
    }
    base.update(overrides)
    return base


def _png_bytes(size=(400, 600), color=(120, 160, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_render_returns_png_with_expected_width() -> None:
    data = FortuneCardRenderer().render(_fortune())

    img = Image.open(io.BytesIO(data))
    assert img.format == "PNG"
    assert img.width == WIDTH
    assert img.height > 600


def test_render_is_fast_enough_for_interactive_use() -> None:
    """本地渲染必须远快于旧的远端 t2i（45-60 秒）。"""
    import time

    renderer = FortuneCardRenderer()
    renderer.render(_fortune())  # 预热字体缓存
    start = time.perf_counter()
    renderer.render(_fortune())
    elapsed = time.perf_counter() - start
    assert elapsed < 3.0, f"local render took {elapsed:.2f}s"


def test_vertical_background_is_not_cropped() -> None:
    """竖版配图完整放入固定图片槽（不裁切），且画布尺寸始终不变。"""
    renderer = FortuneCardRenderer()
    tall = _png_bytes(size=(500, 2000))

    img = Image.open(io.BytesIO(renderer.render(_fortune(), tall)))

    assert img.size == (WIDTH, HEIGHT), "画布必须是固定尺寸，不能随图片高度变化"


def test_canvas_size_is_always_fixed() -> None:
    """无论配图比例如何、开关开合如何，输出恒为 1080x1920。"""
    renderer = FortuneCardRenderer()
    cases = [
        _png_bytes(size=(500, 2000)),  # 超长竖图
        _png_bytes(size=(2000, 500)),  # 超宽横图
        _png_bytes(size=(600, 600)),  # 方形
        None,  # 无图
    ]
    sizes = {
        Image.open(io.BytesIO(renderer.render(_fortune(), bg))).size
        for bg in cases
    }

    assert sizes == {(WIDTH, HEIGHT)}


def test_render_without_background_or_detail_still_works() -> None:
    renderer = FortuneCardRenderer()

    no_bg = renderer.render(_fortune(), None)
    assert Image.open(io.BytesIO(no_bg)).size == (WIDTH, HEIGHT)

    no_detail = renderer.render(_fortune(detail=None), _png_bytes())
    assert Image.open(io.BytesIO(no_detail)).size == (WIDTH, HEIGHT)


def test_render_tolerates_broken_background_bytes() -> None:
    renderer = FortuneCardRenderer()

    data = renderer.render(_fortune(), b"not-an-image")

    assert Image.open(io.BytesIO(data)).width == WIDTH


def test_five_lucky_cards_fit_one_row() -> None:
    """幸运指南最多 5 张卡片，宽度总和不应超出内容区。"""
    from astrbot_plugin_setu.src.infrastructure.fortune import card_renderer as cr

    items = 5
    total_w = cr.CONTENT_W
    gap = 14
    card_w = (total_w - gap * (items - 1)) // items

    assert card_w > 100, "卡片窄到无法容纳文字"
    assert card_w * items + gap * (items - 1) <= total_w


def test_hex_to_rgb_and_level_helpers() -> None:
    assert _hex_to_rgb("#FF8000") == (255, 128, 0)
    assert _hex_to_rgb("ff8000") == (255, 128, 0)
    with pytest.raises(ValueError):
        _hex_to_rgb("bad")

    assert _level(95) == "great"
    assert _level(70) == "good"
    assert _level(50) == "normal"
    assert _level(10) == "low"


def test_font_loader_is_cached_and_never_raises() -> None:
    first = load_font("regular", 20)
    second = load_font("regular", 20)

    assert first is second
    # 未知字重也不能抛异常
    assert load_font("unknown-weight", 16) is not None
