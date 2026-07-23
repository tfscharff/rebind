from pathlib import Path

import pikepdf
import pytest

from rebind.emit import PAGE_ANCHOR_PREFIX
from rebind.extract import ExtractionError
from rebind.model import Document, PageBreak
from rebind.pipeline import NoTextLayerError, _page_labels, convert
from tests.fixtures import born_digital_pdf, pdf_with_text_in_form_xobject


def _page_break(page: int, label: str) -> PageBreak:
    return PageBreak(
        id=f"pb-{page}",
        page=page,
        bbox=(0.0, 0.0, 612.0, 792.0),
        confidence=1.0,
        stage="test",
        label=label,
    )


def _anchor(source_page: int) -> str:
    return f"{PAGE_ANCHOR_PREFIX}{source_page}"


def test_converts_a_born_digital_pdf_end_to_end(tmp_path: Path):
    source = born_digital_pdf(
        "<h1>Chapter One</h1><p>Body text.</p><h2>Section</h2><p>More text.</p>",
        tmp_path / "in.pdf",
    )
    target = tmp_path / "out.pdf"

    result = convert(source, target, title="Test Document")

    assert target.exists()
    headings = [n for n in result.document.nodes if n.kind == "Heading"]
    assert [h.text for h in headings] == ["Chapter One", "Section"]
    assert headings[0].level == 1
    assert headings[1].level == 2


def test_writes_the_model_json_beside_the_pdf(tmp_path: Path):
    source = born_digital_pdf("<h1>T</h1><p>body</p>", tmp_path / "in.pdf")
    target = tmp_path / "out.pdf"

    result = convert(source, target, title="T")

    assert result.model_path.exists()
    restored = Document.from_json(result.model_path.read_text(encoding="utf-8"))
    assert restored == result.document


def test_write_model_false_leaves_model_path_none(tmp_path: Path):
    source = born_digital_pdf("<h1>T</h1><p>body</p>", tmp_path / "in.pdf")
    target = tmp_path / "out.pdf"

    result = convert(source, target, title="T", write_model=False)

    assert result.model_path is None
    assert not target.with_suffix(".model.json").exists()


def test_mixed_document_records_blank_pages_and_emits_placeholders(tmp_path: Path):
    # One text-bearing page followed by two genuinely blank pages appended with pikepdf --
    # WeasyPrint has no way to render a page with literally no content, so the blank pages are
    # bolted on directly rather than produced through the born_digital_pdf fixture.
    text_source = born_digital_pdf("<h1>T</h1><p>body text</p>", tmp_path / "text.pdf")
    source = tmp_path / "mixed.pdf"
    with pikepdf.open(text_source) as pdf:
        pdf.add_blank_page(page_size=(612, 792))
        pdf.add_blank_page(page_size=(612, 792))
        pdf.save(source)

    result = convert(source, tmp_path / "out.pdf", title="T")

    assert result.scanned_pages == (2, 3)
    placeholder_pages = {
        n.page for n in result.document.nodes
        if n.kind == "Placeholder" and "no-text-layer" in n.flags
    }
    assert placeholder_pages == {2, 3}


def test_output_has_page_labels_matching_its_page_count(tmp_path: Path):
    source = born_digital_pdf("<h1>T</h1>" + "<p>filler</p>" * 300, tmp_path / "in.pdf")
    target = tmp_path / "out.pdf"

    convert(source, target, title="T")

    with pikepdf.open(target) as pdf:
        assert "/PageLabels" in pdf.Root
        nums = pdf.Root["/PageLabels"]["/Nums"]
        # Nums alternates (starting page index, label dict); one pair per output page.
        entries = list(nums)
        assert len(entries) // 2 == len(pdf.pages)
        first_label_dict = entries[1]
        assert str(first_label_dict["/P"]) == "1"


