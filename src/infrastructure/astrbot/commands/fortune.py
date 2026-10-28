"""Fortune command handler - all Fortune-related commands."""

from __future__ import annotations

import asyncio
import base64
import random
from collections.abc import AsyncGenerator
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent
from astrbot.core.provider.register import llm_tools

from ....domain.access_control import AccessPolicy
from ....domain.access_control.service import AccessControlService
from ....domain.fortune import (
    FortuneGenerationRequest,
    FortuneRecord,
)
from ....domain.fortune.service import FortuneOptions, FortuneService
from ....domain.setu import SetuRequest
from ....shared import get_logger
from ... import get_access_control_repo, get_provider
from ...fortune import get_fortune_pools
from ...fortune.card_renderer import FortuneCardRenderer
from ...fortune.image_guard import is_valid_image
from ...fortune.lunar_bridge import get_lunar_info
from ...permission_service import PermissionService
from ...persistence import get_fortune_repo
from ...providers import init_provider_from_config
from ...sending import DirectSendStrategy, schedule_revoke
from ...sending.platform_capabilities import is_onebot_like_platform
from ..config import get_config, get_plugin_context
from ..fortune_renderer import FortuneRenderer

logger = get_logger()

# Regex pattern directly in decorator (not in constants)
FORTUNE_REGEX_PATTERN = r"^(?!/)(今日运势|jrys)$"


def _lunar_provider(date_str: str):
    """Adapter so the domain service never imports the optional lunar library."""
    try:
        from datetime import date as _date

        return get_lunar_info(_date.fromisoformat(date_str))
    except (ValueError, TypeError):
        return None


