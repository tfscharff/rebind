"""PDF/UA-1 clause 7.4.2 (heading level sequencing) and rebind.render's normalization of it.

Rebind reconstructs badly scanned PDFs, so recognized heading levels routinely start
mid-chapter (only H2/H3 present, no H1) or skip a level (H1 followed directly by H3, no H2).
Both shapes fail veraPDF PDF/UA-1 validation at clause 7.4.2, which is a live failure mode for
Phase 1, not a hypothetical one -- verified directly below before any fix was written.
"""

from pathlib import Path

from rebind.inspect import structure_tree
from rebind.render import _normalize_heading_levels, render_html_to_pdf
from rebind.validate import validate_pdf_ua

_CLAUSE_7_4_2 = "7.4.2"


def _has_failed_clause(result, clause: str) -> bool:
    return any(rule.clause == clause for rule in result.failed_rules)


def _flatten(elements):
    """`structure_tree` returns only top-level nodes; walk into children too."""
    for element in elements:
        yield element
        yield from _flatten(element.children)


def test_heading_level_skip_fails_pdf_ua_clause_7_4_2(tmp_path: Path, verapdf_exe: Path):
    """H1 directly followed by H3 (skipping H2) must fail veraPDF at clause 7.4.2.

    This asserts the *specific* clause, not mere non-compliance, so this test cannot be
    accidentally satisfied by some unrelated PDF/UA defect. Verified against the renderer's
    raw (unnormalized) heading tags, bypassing `render_html_to_pdf`'s own normalization, so
    this documents the underlying WeasyPrint/PDF-UA behaviour rebind must work around, not
    just rebind's own fixed-up output.
    """
    target = tmp_path / "skip.pdf"
    _render_raw_heading_document(
        "<h1>Chapter Title</h1><h3>Subsection</h3><p>Body.</p>", target
    )

    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)

    assert result.compliant is False
    assert _has_failed_clause(result, _CLAUSE_7_4_2), (
        f"expected clause {_CLAUSE_7_4_2} to fail; got {result.failed_rules!r}"
    )


def test_document_starting_below_h1_fails_pdf_ua_clause_7_4_2(tmp_path: Path, verapdf_exe: Path):
    """A document using only H2 (no H1 at all) must also fail veraPDF at clause 7.4.2."""
    target = tmp_path / "only_h2.pdf"
    _render_raw_heading_document("<h2>Only Heading</h2><p>Body.</p>", target)

    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)

    assert result.compliant is False
    assert _has_failed_clause(result, _CLAUSE_7_4_2), (
        f"expected clause {_CLAUSE_7_4_2} to fail; got {result.failed_rules!r}"
    )


# --- rebind.render's normalization fix ----------------------------------------------------


def test_normalize_heading_levels_closes_a_gap():
    assert (
        _normalize_heading_levels("<h1>A</h1><h3>B</h3><p>x</p>")
        == "<h1>A</h1><h2>B</h2><p>x</p>"
    )


def test_normalize_heading_levels_shifts_a_document_that_starts_at_h2_down_to_h1():
    """A document genuinely starting at H2 is normalized to start at H1 in the output.

    This is a deliberate semantic transformation, not a bug: rebind's own recognized heading
    levels are a signal of *relative* nesting, and a document whose first heading is H2 does
    still start its own top level there -- the correct accessible representation is H1, not
    "H2 with no H1", which is what fails 7.4.2 in the first place.
    """
    assert _normalize_heading_levels("<h2>Only</h2><p>x</p>") == "<h1>Only</h1><p>x</p>"


def test_normalize_heading_levels_preserves_a_well_formed_sequence():
    html = "<h1>A</h1><h2>B</h2><h3>C</h3>"
    assert _normalize_heading_levels(html) == html


def test_normalize_heading_levels_preserves_relative_nesting_on_return_to_a_shallower_level():
    """H1, H3, H2, H3 (raw) -> H1, H2, H2, H3: the second H3 reopens under the new H2, not
    under the level the first H3 was compressed to, matching the source's actual nesting.
    """
    assert (
        _normalize_heading_levels("<h1>A</h1><h3>B</h3><h2>C</h2><h3>D</h3>")
        == "<h1>A</h1><h2>B</h2><h2>C</h2><h3>D</h3>"
    )


def test_normalize_heading_levels_preserves_attributes_and_body_text():
    html = '<h3 id="intro" class="x">Hello &amp; World</h3>'
    assert _normalize_heading_levels(html) == '<h1 id="intro" class="x">Hello &amp; World</h1>'


def test_render_html_to_pdf_normalizes_a_heading_skip_to_pdf_ua_compliance(
    tmp_path: Path, verapdf_exe: Path
):
    """The public entry point applies the normalization, so real callers get a conformant
    document even when the recognized heading levels skip -- this is the regression guard
    for the fix, using the exact input shape proven non-compliant above.
    """
    target = tmp_path / "skip_fixed.pdf"
    render_html_to_pdf(
        "<h1>Chapter Title</h1><h3>Subsection</h3><p>Body.</p>", target, title="Skip", lang="en"
    )

    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary() + "\n" + "\n".join(
        f"  {r.clause}: {r.description}" for r in result.failed_rules
    )

    heading_types = {"H1", "H2", "H3", "H4", "H5", "H6"}
    types_in_order = [e.type for e in _flatten(structure_tree(target)) if e.type in heading_types]
    assert types_in_order == ["H1", "H2"], types_in_order


def test_render_html_to_pdf_normalizes_a_document_starting_at_h2(tmp_path: Path, verapdf_exe: Path):
    """A recognized document that starts mid-chapter at H2 is shifted to H1 and passes."""
    target = tmp_path / "only_h2_fixed.pdf"
    render_html_to_pdf("<h2>Only Heading</h2><p>Body.</p>", target, title="OnlyH2", lang="en")

    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()

    heading_types = {"H1", "H2", "H3", "H4", "H5", "H6"}
    types_in_order = [e.type for e in _flatten(structure_tree(target)) if e.type in heading_types]
    assert types_in_order == ["H1"], types_in_order


# --- helper: render the raw (unnormalized) heading tags directly --------------------------
#
# `render_html_to_pdf` always normalizes; to demonstrate the underlying, pre-fix veraPDF
# failure this module needs a way to render the *raw* heading tags, bypassing that
# normalization entirely (calling WeasyPrint directly, not `render_html_to_pdf`), rather than
# adding a normalize-bypass parameter to the public API purely for this test-only need.

_RAW_DOCUMENT_TEMPLATE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Raw</title></head>
<body>
{body}
</body>
</html>
"""


def _render_raw_heading_document(body: str, target: Path) -> None:
    from weasyprint import HTML

    document = _RAW_DOCUMENT_TEMPLATE.format(body=body)
    HTML(string=document).write_pdf(target, pdf_variant="pdf/ua-1", uncompressed_pdf=False)
