"""A table's cells land in the column they are printed in, whatever their alignment."""

from __future__ import annotations

from rebind.tables import _column_bands, _row_cells


def _box(x0: float, x1: float, y: float = 0.0) -> tuple:
    return (x0, y, x1, y + 10.0)


def test_right_aligned_numbers_share_a_column_with_their_heading():
    heading = _box(100, 200)
    numbers = [_box(170, 200, 20), _box(150, 200, 40)]
    assert len(_column_bands([heading, *numbers])) == 1


def test_centred_cells_of_different_widths_are_one_column():
    assert len(_column_bands([_box(100, 200), _box(130, 170, 20), _box(145, 155, 40)])) == 1


def test_separate_columns_stay_separate():
    boxes = [_box(50, 90), _box(100, 140), _box(150, 190),
             _box(50, 80, 20), _box(110, 140, 20), _box(150, 170, 20)]
    assert len(_column_bands(boxes)) == 3


def test_columns_that_touch_by_a_sliver_stay_separate():
    assert len(_column_bands([_box(50, 100), _box(99, 150)])) == 2


def test_a_heading_over_several_columns_does_not_merge_them():
    spanning = _box(50, 190)
    below = [_box(50, 90, 20), _box(100, 140, 20), _box(150, 190, 20)]
    bands = _column_bands([spanning, *below])
    assert len(bands) == 3
    assert _row_cells([spanning], bands) == [(0, 2, [0])]
    assert _row_cells(below, bands) == [(0, 0, [0]), (1, 1, [1]), (2, 2, [2])]


def test_a_heading_stopping_just_inside_the_second_column_does_not_stretch_the_first():
    # Clio's "Sleep latency" over Scores (178-201) and Frequency (215-251).
    boxes = [_box(178, 224), _box(178, 201, 20), _box(215, 251, 20), _box(179, 183, 40),
             _box(215, 224, 40)]
    bands = _column_bands(boxes)
    assert [(round(a), round(z)) for a, z in bands] == [(178, 201), (215, 251)]
    assert _row_cells(boxes[3:], bands) == [(0, 0, [0]), (1, 1, [1])]


def test_a_heading_broken_into_two_runs_is_not_two_columns():
    boxes = [_box(100, 120), _box(122, 180), _box(140, 180, 20), _box(150, 180, 40)]
    assert len(_column_bands(boxes)) == 1


def _row(*cells, y=0.0):
    from rebind.extract import TextLine

    return [TextLine(text=text, page=1, bbox=(x0, y, x1, y + 10.0), font="F", size=10.0,
                     bold=False, italic=False) for text, x0, x1 in cells]


def test_a_row_of_group_labels_makes_the_next_row_a_header_too():
    from rebind.tables import _layout, default_header_rows

    rows = [_row(("Sleep latency", 50, 96), ("Sleep difficulty", 200, 250), y=60),
            _row(("Scores", 50, 80), ("Frequency", 100, 140), ("Percent", 150, 180),
                 ("Scores", 200, 230), ("Frequency", 250, 290), ("Percent", 300, 330), y=40),
            _row(("0", 50, 55), ("141", 100, 112), ("27", 150, 158),
                 ("0", 200, 205), ("28", 250, 258), ("5", 300, 305), y=20),
            _row(("1", 50, 55), ("195", 100, 112), ("37", 150, 158),
                 ("1", 200, 205), ("379", 250, 262), ("72", 300, 308), y=0)]
    assert default_header_rows(rows) == {0, 1}
    _columns, _placed, groups = _layout(rows)
    assert [(first, last) for first, last, _held in groups] == [(0, 2), (3, 5)]


def test_an_ordinary_table_has_one_header_row():
    from rebind.tables import default_header_rows

    rows = [_row(("Name", 50, 90), ("Count", 100, 140), ("Share", 150, 190), y=40),
            _row(("Apples", 50, 85), ("12", 100, 110), ("40", 150, 160), y=20),
            _row(("Pears", 50, 80), ("18", 100, 110), ("60", 150, 160), y=0)]
    assert default_header_rows(rows) == {0}


def test_a_missing_value_leaves_a_gap_rather_than_shifting_the_row():
    bands = [(50.0, 90.0), (100.0, 140.0), (150.0, 190.0)]
    assert _row_cells([_box(50, 90), _box(150, 190)], bands) == [(0, 0, [0]), (2, 2, [1])]


def test_two_cells_in_one_column_share_it():
    bands = [(50.0, 90.0), (100.0, 140.0)]
    assert _row_cells([_box(50, 60), _box(70, 90)], bands) == [(0, 0, [0, 1])]


def _cell(text: str, x0: float, x1: float, top: float, height: float = 9.0):
    from rebind.extract import TextLine
    return TextLine(text=text, page=1, bbox=(x0, top - height, x1, top), font="F", size=height,
                    bold=False, italic=False)


def _texts(rows) -> list[list[str]]:
    return [[line.text for _key, line in row] for row in rows]


def test_a_row_whose_cells_wrap_is_one_row():
    # A journal's summary of studies, no rules between rows: each row's cells run down as many
    # lines as each needs, and the first column's wrapped lines hang under its first.
    from rebind.tables import _table_rows

    printed = [
        ("First author,", 10, 0, 100), ("Design", 80, 1, 100), ("Participants", 150, 2, 100),
        ("year", 10, 3, 91),
        ("Campbell et al.,", 10, 4, 80), ("Randomized", 80, 5, 80), ("Mothers: 494", 150, 6, 80),
        ("2007 (United", 16, 7, 71), ("trial", 80, 8, 71), ("pregnant women", 150, 9, 71),
        ("States)", 16, 10, 62), ("with a friend", 150, 11, 62),
        ("Edwards et al.,", 10, 12, 50), ("Cohort", 80, 13, 50), ("Mothers: 248", 150, 14, 50),
        ("2013 (Canada)", 16, 15, 41), ("low-income", 150, 16, 41),
    ]
    cells = [(key, _cell(text, x, x + 55, top)) for text, x, key, top in printed]
    assert _texts(_table_rows(cells)) == [
        ["First author,", "Design", "Participants", "year"],
        ["Campbell et al.,", "Randomized", "Mothers: 494", "2007 (United", "trial",
         "pregnant women", "States)", "with a friend"],
        ["Edwards et al.,", "Cohort", "Mothers: 248", "2013 (Canada)", "low-income"],
    ]


def test_a_table_of_one_line_rows_keeps_every_row():
    from rebind.tables import _table_rows

    rows = [("Region", "Sales", "Growth"), ("North", "120", "8"), ("", "95", "3"),
            ("East", "140", "12")]
    cells = [(k, _cell(text, 10 + 70 * c, 60 + 70 * c, 100 - 12 * r))
             for r, row in enumerate(rows) for c, text in enumerate(row) if text
             for k in [r * 3 + c]]
    assert len(_table_rows(cells)) == 4
