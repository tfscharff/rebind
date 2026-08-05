"""Unit tests for the XY-cut layout stage. Synthetic TextLines only -- no PDF involved."""

from __future__ import annotations

import random

from rebind.extract import Page, TextLine
from rebind.layout import _reading_order, _xy_cut, order_page
from rebind.profile import build_profile


def _line(x0, y0, x1, y1, text, *, font="Times", size=10.0, bold=False, italic=False):
    return TextLine(text=text, page=1, bbox=(x0, y0, x1, y1), font=font, size=size,
                    bold=bold, italic=italic)


def test_single_column_reads_top_to_bottom():
    # Three stacked lines, no gutter. y-up: the highest y is first.
    lines = [_line(72, 700, 500, 710, "top"),
             _line(72, 680, 500, 690, "middle"),
             _line(72, 660, 500, 670, "bottom")]
    region = _xy_cut(lines, (72, 660, 500, 710))
    placed = _reading_order(region)
    assert [p.line.text for p in placed] == ["top", "middle", "bottom"]
    assert {p.column for p in placed} == {0}


def test_two_columns_interleave_by_column():
    left = [_line(72, 700, 260, 710, "L1"), _line(72, 680, 260, 690, "L2")]
    right = [_line(320, 700, 500, 710, "R1"), _line(320, 680, 500, 690, "R2")]
    region = _xy_cut(left + right, (72, 680, 500, 710))
    placed = _reading_order(region)
    assert [p.line.text for p in placed] == ["L1", "L2", "R1", "R2"]
    assert [p.column for p in placed] == [0, 0, 1, 1]


def test_narrow_gap_is_not_a_column():
    # Two blocks 2pt apart horizontally -- below GUTTER_MIN_WIDTH_PT, must stay one column.
    a = [_line(72, 700, 260, 710, "A1"), _line(72, 680, 260, 690, "A2")]
    b = [_line(262, 700, 400, 710, "B1"), _line(262, 680, 400, 690, "B2")]
    region = _xy_cut(a + b, (72, 680, 400, 710))
    placed = _reading_order(region)
    assert {p.column for p in placed} == {0}


def test_column_gutter_crossed_by_one_overhang_is_still_found():
    # A real column boundary is rarely perfectly clear: on the 1905 bulletin a single line
    # overhangs the gutter on every page. Left column x in [72,250], right in [262,440], but one
    # left-column line runs long to x=300, straddling the gutter. It must still split into two
    # columns (the overhang tolerated), not collapse into one scrambled block.
    left = [_line(72, 700 - i * 20, 250, 710 - i * 20, f"L{i}") for i in range(10)]
    right = [_line(262, 700 - i * 20, 440, 710 - i * 20, f"R{i}") for i in range(10)]
    left[3] = _line(72, 640, 300, 650, "L3-long")  # overhangs into the gutter
    region = _xy_cut(left + right, (72, 500, 440, 710))
    placed = _reading_order(region)
    cols = {p.column for p in placed if p.column >= 0}
    assert cols == {0, 1}, f"overhang collapsed the columns: {cols}"
    texts = [p.line.text for p in placed]
    assert texts.index("L0") < texts.index("R0")  # left column read before right


def test_full_width_header_isolated_before_columns():
    header = [_line(72, 750, 500, 762, "HEADER")]     # spans full width, above both columns
    left = [_line(72, 700, 260, 710, "L1"), _line(72, 680, 260, 690, "L2")]
    right = [_line(320, 700, 500, 710, "R1"), _line(320, 680, 500, 690, "R2")]
    region = _xy_cut(header + left + right, (72, 680, 500, 762))
    placed = _reading_order(region)
    assert [p.line.text for p in placed] == ["HEADER", "L1", "L2", "R1", "R2"]


