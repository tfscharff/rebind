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


def test_text_inside_a_figure_is_not_measured(tmp_path: Path):
    # A label burnt into a photograph is part of the image, described by the figure's alt text.
    # Measuring it samples the photograph's colours, not any choice the document made.
    source = born_digital_pdf("<p style='color:#aaaaaa'>Faint label text.</p>",
                              tmp_path / "in.pdf")
    pages = list(extract_pages(source))
    assert not measure(source, pages).ok, "fixture should fail when not excluded"
    whole_page = (0.0, 0.0, pages[0].width, pages[0].height)
    assert measure(source, pages, figures={1: (whole_page,)}).ok
