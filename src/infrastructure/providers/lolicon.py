"""Lolicon API V2 提供商。

文档: https://api.lolicon.app
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from urllib.parse import quote

import httpx

from ...domain import HTTP_TIMEOUT_SECONDS
from ...domain.setu import ImageMeta, build_meta
from ...shared import get_logger
from .base import DownloadingSetuImageProvider

logger = get_logger()


class LoliconProvider(DownloadingSetuImageProvider):
    """Lolicon API V2 提供商。

    文档: https://api.lolicon.app
    """

    API_URL = "https://api.lolicon.app/setu/v2"

    def __init__(
        self,
        image_size: str = "original",
        proxy: str = "",
        aspect_ratio: str = "",
        uid: list[int] | None = None,
        keyword: str = "",
    ):
        """初始化 Lolicon 提供商。

        参数:
            image_size: 图片规格 (original, regular, small, thumb, mini)
            proxy: 反代服务
            aspect_ratio: 图片长宽比
            uid: 指定作者 UID 列表
            keyword: 关键词搜索
        """
        self.image_size = image_size
        self.proxy = proxy
        self.aspect_ratio = aspect_ratio
        self.uid = uid or []
        self.keyword = keyword

    async def fetch_image_urls(
        self,
        num: int,
        tags: list[str],
        r18: bool,
        exclude_ai: bool = True,
    ) -> list[ImageMeta]:
        logger.info(
            "[provider] Lolicon request: count=%d, r18=%s, tags=%s, size=%s, proxy=%s, exclude_ai=%s",
            num,
            r18,
            ",".join(tags) or "-",
            self.image_size,
            self.proxy or "-",
            exclude_ai,
        )
        exclude_ai_flag = self._normalize_bool(exclude_ai, default=True)
        query_params: list[tuple[str, str | int]] = [
            ("r18", 1 if r18 else 0),
            ("num", num),
            ("excludeAI", str(exclude_ai_flag).lower()),
            ("size", self.image_size),
        ]

        # 添加可选参数
        if self.proxy:
            query_params.append(("proxy", self.proxy))
        if self.aspect_ratio:
            query_params.append(("aspectRatio", self.aspect_ratio))
        if self.keyword:
            query_params.append(("keyword", self.keyword))

        # Handle uid as repeated parameters
        for uid in self.uid:
            if uid is not None:
                query_params.append(("uid", uid))

        # 构建 URL — handle repeated tag params separately
        tag_params = "&".join(f"tag={quote(t, safe='')}" for t in tags) if tags else ""
        base_params = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in query_params)
        url = f"{self.API_URL}?{base_params}"
        if tag_params:
            url += f"&{tag_params}"

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.warning("Lolicon API 响应错误: %s %s", e, url)
            return []
        except httpx.HTTPError as e:
            logger.warning("Lolicon API 请求失败: %s %s", e, url)
            return []
        except asyncio.TimeoutError:
            logger.warning("Lolicon API 请求超时: %s", url)
            return []
        except Exception as e:
            logger.exception("Lolicon API 异常: %s", e)
            return []

        urls: list[str] = []
        metas: list[ImageMeta] = []
        for item in data.get("data", []):
            # 根据 size 参数返回对应的 URL
            urls_obj = item.get("urls", {})
            img_url = urls_obj.get(self.image_size)
            if img_url:
                urls.append(img_url)
                metas.append(self._build_item_meta(item, img_url))
        urls = self._apply_proxy_to_urls(urls, self.proxy, "LoliconProvider")
        # proxy 重写改变了 URL，元数据必须同步，否则 metas 与 payload.urls 对不上。
        metas = [
            replace(meta, url=new_url)
            for meta, new_url in zip(metas, urls, strict=False)
        ]
        logger.info(
            "[provider] Lolicon response: requested=%d, returned=%d, with_tags=%d",
            num,
            len(metas),
            sum(1 for meta in metas if meta.tags),
        )
        return metas

    def _build_item_meta(self, item: dict, img_url: str) -> ImageMeta:
        """Convert one Lolicon result object into :class:`ImageMeta`."""
        ai_type = item.get("aiType")
        is_ai = ai_type == 2 if isinstance(ai_type, int) else None
        return build_meta(
            img_url,
            title=item.get("title", ""),
            author=item.get("author", ""),
            uid=item.get("uid"),
            pid=item.get("pid"),
            pic_id=item.get("p"),
            tags=item.get("tags", ()),
            width=item.get("width", 0),
            height=item.get("height", 0),
            is_ai=is_ai,
            provider="lolicon",
        )
