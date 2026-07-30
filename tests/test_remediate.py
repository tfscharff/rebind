"""Tests for in-place remediation: preserve the original, add accessibility."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pypdfium2 as pdfium

from rebind.remediate import remediate
from tests.fixtures import born_digital_pdf, pdf_image_only_scan


def _selectable_text(pdf_path: Path) -> str:
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        return " ".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    finally:
        doc.close()


def test_born_digital_is_copied_verbatim_with_metadata(tmp_path: Path):
    # A PDF that already has text is left byte-for-byte as its pages were; only accessibility
    # metadata is added, and no page is re-OCR'd.
    source = born_digital_pdf("<h1>Chapter One</h1><p>The body text is here.</p>",
                              tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"

    result = remediate(source, out, title="My Title", lang="en")

    assert result.ocr_pages == ()          # nothing needed recognizing
    assert result.added_text_layer is False
    with pikepdf.open(out) as pdf:
        assert bool(pdf.Root.MarkInfo.Marked)
        assert str(pdf.Root.Lang) == "en"
        assert str(pdf.docinfo["/Title"]) == "My Title"
    # The original text survives and is still selectable.
    assert "body text" in _selectable_text(out)


def test_scanned_page_gets_an_invisible_text_layer(tmp_path: Path):
    # An image-only scan has no text; remediation OCRs it and adds a selectable text layer over
    # the untouched image.
    source = pdf_image_only_scan(
        "<h1>Fearless Organization</h1><p>Preventable failure is avoidable.</p>",
        tmp_path / "scan.pdf",
    )
    out = tmp_path / "out.pdf"

    result = remediate(source, out, title="Scan")

    assert result.ocr_pages == (1,) and result.added_text_layer is True
    text = _selectable_text(out).lower()
    assert "preventable" in text
    with pikepdf.open(out) as pdf:
        assert bool(pdf.Root.MarkInfo.Marked)


def test_output_page_count_matches_source(tmp_path: Path):
    source = born_digital_pdf("<p>one</p><p style='page-break-before:always'>two</p>",
                              tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    result = remediate(source, out)
    with pikepdf.open(source) as a, pikepdf.open(out) as b:
        assert len(b.pages) == len(a.pages) == result.page_count


def test_remediated_output_is_tagged_and_pdf_ua_compliant(tmp_path: Path, verapdf_exe: Path):
    """The whole point: the output is a real PDF/UA document, not just a PDF with text on it."""
    from rebind.validate import validate_pdf_ua

    source = born_digital_pdf("<h1>Title</h1><p>A paragraph of body text.</p>", tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="A Title")

    with pikepdf.open(out) as pdf:
        assert "/StructTreeRoot" in pdf.Root
        assert bool(pdf.Root.MarkInfo.Marked)

    result = validate_pdf_ua(out, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()


def test_born_digital_headings_are_tagged_as_headings(tmp_path: Path):
    source = born_digital_pdf(
        "<h1>Chapter One</h1><p>Body paragraph here.</p><h2>A Section</h2><p>More body.</p>",
        tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    with pikepdf.open(out) as pdf:
        tags = [str(elem.S) for elem in pdf.Root.StructTreeRoot.K[0].K]
    assert "/H1" in tags and "/H2" in tags and "/P" in tags
    # A heading must not skip a level: the first heading is H1.
    headings = [t for t in tags if t.startswith("/H")]
    assert headings[0] == "/H1"


def test_figure_is_decorative_until_described(tmp_path: Path):
    from tests.fixtures import born_digital_pdf_with_image
    source = born_digital_pdf_with_image(tmp_path / "in.pdf")

    result = remediate(source, tmp_path / "out.pdf")
    assert len(result.figures) == 1
    fig = result.figures[0]
    assert fig["thumb"].startswith("data:image/png;base64,")
    with pikepdf.open(tmp_path / "out.pdf") as pdf:
        assert not any(str(e.get("/S")) == "/Figure" for e in pdf.Root.StructTreeRoot.K[0].K)

    described = remediate(source, tmp_path / "out2.pdf",
                          alt_texts={fig["id"]: "A red bar chart of sales."})
    assert described.figures == ()
    with pikepdf.open(tmp_path / "out2.pdf") as pdf:
        figs = [e for e in pdf.Root.StructTreeRoot.K[0].K if str(e.get("/S")) == "/Figure"]
        assert len(figs) == 1 and str(figs[0].get("/Alt")) == "A red bar chart of sales."
