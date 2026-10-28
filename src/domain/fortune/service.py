"""Fortune domain service for generating fortunes.

Extracts business logic from FortuneCore into a pure domain service.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from ...application.ports import FortuneRepository
from .detail import DEFAULT_MASTER_KEY, build_detail, make_rng
from .detail_dto import FortuneDetail
from .entities import (
    FortuneGenerationRequest,
    FortuneRecord,
    FortuneTheme,
    FortuneWeights,
)


class FortuneOptions:
    """Toggles for the locally derived detail block."""

    __slots__ = (
        "master_key",
        "enable_yi_ji",
        "enable_lucky",
        "enable_dims",
        "enable_hour",
        "enable_blessing",
        "enable_sign",
        "enable_lunar",
        "sign_count",
    )

    def __init__(
        self,
        *,
        master_key: str = DEFAULT_MASTER_KEY,
        enable_yi_ji: bool = True,
        enable_lucky: bool = True,
        enable_dims: bool = True,
        enable_hour: bool = True,
        enable_blessing: bool = True,
        enable_sign: bool = True,
        enable_lunar: bool = True,
        sign_count: int = 1,
    ) -> None:
        self.master_key = str(master_key or DEFAULT_MASTER_KEY)
        self.enable_yi_ji = bool(enable_yi_ji)
        self.enable_lucky = bool(enable_lucky)
        self.enable_dims = bool(enable_dims)
        self.enable_hour = bool(enable_hour)
        self.enable_blessing = bool(enable_blessing)
        self.enable_sign = bool(enable_sign)
        self.enable_lunar = bool(enable_lunar)
        self.sign_count = max(1, int(sign_count))


class FortuneService:
    """Domain service for fortune generation and retrieval.

    Provides pure business logic for fortune operations,
    separate from persistence and infrastructure concerns.
    """

    def __init__(
        self,
        repository: FortuneRepository,
        weights: FortuneWeights | None = None,
        theme: FortuneTheme | None = None,
        options: FortuneOptions | None = None,
        pools_provider: Callable[[], Any] | None = None,
        lunar_provider: Callable[[str], Any] | None = None,
        image_guard: Callable[[bytes], bool] | None = None,
    ) -> None:
        """Initialize fortune service.

        Args:
            repository: Fortune repository for persistence
            weights: Fortune weights (uses default if None)
            theme: Fortune theme (uses default if None)
            options: Detail-generation options (uses defaults if None)
            pools_provider: Returns the local word pools; injected by the
                infrastructure layer so the domain stays I/O free.
            lunar_provider: Maps an ISO date string to lunar info or None.
            image_guard: Validates cached image bytes; injected so the domain
                does not import infrastructure.
        """
        self._repo = repository
        self._weights = weights or FortuneWeights.default()
        self._theme = theme or FortuneTheme.default()
        self._options = options or FortuneOptions()
        self._pools_provider = pools_provider
        self._lunar_provider = lunar_provider
        self._image_guard = image_guard

    async def get_or_create_fortune(
        self, request: FortuneGenerationRequest, force_refresh: bool = False
    ) -> FortuneRecord:
        """Get or create fortune for user.

        Args:
            request: Fortune generation request
            force_refresh: If True, generate new fortune even if one exists

        Returns:
            FortuneRecord

        Raises:
            FortuneException: If fortune generation fails
        """
        # Try to get existing fortune
        existing = await self._repo.get_today_fortune(request)
        if existing and not force_refresh:
            updated = await self._prepare_existing_record(existing, request)
            updated = updated.with_last_view_date(request.date_str)
            # 旧记录里没有 detail（这次升级之前写入的），按确定性种子补齐，
            # 保证同一天同一个人补出来的内容与全新生成完全一致。
            if updated.detail is None:
                updated = updated.with_detail(
                    self._build_detail(updated), updated.nonce
                )
            await self._repo.save_fortune(updated)
            return updated

        # Generate new fortune
        nonce = 0 if not existing else existing.nonce + 1
        record = self._generate(request, nonce)

        await self._repo.save_fortune(record)
        return record

    def _generate(
        self, request: FortuneGenerationRequest, nonce: int = 0
    ) -> FortuneRecord:
        """Build a fresh fortune record deterministically."""
        rng = make_rng(
            request.user_id,
            request.date_str,
            self._options.master_key,
            nonce,
        )
        star_count = self._weights.calculate_star(rng)
        title = self._theme.get_title(star_count)
        description = self._theme.get_message(star_count)
        theme_color = self._theme.get_theme_color(star_count)
        extra_message = self._theme.extra_message

        record = FortuneRecord.create_new(
            user_id=request.user_id,
            username=request.username,
            date_str=request.date_str,
            title=title,
            star_count=star_count,
            description=description,
            extra_message=extra_message,
            theme_color=theme_color,
            group_id=request.group_id,
            nonce=nonce,
        )
        return record.with_detail(self._build_detail(record), nonce)

    def _build_detail(self, record: FortuneRecord) -> FortuneDetail:
        """Derive the local detail block for an existing record."""
        pools = None
        if self._pools_provider is not None:
            try:
                pools = self._pools_provider()
            except Exception:  # a broken data file must not break the card
                pools = None

        lunar_info = None
        if self._options.enable_lunar and self._lunar_provider is not None:
            try:
                lunar_info = self._lunar_provider(record.date_str)
            except Exception:
                lunar_info = None

        return build_detail(
            user_id=record.user_id,
            date_str=record.date_str,
            star_count=record.star_count,
            master_key=self._options.master_key,
            nonce=record.nonce,
            pools=pools,
            lunar_info=lunar_info,
            enable_yi_ji=self._options.enable_yi_ji,
            enable_lucky=self._options.enable_lucky,
            enable_dims=self._options.enable_dims,
            enable_hour=self._options.enable_hour,
            enable_blessing=self._options.enable_blessing,
            enable_sign=self._options.enable_sign,
            enable_lunar=self._options.enable_lunar,
            sign_count=self._options.sign_count,
        )

    async def refresh_fortune(self, request: FortuneGenerationRequest) -> FortuneRecord:
        """Refresh user's fortune (generate new).

        Args:
            request: Fortune generation request

        Returns:
            New FortuneRecord
        """
        # 用递增 nonce 而不是删除后重建：同一天多次刷新会得到不同结果，
        # 同时保留"可复现"性质（nonce 落库，随时能重算出同一张卡）。
        existing = await self._repo.get_today_fortune(request)
        next_nonce = (existing.nonce + 1) if existing else 1

        # Delete cached image
        await self._repo.delete_cached_image(request.user_id, request.date_str)

        record = self._generate(request, next_nonce)
        await self._repo.save_fortune(record)
        return record

    async def refresh_group_fortunes(
        self, group_id: str, date_str: str | None = None
    ) -> int:
        """Refresh all fortunes for a group.

        Args:
            group_id: Group identifier
            date_str: Date string (uses today if None)

        Returns:
            Number of fortunes refreshed
        """
        if date_str is None:
            date_str = date.today().isoformat()

        count = await self._repo.delete_group_fortunes(group_id, date_str)

        # Note: This deletes records but doesn't regenerate them.
        # Regeneration happens when users request their fortune again.

        return count

    async def refresh_all_fortunes(self, date_str: str | None = None) -> int:
        """Refresh all fortunes for a given date.

        Args:
            date_str: Date string (uses today if None)

        Returns:
            Number of fortunes deleted
        """
        if date_str is None:
            date_str = date.today().isoformat()

        return await self._repo.delete_all_fortunes(date_str)

    async def pregenerate_active_users(self, days: int = 3) -> int:
        """Pregenerate fortunes for active users.

        Args:
            days: Number of days to look back for activity

        Returns:
            Number of fortunes pregenerated
        """
        records = await self.pregenerate_active_user_records(
            days=days, include_existing=False
        )
        return len(records)

    async def pregenerate_active_user_records(
        self,
        days: int = 3,
        *,
        include_existing: bool = False,
        access_filter: Callable[[FortuneGenerationRequest], Awaitable[bool]]
        | None = None,
    ) -> list[FortuneRecord]:
        """Ensure today's fortune records exist for recently active users.

        Args:
            days: Number of days to look back for activity
            include_existing: Include existing records in the returned list

        Returns:
            Records that were created, plus existing records when requested
        """
        today_str = date.today().isoformat()
        active_requests = await self._repo.get_active_fortune_requests(
            days, date_str=today_str
        )

        records: list[FortuneRecord] = []
        for request in active_requests:
            if access_filter is not None and not await access_filter(request):
                continue
            existing = await self._repo.get_today_fortune(request)
            if existing:
                existing = await self._prepare_existing_record(existing, request)
                if include_existing:
                    records.append(existing)
                continue

            records.append(await self.get_or_create_fortune(request))

        return records

    async def _prepare_existing_record(
        self, existing: FortuneRecord, request: FortuneGenerationRequest
    ) -> FortuneRecord:
        """Refresh mutable display fields while preserving fortune result."""
        changed = (
            existing.username != request.username
            or existing.group_id != request.group_id
        )
        if not changed:
            return existing

        updated = existing.with_presentation(
            username=request.username,
            group_id=request.group_id,
            invalidate_image_cache=True,
        )
        await self._repo.delete_cached_image(existing.user_id, existing.date_str)
        await self._repo.save_fortune(updated)
        return updated

    async def update_image_cache(
        self, record: FortuneRecord, image_data: bytes, img_url: str | None
    ) -> FortuneRecord:
        """Update image cache for fortune record.

        Args:
            record: Fortune record
            image_data: Image bytes
            img_url: Image URL

        Returns:
            Updated FortuneRecord with image_cached=True
        """
        await self._repo.save_cached_image(
            record.user_id, record.date_str, image_data, img_url
        )
        return record.with_image_cache(img_url)

    async def get_cached_image(self, user_id: str, date_str: str) -> bytes | None:
        """Get cached image data.

        Args:
            user_id: User identifier
            date_str: Date string

        Returns:
            Image bytes if cache exists and is a real image, None otherwise
        """
        cache_path = await self._repo.get_cached_image_path(user_id, date_str)
        if cache_path:
            try:
                import asyncio

                data = await asyncio.to_thread(cache_path.read_bytes)
            except OSError as e:
                logging.debug("Failed to read cached image: %s", e)
                return None
            if self._image_guard is not None and not self._image_guard(data):
                # 旧版本可能把错误页/空响应缓存成了图片，读到就清掉重画。
                logging.warning(
                    "[fortune] dropping invalid cached image: user=%s, date=%s, bytes=%d",
                    user_id,
                    date_str,
                    len(data),
                )
                await self._repo.delete_cached_image(user_id, date_str)
                return None
            return data
        return None

    async def cleanup_cache(self, date_str: str | None = None) -> int:
        """Clean up expired cache files.

        Args:
            date_str: Date string (uses today if None)

        Returns:
            Number of files deleted
        """
        if date_str is None:
            date_str = date.today().isoformat()

        return await self._repo.cleanup_expired_cache(date_str)

    def format_stars(self, star_count: int, max_count: int = 7) -> str:
        """Format star display.

        Args:
            star_count: Number of stars
            max_count: Maximum stars

        Returns:
            Formatted star string (e.g., "★★★★☆☆☆☆")
        """
        filled = "★" * star_count
        empty = "☆" * (max_count - star_count)
        return filled + empty
