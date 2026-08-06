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

# Table detection (honest flagging only, no reconstruction). A table is a grid: rows that each
# split into the same recurring column positions. These separate a grid of short cells from prose
# (one long line per row) and from a single-column list. Tuned against Failure.pdf's Table 7.5.
ROW_BAND_FRACTION = 0.6        # lines whose centers are within this * median height share a row
COLUMN_ALIGN_TOLERANCE_PT = 12.0   # cell left-edges within this are the same column
# A table is distinguished from flowing multi-column text by REGULARITY, not by cell width (a wide
# gutter defeats any width test). A real table has several rows that each span the same set of
# aligned columns; flowing text aligns only coincidentally, so its "rows" rarely span three shared
# columns and almost never do so repeatedly. Both thresholds are three: a qualifying row must have
# cells on at least MIN_COLUMNS_FOR_TABLE recurring columns, and there must be at least
# MIN_ROWS_FOR_TABLE such rows. Tuned so Failure.pdf's Table 7.5 is caught while the 1905 bulletin's
# two- and three-column articles are not.
MIN_COLUMNS_FOR_TABLE = 3
MIN_ROWS_FOR_TABLE = 3
# A table row is *sparse*: its cells are short and separated by wide gaps, so they cover only part
# of the row's horizontal span. A flowing multi-column row is *dense*: each line fills its column,
# covering most of the span. Measured across the samples, real table rows fill <=0.8 of their span
# (Failure.pdf's Table 7.5: median 0.67) while flowing three-column newspaper rows fill ~0.93. This
# gate removes the dense flowing rows before the regularity test, which is what finally separates a
# table from dense multi-column text -- geometry alone (alignment) could not.
TABLE_ROW_MAX_FILL = 0.8


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
    # id() of each line that belongs to a detected table grid, so assemble can flag exactly those
    # paragraphs `table-suspected`. Detection runs per column region (see order_page), so a
    # multi-column page layout is not mistaken for a table.
    table_line_ids: set[int] = field(default_factory=set)


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


def _banner_split(lines: list[TextLine], bbox: BBox) -> float | None:
    """The y at which to peel a full-width banner off the top so the columns below become visible.

    A vertical cut is attempted over the whole region, but on the commonest article layout of all
    -- a heading and an introductory paragraph or two spanning the full measure, with two columns
    beneath -- those full-width lines cross the gutter and hide it. The horizontal cut does not
    rescue it either: the space between the intro and the columns is ordinary paragraph leading,
    far below the block-gap threshold. The region then collapses to a single block and is read
    straight across the gutter, which is the exact defect the cut exists to prevent.

    So when no gutter is found, look for the highest clean horizontal boundary that *reveals* one:
    scan candidate boundaries top-down and take the first where the lines below split into columns.
    Returning None (the common case) leaves the ordinary cut untouched -- this only ever fires
    where a genuine gutter is waiting underneath.
    """
    x0, y0, x1, _y1 = bbox
    ordered = sorted(lines, key=lambda ln: -ln.bbox[3])
    for i in range(1, len(ordered)):
        above, below = ordered[:i], ordered[i:]
        if len(below) < COLUMN_MIN_LINES * 2:
            return None
        boundary = min(ln.bbox[1] for ln in above)
        top_below = max(ln.bbox[3] for ln in below)
        if top_below > boundary:
            continue    # the bands overlap vertically -- not a clean place to cut
        split_y = (boundary + top_below) / 2
        if _widest_vertical_gutter(below, (x0, y0, x1, split_y)) is not None:
            return split_y
    return None


