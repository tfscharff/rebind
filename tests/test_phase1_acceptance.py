"""Acceptance tests for the Phase 1 spine.

These cover the properties the spec claims, not the internals: heading normalization under
adversarial input, artifact suppression, model stability, and that no arbitrary limit exists.
"""

from pathlib import Path

import pytest

from rebind.model import Document
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


def test_two_column_text_is_flagged_not_silently_trusted(tmp_path: Path):
    """A genuinely two-column source must have its paragraphs flagged 'multi-column-suspected'.

    Phase 1's reading order is naive top-to-bottom, left-to-right with no column awareness, so a
    two-column page silently scrambles reading order unless flagged. A test that only asserts
    "some Paragraph exists" would pass even if the flagging code were entirely absent -- it has to
    assert the flag is actually present.
    """
    source = born_digital_pdf(
        "<h1>Doc</h1><div style='column-count:2'>"
        + "".join(f"<p>Paragraph {i} of the column test.</p>" for i in range(40))
        + "</div>",
        tmp_path / "in.pdf",
    )

    result = convert(source, tmp_path / "out.pdf", title="T")

    paragraphs = [n for n in result.document.nodes if n.kind == "Paragraph"]
    assert paragraphs, "expected at least one paragraph"
    assert all("multi-column-suspected" in n.flags for n in paragraphs), (
        "two-column source did not trip the multi-column heuristic"
    )


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
