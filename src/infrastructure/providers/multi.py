"""多 API 提供商，支持轮询、随机和故障转移策略。"""

from __future__ import annotations

import random

from ...application.ports import SetuImageProvider
from ...domain.setu import ImageMeta
from ...shared import get_logger
from .base import DownloadingSetuImageProvider

logger = get_logger()


class MultiApiProvider(DownloadingSetuImageProvider):
    """多 API 提供商，支持轮询、随机和故障转移策略。"""

    def __init__(
        self, providers: list[SetuImageProvider], strategy: str = "round_robin"
    ):
        """初始化多 API 提供商。

        参数:
            providers: 提供商实例列表。
            strategy: 策略类型（'round_robin'、'random'、'failover'）。
        """
        self.providers = providers
        self.strategy = strategy
        self._current_index = 0
        self._last_working_index = 0

    def _get_next_provider(self) -> SetuImageProvider:
        """根据策略获取下一个提供商。"""
        if self.strategy == "random":
            return random.choice(self.providers)
        elif self.strategy == "failover":
            # 故障转移：从上次成功的开始
            return self.providers[self._last_working_index]
        else:
            # 轮询
            provider = self.providers[self._current_index % len(self.providers)]
            self._current_index += 1
            return provider

    async def _try_provider(
        self,
        index: int,
        num: int,
        tags: list[str],
        r18: bool,
        exclude_ai: bool,
    ) -> list[ImageMeta]:
        """Query one child provider, turning exceptions into an empty result.

        子 provider 的异常一律降级为"这个源没货"，但必须留下可检索的日志：
        原来三个分支都用裸 ``except Exception`` 静默吞掉，签名变更后一旦某个
        子 provider 返回了错误类型，症状会变成"永远是空结果"，没有任何线索。
        """
        provider = self.providers[index]
        name = provider.__class__.__name__
        try:
            metas = await provider.fetch_image_urls(num, tags, r18, exclude_ai)
        except Exception as exc:
            logger.warning(
                "[provider] multi child failed: index=%d, provider=%s, error=%r",
                index,
                name,
                exc,
            )
            return []
        if metas and not isinstance(metas[0], ImageMeta):
            logger.error(
                "[provider] multi child returned unexpected type: index=%d, "
                "provider=%s, type=%s; expected ImageMeta",
                index,
                name,
                type(metas[0]).__name__,
            )
            return []
        return metas

    async def fetch_image_urls(
        self,
        num: int,
        tags: list[str],
        r18: bool,
        exclude_ai: bool = True,
    ) -> list[ImageMeta]:
        """从多个 API 获取图片及元数据。"""
        if not self.providers:
            logger.warning("[provider] multi has no child providers")
            return []

        if self.strategy == "failover":
            # 故障转移模式：逐个尝试直到成功
            for i in range(len(self.providers)):
                idx = (self._last_working_index + i) % len(self.providers)
                metas = await self._try_provider(idx, num, tags, r18, exclude_ai)
                if metas:
                    self._last_working_index = idx
                    return metas
            return []
        elif self.strategy == "random":
            # 随机模式：逐个随机选择，失败时继续尝试其他
            tried_indices = set()
            while len(tried_indices) < len(self.providers):
                idx = random.randrange(len(self.providers))
                if idx in tried_indices:
                    continue
                tried_indices.add(idx)
                metas = await self._try_provider(idx, num, tags, r18, exclude_ai)
                if metas:
                    return metas
            return []
        else:
            # 轮询模式：逐个尝试，失败时继续尝试其他
            for i in range(len(self.providers)):
                idx = (self._current_index + i) % len(self.providers)
                metas = await self._try_provider(idx, num, tags, r18, exclude_ai)
                self._current_index = (idx + 1) % len(self.providers)
                if metas:
                    return metas
            return []
