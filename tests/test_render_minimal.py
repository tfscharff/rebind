from pathlib import Path

from rebind.render import render_html_to_pdf
from rebind.validate import validate_pdf_ua

MINIMAL_HTML = """
<h1>The Structure of Scientific Revolutions</h1>
<p>Normal science, the activity in which most scientists inevitably spend almost all
their time, is predicated on the assumption that the scientific community knows what
the world is like.</p>
"""


def test_minimal_document_passes_pdf_ua(tmp_path: Path, verapdf_exe: Path):
    target = tmp_path / "minimal.pdf"

    render_html_to_pdf(MINIMAL_HTML, target, title="Scientific Revolutions", lang="en")

    assert target.exists()
    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary() + "\n" + "\n".join(
        f"  {r.clause}: {r.description}" for r in result.failed_rules
    )
