"""Verify one /setu command produces exactly one outgoing message."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import astrbot.api.message_components as Comp
import pytest

from astrbot_plugin_setu.src.application.setu import ImagePayload
from astrbot_plugin_setu.src.domain.setu.meta import build_meta
from astrbot_plugin_setu.src.infrastructure.astrbot.config import (
    clear_config,
    set_plugin_context,
)
from astrbot_plugin_setu.src.infrastructure.sending.image_sender import ImageSender
from astrbot_plugin_setu.src.shared.config import SetuPluginConfig
from astrbot_plugin_setu.src.shared.send_cache import clear_send_cache


@pytest.fixture(autouse=True)
def reset_singletons() -> None:
    clear_config()
    clear_send_cache()
    yield
    clear_send_cache()
    clear_config()


def _payload(image_path: Path, *, with_meta: bool = True) -> ImagePayload:
    metas = (
        (
            build_meta(
                "https://example.com/image.jpg",
                title="イヴちゃん",
                author="松みこる",
                pid=103600264,
                tags=["双马尾", "ツインテール", "女孩子", "rurudo"],
                provider="lolicon",
            ),
        )
        if with_meta
        else ()
    )
    return ImagePayload(
        urls=("https://example.com/image.jpg",),
        raw_bytes=(),
        file_paths=(image_path,),
        items=(image_path,),
        r18=False,
        tags=(),
        metas=metas,
    )


def _capture_context() -> tuple[MagicMock, list[Any]]:
    context = MagicMock()
    sent: list[Any] = []

    async def send_message(_origin: str, result: Any) -> Any:
        sent.append(result)
        return {"message_id": f"m{len(sent)}"}

    context.send_message = AsyncMock(side_effect=send_message)
    set_plugin_context(context)
    return context, sent


def _chain_of(result: Any) -> list[Any]:
    for attr in ("result_chain", "chain", "message"):
        value = getattr(result, attr, None)
        if isinstance(value, list):
            return value
    return []


@pytest.mark.asyncio
async def test_one_command_sends_single_message_with_caption(
    tmp_path: Path, mock_event, sample_config_dict
) -> None:
    """图与说明在同一条消息里，不再额外发'找到 N 张'。"""
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image-data")

    context, sent = _capture_context()
    mock_event.platform.name = "aiocqhttp"
    mock_event.bot = MagicMock()
    mock_event.bot.call_action = AsyncMock()

    config = SetuPluginConfig(**sample_config_dict)
    results = [
        item
        async for item in ImageSender(config).send_images(
            _payload(image_path), mock_event
        )
    ]

    assert results == [{"send_success": True, "image_count": 1}]
    assert len(sent) == 1, f"expected exactly one outgoing message, got {len(sent)}"

    chain = _chain_of(sent[0])
    images = [c for c in chain if isinstance(c, Comp.Image)]
    plains = [c for c in chain if isinstance(c, Comp.Plain)]

    assert len(images) == 1
    assert len(plains) == 1, "caption should ride inside the same message"

    caption = plains[0].text
    assert "标题: イヴちゃん" in caption
    assert "作者: 松みこる" in caption
    assert "PID: 103600264" in caption
    assert "#双马尾" in caption
    assert "#ツインテール" not in caption


@pytest.mark.asyncio
async def test_caption_omitted_when_provider_has_no_metadata(
    tmp_path: Path, mock_event, sample_config_dict
) -> None:
    """sexnyan 这类无元数据源不追加说明，保持单图消息干净。"""
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image-data")

    context, sent = _capture_context()
    mock_event.platform.name = "aiocqhttp"
    mock_event.bot = MagicMock()
    mock_event.bot.call_action = AsyncMock()

    config = SetuPluginConfig(**sample_config_dict)
    [
        item
        async for item in ImageSender(config).send_images(
            _payload(image_path, with_meta=False), mock_event
        )
    ]

    chain = _chain_of(sent[0])
    plains = [c for c in chain if isinstance(c, Comp.Plain)]
    # found 文案仍会并入，但不应该出现空的元数据标签
    text = "\n".join(p.text for p in plains)
    assert "标题:" not in text
    assert "标签:" not in text


@pytest.mark.asyncio
async def test_merge_disabled_restores_legacy_split_send(
    tmp_path: Path, mock_event, sample_config_dict: dict[str, Any]
) -> None:
    """关闭图文合并后，说明不再进图片链，但「找到 N 张」统计必须补发。"""
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image-data")

    context, sent = _capture_context()
    mock_event.platform.name = "aiocqhttp"
    mock_event.bot = MagicMock()
    mock_event.bot.call_action = AsyncMock()

    config_dict = {**sample_config_dict}
    config_dict["setu_general"] = {
        **config_dict.get("setu_general", {}),
        "merge_text_into_image": False,
    }
    config = SetuPluginConfig(**config_dict)

    [
        item
        async for item in ImageSender(config).send_images(
            _payload(image_path), mock_event
        )
    ]

    chain = _chain_of(sent[0])
    plains = [c for c in chain if isinstance(c, Comp.Plain)]
    assert plains == [], "caption must not be merged when the toggle is off"

    # 旧行为：统计文案单独发一条，不能因为重构而丢失
    assert len(sent) == 2, "found summary should still be sent separately"
    followup = sent[1]
    texts = [getattr(c, "text", "") for c in _chain_of(followup)]
    if not texts:
        # plain_result 可能直接暴露 text 属性而不是 chain
        texts = [str(getattr(followup, "text", ""))]
    assert any("找到" in t for t in texts), f"unexpected followup: {followup!r}"


@pytest.mark.asyncio
async def test_forward_mode_keeps_caption_inside_nodes(
    tmp_path: Path, mock_event, sample_config_dict: dict[str, Any]
) -> None:
    """合并转发模式下说明文字必须作为节点保留，而不是被静默丢掉。

    这是默认配置下多图请求的常见路径：send_mode=auto + 平台支持 forward
    且图片数 > 1 时会走 forward，旧实现只挑 Comp.Image 建节点。
    """
    images = []
    for i in range(2):
        path = tmp_path / f"image{i}.jpg"
        path.write_bytes(b"image-data")
        images.append(path)

    metas = tuple(
        build_meta(
            f"https://example.com/image{i}.jpg",
            title=f"作品{i}",
            author="作者",
            pid=100 + i,
            tags=["女孩子"],
            provider="lolicon",
        )
        for i in range(2)
    )
    payload = ImagePayload(
        urls=tuple(m.url for m in metas),
        raw_bytes=(),
        file_paths=tuple(images),
        items=tuple(images),
        r18=False,
        tags=(),
        metas=metas,
    )

    context = MagicMock()
    sent: list[Any] = []

    async def send_message(_origin: str, result: Any) -> Any:
        sent.append(result)
        return {"message_id": f"m{len(sent)}"}

    context.send_message = AsyncMock(side_effect=send_message)
    set_plugin_context(context)

    mock_event.platform.name = "aiocqhttp"
    mock_event.get_self_id = MagicMock(return_value="10000")
    mock_event.bot = MagicMock()
    mock_event.bot.call_action = AsyncMock(return_value={"message_id": "m1"})

    config_dict = {**sample_config_dict}
    config_dict["delivery"] = {
        **config_dict.get("delivery", {}),
        "send_mode": "forward",
    }
    config = SetuPluginConfig(**config_dict)

    [
        item
        async for item in ImageSender(config).send_images(payload, mock_event)
    ]

    assert sent, "forward send should produce an outgoing message"
    chain = _chain_of(sent[0])

    nodes = [c for c in chain if isinstance(c, Comp.Nodes)]
    assert nodes, "forward mode should build forward nodes"

    contents = []
    for node in nodes[0].nodes:
        contents.extend(node.content)

    plain_texts = [c.text for c in contents if isinstance(c, Comp.Plain)]
    assert plain_texts, "caption must survive forward mode as a node"
    joined = "\n".join(plain_texts)
    assert "作品0" in joined
    assert "#女孩子" in joined
