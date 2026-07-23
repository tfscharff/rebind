"""The composed Phase 1 post-processing order: render -> set_page_labels -> pin_document_metadata.

`test_pagelabels.py` and `test_reproducible.py` each validate page labels and metadata pinning
against PDF/UA conformance in isolation, but neither exercises the actual sequence Phase 1 will
run: two sequential qpdf re-saves of the same tagged file (`set_page_labels` opens, mutates, and
saves; `pin_document_metadata` then opens that already-modified file, mutates, and saves again).
A defect specific to *composition* -- e.g. one step's save clobbering a structure the other step
depends on, or the second qpdf re-save silently discarding the first save's page-label tree --
would not be caught by either isolated test.
"""

from pathlib import Path

from rebind.inspect import page_labels
from rebind.pagelabels import set_page_labels
from rebind.render import render_html_to_pdf
from rebind.reproducible import pin_document_metadata
from rebind.validate import validate_pdf_ua

TWO_PAGE_HTML = """
<h1>Front matter</h1>
<p>First page content.</p>
<p style="break-before: page;">Second page content.</p>
"""


def test_render_then_label_then_pin_then_validate(tmp_path: Path, verapdf_exe: Path):
    target = tmp_path / "pipeline.pdf"

    render_html_to_pdf(TWO_PAGE_HTML, target, title="Pipeline", lang="en")
    set_page_labels(target, ["i", "ii"])
    pin_document_metadata(target, title="Pipeline", lang="en")

    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary() + "\n" + "\n".join(
        f"  {r.clause}: {r.description}" for r in result.failed_rules
    )

    # The second qpdf re-save (pin_document_metadata) must not have clobbered what the first
    # save (set_page_labels) wrote -- this is exactly the kind of ordering defect an isolated
    # test of either function alone cannot see.
    assert page_labels(target) == ["i", "ii"]
