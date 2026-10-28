"""Font resolution for local Pillow rendering.

Follows the same cascade the bundled Steam plugin uses: plugin-bundled font →
system CJK font → PIL default.  Everything is local; no network and no remote
renderer is involved.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import ImageFont

from ...shared import get_logger

logger = get_logger()

_FONTS_DIR = Path(__file__).resolve().parents[3] / "templates" / "res" / "fonts"

# (logical name, bundled file name)
_BUNDLED = {
    "regular": "NotoSansSC-Regular.woff2",
    "bold": "NotoSansSC-Bold.woff2",
    "display": "SSFangTangTi.woff2",
}

# woff2 is a web format Pillow cannot open, so the bundled files are only a
# fallback marker.  Real rendering relies on system CJK fonts.
_SYSTEM_CANDIDATES = {
    "regular": [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\Deng.ttf",
        r"C:\Windows\Fonts\simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ],
    "bold": [
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ],
}

_cache: dict[tuple[str, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
_logged: set[str] = set()


def _first_existing(paths: list[str]) -> str | None:
    for path in paths:
        if path and os.path.isfile(path):
            return path
    return None


def _resolve_path(weight: str) -> str | None:
    """Return a usable font path for the given weight, or None."""
    # 1. A plain TTF/OTF dropped next to the templates for overriding the system font.
    override = os.environ.get("SETU_FORTUNE_FONT")
    if override and os.path.isfile(override):
        return override
    for candidate in (f"fortune-{weight}.ttf", f"fortune-{weight}.otf", "fortune.ttf"):
        path = _FONTS_DIR / candidate
        if path.is_file():
            return str(path)
    # 2. System CJK fonts.
    if sys.platform.startswith("win"):
        windir = os.environ.get("WINDIR", r"C:\Windows")
        fonts = os.path.join(windir, "Fonts")
        preferred = [
            os.path.join(fonts, "msyhbd.ttc" if weight == "bold" else "msyh.ttc"),
            os.path.join(fonts, "msyh.ttc"),
        ]
        return _first_existing(preferred + _SYSTEM_CANDIDATES["regular"])
    return _first_existing(_SYSTEM_CANDIDATES.get(weight) or _SYSTEM_CANDIDATES["regular"])


def load_font(weight: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a CJK-capable font at ``size``, cached per (weight, size).

    Never raises: falls back to PIL's default bitmap font so a missing font
    can never break card generation.
    """
    key = (weight, size)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    path = _resolve_path(weight)
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    if path:
        try:
            font = ImageFont.truetype(path, size)
            _cache[key] = font
            return font
        except Exception as exc:  # corrupt font file
            logger.warning("[fortune] failed to load font %s: %s", path, exc)
    if weight not in _logged:
        _logged.add(weight)
        logger.warning(
            "[fortune] no CJK font found for weight=%s; falling back to PIL default"
            " (card may show tofu boxes)",
            weight,
        )
    font = ImageFont.load_default()
    _cache[key] = font
    return font


def clear_font_cache() -> None:
    """Drop cached font objects (used by tests and hot reload)."""
    _cache.clear()
    _logged.clear()
