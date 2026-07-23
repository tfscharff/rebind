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

# Matches an ordered-list marker, with or without trailing content on the same line:
#   "1. first"  -> digits="1", content="first"   (content already on the marker's line)
#   "1."        -> digits="1", content=None       (WeasyPrint's <ol> marker-only glyph)
#   "2)"        -> digits="2", content=None
#
# The digit run is capped at 3 characters on purpose. Because \d{1,3} cannot skip over a digit
# (a regex match is contiguous), a longer run such as "1996" never matches at all -- taking a
# 1-3 digit prefix of it still leaves a digit as the next character, which fails the literal
# "[.)]" that must follow. That is what keeps "1996. It was a good year" from being misread as
# list item 1996: there is no layout information in a bare line of text to tell a list marker
# from a sentence that happens to start with a number, so the width of the digit run is the only
# signal available, and real list markers are essentially never more than three digits. This is
# a heuristic disambiguator, not a document size limit (invariant 5) -- a numbered list item
# past #999 would fail to match and fall back to being flagged as a plain paragraph, a known,
# documented limitation of text-only disambiguation, not an enforced ceiling.
ORDERED_RE = re.compile(r"^(\d{1,3})[.)](?:\s+(.*))?$")

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
    """Return (item text, ordered) if the line looks like a list item, else None.

    The returned item text is empty when the line is only the marker glyph (a bare bullet, or a
    bare "1." / "2)" with nothing after it) -- the caller holds those and waits for the content to
    arrive as the next line; see `pending_marker` in `assemble`.
    """
    for bullet in BULLET_PREFIXES:
        if text.startswith(bullet):
            return text[len(bullet):].strip(), False
    match = ORDERED_RE.match(text)
    if match:
        content = match.group(2)
        return (content.strip() if content is not None else ""), True
    return None


# Tolerance, in points, when comparing a candidate line's left edge against a held marker's right
# edge to decide whether the candidate is plausibly the marker's own content rather than an
# unrelated line that merely came next in reading order. WeasyPrint abuts the two exactly (marker
# x1 == content x0); this allows a hair of slack for sub-point float jitter from the layout engine
# without being loose enough to accept a genuinely separate line.
_MARKER_MERGE_X_TOLERANCE = 0.5


def _marker_merges_with(marker: TextLine, candidate: TextLine) -> bool:
    """Whether `candidate` is plausibly the marker's own content, not just the next line in
    reading order.

    True only when the two lines' y-ranges overlap (they sit on the same visual line) and the
    candidate starts at or after the marker's right edge (it is the content the marker
    introduces, rather than an unrelated line -- a decorative separator, a footnote marker -- that
    the bare marker glyph merely happened to precede).
    """
    _, m_y0, m_x1, m_y1 = marker.bbox
    c_x0, c_y0, _, c_y1 = candidate.bbox
    vertical_overlap = min(m_y1, c_y1) - max(m_y0, c_y0)
    if vertical_overlap <= 0:
        return False
    return c_x0 >= m_x1 - _MARKER_MERGE_X_TOLERANCE


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
            # Some renderers (WeasyPrint's native <ul>/<ol> markers among them) place the bullet
            # or number glyph in its own text box, separate from the item's content, which
            # pdfminer then yields as the *next* line rather than a prefix of the same one. A
            # marker-only line is held here and merged with whatever comes next -- but only when
            # that next line is plausibly the marker's own content (see `_marker_merges_with`).
            # An unrelated line that merely follows the marker in reading order (a decorative
            # separator, a footnote marker, the next heading) must not be swallowed into a
            # fictional list item with a fabricated bbox; the held marker is flushed honestly
            # instead, as its own degenerate item, rather than being dropped or over-merged.
            pending_marker: tuple[TextLine, float, bool] | None = None

            def flush_pending_marker() -> None:
                """Emit a held marker line as its own item if nothing ever arrived to merge it
                with (end of page, or a heading/artifact intervened) -- honest, if degenerate,
                rather than silently dropping the marker."""
                nonlocal pending_marker, pending_ordered
                if pending_marker is None:
                    return
                marker, marker_confidence, ordered = pending_marker
                if not pending_items:
                    pending_ordered = ordered
                pending_items.append(
                    ListItem(id=_ids(marker, page), page=marker.page, bbox=marker.bbox,
                             confidence=marker_confidence, stage=_STAGE, flags=[], text="")
                )
                pending_marker = None

            def flush_list() -> None:
                nonlocal pending_items, pending_ordered
                flush_pending_marker()
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
                    if text:
                        flush_pending_marker()
                        if not pending_items:
                            pending_ordered = ordered
                        pending_items.append(
                            ListItem(id=_ids(line, page), page=line.page, bbox=line.bbox,
                                     confidence=confidence, stage=_STAGE, flags=[], text=text)
                        )
                    else:
                        # A marker glyph with nothing after it on the same line (e.g. a bare
                        # bullet character). Don't emit an empty item yet -- hold it and see if
                        # the content arrives as the next line.
                        flush_pending_marker()
                        pending_marker = (line, confidence, ordered)
                    continue

                if pending_marker is not None and _marker_merges_with(pending_marker[0], line):
                    marker, marker_confidence, ordered = pending_marker
                    pending_marker = None
                    x0 = min(marker.bbox[0], line.bbox[0])
                    y0 = min(marker.bbox[1], line.bbox[1])
                    x1 = max(marker.bbox[2], line.bbox[2])
                    y1 = max(marker.bbox[3], line.bbox[3])
                    if not pending_items:
                        pending_ordered = ordered
                    pending_items.append(
                        ListItem(
                            id=node_id(page=line.page, bbox=(x0, y0, x1, y1),
                                       page_width=page.width, page_height=page.height,
                                       text=line.text),
                            page=line.page, bbox=(x0, y0, x1, y1),
                            confidence=min(confidence, marker_confidence),
                            stage=_STAGE, flags=[], text=line.text,
                        )
                    )
                    continue

                # A held marker that this line does not plausibly belong to (too far away, or
                # starting to its left) must still be accounted for -- flush it as its own
                # degenerate item rather than silently discarding it or fabricating a merge.
                flush_pending_marker()

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