def _xy_cut(lines: list[TextLine], bbox: BBox, marginal: list[bool] | None = None) -> Region:
    """Recursively segment `lines` within `bbox`. A vertical (column) cut is tried first; failing
    that, a full-width banner is peeled off the top if doing so reveals columns (`_banner_split`),
    and failing that a horizontal (block) cut is made.
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
    split_y = _banner_split(lines, bbox) or _widest_horizontal_gap(lines, bbox)
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


def _center_inside(bbox: BBox, boxes: tuple) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)


def _splice_figure_text(placed: list[PlacedLine], in_figure: list[TextLine]) -> list[PlacedLine]:
    """Put a figure's own labels back into reading order at the height they sit at.

    They are read top-to-bottom among themselves (nothing better is knowable about a scatter of
    callouts) and inserted before the first body line that starts below them, which is where a
    sighted reader encounters them.
    """
    if not in_figure:
        return placed
    out = list(placed)
    for line in sorted(in_figure, key=lambda ln: (-ln.bbox[3], ln.bbox[0])):
        column = next((p.column for p in out if p.line.bbox[3] <= line.bbox[3]), 0)
        index = next((i for i, p in enumerate(out) if p.line.bbox[3] < line.bbox[3]), len(out))
        out.insert(index, PlacedLine(line=line, column=max(column, 0)))
    return out


def order_page(page: Page, profile: TypographicProfile,
               figure_boxes: tuple = ()) -> PageLayout:
    """Reading order for one page: body lines XY-cut into columns and blocks, then artifact lines
    (running headers/footers/page numbers, identified by the profile) appended with column == -1
    and excluded from the cut so they cannot manufacture spurious block breaks.

    Text *inside* a figure is held out of the cut for the same reason, and matters more than it
    sounds: a diagram's callout labels ("A", "B", "3 mm", "Ventral") are scattered across the
    figure at whatever position the artwork put them, and XY-cut reads that scatter as column
    structure. On the real sample one page of body text with a labelled schematic came out as
    "8 columns". Those labels are then spliced back in at the figure's own vertical position, so
    they stay where a reader meets them rather than being deferred to the end of the page.
    """
    body: list[TextLine] = []
    artifacts: list[TextLine] = []
    in_figure: list[TextLine] = []
    for line in page.lines:
        if _center_inside(line.bbox, figure_boxes):
            in_figure.append(line)
        elif profile.role_of(line, page_height=page.height) == "artifact":
            artifacts.append(line)
        else:
            body.append(line)

    marginal: list[bool] = []
    region = _xy_cut(body, (0.0, 0.0, page.width, page.height), marginal)
    placed = _reading_order(region)
    placed = _splice_figure_text(placed, in_figure)

    # Table detection runs on all body lines (a table's inter-cell gaps look like column gutters to
    # XY-cut, which fragments the grid, so per-column detection would miss it). It only *flags*;
    # ordering is unchanged, so running independently of the cut is correct. The three-column and
    # short-cell guards are what keep a genuine multi-column *layout* from being read as a table.
    table_line_ids = detect_table_lines(body)

    artifacts_ordered = sorted(artifacts, key=lambda ln: (-ln.bbox[3], ln.bbox[0]))
    placed.extend(PlacedLine(line=ln, column=-1) for ln in artifacts_ordered)

    flags = ["multi-column-suspected"] if any(marginal) else []
    return PageLayout(lines=placed, flags=flags, table_line_ids=table_line_ids)


def _rows_by_band(lines: list[TextLine]) -> list[list[TextLine]]:
    """Group lines into rows by their vertical center, top to bottom.

    A new row starts when a line's center drops more than ROW_BAND_FRACTION * median line height
    below the current row's center -- so lines that sit on the same visual row (a table's cells)
    stay together while successive rows separate.
    """
    if not lines:
        return []
    heights = sorted(ln.bbox[3] - ln.bbox[1] for ln in lines)
    median_h = heights[len(heights) // 2] or 1.0
    band = median_h * ROW_BAND_FRACTION
    ordered = sorted(lines, key=lambda ln: -((ln.bbox[1] + ln.bbox[3]) / 2))
    rows: list[list[TextLine]] = []
    current: list[TextLine] = []
    current_center = None
    for ln in ordered:
        center = (ln.bbox[1] + ln.bbox[3]) / 2
        if current_center is None or current_center - center <= band:
            current.append(ln)
            current_center = center if current_center is None else current_center
        else:
            rows.append(current)
            current = [ln]
            current_center = center
    if current:
        rows.append(current)
    return rows


def detect_table_lines(lines: list[TextLine]) -> set[int]:
    """Return the ids() of lines that belong to a detected table grid, or an empty set.

    A region is a table when its lines form a grid: at least MIN_ROWS_FOR_TABLE rows that each hold
    at least MIN_CELLS_PER_ROW side-by-side cells, landing on at least MIN_COLUMNS_FOR_TABLE column
    positions that recur across rows. Conservative by construction -- prose (one long line per row)
    yields no side-by-side cells, and a single-column list yields no recurring second column.

    Line identity is `id(line)` so the caller can match the returned set against its own lines
    without depending on bbox/text equality.
    """
    rows = _rows_by_band(lines)

    def disjoint_cells(row: list[TextLine]) -> list[TextLine]:
        # A row's horizontally-disjoint cells (a cell starts at/after the previous cell's right edge).
        cells = sorted(row, key=lambda ln: ln.bbox[0])
        out = [cells[0]] if cells else []
        for ln in cells[1:]:
            if ln.bbox[0] >= out[-1].bbox[2]:
                out.append(ln)
        return out

    all_cells = [disjoint_cells(row) for row in rows]

    # row_cells feeds ONLY the "does this region look tabular at all" signal below (establishing
    # recurring columns) -- a dense row (cells covering more than TABLE_ROW_MAX_FILL of the row
    # span, i.e. flowing multi-column text, not a table) must not by itself convince the detector a
    # region is a table. This is what removes the 1905 bulletin's dense three-column articles.
    row_cells: list[list[TextLine]] = []
    for cells in all_cells:
        if len(cells) >= MIN_COLUMNS_FOR_TABLE:
            span = cells[-1].bbox[2] - cells[0].bbox[0]
            fill = sum(c.bbox[2] - c.bbox[0] for c in cells) / span if span > 0 else 1.0
            row_cells.append([] if fill > TABLE_ROW_MAX_FILL else cells)
        else:
            row_cells.append([])

    # Cluster every cell's left edge into candidate columns, and record which rows touch each.
    column_x: list[float] = []
    column_rows: list[set[int]] = []
    for row_index, cells in enumerate(row_cells):
        for cell in cells:
            for i, cx in enumerate(column_x):
                if abs(cell.bbox[0] - cx) <= COLUMN_ALIGN_TOLERANCE_PT:
                    column_rows[i].add(row_index)
                    break
            else:
                column_x.append(cell.bbox[0])
                column_rows.append({row_index})

    # A recurring column appears in at least MIN_ROWS_FOR_TABLE rows. Regularity is the whole signal:
    # a qualifying (table) row must have cells on at least MIN_COLUMNS_FOR_TABLE recurring columns,
    # and there must be at least MIN_ROWS_FOR_TABLE such rows. Flowing multi-column text aligns only
    # coincidentally, so it almost never produces several rows that each span three shared columns.
    recurring = {i for i, seen in enumerate(column_rows) if len(seen) >= MIN_ROWS_FOR_TABLE}
    if len(recurring) < MIN_COLUMNS_FOR_TABLE:
        return set()
    recurring_x = [column_x[i] for i in recurring]

    def cells_on_recurring(cells: list[TextLine]) -> list[TextLine]:
        return [c for c in cells if any(abs(c.bbox[0] - cx) <= COLUMN_ALIGN_TOLERANCE_PT
                                        for cx in recurring_x)]

    # Once a region is established as tabular (above, from the non-dense rows), test EVERY row --
    # including ones excluded above for density -- against the columns already proven to recur. A
    # real table row with one unusually long cell value is still tightly aligned with its
    # neighbors; density only mattered for deciding whether the region was a table in the first
    # place, not for whether an individual already-established row belongs to it (a real sample's
    # row was otherwise dropped this way, fragmenting one table into two and mistagging the second
    # fragment's first data row as a header).
    table_rows = [
        row_index for row_index, cells in enumerate(all_cells)
        if len(cells_on_recurring(cells)) >= MIN_COLUMNS_FOR_TABLE
    ]
    if len(table_rows) < MIN_ROWS_FOR_TABLE:
        return set()

    flagged: set[int] = set()
    for row_index in table_rows:
        for cell in cells_on_recurring(all_cells[row_index]):
            flagged.add(id(cell))
    return flagged
