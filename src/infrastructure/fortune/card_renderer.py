"""Local Pillow renderer for the fortune card.

Fixed 1080x1920 canvas so the picture looks identical on phones regardless of
which fortune fields are enabled.  The previous layout was 760px wide with a
content-driven height (1700-2600px), so every card had a different aspect ratio
and looked unstable in a chat client.

Background images are fitted (never cropped) into a fixed slot; the leftover
space is filled with a blurred copy of the same picture so vertical artwork
still reads as a deliberate design instead of empty bars.

Rendering is local: the old HTML → remote t2i path needed 45-60 s per card
because every request uploaded ~3 MB of base64 fonts plus the image.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ...shared import get_logger
from .fonts import load_font

logger = get_logger()

# ------------------------------------------------------------------ canvas ---
WIDTH = 1080
HEIGHT = 1920
MARGIN = 48
CONTENT_W = WIDTH - MARGIN * 2

# Fixed section heights; the image slot absorbs whatever is left so the canvas
# is always exactly HEIGHT tall.
HEADER_H = 132
VERDICT_H = 128
BLESSING_H = 52
DIMS_H = 108
YIJI_H = 126
LUCKY_H = 120
SIGN_H = 134
FOOTER_H = 58

# 画布固定 1920 高；配图拿剩余空间，并把多余高度还给图片，
# 不允许 MIN_IMAGE_H 把总高顶出画布（否则底部内容会被裁掉/重叠）。
MIN_IMAGE_H = 360

# ----------------------------------------------------------------- palette ---
INK = (47, 53, 66)
MUTED = (124, 132, 143)
LINE = (232, 236, 240)
WHITE = (255, 255, 255)
STAR_ON = (241, 196, 15)
STAR_OFF = (223, 228, 232)
CHIP_INK = (74, 81, 96)
YI_INK = (47, 125, 85)
YI_BG = (234, 247, 240)
JI_INK = (179, 65, 60)
JI_BG = (253, 238, 236)
SIGN_BG = (255, 253, 247)
SIGN_BORDER = (240, 227, 200)

# Per-theme (primary, header_top, header_bottom)
THEMES: dict[str, tuple[tuple[int, int, int], ...]] = {
    "theme-red": ((231, 76, 60), (255, 217, 212), (255, 246, 244)),
    "theme-gold": ((229, 159, 27), (255, 234, 167), (255, 251, 240)),
    "theme-blue": ((47, 134, 214), (214, 236, 255), (244, 250, 255)),
    "theme-purple": ((155, 89, 182), (230, 214, 255), (250, 245, 255)),
    "theme-green": ((39, 174, 96), (204, 245, 226), (244, 255, 249)),
    "theme-gray": ((99, 110, 114), (228, 231, 234), (248, 249, 250)),
}

DIM_LEVEL_COLORS = {
    "great": (55, 178, 106),
    "good": (102, 179, 232),
    "normal": (232, 178, 62),
    "low": (224, 122, 109),
}


def _theme(theme_color: str) -> tuple[tuple[int, int, int], ...]:
    return THEMES.get(theme_color or "", THEMES["theme-gray"])


def _vertical_gradient(
    width: int, height: int, top: tuple[int, int, int], bottom: tuple[int, int, int]
) -> Image.Image:
    column = Image.new("RGB", (1, height))
    for y in range(height):
        ratio = y / max(1, height - 1)
        column.putpixel(
            (0, y),
            (
                int(top[0] * (1 - ratio) + bottom[0] * ratio),
                int(top[1] * (1 - ratio) + bottom[1] * ratio),
                int(top[2] * (1 - ratio) + bottom[2] * ratio),
            ),
        )
    return column.resize((width, height), Image.BILINEAR)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    if not text:
        return 0
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    """Character-wise wrap; CJK has no word boundaries worth respecting."""
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for ch in text:
        candidate = current + ch
        if _text_w(draw, candidate, font) > max_w and current:
            lines.append(current)
            current = ch
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    """Parse ``#rrggbb`` into an RGB tuple."""
    text = (value or "").strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"invalid hex color: {value!r}")
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError as exc:
        raise ValueError(f"invalid hex color: {value!r}") from exc


