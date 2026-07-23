"""Pass two: turn extracted pages plus a typographic profile into the document model.

Everything emitted here carries provenance and a confidence score. Content that cannot be
modelled becomes an honest placeholder rather than a guess.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .extract import Page, TextLine
from .model import (
    Artifact,
    Document,
    Heading,
    ListItem,
    ListNode,
    Node,
    PageBreak,
    Paragraph,
    Placeholder,
    node_id,
)
from .profile import TypographicProfile, style_of

BULLET_PREFIXES = ("•", "‣", "◦", "-", "*")
ORDERED_RE = re.compile(r"^(\d+)[.)]\s+(.*)$")

_STAGE = "assemble"


def _ids(line: TextLine, page: Page) -> str:
    return node_id(page=line.page, bbox=line.bbox, page_width=page.width,
                   page_height=page.height, text=line.text)


def _list_item_text(text: str) -> tuple[str, bool] | None:
    """Return (item text, ordered) if the line looks like a list item, else None."""
    for bullet in BULLET_PREFIXES:
        if text.startswith(bullet):
            return text[len(bullet):].strip(), False
    match = ORDERED_RE.match(text)
    if match:
        return match.group(2).strip(), True
    return None


def assemble(
    pages: Iterable[Page],
    profile: TypographicProfile,
    *,
    title: str,
    lang: str = "en",
    source_was_tagged: bool = False,
) -> Document:
    nodes: list[Node] = []
    scanned: list[int] = []

    for page in pages:
        nodes.append(
            PageBreak(
                id=node_id(page=page.number, bbox=(0.0, 0.0, page.width, page.height),
                           page_width=page.width, page_height=page.height,
                           text=f"pagebreak-{page.number}"),
                page=page.number,
                bbox=(0.0, 0.0, page.width, page.height),
                confidence=1.0,
                stage=_STAGE,
                flags=[],
                label=str(page.number),
            )
        )

        if not page.has_text_layer:
            scanned.append(page.number)
            nodes.append(
                Placeholder(
                    id=node_id(page=page.number, bbox=(0.0, 0.0, page.width, page.height),
                               page_width=page.width, page_height=page.height,
                               text=f"scanned-{page.number}"),
                    page=page.number,
                    bbox=(0.0, 0.0, page.width, page.height),
                    confidence=0.0,
                    stage=_STAGE,
                    flags=["no-text-layer"],
                    reason=f"no text layer on source page {page.number}; "
                           "OCR branch not implemented",
                )
            )
        else:
            # Reading order for Phase 1 is top-to-bottom within a page. Column detection is
            # Phase 2; a multi-column page therefore produces interleaved paragraphs, which is
            # why such regions are flagged rather than silently trusted.
            ordered_lines = sorted(page.lines, key=lambda line: (-line.bbox[3], line.bbox[0]))

            pending_items: list[ListItem] = []
            pending_ordered = False

            def flush_list() -> None:
                nonlocal pending_items, pending_ordered
                if not pending_items:
                    return
                # The list's own bbox is the union of its items' bboxes, not just the first
                # item's -- a downstream consumer reading ListNode.bbox as "the region this
                # list occupies" would otherwise get only the first line's rectangle.
                x0 = min(item.bbox[0] for item in pending_items)
                y0 = min(item.bbox[1] for item in pending_items)
                x1 = max(item.bbox[2] for item in pending_items)
                y1 = max(item.bbox[3] for item in pending_items)
                union_bbox = (x0, y0, x1, y1)
                first = pending_items[0]
                nodes.append(
                    ListNode(
                        id=node_id(page=first.page, bbox=union_bbox, page_width=page.width,
                                   page_height=page.height,
                                   text="|".join(item.text for item in pending_items)),
                        page=first.page,
                        bbox=union_bbox,
                        confidence=min(item.confidence for item in pending_items),
                        stage=_STAGE,
                        flags=[],
                        ordered=pending_ordered,
                        items=list(pending_items),
                    )
                )
                pending_items = []
                pending_ordered = False

            for line in ordered_lines:
                role = profile.role_of(line, page_height=page.height)
                confidence = profile.confidence_for(line, page_height=page.height)

                if role == "artifact":
                    flush_list()
                    nodes.append(
                        Artifact(id=_ids(line, page), page=line.page, bbox=line.bbox,
                                 confidence=confidence, stage=_STAGE, flags=[], text=line.text)
                    )
                    continue

                if role == "heading":
                    flush_list()
                    nodes.append(
                        Heading(id=_ids(line, page), page=line.page, bbox=line.bbox,
                                confidence=confidence, stage=_STAGE, flags=[],
                                level=profile.heading_level(style_of(line)),
                                text=line.text)
                    )
                    continue

                item = _list_item_text(line.text)
                if item is not None:
                    text, ordered = item
                    if not pending_items:
                        pending_ordered = ordered
                    pending_items.append(
                        ListItem(id=_ids(line, page), page=line.page, bbox=line.bbox,
                                 confidence=confidence, stage=_STAGE, flags=[], text=text)
                    )
                    continue

                flush_list()
                flags = [] if confidence >= 0.5 else ["degraded-region"]
                nodes.append(
                    Paragraph(id=_ids(line, page), page=line.page, bbox=line.bbox,
                              confidence=confidence, stage=_STAGE, flags=flags, text=line.text)
                )

            flush_list()

        for image in page.images:
            nodes.append(
                Placeholder(
                    id=node_id(page=image.page, bbox=image.bbox, page_width=page.width,
                               page_height=page.height, text="image"),
                    page=image.page,
                    bbox=image.bbox,
                    confidence=0.0,
                    stage=_STAGE,
                    flags=["unmodelled-region"],
                    reason=f"image region on source page {image.page}; "
                           "no description available, so no Figure is emitted",
                )
            )

    return Document(title=title, lang=lang, nodes=nodes, scanned_pages=tuple(scanned),
                    source_was_tagged=source_was_tagged)