def test_three_columns_read_left_to_right():
    c0 = [_line(72, 700, 180, 710, "A"), _line(72, 680, 180, 690, "B")]
    c1 = [_line(220, 700, 330, 710, "C"), _line(220, 680, 330, 690, "D")]
    c2 = [_line(370, 700, 500, 710, "E"), _line(370, 680, 500, 690, "F")]
    region = _xy_cut(c0 + c1 + c2, (72, 680, 500, 710))
    placed = _reading_order(region)
    assert [p.line.text for p in placed] == ["A", "B", "C", "D", "E", "F"]
    assert [p.column for p in placed] == [0, 0, 1, 1, 2, 2]


def test_reading_order_is_input_order_independent():
    lines = [_line(72, 700, 260, 710, "L1"), _line(72, 680, 260, 690, "L2"),
             _line(320, 700, 500, 710, "R1"), _line(320, 680, 500, 690, "R2")]
    bbox = (72, 680, 500, 710)
    baseline = [p.line.text for p in _reading_order(_xy_cut(lines, bbox))]
    for seed in range(5):
        shuffled = lines[:]
        random.Random(seed).shuffle(shuffled)
        got = [p.line.text for p in _reading_order(_xy_cut(shuffled, bbox))]
        assert got == baseline


def test_marginal_gutter_is_flagged_multi_column_suspected():
    # Gutter width 5pt: above GUTTER_MIN_WIDTH_PT (4pt, so the columns are reconstructed) but below
    # GUTTER_MARGINAL_WIDTH_PT (6pt, so the cut is marginal and the reading order is flagged
    # uncertain).
    left = [_line(72, 300 + i * 20, 250, 310 + i * 20, f"L{i}") for i in range(5)]
    right = [_line(255, 300 + i * 20, 480, 310 + i * 20, f"R{i}") for i in range(5)]
    page = Page(number=1, width=612, height=792, lines=tuple(left + right), images=())
    profile = build_profile([page])
    layout = order_page(page, profile)
    assert "multi-column-suspected" in layout.flags
    # ...but it is still reconstructed: two distinct columns.
    assert {p.column for p in layout.lines if p.column >= 0} == {0, 1}


def test_order_page_excludes_artifacts_and_orders_body():
    body = [_line(72, 700, 500, 710, "para one"), _line(72, 680, 500, 690, "para two")]
    # The footer carries a distinct style; a footer sharing the body style is deliberately never
    # classified as an artifact (the Phase 1 silent-paragraph-deletion guard in build_profile).
    footer1 = _line(240, 40, 320, 50, "page 3", font="Helvetica", size=8.0)
    footer2 = _line(240, 40, 320, 50, "page 4", font="Helvetica", size=8.0)
    p1 = Page(number=1, width=560, height=740,
              lines=tuple(body + [footer1]), images=())
    p2 = Page(number=2, width=560, height=740,
              lines=(_line(72, 700, 500, 710, "more"), footer2),
              images=())
    profile = build_profile([p1, p2])
    layout = order_page(p1, profile)
    body_lines = [p.line.text for p in layout.lines if p.column >= 0]
    artifact_lines = [p.line.text for p in layout.lines if p.column == -1]
    assert body_lines == ["para one", "para two"]
    assert artifact_lines == ["page 3"]
    assert "multi-column-suspected" not in layout.flags


def _grid_line(col, row, text, *, cell_w=60, cell_h=14, x0=80, y_top=700, row_gap=24, col_gap=120):
    # cell_w / col_gap = 0.5: short cells, as in a real table (Failure.pdf's are ~0.5-0.66).
    x = x0 + col * col_gap
    y = y_top - row * row_gap
    return _line(x, y, x + cell_w, y + cell_h, text)


def test_detect_table_finds_a_grid():
    from rebind.layout import detect_table_lines
    # 3 rows x 3 columns of short cells aligned in a grid.
    lines = [_grid_line(c, r, f"r{r}c{c}") for r in range(3) for c in range(3)]
    flagged = detect_table_lines(lines)
    assert len(flagged) == 9, f"all 9 grid cells should be flagged, got {len(flagged)}"


