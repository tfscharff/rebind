from pathlib import Path

import pikepdf

from rebind.contrast import contrast_ratio, measure
from rebind.extract import extract_pages
from rebind.recolor import _darken, darken_failing_text
from tests.fixtures import born_digital_pdf

WHITE = (255, 255, 255)


def test_darkening_reaches_the_threshold_and_keeps_the_hue():
    # A washed-out lilac must come back lilac -- still recognizably the document's own styling,
    # just dark enough to read. Luminance is scaled in linear light, so the ratio of the colour
    # channels to one another is preserved.
    pale = (162, 162, 209)
    fixed = _darken(pale, WHITE, 4.5)
    assert contrast_ratio(fixed, WHITE) >= 4.5
    assert fixed[2] > fixed[0], "the blue cast should survive"
    assert abs(fixed[0] - fixed[1]) <= 2, "the red/green balance should survive"


def test_a_colour_already_passing_is_left_exactly_alone():
    assert _darken((0, 0, 0), WHITE, 4.5) == (0, 0, 0)
    dark_enough = (80, 80, 80)
    assert contrast_ratio(dark_enough, WHITE) >= 4.5
    assert _darken(dark_enough, WHITE, 4.5) == dark_enough


def test_pale_text_is_darkened_in_the_real_page(tmp_path: Path):
    source = born_digital_pdf(
        "<p>Ordinary black body text.</p>"
        "<p style='color:#a8a8a8'>Pale grey small print that fails contrast.</p>",
        tmp_path / "in.pdf")
    before = measure(source, list(extract_pages(source)))
    assert not before.ok, "fixture must start out failing"

    out = tmp_path / "out.pdf"
    with pikepdf.open(source) as pdf:
        assert darken_failing_text(pdf, pdf.pages[0]) > 0
        pdf.save(out)

    after = measure(out, list(extract_pages(out)))
    assert after.ok, [f"{f.text}: {f.ratio}" for f in after.failures]
    # The text itself must be untouched -- this changes colour, nothing else.
    assert [ln.text for ln in extract_pages(out).__next__().lines] == \
           [ln.text for ln in extract_pages(source).__next__().lines]


def test_a_colour_shared_with_artwork_is_not_touched(tmp_path: Path):
    # A pale colour used for both text and a drawn rule is left alone: recolouring it would restyle
    # the artwork too, which is a change nobody asked for.
    source = born_digital_pdf(
        "<p style='color:#a8a8a8'>Pale text above a rule of the same colour.</p>"
        "<hr style='border:none;border-top:4pt solid #a8a8a8'>",
        tmp_path / "in.pdf")
    with pikepdf.open(source) as pdf:
        changed = darken_failing_text(pdf, pdf.pages[0])
    assert changed == 0


def test_a_page_needing_nothing_is_not_rewritten(tmp_path: Path):
    source = born_digital_pdf("<p>Perfectly ordinary black text on white.</p>",
                              tmp_path / "in.pdf")
    with pikepdf.open(source) as pdf:
        assert darken_failing_text(pdf, pdf.pages[0]) == 0
