"""Layout analysis: recursive XY-cut turns a page's lines into reading order.

Pure geometry over the extracted line boxes -- no ML, no new dependency, deterministic. The
region tree is an intermediate representation and is never added to the document model.

Coordinates are PDF points with the y-axis pointing up, so a bbox is (x0, y0, x1, y1) with y1 the
top edge. Top-to-bottom reading order is therefore *descending* y1.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .extract import Page, TextLine
from .model import BBox
from .profile import TypographicProfile

# Named thresholds, tuned against the 1905 Wheaton Bulletin (a real two-column OCR'd scan).
#
# A column gutter is found as the widest interior *coverage valley* -- an x-range crossed by few
# enough lines -- rather than a perfectly clear gap. On real (OCR'd, justified) text a handful of
# lines overhang the gutter; on the bulletin every page had exactly one line straddling an
# otherwise-clean column boundary, and requiring a zero-crossing gap missed the columns entirely.
# A gutter may be crossed by up to this fraction of the region's lines (floored to an integer). At
# 0.05 a ~5-line region tolerates none -- so a single full-width header still blocks a vertical cut
# and is isolated by a horizontal one first -- while a ~20+ line region tolerates the odd overhang
# that real column text always has (the bulletin's pages, ~110 lines, tolerate 5).
COVERAGE_TOLERANCE_FRACTION = 0.05
# Gutter width is measured in absolute points, not as a fraction of page width: a column gutter is
# a typographic measure (the bulletin's are 7-11pt), and the old 5%-of-page rule (~25pt on Letter)
# rejected every real newspaper gutter.
GUTTER_MIN_WIDTH_PT = 4.0           # a valley narrower than this is not a column boundary
GUTTER_MARGINAL_WIDTH_PT = 6.0      # a gutter narrower than this (but >= MIN) is a marginal cut
GUTTER_MIN_HEIGHT_FRACTION = 0.5    # text on both sides must each span this much of the height
# Both sides of a column cut must hold at least this many lines. Without it, XY-cut over-segments:
# a heading gap isolates a line or two, and a coverage valley then splits those one-line fragments
# into spurious "columns". A real column is many lines; two lines each side is the floor.
COLUMN_MIN_LINES = 2
BLOCK_GAP_MIN_FRACTION = 0.02       # a block break must be this tall (fraction of region height)


@dataclass(frozen=True)
class PlacedLine:
    line: TextLine
    column: int


@dataclass
class Region:
    bbox: BBox
    kind: str  # "columns" | "block-stack" | "block"
    children: list["Region"] = field(default_factory=list)
    lines: list[TextLine] = field(default_factory=list)


@dataclass
class PageLayout:
    lines: list[PlacedLine]
    flags: list[str]


def _gutter_spans_height(lines: list[TextLine], gap_left: float, gap_right: float) -> bool:
    """True when text on BOTH sides of the gutter each spans at least GUTTER_MIN_HEIGHT_FRACTION
    of the region's *content* height. Guards against a lone page number or a centered title
    manufacturing a false column out of what is really one text block.

    The threshold is measured against the vertical extent of the text, not the page box: a real
    column occupies only the text area, so measuring against the full page height (which includes
    the margins order_page passes in) would reject every genuine gutter and silently disable
    column detection on real pages.
    """
    left_lines = [ln for ln in lines if ln.bbox[2] <= gap_left]
    right_lines = [ln for ln in lines if ln.bbox[0] >= gap_right]
    if not left_lines or not right_lines:
        return False

    content_h = max(ln.bbox[3] for ln in lines) - min(ln.bbox[1] for ln in lines)
    if content_h <= 0:
        return False

    def covered(side: list[TextLine]) -> float:
        top = max(ln.bbox[3] for ln in side)
        bottom = min(ln.bbox[1] for ln in side)
        return top - bottom

    return min(covered(left_lines), covered(right_lines)) >= content_h * GUTTER_MIN_HEIGHT_FRACTION


def _coverage_valleys(lines: list[TextLine], x0: float, x1: float,
                      tolerance: int) -> list[tuple[float, float]]:
    """Maximal x-ranges [a, b] within (x0, x1) crossed by at most `tolerance` lines.

    Coverage changes only at line edges, so it is evaluated once per elementary interval between
    consecutive edges. Contiguous low-coverage intervals are merged into a single valley.
    """
    edges = sorted({x0, x1} | {ln.bbox[0] for ln in lines} | {ln.bbox[2] for ln in lines})
    valleys: list[tuple[float, float]] = []
    start: float | None = None
    end: float = x0
    for a, b in zip(edges, edges[1:]):
        mid = (a + b) / 2
        coverage = sum(1 for ln in lines if ln.bbox[0] < mid < ln.bbox[2])
        if coverage <= tolerance:
            if start is None:
                start = a
            end = b
        elif start is not None:
            valleys.append((start, end))
            start = None
    if start is not None:
        valleys.append((start, end))
    return valleys


def _widest_vertical_gutter(lines: list[TextLine], bbox: BBox) -> tuple[float, float] | None:
    """Widest valid column gutter as (gap_left, gap_width), or None.

    The gutter is the widest interior coverage valley -- an x-range crossed by at most a small
    fraction of the lines -- that is wide enough (GUTTER_MIN_WIDTH_PT) and has text spanning enough
    of the height on both sides. Tolerating a few straddling lines is what lets a real, slightly
    ragged column boundary be found; the height guard rejects valleys at the region's empty edges.
    """
    x0, _, x1, _ = bbox
    if x1 - x0 <= 0 or len(lines) < 2:
        return None
    tolerance = int(len(lines) * COVERAGE_TOLERANCE_FRACTION)
    best: tuple[float, float] | None = None  # (gap_left, gap_width)
    for a, b in _coverage_valleys(lines, x0, x1, tolerance):
        width = b - a
        if width < GUTTER_MIN_WIDTH_PT or not _gutter_spans_height(lines, a, b):
            continue
        left_count = sum(1 for ln in lines if ln.bbox[2] <= a)
        right_count = sum(1 for ln in lines if ln.bbox[0] >= b)
        if left_count < COLUMN_MIN_LINES or right_count < COLUMN_MIN_LINES:
            continue
        # Widest wins; ties break to the smaller left coordinate for determinism.
        if best is None or width > best[1] or (width == best[1] and a < best[0]):
            best = (a, width)
    return best


def _widest_horizontal_gap(lines: list[TextLine], bbox: BBox) -> float | None:
    """Y coordinate to split at (top band read first), or None.

    y-up: a gap is open vertical space between the bottom of the running-lowest line above and the
    top of the next line below. Only a gap tall enough (BLOCK_GAP_MIN_FRACTION) counts.
    """
    _, y0, _, y1 = bbox
    region_h = y1 - y0
    if region_h <= 0 or len(lines) < 2:
        return None
    ordered = sorted(lines, key=lambda ln: -ln.bbox[3])
    best_gap = 0.0
    best_y: float | None = None
    running_min_bottom = ordered[0].bbox[1]
    for ln in ordered[1:]:
        gap = running_min_bottom - ln.bbox[3]
        if gap > best_gap:
            best_gap = gap
            best_y = ln.bbox[3] + gap / 2
        running_min_bottom = min(running_min_bottom, ln.bbox[1])
    if best_y is None or best_gap < region_h * BLOCK_GAP_MIN_FRACTION:
        return None
    return best_y


def _xy_cut(lines: list[TextLine], bbox: BBox, marginal: list[bool] | None = None) -> Region:
    """Recursively segment `lines` within `bbox`. Vertical (column) cuts win ties over horizontal
    (block) cuts, so a full-width header above two columns is isolated before the columns split.
    Appends True to `marginal` whenever an accepted gutter is only marginally wide.
    """
    if not lines:
        return Region(bbox=bbox, kind="block", lines=[])
    x0, y0, x1, y1 = bbox
    gutter = _widest_vertical_gutter(lines, bbox)
    if gutter is not None:
        gap_left, gap_width = gutter
        if marginal is not None and gap_width < GUTTER_MARGINAL_WIDTH_PT:
            marginal.append(True)
        split_x = gap_left + gap_width / 2
        left = [ln for ln in lines if ln.bbox[0] < split_x]
        right = [ln for ln in lines if ln.bbox[0] >= split_x]
        return Region(bbox=bbox, kind="columns", children=[
            _xy_cut(left, (x0, y0, split_x, y1), marginal),
            _xy_cut(right, (split_x, y0, x1, y1), marginal),
        ])
    split_y = _widest_horizontal_gap(lines, bbox)
    if split_y is not None:
        top = [ln for ln in lines if ln.bbox[1] >= split_y]
        bottom = [ln for ln in lines if ln.bbox[1] < split_y]
        return Region(bbox=bbox, kind="block-stack", children=[
            _xy_cut(top, (x0, split_y, x1, y1), marginal),
            _xy_cut(bottom, (x0, y0, x1, split_y), marginal),
        ])
    ordered = sorted(lines, key=lambda ln: (-ln.bbox[3], ln.bbox[0]))
    return Region(bbox=bbox, kind="block", lines=ordered)


def _reading_order(region: Region) -> list[PlacedLine]:
    """Depth-first traversal producing lines in reading order, each tagged with a column index.

    A column is a leaf branch of the vertical-cut tree: each direct child of a `columns` node that
    is not itself a `columns` node begins a new column (numbered left-to-right). A `columns` child
    of a `columns` node is a nested split -- its own children are the real columns, so it advances
    no index of its own. Blocks stacked inside a column (a `block-stack`) inherit that column.
    """
    counter = [-1]
    out: list[PlacedLine] = []

    def walk(r: Region, column: int) -> None:
        if r.kind == "columns":
            for child in r.children:
                if child.kind == "columns":
                    walk(child, column)
                else:
                    counter[0] += 1
                    walk(child, counter[0])
        elif r.children:  # block-stack: inherit the column
            for child in r.children:
                walk(child, column)
        else:  # leaf block
            out.extend(PlacedLine(line=ln, column=max(column, 0)) for ln in r.lines)

    walk(region, -1)
    return out


def order_page(page: Page, profile: TypographicProfile) -> PageLayout:
    """Reading order for one page: body lines XY-cut into columns and blocks, then artifact lines
    (running headers/footers/page numbers, identified by the profile) appended with column == -1
    and excluded from the cut so they cannot manufacture spurious block breaks.
    """
    body: list[TextLine] = []
    artifacts: list[TextLine] = []
    for line in page.lines:
        if profile.role_of(line, page_height=page.height) == "artifact":
            artifacts.append(line)
        else:
            body.append(line)

    marginal: list[bool] = []
    region = _xy_cut(body, (0.0, 0.0, page.width, page.height), marginal)
    placed = _reading_order(region)

    artifacts_ordered = sorted(artifacts, key=lambda ln: (-ln.bbox[3], ln.bbox[0]))
    placed.extend(PlacedLine(line=ln, column=-1) for ln in artifacts_ordered)

    flags = ["multi-column-suspected"] if any(marginal) else []
    return PageLayout(lines=placed, flags=flags)
