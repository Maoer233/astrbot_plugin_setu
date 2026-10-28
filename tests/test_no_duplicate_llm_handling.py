"""命令命中后必须阻止同一条消息再被 LLM 工具处理。

线上现象：一条 ``/setu 5 ...`` 会得到「5 张合并转发」+「1 张信息卡」两组图，
因为命令处理器和 ``get_setu_image`` LLM 工具各发了一次。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from astrbot_plugin_setu.main import _route_regex_command, _stop_llm_processing


class _Handler:
    def __init__(self, marker: str = "ok") -> None:
        self.marker = marker

    async def get_random_picture(self, event):
        yield self.marker

    async def random_doujinshi_command(self, event, *, tags: str = ""):
        yield self.marker

    async def fortune_command(self, event):
        yield self.marker


def _event_with_stop(message: str) -> tuple[MagicMock, list[str]]:
    event = MagicMock()
    event.message_str = message
    event.is_at_or_wake_command = False
    calls: list[str] = []
    event.stop_event = lambda: calls.append("stopped")
    return event, calls


def test_stop_llm_processing_calls_stop_event() -> None:
    event, calls = _event_with_stop("x")

    _stop_llm_processing(event)

    assert calls == ["stopped"]


def test_stop_llm_processing_tolerates_missing_api() -> None:
    """框架版本不同、没有 stop_event 时不能抛异常。"""
    event = MagicMock(spec=[])

    _stop_llm_processing(event)  # 不应抛出


@pytest.mark.asyncio
async def test_setu_regex_stops_llm_after_sending() -> None:
    event, calls = _event_with_stop("来份色图")

    results = [r async for r in _route_regex_command(event, _Handler(), _Handler())]

    assert results == ["ok"]
    assert calls == ["stopped"], "命令发图后必须阻止 LLM 再次处理"


@pytest.mark.asyncio
async def test_doujinshi_regex_stops_llm_after_sending() -> None:
    event, calls = _event_with_stop("来份碧蓝档案本子")

    [r async for r in _route_regex_command(event, _Handler(), _Handler())]

    assert calls == ["stopped"]


@pytest.mark.asyncio
async def test_fortune_regex_stops_llm_after_sending() -> None:
    event, calls = _event_with_stop("今日运势")

    [r async for r in _route_regex_command(event, _Handler(), _Handler())]

    assert calls == ["stopped"]
