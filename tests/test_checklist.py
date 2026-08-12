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


def test_reading_order_stays_with_the_person_and_names_every_page(remediated: Path):
    # Adobe reports this as "needs manual check" on every document, always, and no measurement can
    # settle it. The app turns it into something finishable -- tab through every page -- so the
    # check carries every page as a location and is never claimed as a pass here.
    checks = _by_title(build_checklist(remediated, page_count=3))
    order = checks["Logical reading order"]
    assert order["status"] == "manual"
    assert order["action"] == "reading-order"
    assert [loc["page"] for loc in order["locations"]] == [1, 2, 3]


def test_colour_contrast_is_never_an_item_on_the_list(remediated: Path):
    # Every item on the checklist is there because it may need a decision from the person reading
    # it. Colour contrast never can: nobody can look at two colours and compute a luminance ratio.
    # Leaving it on the list only ever put something that was already settled among the things
    # still to do -- so it is not a check, it is a line saying what was done.
    for contrast in ({}, {"measured": 40, "ok": True, "darkened": 3, "failures": []},
                     {"measured": 40, "ok": False, "darkened": 2,
                      "failures": [{"page": 7, "ratio": 2.1}]}):
        titles = [c["title"] for c in build_checklist(remediated, contrast=contrast)]
        assert "Colour contrast" not in titles, contrast


def test_the_contrast_note_says_what_was_done_and_asks_nothing():
    from rebind.checklist import contrast_note

    done = contrast_note({"measured": 40, "ok": True, "darkened": 3, "failures": []})
    assert "40 lines" in done and "3 colour" in done

    # Nothing to measure is not a shortfall: a scan's words are part of its picture.
    assert "scan" in contrast_note({"measured": 0})

    # A line that could not be corrected is still stated -- and still not handed over as a task,
    # because there is nothing the reader could do with it.
    left = contrast_note({"measured": 40, "ok": False, "darkened": 2,
                          "failures": [{"page": 7}, {"page": 9}]})
    assert "2 of 40" in left and "could not" in left
    for word in ("you", "your", "please", "check"):
        assert word not in left.lower().split(), left


def test_nothing_is_reported_without_a_way_to_act_on_it(remediated: Path):
    # The rule the report lives by: naming a fault without a route to fixing it leaves a librarian
    # to work out the remedy and then find the place themselves. Every open item must offer either
    # a fix Rebind can perform, somewhere in the document to go, or an instruction.
    checks = build_checklist(remediated, page_count=1, undescribed_figures=({"id": "f", "page": 1},),
                             contrast={"measured": 5, "ok": False, "darkened": 0,
                                       "failures": [{"page": 1, "ratio": 2.0}]})
    for check in checks:
        if check["status"] in (NEEDS_YOU, "manual"):
            assert check["action"] or check["locations"] or check["need"], check["title"]


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
