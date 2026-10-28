"""Tests for image payload validation."""

from __future__ import annotations

import io

from PIL import Image

from astrbot_plugin_setu.src.infrastructure.fortune.image_guard import (
    detect_image_format,
    is_valid_image,
)

CLOUDFLARE_PAGE = (
    b"<!DOCTYPE html>\n<!--[if lt IE 7]> <html class=\"no-js ie6 oldie\"> <![endif]-->\n"
    b"<html><head><title>Just a moment...</title></head><body>Checking your browser"
    + b"</body></html>" * 20
)


def _png(size=(120, 120)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 120, 60)).save(buf, format="PNG")
    return buf.getvalue()


def test_real_png_is_accepted() -> None:
    data = _png()

    assert is_valid_image(data) is True
    assert detect_image_format(data) == "png"


def test_cloudflare_error_page_is_rejected() -> None:
    """这正是线上实际发生的情况：错误页被当成图片缓存并发给了用户。"""
    assert detect_image_format(CLOUDFLARE_PAGE) is None
    assert is_valid_image(CLOUDFLARE_PAGE) is False


def test_json_and_xml_are_rejected() -> None:
    assert is_valid_image(b'{"code": 500, "msg": "render failed"}' * 10) is False
    assert is_valid_image(b"<?xml version='1.0'?><error>bad</error>" * 10) is False


def test_empty_and_tiny_payloads_are_rejected() -> None:
    assert is_valid_image(b"") is False
    assert is_valid_image(None) is False
    assert is_valid_image(b"\x89PNG") is False  # 太短


def test_webp_and_jpeg_are_detected() -> None:
    webp = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 200
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 200

    assert detect_image_format(webp) == "webp"
    assert detect_image_format(jpeg) == "jpeg"
    assert is_valid_image(webp) is True
    assert is_valid_image(jpeg) is True


def test_cache_rejects_non_image_payload(tmp_path) -> None:
    """仓储层必须拒绝写入坏图，避免坏数据再次污染缓存。"""
    import asyncio

    from astrbot_plugin_setu.src.infrastructure.persistence.sqlite_fortune_repository import (
        SQLiteFortuneRepo,
    )

    repo = SQLiteFortuneRepo(tmp_path)

    async def scenario() -> None:
        await repo.initialize()
        written = await repo.save_cached_image(
            "user-1", "2026-10-27", CLOUDFLARE_PAGE, None
        )
        assert written is None
        assert await repo.get_cached_image_path("user-1", "2026-10-27") is None

        ok = await repo.save_cached_image("user-1", "2026-10-27", _png(), None)
        assert ok is not None
        assert await repo.get_cached_image_path("user-1", "2026-10-27") is not None

    asyncio.run(scenario())


def test_cached_invalid_image_is_dropped_on_read(tmp_path) -> None:
    """读到历史坏缓存时要清掉并返回 None，触发重新渲染。"""
    import asyncio

    from astrbot_plugin_setu.src.domain.fortune.service import FortuneService
    from astrbot_plugin_setu.src.infrastructure.persistence.sqlite_fortune_repository import (
        SQLiteFortuneRepo,
    )

    repo = SQLiteFortuneRepo(tmp_path)

    async def scenario() -> None:
        await repo.initialize()
        # 绕过仓储校验，直接把坏字节写进缓存文件（模拟旧版本留下的脏数据）
        bad_path = repo._cache_dir / "user-1_2026-10-27_v3.jpg"
        bad_path.write_bytes(CLOUDFLARE_PAGE)

        service = FortuneService(repository=repo, image_guard=is_valid_image)
        got = await service.get_cached_image("user-1", "2026-10-27")

        assert got is None
        assert not bad_path.exists(), "坏缓存应被删除"

    asyncio.run(scenario())
