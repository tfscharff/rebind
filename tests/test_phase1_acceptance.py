"""Acceptance tests for the Phase 1 spine.

These cover the properties the spec claims, not the internals: heading normalization under
adversarial input, artifact suppression, model stability, and that no arbitrary limit exists.
"""

from pathlib import Path

import pytest

from rebind.model import Document, Heading, ListNode
from rebind.pipeline import convert
from tests.fixtures import born_digital_pdf

GOLDEN = Path(__file__).parent / "golden" / "simple_document.model.json"


def test_skipped_heading_levels_are_normalized(tmp_path: Path, verapdf_exe: Path):
    """A document whose first heading is an h3 must still pass PDF/UA clause 7.4.2."""
    source = born_digital_pdf(
        "<h3>Starts Deep</h3><p>body</p><h3>Another</h3><p>body</p>", tmp_path / "in.pdf"
    )

    result = convert(source, tmp_path / "out.pdf", title="T", verapdf_exe=verapdf_exe)

    assert result.validation.compliant, result.validation.summary()


def test_running_headers_are_not_read_aloud(tmp_path: Path):
    """A @page margin box header repeats on every page and must not become body text."""
    body = "<h1>Doc</h1>" + "".join(f"<p>Paragraph {i}.</p>" for i in range(200))
    source = born_digital_pdf(
        body,
        tmp_path / "in.pdf",
        extra_css="@page { @top-center { content: 'Course Catalog 2026'; font-size: 9pt; } }",
    )

    result = convert(source, tmp_path / "out.pdf", title="T")

    paragraphs = [n.text for n in result.document.nodes if n.kind == "Paragraph"]
    assert not any("Course Catalog 2026" in text for text in paragraphs), (
        "the running header leaked into the reading order"
    )

    # The header must not merely be excluded from Paragraphs -- confirm it was actually
    # recognized and classified as an artifact, not silently dropped or misclassified as
    # something else entirely.
    artifacts = [n.text for n in result.document.nodes if n.kind == "Artifact"]
    assert any("Course Catalog 2026" in text for text in artifacts), (
        "the running header should have been captured as an Artifact node"
    )


def test_body_style_at_page_edge_is_never_classified_as_an_artifact(tmp_path: Path):
    """Regression test for Finding 1.

    `profile.build_profile` used to key `artifact_keys` on `(Style, band)` alone, with nothing
    excluding the body style itself. Any document whose body text reaches into the top or bottom
    `EDGE_FRACTION` of the page on more than `RECURRENCE_FRACTION` of pages made the body style
    itself an artifact key -- deleting real paragraphs with confidence=1.0 and no flag. The
    default 1in margin every other fixture in this suite uses escapes the edge band by a few
    points, which is why nothing caught this: a narrower, still entirely ordinary 0.75in margin
    is what actually reproduces it, with no running header in the source at all.
    """
    paragraph_count = 120
    body = "".join(
        f"<p>Paragraph number {i} of the reproduction document, long enough to fill a line.</p>"
        for i in range(paragraph_count)
    )
    source = born_digital_pdf(body, tmp_path / "in.pdf", margin="0.75in")

    result = convert(source, tmp_path / "out.pdf", title="T")

    artifacts = [n for n in result.document.nodes if n.kind == "Artifact"]
    assert not artifacts, (
        f"source has no running header; the body style must never be classified as an "
        f"artifact, but got: {artifacts}"
    )

    paragraphs = [n for n in result.document.nodes if n.kind == "Paragraph"]
    assert len(paragraphs) == paragraph_count, (
        f"expected all {paragraph_count} input paragraphs to survive; got {len(paragraphs)} -- "
        "some were silently dropped as artifacts"
    )


def test_two_column_text_is_reconstructed_in_reading_order(tmp_path: Path):
    """A genuinely two-column source must be reconstructed into correct reading order.

    CSS `column-count:2` fills the left column top-to-bottom before the right, so the source's
    paragraph order is left-column-then-right-column per page -- exactly what XY-cut recovers. The
    naive top-to-bottom/left-to-right sort this replaced would interleave the two columns at each
    y, scrambling the numbers; asserting the recovered indices are non-decreasing is what proves
    the columns were actually reconstructed rather than read straight across.
    """
    import re

    source = born_digital_pdf(
        "<h1>Doc</h1><div style='column-count:2'>"
        + "".join(f"<p>Paragraph {i:02d} of the column test.</p>" for i in range(40))
        + "</div>",
        tmp_path / "in.pdf",
    )

    result = convert(source, tmp_path / "out.pdf", title="T")

    paragraphs = [n for n in result.document.nodes if n.kind == "Paragraph"]
    assert paragraphs, "expected at least one paragraph"
    indices = [int(re.search(r"Paragraph (\d+)", p.text).group(1)) for p in paragraphs]
    assert indices == sorted(indices), (
        f"columns were not reconstructed in reading order: {indices}"
    )
    # Multi-column pages record column provenance so a reviewer can trace the interleaving.
    assert any("column-0" in p.flags for p in paragraphs)
    assert any("column-1" in p.flags for p in paragraphs)


