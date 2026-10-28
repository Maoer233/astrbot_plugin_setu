"""Tests for caption formatting merged into the image message chain."""

from __future__ import annotations

from astrbot_plugin_setu.src.domain.setu.meta import build_meta
from astrbot_plugin_setu.src.infrastructure.sending.formatters import (
    format_meta_caption,
    format_multi_caption,
)


def _meta(**kwargs):
    base = {
        "title": "イヴちゃん",
        "author": "松みこる",
        "pid": 103600264,
        "tags": ["双马尾", "ツインテール", "女孩子", "rurudo", "吊袜带"],
        "provider": "lolicon",
    }
    base.update(kwargs)
    return build_meta("https://example.com/a.jpg", **base)


def test_caption_includes_title_author_pid_and_chinese_tags() -> None:
    text = format_meta_caption(_meta())

    assert "标题: イヴちゃん" in text
    assert "作者: 松みこる" in text
    assert "PID: 103600264" in text
    # 只保留中文标签，日文假名与纯英文标签被过滤
    assert "#双马尾" in text
    assert "#吊袜带" in text
    assert "#ツインテール" not in text
    assert "#rurudo" not in text


def test_caption_returns_empty_when_provider_has_no_metadata() -> None:
    """SexNyan 这类只给 URL 的源不应该渲染出一堆空标签。"""
    meta = build_meta("https://example.com/a.jpg", provider="sexnyan")

    assert meta.has_details is False
    assert format_meta_caption(meta) == ""


def test_caption_respects_tag_limit() -> None:
    tags = ["女孩子"] + [f"标签{i}" for i in range(1, 20)]
    meta = build_meta("https://example.com/a.jpg", tags=tags)

    text = format_meta_caption(meta, tag_limit=3)
    shown = [part for part in text.split() if part.startswith("#")]

    assert len(shown) == 3


def test_caption_can_disable_tags() -> None:
    text = format_meta_caption(_meta(), include_tags=False)

    assert "标题: イヴちゃん" in text
    assert "#" not in text


def test_multi_caption_lists_each_image_and_summarises_remaining() -> None:
    metas = tuple(
        build_meta(f"https://example.com/{i}.jpg", title=f"作品{i}", author="作者")
        for i in range(1, 6)
    )

    text = format_multi_caption(metas, max_blocks=3)

    assert "#1 作品1 - 作者" in text
    assert "#3 作品3 - 作者" in text
    assert "另有 2 张" in text


def test_multi_caption_single_image_uses_full_block() -> None:
    text = format_multi_caption((_meta(),))

    assert "标题: イヴちゃん" in text
