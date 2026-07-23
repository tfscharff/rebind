from pathlib import Path

import pikepdf
import pytest

from rebind.extract import ExtractionError
from rebind.model import Document
from rebind.pipeline import NoTextLayerError, convert
from tests.fixtures import born_digital_pdf


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


def test_output_has_page_labels_matching_its_page_count(tmp_path: Path):
    source = born_digital_pdf("<h1>T</h1>" + "<p>filler</p>" * 300, tmp_path / "in.pdf")
    target = tmp_path / "out.pdf"

    convert(source, target, title="T")

    with pikepdf.open(target) as pdf:
        assert "/PageLabels" in pdf.Root


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


def test_zero_page_input_raises_a_clear_error(tmp_path: Path):
    # A PDF with a StructTreeRoot but no pages at all -- pikepdf permits saving this. It must
    # never be quietly treated as "no text found on any page" (that message is a lie for a
    # document that never had any pages to check), and must not crash inside page-count math
    # downstream (e.g. dividing by page_count when computing the artifact threshold).
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
