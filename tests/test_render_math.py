from pathlib import Path

import pikepdf
import pytest

from rebind.inspect import _page_mcid_text, structure_element_types
from rebind.render import render_html_to_pdf
from rebind.validate import validate_pdf_ua

MATHML_HTML = """
<p>The quadratic formula is given below.</p>
<math xmlns="http://www.w3.org/1998/Math/MathML" alttext="x equals negative b plus or minus
the square root of b squared minus four a c, all over two a">
  <mrow><mi>x</mi><mo>=</mo>
    <mfrac>
      <mrow><mo>-</mo><mi>b</mi><mo>&#177;</mo>
        <msqrt><mrow><msup><mi>b</mi><mn>2</mn></msup><mo>-</mo>
        <mn>4</mn><mi>a</mi><mi>c</mi></mrow></msqrt></mrow>
      <mrow><mn>2</mn><mi>a</mi></mrow>
    </mfrac>
  </mrow>
</math>
"""

SVG_FALLBACK_HTML = """
<p>The quadratic formula is given below.</p>
<figure role="math" aria-label="x equals negative b plus or minus the square root of
b squared minus four a c, all over two a">
  <img src="data:image/gif;base64,R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw=="
       alt="x equals negative b plus or minus the square root of b squared minus four a c,
       all over two a" width="200" height="60">
</figure>
"""


@pytest.mark.xfail(reason="WeasyPrint does not tag native MathML as Formula; see ADR 0001")
def test_mathml_produces_a_formula_element(tmp_path: Path, verapdf_exe: Path):
    """Preferred outcome: native MathML tagged as Formula. May legitimately fail."""
    target = tmp_path / "math.pdf"
    render_html_to_pdf(MATHML_HTML, target, title="Quadratic", lang="en")

    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()
    assert "Formula" in structure_element_types(target)


def test_mathml_glyphs_are_present_in_content_stream(tmp_path: Path):
    """Distinguishes 'rendered but mis-tagged' from 'silently dropped'.

    ADR 0001 documents that native MathML is not tagged as `Formula`, but a tag-tree
    inspection alone can't tell whether the equation's glyphs were laid out and merely
    mis-tagged, or whether the MathML was dropped during HTML5 foreign-content parsing
    and never rendered at all -- both produce the same generic NonStruct/Span tag tree.
    This test extracts the actual text shown under each MCID on the page (via the
    ToUnicode-backed extractor `_page_mcid_text`, the same machinery
    `table_header_associations` uses) and asserts the equation's own characters -- the
    variable names, digits, and operators from `x = (-b +/- sqrt(b^2 - 4ac)) / 2a` -- are
    present in the content stream. They are, which pins down that WeasyPrint *did* lay out
    the MathML glyphs; the defect is confined to tagging, not content loss.
    """
    target = tmp_path / "math_text.pdf"
    render_html_to_pdf(MATHML_HTML, target, title="Quadratic", lang="en")

    with pikepdf.open(target) as pdf:
        page = pdf.pages[0]
        mcid_text = _page_mcid_text(page)

    rendered_text = "".join(mcid_text.values())

    # NOTE: the prose paragraph "The quadratic formula is given below." itself already
    # contains the letters a, b, c, and the digit sequence isn't present but "=", "x" etc.
    # are not either -- still, several of the equation's own characters (a, b, c) are *not*
    # unique to the equation and would trivially pass even if the equation's glyphs were
    # never rendered at all, since they'd be satisfied by the prose alone. Assert only on
    # characters that are genuinely equation-only: the digits (absent from the prose, which
    # contains no numerals) and "±", the one character that appears nowhere else in this
    # document and therefore actually distinguishes "the equation rendered" from "the prose
    # rendered but the equation silently dropped".
    for expected_char in ("2", "4", "±"):
        assert expected_char in rendered_text, (
            f"expected {expected_char!r} from the rendered equation in the page content "
            f"stream; got MCID text {mcid_text!r}"
        )

    # And confirm the converse holds for the shared letters: they must appear in the prose
    # MCID specifically (not merely somewhere in the page), so this test cannot be satisfied
    # by, say, only the prose paragraph rendering and the equation being dropped entirely --
    # the digit/± assertion above already rules that out, but this makes the reasoning explicit
    # rather than relying on the reader to notice a, b, c are ambiguous on their own.
    prose_text = mcid_text.get(0, "")
    for shared_char in ("a", "b", "c"):
        assert shared_char in prose_text, (
            f"expected {shared_char!r} in the prose MCID as a sanity check on the fixture "
            f"itself; got {prose_text!r}"
        )


def test_svg_fallback_is_conformant(tmp_path: Path, verapdf_exe: Path):
    """Fallback outcome: equation as an image with a spoken-form alt text. Must pass."""
    target = tmp_path / "math_fallback.pdf"
    render_html_to_pdf(SVG_FALLBACK_HTML, target, title="Quadratic", lang="en")

    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()
