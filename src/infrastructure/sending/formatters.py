"""Caption builders for image delivery.

The plugin used to answer a single ``/setu`` with three separate messages: a
"fetching" notice, the picture, and a trailing "found N images" summary.  The
summary is now folded into the same message chain as the pictures, so one
command produces exactly one message.

This module owns the text that gets appended to that chain: an optional image
caption built from provider metadata (title / author / pid / tags) and the
optional trailing count notice.
"""

from __future__ import annotations

from ...domain.setu.meta import ImageMeta

# Kept in sync with ImageMeta.display_tags default.  Config may override.
DEFAULT_TAG_LIMIT = 10


def format_meta_caption(
    meta: ImageMeta | None,
    *,
    tag_limit: int = DEFAULT_TAG_LIMIT,
    include_tags: bool = True,
) -> str:
    """Build the caption lines describing one image.

    Returns an empty string when the provider exposed no usable metadata, so
    sources like SexNyan (URL only) stay clean instead of rendering a card full
    of blank labels.
    """
    if meta is None or not meta.has_details:
        return ""

    lines: list[str] = []
    if meta.title:
        lines.append(f"标题: {meta.title}")
    if meta.author:
        lines.append(f"作者: {meta.author}")
    if meta.pid is not None:
        lines.append(f"PID: {meta.pid}")
    if meta.uid is not None:
        lines.append(f"UID: {meta.uid}")
    if meta.is_ai is True:
        lines.append("AI 生成")

    if include_tags:
        tags = meta.display_tags(limit=tag_limit)
        if tags:
            lines.append("标签: " + " ".join(f"#{tag}" for tag in tags))

    return "\n".join(lines)


def format_multi_caption(
    metas: tuple[ImageMeta, ...],
    *,
    tag_limit: int = DEFAULT_TAG_LIMIT,
    include_tags: bool = True,
    max_blocks: int = 3,
) -> str:
    """Build captions for a batch, capped so the message stays readable.

    The first image gets the detailed block; further images are listed as a
    compact ``#n 标题 - 作者`` line.  Tags beyond the cap are omitted rather than
    truncated mid-list, which would look like a bug.
    """
    if not metas:
        return ""
    detailed = [meta for meta in metas if meta.has_details]
    if not detailed:
        return ""

    if len(metas) == 1:
        return format_meta_caption(
            metas[0], tag_limit=tag_limit, include_tags=include_tags
        )

    blocks: list[str] = []
    for index, meta in enumerate(metas[:max_blocks], start=1):
        if not meta.has_details:
            continue
        head = f"#{index}"
        if meta.title:
            head += f" {meta.title}"
        if meta.author:
            head += f" - {meta.author}"
        if meta.pid is not None:
            head += f" (PID {meta.pid})"
        blocks.append(head)
        if index == 1 and include_tags:
            tags = meta.display_tags(limit=tag_limit)
            if tags:
                blocks.append("标签: " + " ".join(f"#{tag}" for tag in tags))

    remaining = len(detailed) - min(len(detailed), max_blocks)
    if remaining > 0:
        blocks.append(f"…… 另有 {remaining} 张")
    return "\n".join(blocks)
