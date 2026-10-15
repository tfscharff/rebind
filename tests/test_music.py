"""Printed music: finding staves on a page, grouping them into examples, and the drafted description.

Staves are drawn here as plain pixels (or, end to end, as an SVG on a born-digital page), at the
resolution `music` looks at pages: what it reads is geometry, so geometry is what is tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pikepdf

from rebind import music
from tests.fixtures import born_digital_pdf

DPI = music.SCAN_DPI
PAGE_W, PAGE_H = 612.0, 792.0
PX_W, PX_H = int(PAGE_W * DPI / 72), int(PAGE_H * DPI / 72)
SPACE = 14          # pixels between stave lines: about 5pt, an ordinary music example's size


@dataclass
class Line:
    """Stands in for a page's text line: what `music` reads of one."""

    text: str
    bbox: tuple[float, float, float, float]


def _page() -> np.ndarray:
    return np.full((PX_H, PX_W), 255, dtype=np.uint8)


def _stave(page: np.ndarray, x0: int, x1: int, top: int, space: int = SPACE,
           lines: int = 5) -> None:
    for k in range(lines):
        y = top + k * space
        page[y:y + 2, x0:x1] = 0


def _treble(page: np.ndarray, x: int, top: int) -> None:
    """A mark shaped like a treble clef where it counts: reaching well above and below the stave."""
    page[top - 2 * SPACE:top + 6 * SPACE, x:x + int(1.6 * SPACE)] = 0


def _bass(page: np.ndarray, x: int, top: int) -> None:
    """...and a bass clef: hanging from the top line, stopping a space short of the bottom."""
    page[top:top + 3 * SPACE, x:x + int(1.6 * SPACE)] = 0


def _px_to_pt(x: float, y: float) -> tuple[float, float]:
    return x * 72 / DPI, PAGE_H - y * 72 / DPI


def _text(text: str, x0: int, y0: int, x1: int, y1: int) -> Line:
    """A text line given in pixels (top-down), as the page's PDF-point box."""
    a, top = _px_to_pt(x0, y0)
    b, bottom = _px_to_pt(x1, y1)
    return Line(text, (a, bottom, b, top))


# ---- Staves ------------------------------------------------------------------------------------

def test_five_evenly_spaced_lines_are_a_stave():
    page = _page()
    _stave(page, 200, 1400, 600)
    staves = music.find_staves(page, DPI)
    assert len(staves) == 1
    staff = staves[0]
    assert abs(staff.space - SPACE) < 1 and abs(staff.top - 600) < 2
    assert abs(staff.x0 - 200) < 6 and abs(staff.x1 - 1400) < 6


def test_a_tilted_scan_still_has_its_staves_found():
    import cv2

    page = _page()
    _stave(page, 200, 1400, 600)
    _stave(page, 200, 1400, 900)
    matrix = cv2.getRotationMatrix2D((PX_W / 2, PX_H / 2), 0.8, 1.0)
    tilted = cv2.warpAffine(page, matrix, (PX_W, PX_H), borderValue=255)
    straight, _angle = music.straighten(tilted, DPI)
    assert len(music.find_staves(straight, DPI)) == 2


def test_four_or_six_lines_are_not_a_stave():
    page = _page()
    _stave(page, 200, 1400, 400, lines=4)
    _stave(page, 200, 1400, 900, lines=6)       # ruled paper, or a ruled table
    assert music.find_staves(page, DPI) == []


def test_lines_spaced_like_text_are_not_a_stave():
    page = _page()
    _stave(page, 200, 1400, 400, space=int(14 * DPI / 72))     # 14pt apart: a ruled form
    assert music.find_staves(page, DPI) == []


def test_two_examples_side_by_side_on_one_line_are_two_staves():
    page = _page()
    _stave(page, 200, 700, 600)
    _stave(page, 760, 1400, 600)                # four spaces of clear paper between them
    assert len(music.find_staves(page, DPI)) == 2


