"""Image payload validation helpers.

A renderer or provider can hand back something that is not an image at all: an
HTML error page from a proxy, a Cloudflare interstitial, an empty body.  Those
bytes used to be cached and delivered as if they were pictures, which surfaced
as "the command ran but no image appeared".  Every path that produces image
bytes now checks them first.
"""

from __future__ import annotations

# Magic numbers for the formats an adapter can actually display.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
)

# Defence in depth: an error page is always textual.
_TEXTUAL_PREFIXES = (
    b"<!DOCTYPE",
    b"<!doctype",
    b"<html",
    b"<HTML",
    b"<?xml",
    b"{",
    b"[",
)


def detect_image_format(data: bytes | bytearray | None) -> str | None:
    """Return the image format name when ``data`` really is an image."""
    if not data:
        return None
    view = bytes(data[:16])
    for signature, name in _SIGNATURES:
        if view.startswith(signature):
            return name
    # WebP: RIFF....WEBP
    if len(view) >= 12 and view[:4] == b"RIFF" and view[8:12] == b"WEBP":
        return "webp"
    return None


def is_valid_image(data: bytes | bytearray | None, *, min_bytes: int = 128) -> bool:
    """True when ``data`` looks like a real image, not an error page."""
    if not data or len(data) < min_bytes:
        return False
    if detect_image_format(data) is None:
        return False
    head = bytes(data[:64]).lstrip()
    for prefix in _TEXTUAL_PREFIXES:
        if head.startswith(prefix):
            return False
    return True