class FortuneCommandHandler:
    """Handles all Fortune-related commands.

    Uses singleton pattern for config, fortune repo, and access control repo.
    Commands are auto-registered by AstrBot decorators.
    """

    def __init__(self) -> None:
        # 本地 Pillow 渲染器（毫秒级、零网络）。旧的 HTML 远端 t2i 渲染器
        # 单张要 45-60 秒，已不再使用；FortuneRenderer 保留供降级/调试。
        self._card_renderer = FortuneCardRenderer()
        self._renderer = FortuneRenderer()

    # ==================== Command Handlers ====================

    def _build_fortune_service(self) -> FortuneService:
        """Build a FortuneService wired to the current fortune config."""
        config = get_config()
        options = None
        if config is not None:
            fortune = getattr(config, "fortune", None)
            options = FortuneOptions(
                master_key=getattr(fortune, "master_key", "") or "astrbot-setu",
                enable_yi_ji=getattr(fortune, "enable_yi_ji", True),
                enable_lucky=getattr(fortune, "enable_lucky", True),
                enable_dims=getattr(fortune, "enable_dims", True),
                enable_hour=getattr(fortune, "enable_hour", True),
                enable_blessing=getattr(fortune, "enable_blessing", True),
                enable_sign=getattr(fortune, "enable_sign", True),
                enable_lunar=getattr(fortune, "enable_lunar", True),
                sign_count=getattr(fortune, "sign_count", 1) or 1,
            )
        return FortuneService(
            repository=get_fortune_repo(),
            options=options,
            pools_provider=get_fortune_pools,
            lunar_provider=_lunar_provider,
            image_guard=is_valid_image,
        )

    async def fortune_command(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[Any, None]:
        """Handle /今日运势 command (/今日运势, /jrys)."""
        config = get_config()
        if not config:
            if result := self._plain(event, self._message("config_not_loaded")):
                yield result
            return

        has_perm, msg = await self._check_access(event, config)
        if not has_perm:
            if result := self._plain(event, msg):
                yield result
            return

        request = self._build_fortune_request(event)

        try:
            service = self._build_fortune_service()
            result = await service.get_or_create_fortune(request)

            # 取图 + 渲染要花几秒，先给用户一条"正在生成"的提示。
            # 已有当日缓存时不发，避免秒回情况下多一条废消息。
            cached = await service.get_cached_image(result.user_id, result.date_str)
            if not cached:
                hint = self._message("fortune_generating")
                if hint:
                    await self._send_plain_text(event, hint)

            fortune_image = cached or await self._render_fortune_image(
                result, service
            )
            if fortune_image:
                chain = [Comp.Image.fromBytes(fortune_image)]
                if await self._send_fortune_with_auto_revoke(event, chain, config):
                    return
                yield event.chain_result(chain)
            else:
                text = self._format_fortune(result)
                if await self._send_fortune_with_auto_revoke(
                    event, [Comp.Plain(text)], config
                ):
                    return
                yield event.plain_result(text)
        except Exception as e:
            text = self._message("fortune_get_failed", error=e)
            if result := self._plain(event, text):
                yield result

    async def _send_plain_text(self, event: AstrMessageEvent, text: str) -> bool:
        """Send one standalone plain-text message via the plugin context."""
        plugin_context = get_plugin_context()
        if plugin_context is None:
            return False
        try:
            await plugin_context.send_message(event.unified_msg_origin, event.plain_result(text))
            return True
        except Exception as exc:
            logger.debug("[fortune] failed to send hint text: %s", exc)
            return False

    async def _send_fortune_with_auto_revoke(
        self,
        event: AstrMessageEvent,
        chain: list[Any],
        config: Any,
    ) -> bool:
        """在启用且支持的情况下发送今日运势并登记可恢复撤回任务。"""
        if not getattr(config, "auto_revoke_fortune_enabled", False):
            return False
        if config.auto_revoke_delay <= 0:
            return False

        platform_name = getattr(getattr(event, "platform", None), "name", None)
        if not is_onebot_like_platform(platform_name):
            return False

        plugin_context = get_plugin_context()
        if plugin_context is None:
            logger.warning("[fortune] 自动撤回已启用但插件上下文未初始化")
            return False

        send_result = await DirectSendStrategy(plugin_context).send_with_status(
            event, chain, auto_revoke=True
        )
        if not send_result.accepted:
            return False
        if not send_result.message_ids:
            logger.warning("[fortune] 发送成功但未返回 message_id，无法登记自动撤回")
            return True

        scheduled = 0
        for message_id in send_result.message_ids:
            if await schedule_revoke(event, message_id, config.auto_revoke_delay):
                scheduled += 1
        if scheduled == 0:
            logger.warning("[fortune] 未能登记今日运势自动撤回任务")
        return True

    async def refresh_fortune_command(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[Any, None]:
        """Handle /刷新今日运势 command."""
        has_perm, msg = PermissionService.require_admin(event)
        if not has_perm:
            if result := self._plain(event, msg):
                yield result
            return

        config = get_config()
        if not config:
            if result := self._plain(event, self._message("config_not_loaded")):
                yield result
            return

        request = self._build_fortune_request(event)

        try:
            service = self._build_fortune_service()
            result = await service.refresh_fortune(request)
            yield event.plain_result(self._format_fortune(result))
        except Exception as e:
            text = self._message("fortune_refresh_failed", error=e)
            if result := self._plain(event, text):
                yield result

    async def refresh_group_fortune_command(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[Any, None]:
        """Handle /刷新本群今日运势 command."""
        has_perm, msg = PermissionService.require_admin(event)
        if not has_perm:
            if result := self._plain(event, msg):
                yield result
            return

        group_id = event.get_group_id()
        if not group_id:
            if result := self._plain(event, self._message("fortune_group_only")):
                yield result
            return

        config = get_config()
        if not config:
            if result := self._plain(event, self._message("config_not_loaded")):
                yield result
            return

        try:
            service = self._build_fortune_service()
            refreshed_count = await service.pregenerate_active_users()
            text = self._message("fortune_refresh_group_done", count=refreshed_count)
            if result := self._plain(event, text):
                yield result
        except Exception as e:
            text = self._message("fortune_refresh_group_failed", error=e)
            if result := self._plain(event, text):
                yield result

    async def refresh_all_fortune_command(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[Any, None]:
        """Handle /刷新全局今日运势 command."""
        has_perm, msg = PermissionService.require_super_user(event)
        if not has_perm:
            if result := self._plain(event, msg):
                yield result
            return

        config = get_config()
        if not config:
            if result := self._plain(event, self._message("config_not_loaded")):
                yield result
            return

        try:
            service = self._build_fortune_service()
            refreshed_count = await service.pregenerate_active_users()
            text = self._message("fortune_refresh_all_done", count=refreshed_count)
            if result := self._plain(event, text):
                yield result
        except Exception as e:
            text = self._message("fortune_refresh_all_failed", error=e)
            if result := self._plain(event, text):
                yield result

    async def enable_fortune_group_command(
        self, event: AstrMessageEvent, args: str = ""
    ) -> AsyncGenerator[Any, None]:
        """Handle /开启运势 command (enable Fortune for current group)."""
        has_perm, msg = PermissionService.require_admin(event)
        if not has_perm:
            if result := self._plain(event, msg):
                yield result
            return

        group_id = event.get_group_id()
        if not group_id:
            if result := self._plain(event, self._message("fortune_group_only")):
                yield result
            return

        repo = get_access_control_repo()
        await repo.remove_fortune_blocked_group(str(group_id))
        if result := self._plain(event, self._message("fortune_enabled_group_done")):
            yield result

    async def disable_fortune_group_command(
        self, event: AstrMessageEvent, args: str = ""
    ) -> AsyncGenerator[Any, None]:
        """Handle /关闭运势 command (disable Fortune for current group)."""
        has_perm, msg = PermissionService.require_admin(event)
        if not has_perm:
            if result := self._plain(event, msg):
                yield result
            return

        group_id = event.get_group_id()
        if not group_id:
            if result := self._plain(event, self._message("fortune_group_only")):
                yield result
            return

        repo = get_access_control_repo()
        await repo.add_fortune_blocked_group(str(group_id))
        if result := self._plain(event, self._message("fortune_disabled_group_done")):
            yield result

    async def block_fortune_user_command(
        self, event: AstrMessageEvent, args: str = ""
    ) -> AsyncGenerator[Any, None]:
        """Handle /拉黑运势用户 command (add user to Fortune blacklist)."""
        has_perm, msg = PermissionService.require_admin(event)
        if not has_perm:
            if result := self._plain(event, msg):
                yield result
            return

        target_id = args.strip() or event.get_sender_id()
        if not target_id:
            if result := self._plain(event, self._message("fortune_missing_user_id")):
                yield result
            return

        repo = get_access_control_repo()
        await repo.add_fortune_blocked_user(str(target_id))
        text = self._message("fortune_block_user_done", user_id=target_id)
        if result := self._plain(event, text):
            yield result

    async def unblock_fortune_user_command(
        self, event: AstrMessageEvent, args: str = ""
    ) -> AsyncGenerator[Any, None]:
        """Handle /解除运势拉黑 command (remove user from Fortune blacklist)."""
        has_perm, msg = PermissionService.require_admin(event)
        if not has_perm:
            if result := self._plain(event, msg):
                yield result
            return

        target_id = args.strip()
        if not target_id:
            if result := self._plain(event, self._message("fortune_missing_user_id")):
                yield result
            return

        repo = get_access_control_repo()
        await repo.remove_fortune_blocked_user(str(target_id))
        text = self._message("fortune_unblock_user_done", user_id=target_id)
        if result := self._plain(event, text):
            yield result

    async def trust_fortune_user_command(
        self, event: AstrMessageEvent, args: str = ""
    ) -> AsyncGenerator[Any, None]:
        """Handle /信任运势用户 command (add user to Fortune whitelist)."""
        has_perm, msg = PermissionService.require_admin(event)
        if not has_perm:
            if result := self._plain(event, msg):
                yield result
            return

        target_id = args.strip() or event.get_sender_id()
        if not target_id:
            if result := self._plain(event, self._message("fortune_missing_user_id")):
                yield result
            return

        repo = get_access_control_repo()
        await repo.add_fortune_whitelist_user(str(target_id))
        text = self._message("fortune_trust_user_done", user_id=target_id)
        if result := self._plain(event, text):
            yield result

    async def untrust_fortune_user_command(
        self, event: AstrMessageEvent, args: str = ""
    ) -> AsyncGenerator[Any, None]:
        """Handle /取消运势信任 command (remove user from Fortune whitelist)."""
        has_perm, msg = PermissionService.require_admin(event)
        if not has_perm:
            if result := self._plain(event, msg):
                yield result
            return

        target_id = args.strip()
        if not target_id:
            if result := self._plain(event, self._message("fortune_missing_user_id")):
                yield result
            return

        repo = get_access_control_repo()
        await repo.remove_fortune_whitelist_user(str(target_id))
        text = self._message("fortune_untrust_user_done", user_id=target_id)
        if result := self._plain(event, text):
            yield result

    # ==================== LLM Tool Handlers ====================

    async def _llm_get_fortune(self, event: AstrMessageEvent) -> str:
        """LLM tool handler for getting today's fortune.

        Sends the rendered card itself and returns a short text summary for the
        model to phrase.  Returning text alone made the model reply with prose
        while the user never saw a card.
        """
        config = get_config()
        if not config:
            return self._message("config_not_loaded")

        has_perm, msg = await self._check_access(event, config)
        if not has_perm:
            return msg

        request = self._build_fortune_request(event)

        try:
            service = self._build_fortune_service()
            result = await service.get_or_create_fortune(request)
            await self._send_card_for_llm_tool(event, result, service, config)
            return (
                f"今日运势已生成并发送给用户: {result.title}, "
                f"星级: {result.star_count}/{result.max_stars}。"
                "请用一两句话口语化地告诉用户结果，不要重复数字格式。"
            )
        except Exception as e:
            return self._message("fortune_get_failed", error=e)

    async def _send_card_for_llm_tool(
        self,
        event: AstrMessageEvent,
        record: FortuneRecord,
        service: FortuneService,
        config: Any,
    ) -> bool:
        """Deliver the fortune card for an LLM tool call; never raises."""
        try:
            image_bytes = await self._render_fortune_image(record, service)
            if not image_bytes:
                return False
            return await self._send_fortune_with_auto_revoke(
                event, [Comp.Image.fromBytes(image_bytes)], config
            )
        except Exception as exc:
            logger.warning("[fortune] llm tool card delivery failed: %s", exc)
            return False

    async def _llm_refresh_fortune(self, event: AstrMessageEvent) -> str:
        """LLM tool handler for refreshing today's fortune."""
        has_perm, msg = PermissionService.require_admin(event)
        if not has_perm:
            return msg

        config = get_config()
        if not config:
            return self._message("config_not_loaded")

        request = self._build_fortune_request(event)

        try:
            service = self._build_fortune_service()
            result = await service.refresh_fortune(request)
            return f"今日运势已刷新: {result.title}"
        except Exception as e:
            return self._message("fortune_refresh_failed", error=e)

    async def _llm_refresh_group_fortune(self, event: AstrMessageEvent) -> str:
        """LLM tool handler for refreshing group fortunes."""
        has_perm, msg = PermissionService.require_admin(event)
        if not has_perm:
            return msg

        group_id = event.get_group_id()
        if not group_id:
            return self._message("fortune_group_only")

        config = get_config()
        if not config:
            return self._message("config_not_loaded")

        try:
            service = self._build_fortune_service()
            refreshed_count = await service.pregenerate_active_users()
            return self._message("fortune_refresh_group_done", count=refreshed_count)
        except Exception as e:
            return self._message("fortune_refresh_group_failed", error=e)

    async def _llm_refresh_all_fortune(self, event: AstrMessageEvent) -> str:
        """LLM tool handler for refreshing all fortunes."""
        has_perm, msg = PermissionService.require_super_user(event)
        if not has_perm:
            return msg

        config = get_config()
        if not config:
            return self._message("config_not_loaded")

        try:
            service = self._build_fortune_service()
            refreshed_count = await service.pregenerate_active_users()
            return self._message("fortune_refresh_all_done", count=refreshed_count)
        except Exception as e:
            return self._message("fortune_refresh_all_failed", error=e)

    async def pregenerate_active_fortune_images(self, days: int = 3) -> int:
        """Pregenerate and cache rendered fortune cards for active users."""
        config = get_config()
        if not config or not config.fortune.enabled or not config.fortune.auto_refresh:
            return 0

        service = self._build_fortune_service()
        access_repo = get_access_control_repo()
        access_service = AccessControlService(access_repo)
        modes = access_repo.get_modes()

        async def can_pregenerate(request: FortuneGenerationRequest) -> bool:
            policy = AccessPolicy.for_session(
                user_id=request.user_id,
                group_id=request.group_id,
                user_mode=modes.get(
                    "fortune_user_access_control_mode",
                    config.fortune_user_access_control_mode if config else "none",
                ),
                group_mode=modes.get(
                    "fortune_group_access_control_mode",
                    config.fortune_group_access_control_mode if config else "none",
                ),
            )
            allowed, reason = await access_service.check_fortune_access(policy)
            if not allowed:
                logger.debug(
                    "[fortune] skip pregeneration by access-control: user=%s, group=%s, reason=%s",
                    request.user_id,
                    request.group_id or "-",
                    reason or "-",
                )
            return allowed

        records = await service.pregenerate_active_user_records(
            days=days,
            include_existing=True,
            access_filter=can_pregenerate,
        )

        cached_count = 0
        for record in records:
            try:
                # 升级前的旧行没有 detail，直接渲染会缓存一张没有宜忌/分运的空白卡，
                # 而用户当天会因为缓存命中一直看到它。先补算再画。
                if record.detail is None:
                    detail = service._build_detail(record)
                    record = record.with_detail(detail, record.nonce)
                    await service._repo.save_fortune(record)
                    # 旧版本可能已经用 detail=None 渲染过一张空白卡，
                    # 不清掉的话当天会一直命中它。
                    await service._repo.delete_cached_image(
                        record.user_id, record.date_str
                    )
                if await service.get_cached_image(record.user_id, record.date_str):
                    continue
                image_bytes = await self._render_fortune_image(record, service)
                if image_bytes:
                    cached_count += 1
            except Exception as exc:
                logger.warning(
                    "[fortune] Failed to pregenerate cache for %s: %s",
                    record.user_id,
                    exc,
                )
        return cached_count

    # ==================== Helper Methods ====================

    async def _check_access(self, event: AstrMessageEvent, config) -> tuple[bool, str]:
        """Check if user/group has access to Fortune feature."""
        user_id = event.get_sender_id()
        group_id = event.get_group_id()

        repo = get_access_control_repo()
        modes = repo.get_modes()
        policy = AccessPolicy.for_session(
            user_id=user_id,
            group_id=group_id,
            user_mode=modes.get(
                "fortune_user_access_control_mode",
                config.fortune_user_access_control_mode if config else "none",
            ),
            group_mode=modes.get(
                "fortune_group_access_control_mode",
                config.fortune_group_access_control_mode if config else "none",
            ),
        )
        service = AccessControlService(repo)
        allowed, reason = await service.check_fortune_access(policy)
        return allowed, reason or ""

    def _build_fortune_request(
        self, event: AstrMessageEvent
    ) -> FortuneGenerationRequest:
        """Build a framework-free fortune generation request from an AstrBot event."""
        user_id = str(event.get_sender_id())
        username = user_id
        message_obj = getattr(event, "message_obj", None)
        sender = getattr(message_obj, "sender", None)
        for attr in ("nickname", "name", "user_name"):
            value = getattr(sender, attr, None)
            if value:
                username = str(value)
                break
        group_id = event.get_group_id()
        return FortuneGenerationRequest.for_today(
            user_id=user_id,
            username=username,
            group_id=str(group_id) if group_id else None,
        )

    def _format_fortune(self, result: FortuneRecord) -> str:
        """Format fortune result for message sending."""
        return (
            f"🔮 {result.date_str} 运势\n"
            f"📊 {result.title}\n"
            f"⭐ {'★' * result.star_count}{'☆' * (result.max_stars - result.star_count)}\n"
            f"💬 {result.description}"
        )

    def _plain(self, event: AstrMessageEvent, text: str | None) -> Any | None:
        """Build a plain result only when the configured text is not empty."""
        if not text:
            return None
        return event.plain_result(text)

    async def _render_fortune_image(
        self, record: FortuneRecord, service: FortuneService
    ) -> bytes | None:
        """Render fortune to image, fallback to None when unavailable.

        Uses the local Pillow renderer: the previous HTML → remote t2i path cost
        45-60 s per card because every render uploaded ~3 MB (2 MB of base64
        fonts plus the picture) to a remote service.  Local drawing takes well
        under a second and needs no network at all.
        """
        cached = await service.get_cached_image(record.user_id, record.date_str)
        if cached:
            return cached

        bg_bytes, img_url = await self._get_fortune_background_image()
        if not bg_bytes:
            return None

        fortune_dict = {
            "username": record.username,
            "date_str": record.date_str,
            "title": record.title,
            "star_count": record.star_count,
            "max_stars": record.max_stars,
            "description": record.description,
            "extra_message": record.extra_message,
            "theme_color": record.theme_color,
            "detail": record.detail,
        }
        try:
            # Pillow 绘制是纯 CPU 活，放到线程池里避免阻塞事件循环。
            image_bytes = await asyncio.to_thread(
                self._card_renderer.render, fortune_dict, bg_bytes
            )
            if image_bytes:
                await service.update_image_cache(record, image_bytes, img_url)
            return image_bytes
        except Exception as exc:
            logger.warning("[fortune] local card render failed: %s", exc)

        # 本地渲染异常时的兜底：仍可尝试远端 HTML 渲染（较慢，但不至于无图）。
        try:
            image_bytes = await self._renderer.render_to_image(
                fortune_dict, image_base64=base64.b64encode(bg_bytes).decode("ascii")
            )
            if image_bytes:
                await service.update_image_cache(record, image_bytes, img_url)
            return image_bytes
        except Exception as exc:
            logger.warning("[fortune] html fallback render failed: %s", exc)
            return None

    async def _get_fortune_background_image(self) -> tuple[bytes | None, str | None]:
        config = get_config()
        if not config:
            return None, None
        try:
            init_provider_from_config(config)
            provider = get_provider()

            tags = [t.strip() for t in config.fortune.tags.split(",") if t.strip()]
            mode = config.fortune.content_mode.value
            if mode == "r18":
                is_r18 = True
            elif mode == "mix":
                is_r18 = random.random() > 0.5
            else:
                is_r18 = False

            request = SetuRequest.from_user_input(
                count=1,
                tags=tags,
                r18=is_r18,
                exclude_ai=config.exclude_ai,
                max_replenish_rounds=config.max_replenish_rounds,
            )
            payload = await provider.fetch_and_download(request)
            if payload.is_empty:
                return None, None

            if payload.raw_bytes:
                return payload.raw_bytes[0], payload.urls[0] if payload.urls else None
            if payload.items and isinstance(payload.items[0], bytes):
                return payload.items[0], payload.urls[0] if payload.urls else None
            if payload.file_paths:
                return payload.file_paths[0].read_bytes(), (
                    payload.urls[0] if payload.urls else None
                )
            return None, None
        except Exception as exc:
            logger.warning("[fortune] failed to fetch background image: %s", exc)
            return None, None

    def _message(self, key: str, **kwargs: Any) -> str:
        config = get_config()
        if config and hasattr(config, "resolve_message"):
            text = config.resolve_message(key, **kwargs)
            if text is not None:
                return text
        return ""


# ==================== LLM Tools Registration ====================


def register_llm_tools() -> None:
    """Register Fortune LLM tools."""
    _handler = FortuneCommandHandler()
    tools = [
        (
            "get_today_fortune",
            _handler._llm_get_fortune,
            [],
            "Get today's fortune for the user.",
        ),
        (
            "refresh_my_fortune",
            _handler._llm_refresh_fortune,
            [],
            "Refresh my today's fortune (admin only).",
        ),
        (
            "refresh_group_fortune",
            _handler._llm_refresh_group_fortune,
            [],
            "Refresh today's fortune for the current group (admin only).",
        ),
        (
            "refresh_all_fortune",
            _handler._llm_refresh_all_fortune,
            [],
            "Refresh today's fortune for all users (super admin only).",
        ),
    ]

    for name, handler, args, desc in tools:
        try:
            llm_tools.add_func(name=name, func_args=args, desc=desc, handler=handler)
            tool = llm_tools.get_func(name)
            if tool:
                tool.handler_module_path = __name__
        except (AttributeError, RuntimeError):
            pass


def unregister_llm_tools() -> None:
    """Unregister Fortune LLM tools."""
    tool_names = [
        "get_today_fortune",
        "refresh_my_fortune",
        "refresh_group_fortune",
        "refresh_all_fortune",
    ]

    for name in tool_names:
        try:
            llm_tools.remove_func(name)
        except (AttributeError, RuntimeError):
            pass
