from pathlib import Path

import pikepdf

from rebind.contrast import contrast_ratio, measure
from rebind.extract import extract_pages
from rebind.recolor import (
    _darken,
    _lighten,
    apply_corrections,
    correction_for,
    corrections_for,
)
from tests.fixtures import born_digital_pdf

WHITE = (255, 255, 255)
NEAR_BLACK = (20, 20, 24)


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


def test_text_on_a_dark_background_is_lightened_not_darkened():
    # Reverse video is a real design -- a heading knocked out of a dark banner. Darkening it would
    # turn legible light-on-dark into dark-on-dark, making the document worse while "fixing" it.
    # Which way to move is decided by what is actually behind the text.
    dim = (90, 90, 110)
    assert contrast_ratio(dim, NEAR_BLACK) < 4.5, "fixture must start out failing"
    fixed = _lighten(dim, NEAR_BLACK, 4.5)
    assert contrast_ratio(fixed, NEAR_BLACK) >= 4.5
    assert sum(fixed) > sum(dim), "it must move away from the background, not toward it"
    assert correction_for(dim, NEAR_BLACK, 4.5) == fixed
    assert correction_for(dim, WHITE, 4.5) == _darken(dim, WHITE, 4.5)


def test_pale_text_is_corrected_in_the_real_page(tmp_path: Path):
    source = born_digital_pdf(
        "<p>Ordinary black body text.</p>"
        "<p style='color:#a8a8a8'>Pale grey small print that fails contrast.</p>",
        tmp_path / "in.pdf")
    before = measure(source, list(extract_pages(source)))
    assert not before.ok, "fixture must start out failing"

    out = tmp_path / "out.pdf"
    with pikepdf.open(source) as pdf:
        assert apply_corrections(pdf, pdf.pages[0], corrections_for(before)) > 0
        pdf.save(out)

    after = measure(out, list(extract_pages(out)))
    assert after.ok, [f"{f.text}: {f.ratio}" for f in after.failures]
    # The text itself must be untouched -- this changes colour, nothing else.
    assert [ln.text for ln in extract_pages(out).__next__().lines] == \
           [ln.text for ln in extract_pages(source).__next__().lines]


def test_a_colour_shared_with_artwork_corrects_the_text_and_leaves_the_rule(tmp_path: Path):
    # The old rule was to skip a colour the artwork also used, which left the text failing. Every
    # correction is now made inside a text object and undone at its end, so the same colour can be
    # corrected for the text and still paint the rule at its original shade.
    source = born_digital_pdf(
        "<p style='color:#a8a8a8'>Pale text above a rule of the same colour.</p>"
        "<hr style='border:none;border-top:4pt solid #a8a8a8'>",
        tmp_path / "in.pdf")
    before = measure(source, list(extract_pages(source)))
    assert not before.ok

    out = tmp_path / "out.pdf"
    with pikepdf.open(source) as pdf:
        assert apply_corrections(pdf, pdf.pages[0], corrections_for(before)) > 0
        pdf.save(out)

    after = measure(out, list(extract_pages(out)))
    assert after.ok, [f"{f.text}: {f.ratio}" for f in after.failures]
    # The rule is painted outside any text object, so its colour operator is still in the stream.
    with pikepdf.open(out) as pdf:
        body = b"".join(bytes(s.read_bytes()) for s in
                        ([pdf.pages[0].Contents] if not isinstance(
                            pdf.pages[0].Contents, pikepdf.Array) else pdf.pages[0].Contents))
    assert b"0.658824" in body or b"0.6588" in body, "the artwork's own colour must survive"


def test_a_page_needing_nothing_is_not_rewritten(tmp_path: Path):
    source = born_digital_pdf("<p>Perfectly ordinary black text on white.</p>",
                              tmp_path / "in.pdf")
    with pikepdf.open(source) as pdf:
        report = measure(source, list(extract_pages(source)))
        assert apply_corrections(pdf, pdf.pages[0], corrections_for(report)) == 0
