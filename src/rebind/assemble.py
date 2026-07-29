"""Pass two: turn extracted pages plus a typographic profile into the document model.

Everything emitted here carries provenance and a confidence score. Content that cannot be
modelled becomes an honest placeholder rather than a guess.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .extract import Page, TextLine
from .layout import order_page
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

# A page with a text layer AND a raster image covering at least this fraction of its area is an
# OCR-over-scan page: the image is the scanned page, and the text on top is recognizer output, not
# born-digital text. Measured against real samples, genuine scans cover ~100% and born-digital
# decorative images top out at a few percent, so 0.6 separates them with wide margin.
OCR_SCAN_COVERAGE = 0.6
# OCR-sourced text is recognizer output of unknown accuracy, so its confidence -- which otherwise
# means style-match cleanliness -- is capped here rather than left at a misleading 1.0. This is a
# coarse placeholder; a calibrated per-character confidence only becomes possible once Rebind runs
# its own OCR. Capping never raises confidence.
OCR_SOURCE_CONFIDENCE = 0.5

# Node types whose text is carried into the output; these are the ones labelled 'ocr-source' and
# confidence-capped on an OCR-over-scan page.
_OCR_MARKABLE = (Heading, Paragraph, ListItem, ListNode)


def _image_covers_page(image, page: Page) -> bool:
    """Whether an image covers enough of the page to be its background scan rather than a figure."""
    page_area = page.width * page.height
    if page_area <= 0:
        return False
    x0, y0, x1, y1 = image.bbox
    return (x1 - x0) * (y1 - y0) >= page_area * OCR_SCAN_COVERAGE


def _is_ocr_over_scan(page: Page) -> bool:
    """A text layer sitting on top of a page-covering scan image."""
    return page.has_text_layer and any(_image_covers_page(im, page) for im in page.images)


def _ids(line: TextLine, page: Page) -> str:
    return node_id(page=line.page, bbox=line.bbox, page_width=page.width,
                   page_height=page.height, text=line.text)


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
    ocr_pages: set[int] = set()

    for page in pages:
        page_is_ocr = _is_ocr_over_scan(page)
        if page_is_ocr:
            ocr_pages.add(page.number)
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
            # Reading order comes from the layout stage: recursive XY-cut segments the page into
            # columns and blocks and returns the body lines in reading order, each tagged with a
            # column index, plus the artifact lines (running headers/footers/page numbers) held
            # out of the cut and appended last. A page whose column gutter was only marginal
            # carries `multi-column-suspected` so a reviewer is warned the interleaving may be
            # wrong rather than being given falsely-confident order.
            page_layout = order_page(page, profile)
            page_flags = page_layout.flags
            # Column provenance is recorded only when a page actually has more than one column --
            # on a single-column page every line is column 0 and the tag is noise.
            column_count = len({p.column for p in page_layout.lines if p.column >= 0})

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
                rather than silently dropping the marker.

                The item's text carries the marker's own glyph (e.g. "7." or a bullet character)
                rather than being left empty: an empty `<li>` inside `<ol>` still gets a CSS
                auto-number from the browser/renderer, so a stray numeric marker with nothing
                ever following it would otherwise reappear in the output as a fabricated "1." --
                real content the source never had. A marker that never got its content also must
                not decide the list's ordered-ness for the same reason: rendering it inside <ol>
                invents that ordinal regardless of what the marker glyph actually was, so a
                content-less marker always falls back to unordered (a bullet), which makes no
                numeric claim at all.
                """
                nonlocal pending_marker, pending_ordered
                if pending_marker is None:
                    return
                marker, marker_confidence, _ordered = pending_marker
                if not pending_items:
                    pending_ordered = False
                pending_items.append(
                    ListItem(id=_ids(marker, page), page=marker.page, bbox=marker.bbox,
                             confidence=marker_confidence, stage=_STAGE, flags=[], text=marker.text)
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

            for placed in page_layout.lines:
                line = placed.line
                role = profile.role_of(line, page_height=page.height)
                confidence = profile.confidence_for(line, page_height=page.height)
                provenance = (
                    [f"column-{placed.column}"]
                    if column_count > 1 and placed.column >= 0
                    else []
                )

                if role == "artifact":
                    flush_list()
                    nodes.append(
                        Artifact(id=_ids(line, page), page=line.page, bbox=line.bbox,
                                 confidence=confidence, stage=_STAGE, flags=[], text=line.text)
                    )
                    continue

                if role == "heading":
                    flush_list()
                    level = profile.heading_level(style_of(line))
                    # HTML/PDF-UA headings only go to h6; `emit` and `render` both clamp to that
                    # ceiling downstream. A document with more than six genuinely distinct heading
                    # styles (a real catalog routinely has a dozen) has some of those distinct
                    # levels collapse into h6 together at that point -- a real loss of structure,
                    # not a cosmetic clamp. The model keeps the true, uncapped level (the source
                    # of truth is never lossy on our account), and flags the node so the collapse
                    # is visible to a human reviewer rather than silently absorbed on the way out.
                    heading_flags = ["heading-level-collapsed"] if level > 6 else []
                    nodes.append(
                        Heading(id=_ids(line, page), page=line.page, bbox=line.bbox,
                                confidence=confidence, stage=_STAGE,
                                flags=heading_flags + provenance,
                                level=level,
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
                flags.extend(page_flags)
                flags.extend(provenance)
                nodes.append(
                    Paragraph(id=_ids(line, page), page=line.page, bbox=line.bbox,
                              confidence=confidence, stage=_STAGE, flags=flags, text=line.text)
                )

            flush_list()

        for image in page.images:
            # On an OCR-over-scan page the page-covering image IS the scanned page, already
            # represented by the recovered text -- emitting it as an undescribed figure placeholder
            # would be misleading. Genuinely smaller embedded images are still placeholdered.
            if page_is_ocr and _image_covers_page(image, page):
                continue
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

    _mark_ocr_source(nodes, ocr_pages)

    return Document(title=title, lang=lang, nodes=nodes, scanned_pages=tuple(scanned),
                    source_was_tagged=source_was_tagged)


def _mark_ocr_source(nodes: list[Node], ocr_pages: set[int]) -> None:
    """Flag every content node on an OCR-over-scan page 'ocr-source' and cap its confidence.

    Done as a post-pass over the assembled nodes rather than threaded through every creation site:
    list items in particular are built in several places, and a single pass keeps the honesty rule
    in one obvious spot. Capping only ever lowers confidence.
    """
    if not ocr_pages:
        return
    for node in nodes:
        if node.page not in ocr_pages or not isinstance(node, _OCR_MARKABLE):
            continue
        if "ocr-source" not in node.flags:
            node.flags.append("ocr-source")
        node.confidence = min(node.confidence, OCR_SOURCE_CONFIDENCE)
        if isinstance(node, ListNode):
            for item in node.items:
                if "ocr-source" not in item.flags:
                    item.flags.append("ocr-source")
                item.confidence = min(item.confidence, OCR_SOURCE_CONFIDENCE)
