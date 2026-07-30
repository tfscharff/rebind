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
