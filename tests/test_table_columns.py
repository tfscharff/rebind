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


def test_a_missing_value_leaves_a_gap_rather_than_shifting_the_row():
    bands = [(50.0, 90.0), (100.0, 140.0), (150.0, 190.0)]
    assert _row_cells([_box(50, 90), _box(150, 190)], bands) == [(0, 0, [0]), (2, 2, [1])]


def test_two_cells_in_one_column_share_it():
    bands = [(50.0, 90.0), (100.0, 140.0)]
    assert _row_cells([_box(50, 60), _box(70, 90)], bands) == [(0, 0, [0, 1])]
