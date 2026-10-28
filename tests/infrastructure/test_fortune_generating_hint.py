"""Verify the fortune command emits a 'generating' hint before the card."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot_plugin_setu.src.infrastructure.astrbot.commands import fortune as fortune_cmd
from astrbot_plugin_setu.src.shared.config import SetuPluginConfig


@pytest.fixture
def configured(monkeypatch, sample_config_dict):
    config = SetuPluginConfig(**sample_config_dict)
    monkeypatch.setattr(fortune_cmd, "get_config", lambda: config)
    return config


def _event() -> MagicMock:
    event = MagicMock()
    event.get_sender_id.return_value = "user-1"
    event.get_group_id.return_value = "group-1"
    event.unified_msg_origin = "group-1"
    event.message_obj.sender.nickname = "tester"
    event.plain_result = lambda text: {"plain": text}
    event.chain_result = lambda chain: {"chain": chain}
    return event


@pytest.mark.asyncio
async def test_generating_hint_sent_when_card_is_not_cached(
    monkeypatch, configured
) -> None:
    """首次生成会先发一条提示，再发卡片。"""
    handler = fortune_cmd.FortuneCommandHandler()
    event = _event()

    fake_service = MagicMock()

    async def get_or_create(_request):
        record = MagicMock()
        record.user_id = "user-1"
        record.date_str = "2026-10-27"
        return record

    async def no_cache(_uid, _date):
        return None

    async def render(_record, _service):
        return b"PNGDATA"

    fake_service.get_or_create_fortune = get_or_create
    fake_service.get_cached_image = no_cache
    monkeypatch.setattr(handler, "_build_fortune_service", lambda: fake_service)
    monkeypatch.setattr(handler, "_render_fortune_image", render)
    monkeypatch.setattr(handler, "_check_access", AsyncMock(return_value=(True, "")))
    monkeypatch.setattr(handler, "_send_fortune_with_auto_revoke", AsyncMock(return_value=False))

    context = MagicMock()
    context.send_message = AsyncMock()
    monkeypatch.setattr(fortune_cmd, "get_plugin_context", lambda: context)

    results = [item async for item in handler.fortune_command(event)]

    context.send_message.assert_awaited_once()
    sent = context.send_message.await_args.args[1]
    assert "生成" in sent["plain"]
    assert sent["plain"] == configured.resolve_message("fortune_generating")
    # 提示之后仍然正常产出卡片
    assert results and "chain" in results[0]


@pytest.mark.asyncio
async def test_no_hint_when_cache_hit(monkeypatch, configured) -> None:
    """已有当日缓存时不再发提示，避免秒回还多一条废消息。"""
    handler = fortune_cmd.FortuneCommandHandler()
    event = _event()

    fake_service = MagicMock()

    async def get_or_create(_request):
        record = MagicMock()
        record.user_id = "user-1"
        record.date_str = "2026-10-27"
        return record

    async def has_cache(_uid, _date):
        return b"CACHED"

    fake_service.get_or_create_fortune = get_or_create
    fake_service.get_cached_image = has_cache
    monkeypatch.setattr(handler, "_build_fortune_service", lambda: fake_service)
    monkeypatch.setattr(handler, "_check_access", AsyncMock(return_value=(True, "")))
    monkeypatch.setattr(handler, "_send_fortune_with_auto_revoke", AsyncMock(return_value=False))

    context = MagicMock()
    context.send_message = AsyncMock()
    monkeypatch.setattr(fortune_cmd, "get_plugin_context", lambda: context)

    results = [item async for item in handler.fortune_command(event)]

    context.send_message.assert_not_awaited()
    assert results and "chain" in results[0]


@pytest.mark.asyncio
async def test_hint_can_be_disabled_by_config(monkeypatch, sample_config_dict) -> None:
    """配置里关掉 fortune_generating 后不再发送提示。"""
    config_dict = {**sample_config_dict}
    config_dict["messages"] = {
        **config_dict.get("messages", {}),
        "fortune_generating": {"enabled": False, "text": "x"},
    }
    config = SetuPluginConfig(**config_dict)
    monkeypatch.setattr(fortune_cmd, "get_config", lambda: config)

    handler = fortune_cmd.FortuneCommandHandler()
    event = _event()

    fake_service = MagicMock()

    async def get_or_create(_request):
        record = MagicMock()
        record.user_id = "user-1"
        record.date_str = "2026-10-27"
        return record

    async def no_cache(_uid, _date):
        return None

    fake_service.get_or_create_fortune = get_or_create
    fake_service.get_cached_image = no_cache
    monkeypatch.setattr(handler, "_build_fortune_service", lambda: fake_service)
    monkeypatch.setattr(
        handler, "_render_fortune_image", AsyncMock(return_value=b"PNG")
    )
    monkeypatch.setattr(handler, "_check_access", AsyncMock(return_value=(True, "")))
    monkeypatch.setattr(handler, "_send_fortune_with_auto_revoke", AsyncMock(return_value=False))

    context = MagicMock()
    context.send_message = AsyncMock()
    monkeypatch.setattr(fortune_cmd, "get_plugin_context", lambda: context)

    [item async for item in handler.fortune_command(event)]

    context.send_message.assert_not_awaited()
