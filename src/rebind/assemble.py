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

# A horizontal cluster only counts as a candidate column if it has more than this many lines --
# otherwise a single stray element (a page number, a footnote marker, a drop cap) that happens to
# sit apart on the x-axis would be enough to call a page multi-column.
_MIN_LINES_PER_CLUSTER = 3

# Two clusters are judged to run side-by-side (columns) rather than one sitting above the other
# (e.g. a centered title above full-width body text) only if their vertical extents overlap for
# most of the shorter cluster's height. This is deliberately a high bar: a title that merely
# brushes the top of the body column must not trip the heuristic.
_MIN_VERTICAL_OVERLAP_FRACTION = 0.6

# Minimum number of horizontally disjoint, vertically overlapping clusters required to call a
# page multi-column. Two is the smallest meaningful column count.
_MIN_COLUMN_CLUSTERS = 2


def _ids(line: TextLine, page: Page) -> str:
    return node_id(page=line.page, bbox=line.bbox, page_width=page.width,
                   page_height=page.height, text=line.text)


def _horizontal_clusters(lines: Iterable[TextLine]) -> list[tuple[float, float, float, float, int]]:
    """Group lines into horizontal (x-axis) clusters, merging lines whose x-intervals overlap.

    Returns each cluster as (x0, x1, y0, y1, line_count), sorted by x0. Because clusters are
    formed by merging any lines whose x-intervals overlap, the resulting clusters are guaranteed
    pairwise disjoint on the x-axis -- no further disjointness check is needed downstream.
    """
    ordered = sorted(lines, key=lambda ln: ln.bbox[0])
    clusters: list[list[float]] = []  # each entry: [x0, x1, y0, y1, count]
    for ln in ordered:
        x0, y0, x1, y1 = ln.bbox
        if clusters and x0 <= clusters[-1][1]:
            cluster = clusters[-1]
            cluster[1] = max(cluster[1], x1)
            cluster[2] = min(cluster[2], y0)
            cluster[3] = max(cluster[3], y1)
            cluster[4] += 1
        else:
            clusters.append([x0, x1, y0, y1, 1])
    return [(c[0], c[1], c[2], c[3], int(c[4])) for c in clusters]


def _vertical_overlap_fraction(a: tuple[float, float, float, float, int],
                                b: tuple[float, float, float, float, int]) -> float:
    """Fraction of the shorter cluster's height that overlaps the other cluster's y-range."""
    _, _, a_y0, a_y1, _ = a
    _, _, b_y0, b_y1, _ = b
    overlap = min(a_y1, b_y1) - max(a_y0, b_y0)
    if overlap <= 0:
        return 0.0
    shorter_height = min(a_y1 - a_y0, b_y1 - b_y0)
    if shorter_height <= 0:
        return 0.0
    return overlap / shorter_height


def _looks_multi_column(lines: Iterable[TextLine]) -> bool:
    """Cheap suspicion heuristic, not real column detection (that is Phase 2).

    A page looks multi-column when its lines' horizontal extents form two or more clusters that
    are disjoint on the x-axis but run side by side -- their y-extents overlap substantially. A
    single full-width column, or a centered title sitting above full-width body text, does not
    satisfy this.
    """
    qualifying = [c for c in _horizontal_clusters(lines) if c[4] >= _MIN_LINES_PER_CLUSTER]
    if len(qualifying) < _MIN_COLUMN_CLUSTERS:
        return False
    for i, a in enumerate(qualifying):
        for b in qualifying[i + 1:]:
            if _vertical_overlap_fraction(a, b) >= _MIN_VERTICAL_OVERLAP_FRACTION:
                return True
    return False


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
            # Reading order for Phase 1 is naive: top-to-bottom then left-to-right, with no
            # awareness of columns. On a multi-column page that sort interleaves the columns'
            # lines, scrambling reading order -- real column detection is deferred to Phase 2.
            # `_looks_multi_column` is a cheap suspicion heuristic (not detection) so that such
            # pages are flagged rather than silently trusted; see its docstring for the signature
            # it looks for.
            ordered_lines = sorted(page.lines, key=lambda line: (-line.bbox[3], line.bbox[0]))
            page_multi_column = _looks_multi_column(page.lines)

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
                if page_multi_column:
                    flags.append("multi-column-suspected")
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