def test_form_xobject_text_is_not_mistaken_for_a_scan(tmp_path: Path):
    """Regression test for Finding 2's document-wide failure mode: a page whose only content
    lives inside a Form XObject must not be refused as `NoTextLayerError` -- it has a real text
    layer, just one the pre-fix extractor could not see."""
    source = pdf_with_text_in_form_xobject(tmp_path / "xobj.pdf", text="Hello Figure")

    result = convert(source, tmp_path / "out.pdf", title="T")

    assert result.scanned_pages == ()
    paragraphs = [n.text for n in result.document.nodes if n.kind == "Paragraph"]
    assert any("Hello Figure" in text for text in paragraphs), paragraphs


def test_all_scanned_input_is_refused(tmp_path: Path):
    target = tmp_path / "scan.pdf"
    pdf = pikepdf.new()
    for _ in range(3):
        pdf.add_blank_page(page_size=(612, 792))
    pdf.save(target)

    with pytest.raises(NoTextLayerError):
        convert(target, tmp_path / "out.pdf", title="T")


def test_generated_output_passes_pdf_ua(tmp_path: Path, verapdf_exe: Path):
    source = born_digital_pdf(
        "<h1>Chapter One</h1><p>Body.</p><ul><li>one</li><li>two</li></ul>",
        tmp_path / "in.pdf",
    )
    target = tmp_path / "out.pdf"

    result = convert(source, target, title="T", verapdf_exe=verapdf_exe)

    assert result.validation is not None
    assert result.validation.compliant, result.validation.summary()


def test_convert_refuses_to_overwrite_the_source(tmp_path: Path):
    """Regression test for Finding 5: `render_html_to_pdf_with_anchors` writes `target`
    unconditionally and `set_page_labels` opens with `allow_overwriting_input=True`, so
    `convert(same_path, same_path)` would silently destroy the librarian's only copy of the
    source evidence and report success. Must raise before any of that runs."""
    source = born_digital_pdf("<h1>T</h1><p>body</p>", tmp_path / "in.pdf")

    with pytest.raises(ValueError, match="same file"):
        convert(source, source, title="T")

    # The source must survive untouched -- not truncated or partially overwritten.
    assert source.exists()
    assert source.stat().st_size > 0


def test_convert_refuses_to_overwrite_the_source_via_a_differently_spelled_path(tmp_path: Path):
    """The guard must compare resolved paths, not raw strings -- a relative path naming the same
    file as the absolute `source` path must be caught too."""
    source = born_digital_pdf("<h1>T</h1><p>body</p>", tmp_path / "in.pdf")
    differently_spelled = tmp_path / "." / source.name

    with pytest.raises(ValueError, match="same file"):
        convert(source, differently_spelled, title="T")


def test_zero_page_input_raises_a_clear_error(tmp_path: Path):
    # A minimal, otherwise-valid PDF with no pages at all -- pikepdf.new() permits saving this
    # (it creates no StructTreeRoot; a bare PDF has none by default). This must never be quietly
    # treated as "no text found on any page" (that message is a lie for a document that never had
    # any pages to check), and must not crash inside page-count math downstream (e.g. dividing by
    # page_count when computing the artifact threshold).
    target = tmp_path / "empty.pdf"
    pdf = pikepdf.new()
    pdf.save(target)

    with pytest.raises(ExtractionError) as excinfo:
        convert(target, tmp_path / "out.pdf", title="T")

    assert "page" in str(excinfo.value).lower()
    assert not isinstance(excinfo.value, NoTextLayerError)


def test_encrypted_source_raises_extraction_error_not_something_else(tmp_path: Path):
    # extract.extract_pages already raises ExtractionError for encrypted input; the pipeline
    # must let it propagate unchanged rather than swallowing it or reporting it as a scan.
    target = tmp_path / "encrypted.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.save(target, encryption=pikepdf.Encryption(owner="o", user="u"))

    with pytest.raises(ExtractionError) as excinfo:
        convert(target, tmp_path / "out.pdf", title="T")

    assert not isinstance(excinfo.value, NoTextLayerError)
    assert "password" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------------------------
# Direct unit tests for `_page_labels` (Finding 3). This is the highest-risk function in the
# module: a wrong mapping here silently mislabels citation-facing page numbers.
# ---------------------------------------------------------------------------------------------

