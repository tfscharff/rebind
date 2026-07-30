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


def _all_struct_tags(pdf: pikepdf.Pdf) -> list[str]:
    """Every structure-element type in the tree, in a depth-first walk (e.g. '/H1', '/Table')."""
    out: list[str] = []

    def walk(elem: pikepdf.Object) -> None:
        s = elem.get("/S")
        if s is not None:
            out.append(str(s))
        kids = elem.get("/K")
        if isinstance(kids, pikepdf.Array):
            for kid in kids:
                if isinstance(kid, pikepdf.Dictionary) and kid.get("/Type") == pikepdf.Name.StructElem:
                    walk(kid)
        elif isinstance(kids, pikepdf.Dictionary) and kids.get("/Type") == pikepdf.Name.StructElem:
            walk(kids)

    for kid in pdf.Root.StructTreeRoot.K:
        walk(kid)
    return out


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


def test_ocr_heading_recovered_from_scan(tmp_path: Path):
    # A scan with a large, isolated title and full-width body: the title should be recovered as a
    # heading from OCR (size + isolation + shortness), where before every OCR line was a paragraph.
    source = pdf_image_only_scan(
        "<h1 style='font-size:34pt'>Annual Report</h1>"
        "<p>This first paragraph of ordinary body text runs the full width of the column, so it is "
        "plainly not a heading despite whatever height OCR assigns its box.</p>"
        "<p>A second ordinary paragraph of body text follows here, again spanning the full width "
        "of the text column beneath the title above it.</p>",
        tmp_path / "scan.pdf",
    )
    out = tmp_path / "out.pdf"
    result = remediate(source, out, title="R")

    assert result.ocr_pages == (1,)
    with pikepdf.open(out) as pdf:
        tags = _all_struct_tags(pdf)
    assert "/H1" in tags, tags


def test_ocr_body_only_scan_invents_no_headings(tmp_path: Path):
    # Uniform body text with no title must not manufacture headings from OCR box-height noise
    # (the pernambuco/Failure.pdf regression: an over-tall body line is not a heading).
    source = pdf_image_only_scan(
        "<p>The first paragraph of body text spans the full width of the column here.</p>"
        "<p>The second paragraph of body text also spans the full width of the column.</p>"
        "<p>The third paragraph of body text continues at the same size across the column.</p>",
        tmp_path / "scan.pdf",
    )
    out = tmp_path / "out.pdf"
    remediate(source, out, title="B")
    with pikepdf.open(out) as pdf:
        tags = _all_struct_tags(pdf)
    assert not any(t.startswith("/H") for t in tags), tags


def test_table_is_fully_tagged_with_header_cells(tmp_path: Path):
    from tests.fixtures import born_digital_pdf_with_table
    source = born_digital_pdf_with_table(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    with pikepdf.open(out) as pdf:
        tags = _all_struct_tags(pdf)
        # The table and its parts are present.
        assert "/Table" in tags and "/TR" in tags and "/TD" in tags
        # The header row is tagged as header cells with a column scope, not plain data cells.
        table = next(e for e in pdf.Root.StructTreeRoot.K[0].K if str(e.get("/S")) == "/Table")
        rows = [tr for tr in table.K if str(tr.get("/S")) == "/TR"]
        assert len(rows) >= 3
        header_cells = [c for c in rows[0].K if str(c.get("/S")) == "/TH"]
        assert len(header_cells) >= 3, "first row should be header cells"
        assert str(header_cells[0].A.Scope) == "/Column"
        # The grid is regular: every row has the same number of cells.
        widths = {len(list(tr.K)) for tr in rows}
        assert len(widths) == 1, f"irregular table: rows have {widths} cells"


def test_sparse_table_row_is_kept_as_a_row(tmp_path: Path):
    # A subtotal-style row with an empty middle cell must not fragment the table or vanish: it stays
    # one table, and the sparse row is a /TR with an empty cell filling the gap.
    from tests.fixtures import born_digital_pdf_with_sparse_row_table
    source = born_digital_pdf_with_sparse_row_table(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    with pikepdf.open(out) as pdf:
        tables = [e for e in pdf.Root.StructTreeRoot.K[0].K if str(e.get("/S")) == "/Table"]
        assert len(tables) == 1, f"table fragmented into {len(tables)}"
        rows = [tr for tr in tables[0].K if str(tr.get("/S")) == "/TR"]
        assert len(rows) == 5, f"expected 5 rows (header + 4 data), got {len(rows)}"
        assert {len(list(tr.K)) for tr in rows} == {3}, "every row should have 3 cells"
    # The sparse row's values survive and are selectable.
    text = _selectable_text(out)
    assert "West" in text and "South" in text


def test_tagged_table_is_pdf_ua_compliant(tmp_path: Path, verapdf_exe: Path):
    from rebind.validate import validate_pdf_ua

    from tests.fixtures import born_digital_pdf_with_table
    source = born_digital_pdf_with_table(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    result = validate_pdf_ua(out, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()


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
