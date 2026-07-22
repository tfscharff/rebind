from pathlib import Path

from rebind.inspect import page_labels
from rebind.pagelabels import set_page_labels
from rebind.render import render_html_to_pdf
from rebind.validate import validate_pdf_ua

TWO_PAGE_HTML = """
<h1>Front matter</h1>
<p>First page content.</p>
<p style="break-before: page;">Second page content.</p>
"""


def test_page_labels_round_trip(tmp_path: Path):
    target = tmp_path / "labelled.pdf"
    render_html_to_pdf(TWO_PAGE_HTML, target, title="Labelled", lang="en")

    set_page_labels(target, ["47", "48"])

    assert page_labels(target) == ["47", "48"]


def test_page_labels_do_not_break_conformance(tmp_path: Path, verapdf_exe: Path):
    target = tmp_path / "labelled.pdf"
    render_html_to_pdf(TWO_PAGE_HTML, target, title="Labelled", lang="en")
    set_page_labels(target, ["47", "48"])

    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()


def test_roman_and_arabic_labels_are_both_supported(tmp_path: Path):
    """Front matter is numbered i, ii; the body restarts at 1."""
    target = tmp_path / "mixed.pdf"
    render_html_to_pdf(TWO_PAGE_HTML, target, title="Mixed", lang="en")

    set_page_labels(target, ["ix", "1"])

    assert page_labels(target) == ["ix", "1"]
