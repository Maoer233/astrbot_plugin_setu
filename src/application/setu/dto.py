"""Setu application DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...domain.setu.meta import ImageMeta


@dataclass(frozen=True)
class ImagePayload:
    """Fetched image data ready for adapter-level delivery.

    ``metas`` is appended after ``items`` on purpose: existing call sites pass
    the leading fields positionally, so a new field must never be inserted in
    the middle of the dataclass.
    """

    urls: tuple[str, ...]
    raw_bytes: tuple[bytes, ...]
    r18: bool
    tags: tuple[str, ...]
    file_paths: tuple[Path, ...] = ()
    items: tuple[Path | bytes, ...] = ()
    metas: tuple[ImageMeta, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Check if payload contains no data."""
        return (
            not self.urls
            and not self.raw_bytes
            and not self.file_paths
            and not self.items
        )

    @property
    def count(self) -> int:
        """Return number of images in payload."""
        if self.items:
            return len(self.items)
        return max(len(self.urls), len(self.raw_bytes), len(self.file_paths))

    def meta_for_url(self, url: str) -> ImageMeta | None:
        """Return the metadata entry matching ``url``, when one exists."""
        if not url:
            return None
        for meta in self.metas:
            if meta.url == url:
                return meta
        return None

    def first_meta(self) -> ImageMeta | None:
        """Return the first metadata entry, for single-image captions."""
        return self.metas[0] if self.metas else None


@dataclass(frozen=True)
class SetuImagesResult:
    """Application result for a Setu image request."""

    payload: ImagePayload | None
    notice: str | None = None
