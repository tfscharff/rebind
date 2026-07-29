import inspect
from pathlib import Path

import pikepdf
import pytest

from rebind.extract import ExtractionError, extract_pages, source_is_tagged
from tests.fixtures import born_digital_pdf, pdf_with_text_in_form_xobject


def test_extracts_text_lines_with_style_and_provenance(tmp_path: Path):
    source = born_digital_pdf("<h1>Chapter One</h1><p>Body text here.</p>", tmp_path / "a.pdf")

    pages = list(extract_pages(source))

    assert len(pages) == 1
    page = pages[0]
    assert page.number == 1
    assert page.has_text_layer
    texts = [line.text for line in page.lines]
    assert "Chapter One" in texts
    assert "Body text here." in texts

    heading = next(line for line in page.lines if line.text == "Chapter One")
    body = next(line for line in page.lines if line.text == "Body text here.")
    assert heading.size > body.size
    assert heading.page == 1
    assert len(heading.bbox) == 4
    assert heading.bbox[3] > heading.bbox[1]


def test_page_without_text_is_classified_as_scanned(tmp_path: Path):
    import pikepdf

    target = tmp_path / "blank.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.save(target)

    pages = list(extract_pages(target))

    assert len(pages) == 1
    assert not pages[0].has_text_layer
    assert pages[0].lines == ()


def test_extraction_is_lazy(tmp_path: Path):
    source = born_digital_pdf(
        "<p>one</p><div style='page-break-before: always'>two</div>"
        "<div style='page-break-before: always'>three</div>",
        tmp_path / "lazy.pdf",
    )

    result = extract_pages(source)
    assert inspect.isgenerator(result), "extract_pages must return a generator, not a list"

    first = next(result)

    assert first.number == 1
    # The iterator must not be exhausted after pulling only the first page -- an eager
    # implementation that materializes every page up front would fail this.
    assert list(result), "expected more pages to remain after taking only the first"


def test_missing_file_raises_extraction_error(tmp_path: Path):
    with pytest.raises(ExtractionError):
        list(extract_pages(tmp_path / "nope.pdf"))


def test_untagged_fixture_is_reported_as_untagged(tmp_path: Path):
    source = born_digital_pdf("<p>text</p>", tmp_path / "u.pdf")

    assert source_is_tagged(source) is False


def _encrypted_pdf(tmp_path: Path) -> Path:
    target = tmp_path / "encrypted.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.save(target, encryption=pikepdf.Encryption(owner="o", user="u"))
    return target


def test_encrypted_pdf_raises_extraction_error_on_extract(tmp_path: Path):
    source = _encrypted_pdf(tmp_path)

    with pytest.raises(ExtractionError):
        list(extract_pages(source))


def test_encrypted_pdf_raises_extraction_error_on_is_tagged(tmp_path: Path):
    source = _encrypted_pdf(tmp_path)

    with pytest.raises(ExtractionError):
        source_is_tagged(source)


def test_text_inside_a_form_xobject_is_extracted_not_reported_as_an_image(tmp_path: Path):
    """Regression test for Finding 2: text drawn from inside a Form XObject must surface as a
    real `TextLine`, not silently vanish into a false "image region" placeholder. This is a live
    PDF-shape failure, not a hypothetical one -- confirmed against the pre-fix extractor before
    writing the fix (see the report), which found zero lines and one `ImageRegion` for the whole
    figure instead.
    """
    source = pdf_with_text_in_form_xobject(tmp_path / "xobj.pdf", text="Hello Figure")

    pages = list(extract_pages(source))

    assert len(pages) == 1
    page = pages[0]
    assert page.has_text_layer, "the page has real, extractable text and must not read as a scan"
    texts = [ln.text for ln in page.lines]
    assert any("Hello Figure" in t for t in texts), (
        f"expected the form xobject's text among extracted lines, got: {texts!r}"
    )
    # The figure held only text, no embedded raster image -- it must not also be reported as an
    # opaque whole-figure ImageRegion (the double-counting rule: text and image are independent
    # signals, and a text-only figure contributes no image region at all).
    assert page.images == ()


def test_text_lines_treats_a_bare_top_level_line_as_a_line_not_a_box():
    """A top-level bare LTTextLine must be yielded as one line, not descended into.

    pdfminer usually wraps text lines in an LTTextBox, but on some OCR'd PDFs (confirmed against a
    real 16-page OCR sample, page 8) it emits a bare LTTextLine at the page's top level. An
    LTTextLine is itself an LTTextContainer, so the old code -- which iterated every top-level
    LTTextContainer expecting line children -- descended into it and handed its LTChars to
    `_line_from_container`, which then crashed trying to iterate an LTChar. `_text_lines` must
    treat a line as a line: yield it whole rather than yielding its characters.
    """
    from pdfminer.layout import LTAnno, LTTextBoxHorizontal, LTTextLineHorizontal

    from rebind.extract import _text_lines

    bare_line = LTTextLineHorizontal(0.1)
    bare_line._objs.append(LTAnno(" "))  # a child, so wrongly descending would be observable
    assert list(_text_lines(bare_line)) == [bare_line]

    box = LTTextBoxHorizontal()
    inner = LTTextLineHorizontal(0.1)
    box._objs.append(inner)
    assert list(_text_lines(box)) == [inner]

    # A stray non-line child directly inside a box (an ungrouped LTAnno/LTChar) is skipped, never
    # passed on as if it were a line.
    box_with_stray = LTTextBoxHorizontal()
    box_with_stray._objs.append(LTAnno(" "))
    assert list(_text_lines(box_with_stray)) == []
