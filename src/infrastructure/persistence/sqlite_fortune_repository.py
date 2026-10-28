"""SQLite implementation for fortune data persistence."""

from __future__ import annotations

import asyncio
import datetime
import json
import re
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ...application.ports import FortuneRepository
from ...domain.fortune.detail_dto import FortuneDetail
from ...domain.fortune.entities import FortuneGenerationRequest, FortuneRecord
from ..fortune.image_guard import is_valid_image

# 缓存文件名里的模板版本号。改动卡片布局/字段时递增，使当日已渲染的旧图失效。
CACHE_TAG = "v3"


def _cache_date_of(file_stem: str) -> str:
    """Extract the ISO date from a cache file stem.

    Stems look like ``{user_id}_{date}_{tag}`` or the legacy
    ``{user_id}_{date}``.  The date is the middle segment, so it is matched by
    pattern rather than by blindly splitting on the last underscore.
    """
    match = re.search(r"(\d{4}-\d{2}-\d{2})", file_stem)
    return match.group(1) if match else ""


class SQLiteFortuneRepo(FortuneRepository):
    """SQLite-backed fortune repository implementation."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._db_path = data_dir / "fortune.db"
        self._cache_dir = data_dir / "cache"
        self._db_lock = asyncio.Lock()
        self._cache_lock = asyncio.Lock()
        self._db_inited = False
        self._last_cleanup_date: str | None = None

    async def initialize(self) -> None:
        """Initialize database and cache directory."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        await self._init_db()
        today = datetime.date.today().isoformat()
        await self.cleanup_expired_cache(today)
        self._db_inited = True
        logger.info("[fortune] Database initialized at %s", self._db_path)

    async def _init_db(self) -> None:
        """Initialize database tables."""
        import aiosqlite

        async with self._db_lock:
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fortune_data (
                        user_id   TEXT NOT NULL,
                        username  TEXT,
                        date_str  TEXT NOT NULL,
                        title     TEXT NOT NULL,
                        stars     INTEGER NOT NULL,
                        desc_text TEXT NOT NULL,
                        extra     TEXT NOT NULL,
                        theme     TEXT NOT NULL,
                        image_cached INTEGER DEFAULT 0,
                        img_url   TEXT,
                        last_view_date TEXT,
                        group_id  TEXT,
                        PRIMARY KEY (user_id, date_str)
                    )
                    """
                )
                await db.commit()
                await self._migrate_db(db)

    async def _migrate_db(self, db: Any) -> None:
        """Database migration: add missing columns."""
        cursor = await db.execute("PRAGMA table_info(fortune_data)")
        columns = await cursor.fetchall()
        column_names = [col[1] for col in columns]

        if "img_url" not in column_names:
            await db.execute("ALTER TABLE fortune_data ADD COLUMN img_url TEXT")
            await db.commit()

        if "username" not in column_names:
            await db.execute("ALTER TABLE fortune_data ADD COLUMN username TEXT")
            await db.commit()

        if "last_view_date" not in column_names:
            await db.execute("ALTER TABLE fortune_data ADD COLUMN last_view_date TEXT")
            await db.commit()

        if "group_id" not in column_names:
            await db.execute("ALTER TABLE fortune_data ADD COLUMN group_id TEXT")
            await db.commit()
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_fortune_group ON fortune_data(group_id, date_str)"
            )
            await db.commit()

        # 扩展字段整块存进 payload 列，避免为每个新字段各加一列；
        # nonce 决定同一天刷新几次的内容，必须持久化才能复现同一张卡。
        if "payload" not in column_names:
            await db.execute("ALTER TABLE fortune_data ADD COLUMN payload TEXT")
            await db.commit()

        if "nonce" not in column_names:
            await db.execute(
                "ALTER TABLE fortune_data ADD COLUMN nonce INTEGER DEFAULT 0"
            )
            await db.commit()

    @staticmethod
    def _encode_detail(detail: Any) -> str | None:
        """Serialize a FortuneDetail into a compact JSON string."""
        if detail is None:
            return None
        try:
            payload = {
                "yi": list(detail.yi),
                "ji": list(detail.ji),
                "lucky_color": detail.lucky_color,
                "lucky_color_hex": detail.lucky_color_hex,
                "lucky_number": detail.lucky_number,
                "lucky_direction": detail.lucky_direction,
                "lucky_item": detail.lucky_item,
                "dims": [list(item) for item in detail.dims],
                "auspicious_hour": detail.auspicious_hour,
                "auspicious_hour_window": detail.auspicious_hour_window,
                "blessing": detail.blessing,
                "sign_title": detail.sign_title,
                "sign_type": detail.sign_type,
                "sign_grade": detail.sign_grade,
                "sign_poetry": detail.sign_poetry,
                "sign_meaning": detail.sign_meaning,
                "sign_explanation": detail.sign_explanation,
                "lunar_date": detail.lunar_date,
                "ganzhi": detail.ganzhi,
                "zodiac": detail.zodiac,
                "solar_term": detail.solar_term,
            }
            return json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            logger.warning("[fortune] failed to encode detail: %s", exc)
            return None

    @staticmethod
    def _decode_detail(raw: Any) -> FortuneDetail | None:
        """Rebuild a FortuneDetail from stored JSON, tolerating old rows."""
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None

        dims: list[tuple[str, str, int]] = []
        for item in data.get("dims") or []:
            if isinstance(item, list) and len(item) == 3:
                try:
                    dims.append((str(item[0]), str(item[1]), int(item[2])))
                except (TypeError, ValueError):
                    continue

        return FortuneDetail(
            yi=tuple(str(x) for x in (data.get("yi") or [])),
            ji=tuple(str(x) for x in (data.get("ji") or [])),
            lucky_color=str(data.get("lucky_color") or ""),
            lucky_color_hex=str(data.get("lucky_color_hex") or ""),
            lucky_number=(
                data.get("lucky_number")
                if isinstance(data.get("lucky_number"), int)
                else None
            ),
            lucky_direction=str(data.get("lucky_direction") or ""),
            lucky_item=str(data.get("lucky_item") or ""),
            dims=tuple(dims),
            auspicious_hour=str(data.get("auspicious_hour") or ""),
            auspicious_hour_window=str(data.get("auspicious_hour_window") or ""),
            blessing=str(data.get("blessing") or ""),
            sign_title=str(data.get("sign_title") or ""),
            sign_type=str(data.get("sign_type") or ""),
            sign_grade=str(data.get("sign_grade") or ""),
            sign_poetry=str(data.get("sign_poetry") or ""),
            sign_meaning=str(data.get("sign_meaning") or ""),
            sign_explanation=str(data.get("sign_explanation") or ""),
            lunar_date=str(data.get("lunar_date") or ""),
            ganzhi=str(data.get("ganzhi") or ""),
            zodiac=str(data.get("zodiac") or ""),
            solar_term=str(data.get("solar_term") or ""),
        )

    def _row_to_record(
        self, row: tuple, user_id: str, username: str, date_str: str
    ) -> FortuneRecord:
        """Convert a database row to FortuneRecord.

        Column order must stay in sync with the SELECT in
        :meth:`get_today_fortune`; the new ``payload``/``nonce`` columns are
        appended at the end so every existing index keeps its meaning.
        """
        stored_username = row[9] if len(row) > 9 else None
        payload_raw = row[10] if len(row) > 10 else None
        nonce = row[11] if len(row) > 11 else 0
        return FortuneRecord(
            user_id=user_id,
            username=username or stored_username or user_id,
            date_str=date_str,
            title=row[0],
            star_count=row[1],
            description=row[2],
            extra_message=row[3],
            theme_color=row[4],
            image_cached=bool(row[5]),
            img_url=row[6],
            last_view_date=row[7] if len(row) > 7 else date_str,
            group_id=row[8] if len(row) > 8 else None,
            nonce=int(nonce or 0),
            detail=self._decode_detail(payload_raw),
        )

    async def get_today_fortune(
        self, request: FortuneGenerationRequest
    ) -> FortuneRecord | None:
        """Get fortune for today."""
        import aiosqlite

        async with self._db_lock:
            async with aiosqlite.connect(str(self._db_path)) as db:
                cursor = await db.execute(
                    "SELECT title, stars, desc_text, extra, theme, image_cached, img_url, last_view_date, group_id, username, payload, nonce "
                    "FROM fortune_data WHERE user_id = ? AND date_str = ?",
                    (request.user_id, request.date_str),
                )
                row = await cursor.fetchone()
                if row:
                    return self._row_to_record(
                        row, request.user_id, request.username, request.date_str
                    )
        return None

    async def save_fortune(self, record: FortuneRecord) -> bool:
        """Save or update fortune record."""
        import aiosqlite

        try:
            async with self._db_lock:
                async with aiosqlite.connect(str(self._db_path)) as db:
                    await db.execute(
                        "INSERT OR REPLACE INTO fortune_data "
                        "(user_id, username, date_str, title, stars, desc_text, extra, theme, image_cached, img_url, last_view_date, group_id, payload, nonce) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            record.user_id,
                            record.username,
                            record.date_str,
                            record.title,
                            record.star_count,
                            record.description,
                            record.extra_message,
                            record.theme_color,
                            int(record.image_cached),
                            record.img_url,
                            record.last_view_date,
                            record.group_id,
                            self._encode_detail(record.detail),
                            int(record.nonce),
                        ),
                    )
                    await db.commit()
            return True
        except Exception as exc:
            logger.warning("[fortune] Failed to save fortune: %s", exc)
            return False

    async def delete_fortune(self, user_id: str, date_str: str) -> bool:
        """Delete fortune record."""
        import aiosqlite

        try:
            async with self._db_lock:
                async with aiosqlite.connect(str(self._db_path)) as db:
                    await db.execute(
                        "DELETE FROM fortune_data WHERE user_id = ? AND date_str = ?",
                        (user_id, date_str),
                    )
                    await db.commit()
            return True
        except Exception as exc:
            logger.warning("[fortune] Failed to delete fortune: %s", exc)
            return False

    async def delete_group_fortunes(self, group_id: str, date_str: str) -> int:
        """Delete all fortune records for a group on a given date."""
        import aiosqlite

        async with self._db_lock:
            async with aiosqlite.connect(str(self._db_path)) as db:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM fortune_data WHERE date_str = ? AND (group_id = ? OR group_id IS NULL)",
                    (date_str, group_id),
                )
                row = await cursor.fetchone()
                count = row[0] if row else 0

                await db.execute(
                    "DELETE FROM fortune_data WHERE date_str = ? AND (group_id = ? OR group_id IS NULL)",
                    (date_str, group_id),
                )
                await db.commit()

        return count

    async def delete_all_fortunes(self, date_str: str) -> int:
        """Delete all fortune records for a given date."""
        import aiosqlite

        async with self._db_lock:
            async with aiosqlite.connect(str(self._db_path)) as db:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM fortune_data WHERE date_str = ?",
                    (date_str,),
                )
                row = await cursor.fetchone()
                count = row[0] if row else 0

                await db.execute(
                    "DELETE FROM fortune_data WHERE date_str = ?",
                    (date_str,),
                )
                await db.commit()

        return count

    async def get_active_users(self, days: int = 3) -> list[str]:
        """Get list of active users (viewed fortune within N days)."""
        import aiosqlite

        cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
        async with self._db_lock:
            async with aiosqlite.connect(str(self._db_path)) as db:
                cursor = await db.execute(
                    "SELECT DISTINCT user_id FROM fortune_data WHERE COALESCE(last_view_date, date_str) >= ?",
                    (cutoff,),
                )
                rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def get_active_fortune_requests(
        self, days: int = 3, date_str: str | None = None
    ) -> list[FortuneGenerationRequest]:
        """Get latest generation request data for recently active users."""
        import aiosqlite

        target_date = date_str or datetime.date.today().isoformat()
        cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
        async with self._db_lock:
            async with aiosqlite.connect(str(self._db_path)) as db:
                cursor = await db.execute(
                    """
                    SELECT user_id,
                           COALESCE(NULLIF(username, ''), user_id) AS username,
                           group_id
                    FROM fortune_data
                    WHERE COALESCE(last_view_date, date_str) >= ?
                    ORDER BY user_id,
                             COALESCE(last_view_date, date_str) DESC,
                             date_str DESC,
                             rowid DESC
                    """,
                    (cutoff,),
                )
                rows = await cursor.fetchall()

        requests: list[FortuneGenerationRequest] = []
        seen: set[str] = set()
        for user_id, username, group_id in rows:
            if user_id in seen:
                continue
            seen.add(user_id)
            requests.append(
                FortuneGenerationRequest(
                    user_id=str(user_id),
                    username=str(username or user_id),
                    date_str=target_date,
                    group_id=str(group_id) if group_id else None,
                )
            )
        return requests

    async def get_cached_image_path(self, user_id: str, date_str: str) -> Any | None:
        """Get cached image path for fortune.

        The file name carries a template version so that upgrading the card
        layout invalidates today's already-rendered images instead of serving a
        stale picture until midnight.
        """
        cache_path = self._cache_dir / f"{user_id}_{date_str}_{CACHE_TAG}.jpg"
        if cache_path.exists():
            return cache_path
        return None

    async def save_cached_image(
        self, user_id: str, date_str: str, image_data: bytes, img_url: str | None
    ) -> Any:
        """Save image to cache.

        Bytes that are not a real image are rejected instead of being written:
        a cached error page used to be delivered as a picture, which showed up
        as "the command ran but no image appeared".
        """
        if not is_valid_image(image_data):
            logger.warning(
                "[fortune] refuse to cache non-image payload: user=%s, bytes=%d",
                user_id,
                len(image_data or b""),
            )
            return None

        cache_path = self._cache_dir / f"{user_id}_{date_str}_{CACHE_TAG}.jpg"

        async with self._cache_lock:
            await asyncio.to_thread(cache_path.write_bytes, image_data)

        import aiosqlite

        async with self._db_lock:
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.execute(
                    "UPDATE fortune_data SET image_cached = 1, img_url = ? "
                    "WHERE user_id = ? AND date_str = ?",
                    (img_url, user_id, date_str),
                )
                await db.commit()

        return cache_path

    async def delete_cached_image(self, user_id: str, date_str: str) -> bool:
        """Delete cached image, including files from older template versions."""
        removed = False
        try:
            async with self._cache_lock:
                for candidate in self._cache_dir.glob(f"{user_id}_{date_str}*.jpg"):
                    try:
                        candidate.unlink()
                        removed = True
                    except OSError:
                        continue
        except OSError:
            pass
        return removed

    async def cleanup_expired_cache(self, date_str: str) -> int:
        """Clean up cache files from before given date."""
        if self._last_cleanup_date == date_str:
            return 0

        removed = 0
        removed_size = 0

        try:
            async with self._cache_lock:
                for file_path in self._cache_dir.iterdir():
                    if not file_path.is_file():
                        continue
                    try:
                        file_name = file_path.stem
                        file_date = _cache_date_of(file_name)
                        if file_date and file_date == date_str:
                            continue
                        file_size = file_path.stat().st_size
                        file_path.unlink()
                        removed += 1
                        removed_size += file_size
                    except (OSError, ValueError):
                        try:
                            file_size = file_path.stat().st_size
                            file_path.unlink()
                            removed += 1
                            removed_size += file_size
                        except OSError:
                            pass

            if removed > 0:
                logger.info(
                    "[fortune] Cleaned up %d expired cache files (%.2f MB)",
                    removed,
                    removed_size / 1024 / 1024,
                )

            self._last_cleanup_date = date_str

        except OSError as exc:
            logger.warning("[fortune] Failed to cleanup cache: %s", exc)

        return removed
