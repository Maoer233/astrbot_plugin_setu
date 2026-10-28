"""Infrastructure base class for provider URL fetch and image download."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import httpx

from ...application.ports import SetuImageProvider
from ...application.setu.dto import ImagePayload
from ...domain import HTTP_TIMEOUT_SECONDS
from ...domain.setu import ImageMeta, SetuRequest
from ...shared import get_logger
from ...shared.send_cache import get_send_cache

logger = get_logger()


@dataclass(frozen=True)
class DownloadAttemptResult:
    """单个图片的下载结果，避免用 None 同时表示失败和未尝试。"""

    meta: ImageMeta
    item: Path | bytes | None

    @property
    def url(self) -> str:
        """URL of the attempted image."""
        return self.meta.url

    @property
    def succeeded(self) -> bool:
        """下载是否已落地为可发送数据。"""
        return self.item is not None


def select_replenish_metas(
    metas: list[ImageMeta],
    missing: int,
    *,
    reported_urls: set[str],
    completed_urls: set[str],
    download_attempts: dict[str, int],
    max_rounds: int,
) -> tuple[list[ImageMeta], int]:
    """从一次 provider 响应中选择不超过当前缺口数量的候选条目。

    去重与重试计数一律以 ``meta.url`` 字符串为键：``ImageMeta`` 虽然只按 URL
    参与哈希，但把元数据对象混进集合会让"同一 URL 不同元数据"这种脏数据路径
    变得难以推理，统一用 URL 字符串最稳。
    """
    selected: list[ImageMeta] = []
    retry_candidates: list[ImageMeta] = []
    round_seen: set[str] = set()
    exhausted_urls = 0

    for meta in metas:
        url = meta.url
        if not url or url in completed_urls or url in round_seen:
            continue
        round_seen.add(url)
        if download_attempts.get(url, 0) >= max_rounds:
            exhausted_urls += 1
            continue
        if url in reported_urls:
            retry_candidates.append(meta)
            continue
        if len(selected) < missing:
            selected.append(meta)

    for meta in retry_candidates:
        if len(selected) >= missing:
            break
        selected.append(meta)

    return selected, exhausted_urls


class DownloadingSetuImageProvider(SetuImageProvider):
    """Provider base that materializes image URLs into sendable local data."""

    async def fetch_and_download(self, request: SetuRequest) -> ImagePayload:
        """Fetch URLs, download images, and replenish short batches when needed."""
        provider_name = self._provider_name()
        logger.info(
            "[provider] fetch start: provider=%s, count=%d, r18=%s, tags=%s, exclude_ai=%s, replenish_rounds=%d",
            provider_name,
            request.count,
            request.r18,
            ",".join(request.tags) or "-",
            request.exclude_ai,
            request.max_replenish_rounds,
        )

        if request.count <= 0:
            return ImagePayload(
                urls=(), raw_bytes=(), file_paths=(), r18=request.r18, tags=request.tags
            )

        all_urls: list[str] = []
        reported_urls: set[str] = set()
        completed_urls: set[str] = set()
        download_attempts: dict[str, int] = {}
        items: list[Path | bytes] = []
        metas: list[ImageMeta] = []
        max_rounds = max(1, request.max_replenish_rounds)

        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            for round_index in range(1, max_rounds + 1):
                missing = request.count - len(items)
                if missing <= 0:
                    break

                fetched = await self._fetch_replenish_metas(
                    request, missing, round_index
                )
                fresh_metas, exhausted_urls = select_replenish_metas(
                    fetched,
                    missing,
                    reported_urls=reported_urls,
                    completed_urls=completed_urls,
                    download_attempts=download_attempts,
                    max_rounds=max_rounds,
                )
                for meta in fresh_metas:
                    if meta.url not in reported_urls:
                        reported_urls.add(meta.url)
                        all_urls.append(meta.url)

                if not fresh_metas:
                    logger.warning(
                        "[provider] replenish round returned no fresh urls: provider=%s, round=%d, missing=%d, tags=%s",
                        provider_name,
                        round_index,
                        missing,
                        ",".join(request.tags) or "-",
                    )
                    continue
                if exhausted_urls:
                    logger.warning(
                        "[provider] skipped exhausted download urls: provider=%s, round=%d, skipped=%d, max_attempts=%d",
                        provider_name,
                        round_index,
                        exhausted_urls,
                        max_rounds,
                    )

                round_downloads = await self._download_metas(
                    client,
                    fresh_metas,
                    download_attempts,
                    max_attempts=max_rounds,
                )
                successful_downloads = [
                    result for result in round_downloads if result.succeeded
                ]
                completed_urls.update(result.url for result in successful_downloads)
                for result in successful_downloads:
                    if result.item is not None:
                        items.append(result.item)
                        metas.append(result.meta)

                failed = len(round_downloads) - len(successful_downloads)
                logger.info(
                    "[provider] replenish round summary: provider=%s, round=%d/%d, requested=%d, urls=%d, succeeded=%d, failed=%d, total=%d/%d",
                    provider_name,
                    round_index,
                    max_rounds,
                    missing,
                    len(fresh_metas),
                    len(successful_downloads),
                    failed,
                    len(items),
                    request.count,
                )

        raw_bytes = tuple(item for item in items if isinstance(item, bytes))
        file_paths = tuple(item for item in items if isinstance(item, Path))
        logger.info(
            "[provider] download summary: provider=%s, urls=%d, succeeded=%d, failed_or_missing=%d, bytes=%d, files=%d, with_tags=%d",
            provider_name,
            len(all_urls),
            len(items),
            max(0, request.count - len(items)),
            len(raw_bytes),
            len(file_paths),
            sum(1 for meta in metas if meta.tags),
        )
        if not items:
            logger.error(
                "[provider] all downloads failed: provider=%s, requested=%d, tags=%s",
                provider_name,
                request.count,
                ",".join(request.tags) or "-",
            )
        return ImagePayload(
            urls=tuple(all_urls),
            raw_bytes=raw_bytes,
            file_paths=file_paths,
            items=tuple(items),
            r18=request.r18,
            tags=request.tags,
            metas=tuple(metas),
        )

    async def _fetch_replenish_metas(
        self, request: SetuRequest, count: int, round_index: int
    ) -> list[ImageMeta]:
        provider_name = self._provider_name()
        metas = await self.fetch_image_urls(
            num=count,
            tags=list(request.tags),
            r18=request.r18,
            exclude_ai=request.exclude_ai,
        )
        if not metas:
            logger.warning(
                "[provider] no urls returned: provider=%s, round=%d, count=%d, r18=%s, tags=%s",
                provider_name,
                round_index,
                count,
                request.r18,
                ",".join(request.tags) or "-",
            )
        else:
            logger.info(
                "[provider] fetch result: provider=%s, round=%d, urls=%d, requested=%d, with_tags=%d",
                provider_name,
                round_index,
                len(metas),
                count,
                sum(1 for meta in metas if meta.tags),
            )
        return metas

    async def _download_metas(
        self,
        client: httpx.AsyncClient,
        metas: list[ImageMeta],
        download_attempts: dict[str, int] | None = None,
        max_attempts: int = 1,
    ) -> list[DownloadAttemptResult]:
        attempts = download_attempts if download_attempts is not None else {}
        items = await asyncio.gather(
            *(
                self._download_one(client, meta.url, attempts, max_attempts)
                for meta in metas
            )
        )
        # 保留失败结果给上层统计；只由上层把成功 URL 记为 completed，
        # 避免 CDN/反代短暂不可用时被 seen 去重永久跳过。
        return [
            DownloadAttemptResult(meta=meta, item=item)
            for meta, item in zip(metas, items, strict=False)
        ]

    async def _download_one(
        self,
        client: httpx.AsyncClient,
        url: str,
        download_attempts: dict[str, int] | None = None,
        max_attempts: int = 1,
    ) -> Path | bytes | None:
        attempts = download_attempts if download_attempts is not None else {}
        max_allowed = max(1, max_attempts)
        while attempts.get(url, 0) < max_allowed:
            attempts[url] = attempts.get(url, 0) + 1
            attempt = attempts[url]
            item = await self._download_one_attempt(client, url, attempt, max_allowed)
            if item is not None:
                return item
        return None

    async def _download_one_attempt(
        self,
        client: httpx.AsyncClient,
        url: str,
        attempt: int,
        max_attempts: int,
    ) -> Path | bytes | None:
        provider_name = self._provider_name()
        cache = get_send_cache()
        cache_enabled = bool(cache and cache.enabled)
        try:
            if cache_enabled and cache:
                cached = await cache.get(url)
                if cached is not None:
                    logger.debug(
                        "[provider] cache hit: provider=%s, url=%s",
                        provider_name,
                        url,
                    )
                    return cached

            if cache_enabled and cache is not None:
                write = None
                try:
                    async with client.stream("GET", url) as response:
                        response.raise_for_status()
                        write = await cache.reserve(
                            url, response.headers.get("content-type")
                        )
                        with write.temp_path.open("wb") as file:
                            async for chunk in response.aiter_bytes():
                                if chunk:
                                    await asyncio.to_thread(file.write, chunk)
                    final_path = await cache.commit(write)
                    logger.debug(
                        "[provider] download cached: provider=%s, url=%s, path=%s",
                        provider_name,
                        url,
                        final_path,
                    )
                    return final_path
                except Exception:
                    if write is not None:
                        await cache.discard(write)
                    raise

            if cache is not None:
                chunks: list[bytes] = []
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            chunks.append(chunk)
                content = b"".join(chunks)
                logger.debug(
                    "[provider] download bytes: provider=%s, url=%s, bytes=%d",
                    provider_name,
                    url,
                    len(content),
                )
                return content

            response = await client.get(url)
            response.raise_for_status()
            logger.debug(
                "[provider] download bytes: provider=%s, url=%s, bytes=%d",
                provider_name,
                url,
                len(response.content),
            )
            return response.content
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            if attempt < max_attempts:
                logger.warning(
                    "[provider] transient download failed, will retry: provider=%s, url=%s, attempt=%d/%d, error=%s",
                    provider_name,
                    url,
                    attempt,
                    max_attempts,
                    exc,
                )
            else:
                logger.warning(
                    "[provider] download failed: provider=%s, url=%s, attempts=%d, error=%s",
                    provider_name,
                    url,
                    max_attempts,
                    exc,
                )
            return None
