from pathlib import Path

import pytest

from rebind.inspect import structure_element_types
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


def test_svg_fallback_is_conformant(tmp_path: Path, verapdf_exe: Path):
    """Fallback outcome: equation as an image with a spoken-form alt text. Must pass."""
    target = tmp_path / "math_fallback.pdf"
    render_html_to_pdf(SVG_FALLBACK_HTML, target, title="Quadratic", lang="en")

    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()
