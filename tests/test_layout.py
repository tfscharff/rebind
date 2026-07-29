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
    # Two blocks 2pt apart horizontally -- below GUTTER_MIN_FRACTION, must stay one column.
    a = [_line(72, 700, 260, 710, "A1"), _line(72, 680, 260, 690, "A2")]
    b = [_line(262, 700, 400, 710, "B1"), _line(262, 680, 400, 690, "B2")]
    region = _xy_cut(a + b, (72, 680, 400, 710))
    placed = _reading_order(region)
    assert {p.column for p in placed} == {0}


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