def test_single_column_text_is_not_flagged(tmp_path: Path):
    """A heuristic that flags everything is useless -- a single-column page must stay unflagged."""
    source = born_digital_pdf(
        "<h1>Doc</h1>"
        + "".join(f"<p>Paragraph {i} of the single column test.</p>" for i in range(40)),
        tmp_path / "in.pdf",
    )

    result = convert(source, tmp_path / "out.pdf", title="T")

    paragraphs = [n for n in result.document.nodes if n.kind == "Paragraph"]
    assert paragraphs, "expected at least one paragraph"
    assert not any("multi-column-suspected" in n.flags for n in paragraphs), (
        "single-column source was incorrectly flagged as multi-column"
    )


def test_model_matches_the_golden_file(tmp_path: Path):
    """Golden-file test on the model JSON. Never on PDF bytes -- see ADR 0003."""
    source = born_digital_pdf(
        "<h1>Chapter One</h1><p>Body text.</p><ul><li>alpha</li><li>beta</li></ul>",
        tmp_path / "in.pdf",
    )

    result = convert(source, tmp_path / "out.pdf", title="Golden")

    if not GOLDEN.exists():
        pytest.fail(f"golden file missing; create it with the model at {result.model_path}")
    expected = Document.from_json(GOLDEN.read_text(encoding="utf-8"))
    assert result.document == expected

    # Whole-document equality against the golden file only proves "matches whatever was last
    # blessed" -- it says nothing about whether that blessing was correct. These assertions
    # encode the structural properties a human actually reviewed, so regenerating the golden file
    # can never silently re-bless a regression without someone re-checking these by hand.
    document = result.document
    heading = next(n for n in document.nodes if isinstance(n, Heading))
    assert heading.level == 1, "the top-level heading must be level 1"

    lists = [n for n in document.nodes if isinstance(n, ListNode)]
    assert len(lists) == 1, "alpha/beta must be recovered as a single list, not scattered items"
    assert [item.text for item in lists[0].items] == ["alpha", "beta"]

    for node in document.nodes:
        assert node.id, f"{node.kind} node is missing an id"
        assert isinstance(node.page, int) and node.page >= 1, f"{node.kind} node has no page"
        assert isinstance(node.bbox, tuple) and len(node.bbox) == 4, (
            f"{node.kind} node's bbox is not a 4-tuple"
        )
        if isinstance(node, ListNode):
            for item in node.items:
                assert item.id, "list item is missing an id"
                assert item.page >= 1, "list item has no page"
                assert isinstance(item.bbox, tuple) and len(item.bbox) == 4, (
                    "list item's bbox is not a 4-tuple"
                )


def test_ordered_list_from_real_weasyprint_output_is_recognized(tmp_path: Path):
    """Regression guard for Finding 3: WeasyPrint emits an <ol> marker glyph as its own line with
    no trailing space ("1.", not "1. "). `ORDERED_RE` originally required trailing whitespace, so
    no ListNode was ever produced and the bare numerals leaked into the reading order as
    Paragraphs -- exactly the kind of thing a screen reader should never announce. Nothing in the
    original suite rendered a real <ol> through WeasyPrint, which is how this shipped unnoticed.
    """
    source = born_digital_pdf(
        "<h1>Doc</h1><ol><li>first</li><li>second</li></ol>", tmp_path / "in.pdf"
    )

    result = convert(source, tmp_path / "out.pdf", title="T")

    lists = [n for n in result.document.nodes if isinstance(n, ListNode)]
    assert len(lists) == 1, "the ordered list must be recovered as a single ListNode"
    assert lists[0].ordered is True
    assert [item.text for item in lists[0].items] == ["first", "second"]

    paragraphs = [n.text for n in result.document.nodes if n.kind == "Paragraph"]
    assert not any(text.strip() in ("1.", "2.") for text in paragraphs), (
        "the bare ordinal markers must not leak into the reading order as paragraphs"
    )


@pytest.mark.slow
def test_three_hundred_pages_convert_without_an_element_limit(tmp_path: Path):
    """Invariant 5: no arbitrary limits. This is the failure that started the project --
    Yuja refused the course catalog at its 999-structure-element ceiling."""
    body = "".join(
        f"<h2>Section {i}</h2><p>Body for section {i}.</p><p style='break-after:page'>x</p>"
        for i in range(300)
    )
    source = born_digital_pdf(body, tmp_path / "big.pdf")

    result = convert(source, tmp_path / "big-out.pdf", title="Big")

    headings = [n for n in result.document.nodes if n.kind == "Heading"]
    assert len(headings) == 300
    assert len(result.document.nodes) > 999, "the point is to exceed 999 structure elements"
