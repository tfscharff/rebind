"""The Adobe checklist, judged against a real remediated document.

The point of these tests is that a tick is a claim: every one of them has to be read off the
finished PDF, so a check that would pass on a document that does not deserve it is a bug of the
worst kind -- it tells a librarian the document is fine when it is not.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from rebind.checklist import NEEDS_YOU, NOT_APPLICABLE, PASS, build_checklist
from rebind.remediate import remediate
from tests.fixtures import born_digital_pdf


def _by_title(checks: list[dict]) -> dict[str, dict]:
    return {check["title"]: check for check in checks}


@pytest.fixture(scope="module")
def remediated(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("checklist")
    source = born_digital_pdf(
        "<h1>Chapter One</h1><p>Body text that is long enough to be a paragraph.</p>"
        "<h2>A Section</h2><p>More body text follows here.</p>",
        tmp / "in.pdf")
    out = tmp / "out.pdf"
    remediate(source, out, title="A Document")
    return out


def test_every_adobe_group_is_covered(remediated: Path):
    checks = build_checklist(remediated)
    groups = {check["group"] for check in checks}
    assert groups == {"Document", "Page content", "Forms", "Alternate text", "Tables", "Lists",
                      "Headings"}
    # Every check reports one of the four verdicts and says something about why.
    for check in checks:
        assert check["status"] in {PASS, NEEDS_YOU, "manual", NOT_APPLICABLE}, check
        assert check["detail"], f"{check['title']} gives no reason"


def test_a_remediated_document_passes_the_mechanical_checks(remediated: Path):
    checks = _by_title(build_checklist(remediated, page_count=1))
    for title in ("Tagged PDF", "Primary language", "Title", "Tagged content", "Tab order",
                  "Character encoding", "Scripts", "Bookmarks", "Image-only PDF",
                  "Accessibility permission flag"):
        assert checks[title]["status"] == PASS, f"{title}: {checks[title]['detail']}"
    assert checks["Appropriate nesting"]["status"] == PASS


def test_the_two_checks_no_tool_can_pass_are_reported_as_manual(remediated: Path):
    # Adobe reports these as "needs manual check" on every document, always. Claiming a pass here
    # would be the single most dishonest thing this file could do.
    checks = _by_title(build_checklist(remediated))
    assert checks["Logical reading order"]["status"] == "manual"
    assert checks["Colour contrast"]["status"] == "manual"
    assert checks["Logical reading order"]["action"] == "reading-order"
    assert checks["Colour contrast"]["action"] == "contrast"


def test_absent_features_are_not_applicable_rather_than_passing(remediated: Path):
    # A document with no forms has not "passed" the form checks -- there was nothing to check.
    # Reporting those as passes is how a checklist becomes decorative.
    checks = _by_title(build_checklist(remediated))
    for title in ("Tagged form fields", "Field descriptions", "Rows", "List items"):
        assert checks[title]["status"] == NOT_APPLICABLE, title


def test_undescribed_figures_ask_the_user_for_what_only_they_can_give(remediated: Path):
    checks = _by_title(build_checklist(
        remediated, undescribed_figures=({"id": "p2f0", "page": 2},)))
    figures = checks["Figures alternate text"]
    assert figures["status"] == NEEDS_YOU
    assert figures["action"] == "describe"
    assert figures["need"], "a needs-you check must say what it needs"
    assert "page 2" in figures["detail"]


def test_an_untagged_document_fails_the_tagged_checks(tmp_path: Path):
    # The inverse of the pass test, and the one that proves the checks are actually reading the
    # document: a bare PDF must not collect ticks.
    bare = tmp_path / "bare.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.save(bare)

    checks = _by_title(build_checklist(bare))
    assert checks["Tagged PDF"]["status"] == NEEDS_YOU
    assert checks["Primary language"]["status"] == NEEDS_YOU
    assert checks["Title"]["status"] == NEEDS_YOU
    assert checks["Tab order"]["status"] == NEEDS_YOU


def test_a_font_with_no_unicode_mapping_is_reported(tmp_path: Path):
    # What the real already-OCR'd sample had: Tesseract's GlyphLessFont, whose CMap veraPDF
    # rejects. Rebind strips it, but if one ever survives the checklist must say so.
    path = tmp_path / "fontless.pdf"
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(
        F=pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name("/GlyphLessFont")))))
    pdf.save(path)

    checks = _by_title(build_checklist(path))
    assert checks["Character encoding"]["status"] == NEEDS_YOU
