from pathlib import Path

from rebind.render import render_html_to_pdf
from rebind.reproducible import pin_document_metadata
from rebind.validate import validate_pdf_ua

HTML = "<h1>Determinism</h1><p>Two runs must produce identical bytes.</p>"


def _build(target: Path) -> bytes:
    render_html_to_pdf(HTML, target, title="Determinism", lang="en")
    pin_document_metadata(target, title="Determinism", lang="en")
    return target.read_bytes()


def test_two_runs_produce_identical_bytes(tmp_path: Path):
    """Global constraint: same input at same version yields the same output."""
    first = _build(tmp_path / "one.pdf")
    second = _build(tmp_path / "two.pdf")

    assert first == second, "PDF output is not byte-reproducible"


def test_pinned_metadata_preserves_conformance(tmp_path: Path, verapdf_exe: Path):
    target = tmp_path / "pinned.pdf"
    _build(target)

    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()