def test_a_faded_break_in_a_stave_does_not_split_it():
    page = _page()
    _stave(page, 200, 1400, 600)
    page[600:600 + 5 * SPACE, 700:715] = 255    # a stretch where the print has worn away
    assert len(music.find_staves(page, DPI)) == 1


# ---- Examples -----------------------------------------------------------------------------------

def _examples(page: np.ndarray, lines=()) -> list[music.Example]:
    return music.find_examples(page, DPI, PAGE_W, PAGE_H, list(lines),
                               keep_out=lambda line: music.is_label(line.text)).examples


def _clefs(page: np.ndarray, *tops: int) -> None:
    for top in tops:
        _treble(page, 215, top)


def test_a_piano_staff_is_one_example_with_a_box_around_its_staves():
    page = _page()
    _stave(page, 200, 1400, 600)
    _stave(page, 200, 1400, 720)
    _clefs(page, 600, 720)
    examples = _examples(page)
    assert len(examples) == 1 and len(examples[0].staves) == 2
    x0, y0, x1, y1 = examples[0].bbox
    left, top = _px_to_pt(200, 600)
    right, bottom = _px_to_pt(1400, 720 + 4 * SPACE)
    assert x0 <= left and x1 >= right - 2 and y1 >= top and y0 <= bottom


def test_prose_between_two_staves_makes_them_two_examples():
    page = _page()
    _stave(page, 200, 1400, 600)
    _stave(page, 200, 1400, 760)
    _clefs(page, 600, 760)
    sentence = _text("The same progression, with the third in the bass:", 150, 700, 1300, 730)
    page[700:730, 150:1300] = 0
    assert len(_examples(page, [sentence])) == 2


def test_figures_and_a_lyric_between_two_staves_keep_them_one_example():
    page = _page()
    _stave(page, 200, 1400, 600)
    _stave(page, 200, 1400, 760)
    _clefs(page, 600, 760)
    lyric = _text("Mi - se - re - re me - i De - us", 260, 700, 1300, 730)
    figures = _text("6 5", 400, 670, 440, 690)
    assert len(_examples(page, [lyric, figures])) == 1


def test_staves_with_no_clef_are_not_taken_for_music():
    # Five evenly spaced hairlines and nothing to open them: on a real book, the brickwork of an
    # architectural drawing.
    page = _page()
    _stave(page, 200, 1400, 600)
    assert _examples(page) == []


def test_lines_of_text_are_not_a_stave():
    page = _page()
    for k in range(5):          # text: bands half as thick as their spacing
        page[600 + k * 20:600 + k * 20 + 10, 200:1400] = 0
    assert music.find_staves(page, DPI) == []


def test_a_ruled_table_with_words_in_its_rows_is_not_music():
    page = _page()
    _stave(page, 200, 1400, 600, space=24)
    rows = [_text("Totals carried forward", 220, 600 + k * 24 + 4, 700, 600 + k * 24 + 20)
            for k in range(4)]
    assert _examples(page, rows) == []


# ---- The draft ----------------------------------------------------------------------------------

def _describe(page: np.ndarray, lines=()) -> list[str]:
    found = music.find_examples(page, DPI, PAGE_W, PAGE_H, list(lines))
    return [music.describe(example, found) for example in found.examples]


def test_a_piano_staff_is_described_by_its_staves_and_clefs():
    page = _page()
    _stave(page, 200, 1400, 600)
    _stave(page, 200, 1400, 720)
    _treble(page, 215, 600)
    _bass(page, 215, 720)
    assert _describe(page) == ["Music on two staves: treble and bass clefs."]


def test_systems_are_told_apart_by_their_clefs_repeating():
    page = _page()
    for top, clef in ((500, _treble), (620, _bass), (760, _treble), (880, _bass)):
        _stave(page, 200, 1400, top)
        clef(page, 215, top)
    assert _describe(page) == ["Music in two lines, each on two staves: treble and bass clefs."]