def test_prose_is_not_a_table():
    from rebind.layout import detect_table_lines
    # One long line per row -- ordinary paragraphs, no side-by-side cells.
    lines = [_line(80, 700 - i * 20, 520, 714 - i * 20, f"A full sentence of prose number {i}.")
             for i in range(8)]
    assert detect_table_lines(lines) == set()


def test_single_column_list_is_not_a_table():
    from rebind.layout import detect_table_lines
    # One item per row, all at the same x -- a list, not a grid (no recurring 2nd column).
    lines = [_line(80, 700 - i * 20, 300, 714 - i * 20, f"item {i}") for i in range(6)]
    assert detect_table_lines(lines) == set()


def test_two_column_flowing_text_is_not_a_table_via_order_page():
    # A genuine two-column page layout must not be flagged as a table: order_page runs table
    # detection per column, so the two columns' lines never pair into spurious grid rows.
    left = [_line(72, 700 - i * 20, 250, 714 - i * 20, f"left flowing sentence {i}")
            for i in range(8)]
    right = [_line(320, 700 - i * 20, 500, 714 - i * 20, f"right flowing sentence {i}")
             for i in range(8)]
    page = Page(number=1, width=612, height=792, lines=tuple(left + right), images=())
    profile = build_profile([page])
    layout = order_page(page, profile)
    assert layout.table_line_ids == set(), "two-column layout was mistaken for a table"


def test_a_dense_row_on_established_columns_is_still_a_table_row():
    from rebind.layout import detect_table_lines
    # Real bboxes from a real sample (1429254.pdf, gitignored): a genuine table row whose middle
    # cell has an unusually long value ("Initially pan-endothelial, enriched in arteries at later
    # stages") pushes that ROW's own fill fraction to ~0.90 -- over TABLE_ROW_MAX_FILL (0.8) -- even
    # though its three cells land on exactly the same recurring column positions as every other row.
    # The dense-row filter exists to keep flowing multi-column prose (the 1905 bulletin) from
    # manufacturing a false table; it must not, as a side effect, drop a real row of a real table
    # just because one of its values happens to be longer than its neighbors'. This split one real
    # table into two detected islands, and the second island's first (data) row was then mistagged
    # as a header.
    normal_rows = [
        [_line(62, 236, 110, 244, "Marker genes"),
         _line(124, 236, 200, 244, "Expression pattern"),
         _line(330, 236, 390, 244, "Reference")],
        [_line(61.8, 218, 75.6, 226, "Fli1a"),
         _line(124.2, 218, 175.5, 226, "Pan-endothelial"),
         _line(330.4, 218, 393.8, 226, "Brown et al. (2000)")],
        [_line(61.8, 208, 75.6, 216, "tie2"),
         _line(124.2, 208, 175.5, 216, "Pan-endothelial"),
         _line(330.4, 208, 393.8, 216, "Lyons et al. (1998)")],
    ]
    dense_row = [
        _line(61.7872, 198.0449, 97.5177, 206.039, "Kdrl (flk1)"),
        _line(124.1461, 198.0449, 313.3506, 206.039,
             "Initially pan-endothelial, enriched in arteries at later stages"),
        _line(330.383, 198.0449, 478.2461, 206.039, "(Bussmann et al., 2008); Sumoy et al. (1997)"),
    ]
    lines = [ln for row in normal_rows for ln in row] + dense_row
    flagged = detect_table_lines(lines)
    assert all(id(ln) in flagged for ln in dense_row), (
        "a genuine table row was dropped for being dense, fragmenting the table"
    )


def test_order_page_flags_a_real_table_grid():
    grid = [_grid_line(c, r, f"r{r}c{c}") for r in range(4) for c in range(3)]
    page = Page(number=1, width=612, height=792, lines=tuple(grid), images=())
    profile = build_profile([page])
    layout = order_page(page, profile)
    assert len(layout.table_line_ids) == 12, f"expected 12 table cells, got {len(layout.table_line_ids)}"
