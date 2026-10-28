"""Today's fortune HTML renderer."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.core import html_renderer

from ..fortune.image_guard import is_valid_image

# Bump when the template's data contract changes so stale cached PNGs are not
# served for the rest of the day.  See FortuneDetail / _cache signature.
TEMPLATE_VERSION = "3"


class FortuneRenderer:
    """Render fortune cards to images via AstrBot's HTML renderer."""

    def __init__(self, template_path: Path | None = None) -> None:
        if template_path is None:
            template_path = (
                Path(__file__).resolve().parents[3] / "templates" / "fortune.html"
            )
        self.template_path = template_path
        self._fonts_dir = self.template_path.parent / "res" / "fonts"
        self._embedded_fonts_css: str | None = None

    def _get_embedded_fonts_css(self) -> str:
        if self._embedded_fonts_css is not None:
            return self._embedded_fonts_css

        fonts_config = [
            ("NotoSansSC-Regular", "NotoSansSC-Regular.woff2"),
            ("NotoSansSC-Bold", "NotoSansSC-Bold.woff2"),
            ("SSFangTangTi", "SSFangTangTi.woff2"),
        ]
        css_parts = ["    /* Embedded fonts - auto-generated */"]

        for font_name, file_name in fonts_config:
            font_path = self._fonts_dir / file_name
            try:
                if not font_path.exists():
                    logger.warning("[fortune] Font file not found: %s", font_path)
                    continue
                font_data = font_path.read_bytes()
                b64_data = base64.b64encode(font_data).decode("ascii")
                css_parts.append(
                    f"""    @font-face {{
      font-family: '{font_name}';
      src: url('data:font/woff2;base64,{b64_data}') format('woff2');
      font-weight: normal;
      font-style: normal;
      font-display: swap;
    }}"""
                )
            except OSError as exc:
                logger.error("[fortune] Failed to read font %s: %s", file_name, exc)

        self._embedded_fonts_css = "\n".join(css_parts)
        return self._embedded_fonts_css

    @staticmethod
    def _truncate_username(username: str, max_length: int = 15) -> str:
        return username[:max_length] + "..." if len(username) > max_length else username

    @staticmethod
    def _detect_image_mime(image_base64: str | None) -> str:
        """Guess the MIME type from the first bytes of the embedded image.

        Providers can return JPEG, PNG or WebP; hard-coding ``image/jpeg`` made
        WebP sources render as a broken-image placeholder.
        """
        if not image_base64:
            return "image/jpeg"
        try:
            head = base64.b64decode(image_base64[:24] + "==")
        except Exception:
            return "image/jpeg"
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if head.startswith(b"GIF8"):
            return "image/gif"
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return "image/webp"
        if head.startswith(b"BM"):
            return "image/bmp"
        return "image/jpeg"

    def _get_template(self) -> str:
        try:
            return self.template_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("[fortune] Failed to read template: %s", exc)
            return """<!DOCTYPE html>
<html><body style="width:720px;padding:20px;">
<div style="background:#f0f0f0;border-radius:10px;padding:20px;">
<h2>{{ username }} 的今日运势 ({{ date_str }})</h2>
<p>运势: {{ title }}</p>
<p>星级: {% for filled in star_flags %}{% if filled %}&#9733;{% else %}&#9734;{% endif %}{% endfor %}</p>
<p>{{ blessing }}</p>
</div></body></html>"""

    @staticmethod
    def _format_stars(count: int, max_count: int = 7) -> str:
        stars_html = []
        for _ in range(count):
            stars_html.append('<span class="star">★</span>')
        for _ in range(max_count - count):
            stars_html.append('<span class="star empty">★</span>')
        return "".join(stars_html)

    @staticmethod
    def _star_list(count: int, max_count: int = 7) -> list[bool]:
        """Star flags so the template can loop without raw HTML injection."""
        return [True] * count + [False] * max(0, max_count - count)

    def build_template_data(
        self, fortune: dict[str, Any], image_base64: str | None = None
    ) -> dict[str, Any]:
        """Build Jinja2 template data for the fortune card.

        Every value here is a plain string / number / list so the template never
        needs ``| safe`` for user content.  ``fonts_css`` remains raw CSS and is
        the only ``| safe`` value.
        """
        username = self._truncate_username(fortune.get("username", "用户"))
        star_count = int(fortune.get("star_count", 3) or 0)
        max_stars = int(fortune.get("max_stars", 7) or 7)
        detail = fortune.get("detail")

        data: dict[str, Any] = {
            "fonts_css": self._get_embedded_fonts_css(),
            "username": username,
            "date_str": fortune.get("date_str", ""),
            "title": fortune.get("title", "未知"),
            "star_flags": self._star_list(star_count, max_stars),
            "description": fortune.get("description", ""),
            "extra_message": fortune.get("extra_message", ""),
            "theme_color": fortune.get("theme_color", "theme-gray"),
            "image_base64": image_base64 or "",
            "image_mime": self._detect_image_mime(image_base64),
            "yi": [],
            "ji": [],
            "dims": [],
            "lucky_color": "",
            "lucky_color_hex": "",
            "lucky_number": "",
            "lucky_direction": "",
            "lucky_item": "",
            "auspicious_hour": "",
            "auspicious_hour_window": "",
            "blessing": "",
            "sign_grade": "",
            "sign_title": "",
            "sign_type": "",
            "sign_poetry": "",
            "sign_meaning": "",
            "sign_explanation": "",
            "lunar_date": "",
            "ganzhi": "",
            "zodiac": "",
            "solar_term": "",
        }

        if detail is not None:
            data.update(
                {
                    "yi": list(detail.yi),
                    "ji": list(detail.ji),
                    "dims": [
                        {
                            "key": key,
                            "label": label,
                            "score": score,
                            "level": _score_level(score),
                        }
                        for key, label, score in detail.dims
                    ],
                    "lucky_color": detail.lucky_color,
                    "lucky_color_hex": detail.lucky_color_hex,
                    "lucky_number": (
                        detail.lucky_number if detail.lucky_number is not None else ""
                    ),
                    "lucky_direction": detail.lucky_direction,
                    "lucky_item": detail.lucky_item,
                    "auspicious_hour": detail.auspicious_hour,
                    "auspicious_hour_window": detail.auspicious_hour_window,
                    "blessing": detail.blessing,
                    "sign_grade": detail.sign_grade,
                    "sign_title": detail.sign_title,
                    "sign_type": detail.sign_type,
                    "sign_poetry": detail.sign_poetry,
                    "sign_meaning": detail.sign_meaning,
                    "sign_explanation": detail.sign_explanation,
                    "lunar_date": detail.lunar_date,
                    "ganzhi": detail.ganzhi,
                    "zodiac": detail.zodiac,
                    "solar_term": detail.solar_term,
                }
            )

        return data

    async def render_to_image(
        self, fortune: dict[str, Any], image_base64: str | None = None
    ) -> bytes | None:
        try:
            template = self._get_template()
            tmpl_data = self.build_template_data(fortune, image_base64=image_base64)
            output = await html_renderer.render_custom_template(
                tmpl_str=template,
                tmpl_data=tmpl_data,
                return_url=False,
                options={"full_page": True, "type": "png", "scale": "device"},
            )
            if output is None:
                return None
            if isinstance(output, bytes):
                return output if is_valid_image(output) else None
            if isinstance(output, str):
                path = Path(output)
                if path.exists():
                    if path.stat().st_size < 100:
                        return None
                    data = path.read_bytes()
                    return data if is_valid_image(data) else None
            return None
        except Exception as exc:
            logger.exception("[fortune] Failed to render image: %s", exc)
            return None


def _score_level(score: int) -> str:
    """Map a 0-100 score to a coarse CSS class."""
    if score >= 80:
        return "great"
    if score >= 60:
        return "good"
    if score >= 40:
        return "normal"
    return "low"