_EMPTY_DOCUMENT = Document(title="t", lang="en", nodes=[])


def test_page_labels_a_source_page_spanning_several_output_pages_repeats_its_label():
    # Source pages 1, 2 and 3 start at output pages 1, 2 and 4 respectively -- source page 2
    # reflows across output pages 2 and 3, and source page 3 across output pages 4 and 5.
    anchor_pages = {_anchor(1): 1, _anchor(2): 2, _anchor(3): 4}

    labels = _page_labels(anchor_pages, output_page_count=5, document=_EMPTY_DOCUMENT)

    assert labels == ["1", "2", "2", "3", "3"]
    assert len(labels) == 5


def test_page_labels_ignores_an_anchor_that_lands_on_no_output_page():
    # An anchor's recorded output page (10) is past the actual output page count (2). This is
    # not reachable in practice today, but must be ignored rather than crash.
    anchor_pages = {_anchor(1): 1, _anchor(2): 10}

    labels = _page_labels(anchor_pages, output_page_count=2, document=_EMPTY_DOCUMENT)

    assert labels == ["1", "1"]
    assert len(labels) == 2


def test_page_labels_tie_break_is_the_highest_source_page_regardless_of_insertion_order():
    # Four source-page anchors land on the same output page (2). The highest source page (5)
    # must win, and the result must be identical no matter what order the anchors were supplied
    # in -- this is the regression test for Finding 1's undocumented-ordering bug.
    descending = {_anchor(5): 2, _anchor(4): 2, _anchor(3): 2, _anchor(1): 1}
    ascending = {_anchor(1): 1, _anchor(3): 2, _anchor(4): 2, _anchor(5): 2}

    labels_descending = _page_labels(descending, output_page_count=2, document=_EMPTY_DOCUMENT)
    labels_ascending = _page_labels(ascending, output_page_count=2, document=_EMPTY_DOCUMENT)

    assert labels_descending == labels_ascending == ["1", "5"]


def test_page_labels_tie_break_sorts_source_page_numerically_not_lexically():
    # If the tie-break compared the stripped anchor id as a string, "10" would sort before "9".
    anchor_pages = {_anchor(9): 1, _anchor(10): 1}

    labels = _page_labels(anchor_pages, output_page_count=1, document=_EMPTY_DOCUMENT)

    assert labels == ["10"]


def test_page_labels_are_read_from_the_documents_page_break_nodes():
    # PageBreak.label is where roman numerals or "A-17"-style source labels will live once real
    # pagination is extracted -- the anchor id is only ever a page *number* and must not be used
    # as the label once a PageBreak node disagrees with it.
    document = Document(
        title="t", lang="en",
        nodes=[_page_break(1, "i"), _page_break(2, "A-17")],
    )
    anchor_pages = {_anchor(1): 1, _anchor(2): 2}

    labels = _page_labels(anchor_pages, output_page_count=2, document=document)

    assert labels == ["i", "A-17"]


def test_page_labels_falls_back_to_the_page_number_with_no_matching_page_break():
    anchor_pages = {_anchor(1): 1, _anchor(2): 2}

    labels = _page_labels(anchor_pages, output_page_count=2, document=_EMPTY_DOCUMENT)

    assert labels == ["1", "2"]


def test_page_labels_before_the_first_anchor_borrow_its_label_rather_than_fabricate_one():
    # Output page 1 has no anchor of its own; the first anchor available is source page 2 at
    # output page 2. The honest answer for output page 1 is "whatever the first known anchor
    # says", never an invented "1".
    anchor_pages = {_anchor(2): 2}

    labels = _page_labels(anchor_pages, output_page_count=3, document=_EMPTY_DOCUMENT)

    assert labels == ["2", "2", "2"]


def test_page_labels_with_no_anchors_at_all_emits_a_sentinel_not_a_fabricated_page_number():
    labels = _page_labels({}, output_page_count=2, document=_EMPTY_DOCUMENT)

    assert labels == ["", ""]
    assert len(labels) == 2