def test_a_clef_that_cannot_be_read_is_left_out_of_the_draft():
    page = _page()
    _stave(page, 200, 1400, 600)
    _stave(page, 200, 1400, 720)
    _treble(page, 215, 600)                     # the lower stave's clef is missing
    assert _describe(page) == ["Music on two staves."]


def test_a_single_stave():
    page = _page()
    _stave(page, 200, 1400, 600)
    _treble(page, 215, 600)
    assert _describe(page) == ["Music on one stave, treble clef."]


def test_an_example_label_is_recognised_and_cleaned():
    assert music.is_label("Ex. 24.") and music.is_label("Example 3") and music.is_label("Mus. ex. 2")
    assert not music.is_label("Example of pure composition")
    example = music.Example(bbox=(100.0, 400.0, 500.0, 460.0))
    above = Line("Ex.  24.", (100.0, 464.0, 130.0, 474.0))
    assert music.label_for(example, [above]) == "Ex. 24."
    junk = Line("Ex. 242.^m^^^s^m", (90.0, 420.0, 500.0, 450.0))     # run on into the stave
    assert music.label_for(example, [junk]) == "Ex. 242."
    titled = Line("Example 3. Beethoven, Sonata Op. 27 No. 2", (100.0, 464.0, 400.0, 474.0))
    assert music.label_for(example, [titled]) == "Example 3. Beethoven, Sonata Op. 27 No. 2."
    far = Line("Ex. 23.", (100.0, 520.0, 130.0, 530.0))              # a previous example's
    assert music.label_for(example, [far]) == ""


# ---- End to end ---------------------------------------------------------------------------------

def _staves_svg(*, figures: str = "") -> str:
    """A two-stave example drawn as vector lines, the way a born-digital article carries one."""
    lines = []
    for top in (10, 70):
        for k in range(5):
            y = top + 6 * k
            lines.append(f"<line x1='10' y1='{y}' x2='400' y2='{y}' stroke='black' "
                         f"stroke-width='0.8'/>")
    lines.append("<rect x='14' y='2' width='9' height='44' fill='black'/>")      # treble
    lines.append("<rect x='14' y='70' width='9' height='18' fill='black'/>")     # bass
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='420' height='110' "
            f"viewBox='0 0 420 110'>{''.join(lines)}{figures}</svg>")


def test_a_music_example_becomes_a_figure_with_a_drafted_description(tmp_path: Path):
    """End to end: the example is a figure for the person to describe, opened with its own label
    and what Rebind read off its staves; nothing inside its staves is read out as text; and the
    draft never reaches the document on its own."""
    from rebind.remediate import remediate

    figures = "<text x='200' y='60' font-size='9'>6 5</text>"
    source = born_digital_pdf(
        "<p>The perfect cadence is V followed by I, and is the usual close.</p>"
        "<p>Ex. 24.</p>" + _staves_svg(figures=figures) +
        "<p>Note that in every case the leading note rises to the tonic.</p>",
        tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    result = remediate(source, out, title="T")

    music_figures = [e for e in result.elements if e.get("music")]
    assert len(music_figures) == 1, result.elements
    draft = next(f["alt_guess"] for f in result.figures if f["id"] == music_figures[0]["id"])
    assert draft == "Ex. 24. Music on two staves: treble and bass clefs."
    assert not any(e["text"].strip() == "6 5" for e in result.elements)
    with pikepdf.open(out) as pdf:
        tagged = [e for e in pdf.Root.StructTreeRoot.K[0].K if str(e.S) == "/Figure"]
    assert tagged == []
    assert result.structure_ok

    described = remediate(source, tmp_path / "described.pdf", title="T",
                          alt_texts={music_figures[0]["id"]: "A perfect cadence in C major."})
    assert not any(f["id"] == music_figures[0]["id"] for f in described.figures)
    with pikepdf.open(tmp_path / "described.pdf") as pdf:
        alts = [str(e.Alt) for e in pdf.Root.StructTreeRoot.K[0].K if str(e.S) == "/Figure"]
    assert alts == ["A perfect cadence in C major."]
