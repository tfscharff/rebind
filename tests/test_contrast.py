from pathlib import Path

from rebind.contrast import (
    AA_LARGE_RATIO,
    AA_NORMAL_RATIO,
    contrast_ratio,
    measure,
    relative_luminance,
    required_ratio,
)
from rebind.extract import TextLine, extract_pages
from tests.fixtures import born_digital_pdf


def test_wcag_reference_values():
    # WCAG 2.1's own worked numbers: black on white is exactly 21:1, and a colour against itself
    # is 1:1. If the luminance formula drifts, these move first.
    assert round(contrast_ratio((0, 0, 0), (255, 255, 255)), 2) == 21.0
    assert contrast_ratio((90, 90, 90), (90, 90, 90)) == 1.0
    assert relative_luminance((255, 255, 255)) == 1.0
    assert relative_luminance((0, 0, 0)) == 0.0
    # Order must not matter -- the ratio is defined lighter-over-darker either way round.
    assert contrast_ratio((10, 20, 30), (200, 200, 200)) == contrast_ratio(
        (200, 200, 200), (10, 20, 30))


def test_large_text_uses_the_lower_threshold():
    # SC 1.4.3 relaxes to 3:1 for large text: 18pt, or 14pt when bold.
    def line(size, bold):
        return TextLine(text="x", page=1, bbox=(0, 0, 10, 10), font="F", size=size, bold=bold,
                        italic=False)

    assert required_ratio(line(18.0, False)) == AA_LARGE_RATIO
    assert required_ratio(line(14.0, True)) == AA_LARGE_RATIO
    assert required_ratio(line(14.0, False)) == AA_NORMAL_RATIO
    assert required_ratio(line(11.0, True)) == AA_NORMAL_RATIO


def test_black_on_white_body_text_passes(tmp_path: Path):
    source = born_digital_pdf("<h1>Title</h1><p>Ordinary black body text on white paper.</p>",
                              tmp_path / "in.pdf")
    report = measure(source, list(extract_pages(source)))
    assert report.measured > 0
    assert report.ok, [f"{f.text}: {f.ratio}" for f in report.failures]


def test_pale_grey_text_is_reported_with_its_measured_ratio(tmp_path: Path):
    # Light grey on white is the single most common real contrast failure (small print, captions,
    # footnote markers -- all three occur in the real sample). #aaaaaa on white is ~2.3:1.
    source = born_digital_pdf(
        "<p>Readable black paragraph.</p>"
        "<p style='color:#aaaaaa'>Barely visible grey small print here.</p>",
        tmp_path / "in.pdf")
    report = measure(source, list(extract_pages(source)))
    assert not report.ok
    failing = " ".join(f.text for f in report.failures)
    assert "grey small print" in failing
    assert "Readable black paragraph" not in failing
    assert all(f.ratio < AA_NORMAL_RATIO for f in report.failures)


def test_the_declared_ink_is_used_not_a_sample_of_the_glyphs(tmp_path: Path):
    # Body text is thin enough that nearly every pixel of a glyph is an anti-aliased blend, so
    # sampling reads pure black as mid-grey. On a real sample that turned a document with no
    # contrast problem into forty-three reported failures. The page declares the colour; use it.
    source = born_digital_pdf(
        "<p style='font-size:9pt'>Small pure-black body text, thin strokes and all.</p>",
        tmp_path / "in.pdf")
    pages = list(extract_pages(source))
    assert pages[0].lines[0].color == (0, 0, 0), "the declared ink should be read from the page"

    report = measure(source, pages)
    assert report.lowest is not None
    assert report.lowest.ink == (0, 0, 0)
    assert report.lowest.ratio == 21.0, "black on white is 21:1, not whatever the pixels blended to"


def test_text_recovered_by_ocr_is_not_measured(tmp_path: Path):
    # An OCR'd line declares no colour, because it *is* the picture: sampling it measures the
    # photocopier rather than any colour decision the document made, exactly as for text inside a
    # figure. It also cannot be corrected -- repainting it would mean altering the scan -- and
    # contrast is a check Rebind settles rather than hands back, so measuring what it could only
    # report and never fix would be reporting for its own sake.
    from rebind.extract import Page

    source = born_digital_pdf("<p>Ordinary black body text on white.</p>", tmp_path / "in.pdf")
    pages = list(extract_pages(source))
    stripped = [
        Page(number=p.number, width=p.width, height=p.height,
             lines=tuple(TextLine(text=ln.text, page=ln.page, bbox=ln.bbox, font=ln.font,
                                  size=ln.size, bold=ln.bold, italic=ln.italic,
                                  ocr_confidence=0.9, color=None)
                         for ln in p.lines),
             images=p.images)
        for p in pages
    ]
    report = measure(source, stripped)
    assert report.measured == 0
    assert report.ok


def test_text_inside_a_figure_is_not_measured(tmp_path: Path):
    # A label burnt into a photograph is part of the image, described by the figure's alt text.
    # Measuring it samples the photograph's colours, not any choice the document made.
    source = born_digital_pdf("<p style='color:#aaaaaa'>Faint label text.</p>",
                              tmp_path / "in.pdf")
    pages = list(extract_pages(source))
    assert not measure(source, pages).ok, "fixture should fail when not excluded"
    whole_page = (0.0, 0.0, pages[0].width, pages[0].height)
    assert measure(source, pages, figures={1: (whole_page,)}).ok