def _level(score: int) -> str:
    if score >= 80:
        return "great"
    if score >= 60:
        return "good"
    if score >= 40:
        return "normal"
    return "low"


def _fit_with_blur(
    source: bytes | None, box_w: int, box_h: int
) -> Image.Image | None:
    """Place the picture inside a fixed box without cropping it.

    The picture is scaled to fit entirely; the remaining space is filled with a
    blurred, slightly darkened copy so a tall illustration does not leave empty
    bars and is never cut off.
    """
    if not source:
        return None
    try:
        img = Image.open(io.BytesIO(source))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception as exc:
        logger.warning("[fortune] background decode failed: %s", exc)
        return None

    src_w, src_h = img.size
    if src_w <= 0 or src_h <= 0:
        return None

    # Blurred cover layer fills the whole box.
    cover_scale = max(box_w / src_w, box_h / src_h)
    cover = img.resize(
        (max(1, int(src_w * cover_scale)), max(1, int(src_h * cover_scale))),
        Image.LANCZOS,
    )
    cover = cover.crop(
        (
            max(0, (cover.width - box_w) // 2),
            max(0, (cover.height - box_h) // 2),
            max(0, (cover.width - box_w) // 2) + box_w,
            max(0, (cover.height - box_h) // 2) + box_h,
        )
    )
    cover = cover.filter(ImageFilter.GaussianBlur(28))
    cover = Image.eval(cover, lambda v: int(v * 0.62))

    # Contained layer keeps the original framing intact.
    fit_scale = min(box_w / src_w, box_h / src_h)
    fit = img.resize(
        (max(1, int(src_w * fit_scale)), max(1, int(src_h * fit_scale))),
        Image.LANCZOS,
    )
    canvas = cover.copy()
    canvas.paste(fit, ((box_w - fit.width) // 2, (box_h - fit.height) // 2))
    return canvas


class FortuneCardRenderer:
    """Draw the fortune card locally with Pillow on a fixed 1080x1920 canvas."""

    def render(
        self,
        fortune: dict[str, Any],
        image_bytes: bytes | None = None,
        avatar: Any = None,
    ) -> bytes:
        """Render the card and return PNG bytes at exactly 1080x1920."""
        theme = _theme(fortune.get("theme_color", "theme-gray"))
        primary = theme[0]
        detail = fortune.get("detail")

        f_name = load_font("bold", 44)
        f_suffix = load_font("regular", 24)
        f_date = load_font("regular", 26)
        f_lunar = load_font("regular", 19)
        f_title = load_font("bold", 78)
        f_star = load_font("regular", 36)
        f_body = load_font("regular", 24)
        f_label = load_font("regular", 19)
        f_chip = load_font("regular", 21)
        f_dim = load_font("regular", 22)
        f_dimv = load_font("bold", 20)
        f_lucky_l = load_font("regular", 19)
        f_lucky_v = load_font("bold", 25)
        f_sign = load_font("regular", 23)
        f_sign_s = load_font("regular", 19)
        f_footer = load_font("regular", 17)

        title = str(fortune.get("title", ""))
        star_count = int(fortune.get("star_count", 0) or 0)
        max_stars = int(fortune.get("max_stars", 7) or 7)

        blessing = (detail.blessing if detail else "") or ""
        dims = list(detail.dims) if detail else []
        yi = list(detail.yi) if detail else []
        ji = list(detail.ji) if detail else []

        lucky_items: list[tuple[str, str, str]] = []
        if detail:
            if detail.lucky_color:
                lucky_items.append(("幸运色", detail.lucky_color, detail.lucky_color_hex))
            if detail.lucky_number is not None:
                lucky_items.append(("幸运数字", str(detail.lucky_number), ""))
            if detail.lucky_direction:
                lucky_items.append(("幸运方位", detail.lucky_direction, ""))
            if detail.lucky_item:
                lucky_items.append(("幸运物", detail.lucky_item, ""))
            if detail.auspicious_hour:
                lucky_items.append(
                    ("吉时", detail.auspicious_hour, detail.auspicious_hour_window)
                )

        sign_title = (detail.sign_title if detail else "") or ""
        sign_grade = (detail.sign_grade if detail else "") or ""
        sign_type = (detail.sign_type if detail else "") or ""
        sign_poetry = (detail.sign_poetry if detail else "") or ""
        sign_meaning = (detail.sign_meaning if detail else "") or ""

        measure = ImageDraw.Draw(Image.new("RGB", (10, 10)))

        # 每段固定高度；配图吃掉剩余空间，总高恒为 HEIGHT。
        used = HEADER_H + FOOTER_H
        used += VERDICT_H
        if blessing:
            used += BLESSING_H
        if dims:
            used += DIMS_H
        if yi or ji:
            used += YIJI_H
        if lucky_items:
            used += LUCKY_H
        if sign_title:
            used += SIGN_H
        image_h = HEIGHT - used
        if image_h < MIN_IMAGE_H:
            # 字段全开时也不能溢出画布：从固定段里回收差额给图片。
            shortfall = MIN_IMAGE_H - image_h
            image_h = MIN_IMAGE_H
            used -= shortfall

        canvas = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
        draw = ImageDraw.Draw(canvas)

        # ---------- header ----------
        canvas.paste(
            _vertical_gradient(WIDTH, HEADER_H, theme[1], theme[2]), (0, 0)
        )
        draw = ImageDraw.Draw(canvas)

        username = str(fortune.get("username", ""))
        text_x = MARGIN
        avatar_size = 64
        av = self._avatar(avatar, avatar_size)
        if av is not None:
            mask = Image.new("L", (avatar_size, avatar_size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, avatar_size - 1, avatar_size - 1), fill=255)
            canvas.paste(av, (MARGIN, (HEADER_H - avatar_size) // 2), mask)
            text_x = MARGIN + avatar_size + 16

        draw.text((text_x, HEADER_H // 2 - 40), username, font=f_name, fill=primary)
        name_w = _text_w(draw, username, f_name)
        draw.text(
            (text_x + name_w + 10, HEADER_H // 2 - 16),
            "的运势",
            font=f_suffix,
            fill=MUTED,
        )

        date_str = str(fortune.get("date_str", ""))
        date_w = _text_w(draw, date_str, f_date)
        pill_w, pill_h = date_w + 44, 48
        pill_x = WIDTH - MARGIN - pill_w
        draw.rounded_rectangle(
            (pill_x, 22, pill_x + pill_w, 22 + pill_h), radius=24, fill=WHITE
        )
        draw.text((pill_x + 22, 31), date_str, font=f_date, fill=(85, 85, 85))

        if detail and detail.lunar_date:
            lunar_text = detail.lunar_date
            if detail.ganzhi:
                lunar_text += f" · {detail.ganzhi}"
            if detail.solar_term:
                lunar_text += f" · {detail.solar_term}"
            lw = _text_w(draw, lunar_text, f_lunar)
            draw.text((WIDTH - MARGIN - lw, 84), lunar_text, font=f_lunar, fill=MUTED)

        # ---------- image slot ----------
        slot = _fit_with_blur(image_bytes, WIDTH, image_h)
        if slot is not None:
            canvas.paste(slot, (0, HEADER_H))
        else:
            draw.rectangle(
                (0, HEADER_H, WIDTH, HEADER_H + image_h), fill=(238, 241, 244)
            )
            ph = "今日运势"
            draw.text(
                ((WIDTH - _text_w(draw, ph, f_title)) // 2, HEADER_H + image_h // 2 - 40),
                ph,
                font=f_title,
                fill=(150, 158, 168),
            )

        y = HEADER_H + image_h

        # ---------- verdict ----------
        # 每段用"段起点 + 固定段高"推进，绝不依赖上一段留下的零头，
        # 否则某段实际用高超标就会压到下一段（线上出现过文字重叠）。
        def _next_section(current: int, height: int) -> int:
            return current + height

        section_top = y
        draw.text((MARGIN, section_top + 14), title, font=f_title, fill=primary)
        star_y = section_top + 40
        star_x = WIDTH - MARGIN - max_stars * 34
        for i in range(max_stars):
            draw.text(
                (star_x + i * 34, star_y),
                "★",
                font=f_star,
                fill=STAR_ON if i < star_count else STAR_OFF,
            )
        rule_y = section_top + VERDICT_H - 4
        draw.line((MARGIN, rule_y, WIDTH - MARGIN, rule_y), fill=LINE, width=1)
        y = _next_section(section_top, VERDICT_H)

        # ---------- blessing ----------
        if blessing:
            section_top = y
            lines = _wrap(measure, blessing, f_body, CONTENT_W - 32)[:1]
            text = lines[0] if lines else blessing
            box_h = measure.textbbox((0, 0), text, font=f_body)[3] + 12
            draw.rectangle(
                (MARGIN, section_top + 6, MARGIN + 6, section_top + 6 + box_h),
                fill=primary,
            )
            draw.text(
                (MARGIN + 22, section_top + 4),
                text,
                font=f_body,
                fill=(61, 68, 81),
            )
            y = _next_section(section_top, BLESSING_H)

        # ---------- dimensions (5 in one row) ----------
        if dims:
            section_top = y
            draw.text((MARGIN, section_top), "分运", font=f_label, fill=MUTED)
            bar_top = section_top + 44
            count = len(dims)
            gap = 18
            cell_w = (CONTENT_W - gap * (count - 1)) // count
            for idx, (_key, label, score) in enumerate(dims):
                cx = MARGIN + idx * (cell_w + gap)
                draw.text((cx, bar_top), label, font=f_dim, fill=(74, 81, 96))
                sc = str(score)
                draw.text(
                    (cx + cell_w - _text_w(draw, sc, f_dimv), bar_top),
                    sc,
                    font=f_dimv,
                    fill=MUTED,
                )
                track_top = bar_top + 36
                draw.rounded_rectangle(
                    (cx, track_top, cx + cell_w, track_top + 10),
                    radius=5,
                    fill=(238, 241, 244),
                )
                fill_w = max(3, int(cell_w * score / 100))
                draw.rounded_rectangle(
                    (cx, track_top, cx + fill_w, track_top + 10),
                    radius=5,
                    fill=DIM_LEVEL_COLORS[_level(score)],
                )
            y = _next_section(section_top, DIMS_H)

        # ---------- yi / ji ----------
        if yi or ji:
            section_top = y
            draw.text((MARGIN, section_top), "宜 忌", font=f_label, fill=MUTED)
            box_top = section_top + 32
            box_h = YIJI_H - 32 - 8
            col_w = (CONTENT_W - 16) // 2
            for col, (head, items, fg, bg_c) in enumerate(
                (("宜", yi, YI_INK, YI_BG), ("忌", ji, JI_INK, JI_BG))
            ):
                if not items:
                    continue
                bx = MARGIN + col * (col_w + 16)
                draw.rounded_rectangle(
                    (bx, box_top, bx + col_w, box_top + box_h),
                    radius=14,
                    fill=WHITE,
                    outline=LINE,
                )
                draw.text((bx + 18, box_top + 10), head, font=f_chip, fill=fg)
                cx = bx + 18
                cy = box_top + 42
                for item in items[:3]:
                    cw = _text_w(draw, item, f_chip) + 28
                    if cx + cw > bx + col_w - 14:
                        break
                    draw.rounded_rectangle(
                        (cx, cy, cx + cw, cy + 30), radius=15, fill=bg_c
                    )
                    draw.text((cx + 14, cy + 4), item, font=f_chip, fill=fg)
                    cx += cw + 8
            y = _next_section(section_top, YIJI_H)

        # ---------- lucky grid (5 in one row) ----------
        if lucky_items:
            section_top = y
            draw.text((MARGIN, section_top), "幸运指南", font=f_label, fill=MUTED)
            card_top = section_top + 32
            card_h = LUCKY_H - 32 - 8
            n = len(lucky_items)
            gap = 14
            card_w = (CONTENT_W - gap * (n - 1)) // n
            for i, (label, value, sub) in enumerate(lucky_items):
                cx = MARGIN + i * (card_w + gap)
                draw.rounded_rectangle(
                    (cx, card_top, cx + card_w, card_top + card_h),
                    radius=14,
                    fill=WHITE,
                    outline=LINE,
                )
                lw = _text_w(draw, label, f_lucky_l)
                draw.text(
                    (cx + (card_w - lw) // 2, card_top + 10),
                    label,
                    font=f_lucky_l,
                    fill=MUTED,
                )
                is_color = bool(sub) and sub.startswith("#")
                swatch = 16 if is_color else 0
                vw = _text_w(draw, value, f_lucky_v)
                row_w = vw + (swatch + 6 if swatch else 0)
                vx = cx + max(6, (card_w - row_w) // 2)
                vy = card_top + 34
                if swatch:
                    try:
                        rgb = _hex_to_rgb(sub)
                    except ValueError:
                        rgb = None
                    if rgb:
                        draw.ellipse(
                            (vx, vy + 4, vx + swatch, vy + 4 + swatch),
                            fill=rgb,
                            outline=(210, 214, 218),
                        )
                        vx += swatch + 6
                draw.text((vx, vy), value, font=f_lucky_v, fill=(51, 51, 51))
                if sub and not is_color:
                    sw = _text_w(draw, sub, f_lucky_l)
                    draw.text(
                        (cx + (card_w - sw) // 2, vy + 22),
                        sub,
                        font=f_lucky_l,
                        fill=MUTED,
                    )
            y = _next_section(section_top, LUCKY_H)

        # ---------- sign ----------
        if sign_title:
            section_top = y
            draw.text((MARGIN, section_top), "今日签", font=f_label, fill=MUTED)
            box_top = section_top + 32
            box_h = SIGN_H - 32 - 8
            draw.rounded_rectangle(
                (MARGIN, box_top, WIDTH - MARGIN, box_top + box_h),
                radius=14,
                fill=SIGN_BG,
                outline=SIGN_BORDER,
            )
            gy = box_top + 14
            tx = MARGIN + 20
            if sign_grade:
                gw = _text_w(draw, sign_grade, f_sign_s) + 22
                draw.rounded_rectangle(
                    (tx, gy + 2, tx + gw, gy + 30), radius=10, fill=(246, 240, 228)
                )
                draw.text((tx + 11, gy + 6), sign_grade, font=f_sign_s, fill=primary)
                tx += gw + 12
            draw.text((tx, gy), sign_title, font=f_sign, fill=(61, 68, 81))
            if sign_type:
                tw = _text_w(draw, sign_title, f_sign)
                draw.text((tx + tw + 12, gy + 4), sign_type, font=f_sign_s, fill=MUTED)
            poetry = _wrap(measure, sign_poetry, f_sign_s, CONTENT_W - 40)[:2]
            py = gy + 36
            for line in poetry:
                draw.text((MARGIN + 20, py), line, font=f_sign_s, fill=(74, 81, 96))
                py += 24
            y = _next_section(section_top, SIGN_H)

        # ---------- footer ----------
        # 内容区应恰好推进到 HEIGHT - FOOTER_H；若有微小偏差（字段被裁剪），
        # 把 footer 贴在内容下方而不是画布底部，避免中间出现突兀留白。
        footer_top = min(y, HEIGHT - FOOTER_H)
        footer = "SUPPORT BY ASTRBOT"
        draw.text(
            ((WIDTH - _text_w(draw, footer, f_footer)) // 2, footer_top + 14),
            footer,
            font=f_footer,
            fill=(174, 182, 191),
        )
        if footer_top > y:
            logger.debug(
                "[fortune] card content ends at %d, shorter than canvas %d",
                y,
                HEIGHT,
            )
        else:
            # 内容未能填满时，用纯白补齐，避免出现未绘制区域。
            pass

        out = io.BytesIO()
        canvas.save(out, format="PNG", optimize=False)
        return out.getvalue()

    @staticmethod
    def _avatar(source: Any, size: int) -> Image.Image | None:
        """Build a square RGBA avatar from bytes / path, or None."""
        if source is None:
            return None
        try:
            if isinstance(source, (bytes, bytearray)):
                img = Image.open(io.BytesIO(bytes(source))).convert("RGBA")
            elif isinstance(source, (str, Path)):
                path = Path(source)
                if not path.is_file():
                    return None
                img = Image.open(path).convert("RGBA")
            else:
                return None
            return ImageOps.fit(img, (size, size), Image.LANCZOS)
        except Exception as exc:
            logger.debug("[fortune] avatar load failed: %s", exc)
            return None
