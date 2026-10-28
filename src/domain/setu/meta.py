"""Setu image metadata value objects.

Provider responses carry far more than a bare URL: Pixiv id, title, author,
and a bilingual tag list.  Historically only the URL survived the trip into
:class:`ImagePayload`, so every downstream consumer lost the ability to show
image details.  ``ImageMeta`` keeps that information attached to its URL.

Only ``url`` takes part in hashing and equality.  De-duplication bookkeeping in
the provider layer uses URL strings as keys, and two responses describing the
same picture must be treated as the same picture regardless of which optional
fields the API happened to include.
"""

from __future__ import annotations

from dataclasses import dataclass

# Japanese tag fragments that the Pixiv tag set repeats next to the Chinese
# translation.  Used only for display filtering, never for de-duplication.
_CJK_RANGES = (
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0x3400, 0x4DBF),  # CJK Extension A
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
)
_HIRAGANA_KATAKANA = (
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
)


def _char_in(ch: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    code = ord(ch)
    return any(low <= code <= high for low, high in ranges)


def has_han(ch: str) -> bool:
    """Return True when the character is a Han (Chinese) ideograph."""
    return _char_in(ch, _CJK_RANGES)


def is_kana(text: str) -> bool:
    """Return True when the text contains any kana character."""
    return any(_char_in(ch, _HIRAGANA_KATAKANA) for ch in text)


def is_chinese_tag(tag: str) -> bool:
    """Heuristic: keep tags that contain Han characters and no kana.

    Pixiv authors usually submit both ``双马尾`` and ``ツインテール``.  Showing
    both doubles the length of the card without adding meaning, so the display
    layer keeps the Chinese reading and drops the kana one.
    """
    stripped = tag.strip()
    if not stripped:
        return False
    if is_kana(stripped):
        return False
    return any(has_han(ch) for ch in stripped)


@dataclass(frozen=True, slots=True)
class ImageMeta:
    """Metadata for one image returned by a provider.

    Attributes:
        url: Direct image URL after any proxy rewriting.  Identity of the value.
        title: Pixiv work title when the provider exposes it.
        author: Pixiv author name.
        uid: Pixiv author id.
        pid: Pixiv illustration id.
        pic_id: Index of the page inside a multi-page illustration (``p``).
        tags: Raw tag list as returned by the API, in original order.
        width: Pixel width when known.
        height: Pixel height when known.
        is_ai: ``True``/``False`` when the API declares AI generation status.
        provider: Provider class name that produced this entry.
    """

    url: str
    title: str = ""
    author: str = ""
    uid: int | None = None
    pid: int | None = None
    pic_id: int | None = None
    tags: tuple[str, ...] = ()
    width: int = 0
    height: int = 0
    is_ai: bool | None = None
    provider: str = ""

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ImageMeta):
            return NotImplemented
        return self.url == other.url

    def __hash__(self) -> int:
        return hash(self.url)

    @property
    def has_details(self) -> bool:
        """True when there is anything worth rendering besides the image."""
        return bool(self.title or self.author or self.tags)

    def display_tags(self, limit: int = 10) -> tuple[str, ...]:
        """Return de-duplicated Chinese tags, capped at ``limit``.

        Falls back to the raw list when no tag survives the Chinese filter, so
        a work tagged purely in kana still shows something instead of nothing.
        """
        kept: list[str] = []
        seen: set[str] = set()
        for tag in self.tags:
            candidate = tag.strip()
            if not candidate or candidate in seen:
                continue
            if not is_chinese_tag(candidate):
                continue
            seen.add(candidate)
            kept.append(candidate)
            if len(kept) >= limit:
                return tuple(kept)
        if kept:
            return tuple(kept)

        # No Han tags at all: fall back to the raw order, still de-duplicated.
        raw: list[str] = []
        for tag in self.tags:
            candidate = tag.strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            raw.append(candidate)
            if len(raw) >= limit:
                break
        return tuple(raw)


def build_meta(
    url: str,
    *,
    title: str = "",
    author: str = "",
    uid: int | None = None,
    pid: int | None = None,
    pic_id: int | None = None,
    tags: list[str] | tuple[str, ...] = (),
    width: int = 0,
    height: int = 0,
    is_ai: bool | None = None,
    provider: str = "",
) -> ImageMeta:
    """Build an :class:`ImageMeta`, tolerating dirty values from APIs."""
    clean_tags = tuple(str(tag).strip() for tag in tags if str(tag).strip())
    return ImageMeta(
        url=url,
        title=str(title or "").strip(),
        author=str(author or "").strip(),
        uid=uid if isinstance(uid, int) else None,
        pid=pid if isinstance(pid, int) else None,
        pic_id=pic_id if isinstance(pic_id, int) else None,
        tags=clean_tags,
        width=width if isinstance(width, int) and width > 0 else 0,
        height=height if isinstance(height, int) and height > 0 else 0,
        is_ai=is_ai if isinstance(is_ai, bool) else None,
        provider=str(provider or ""),
    )
