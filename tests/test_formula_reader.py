"""Drafting a formula's description in words: the LaTeX-to-words rules, and the reader itself.

The ones marked `formula_reader` run the real equation reader and need its models (fetched into
`models/` on first use).
"""

from pathlib import Path

import numpy as np
import pikepdf
import pytest

from rebind.formula_reader import _Reader, speak
from tests.fixtures import born_digital_pdf


@pytest.mark.parametrize("latex, words", [
    (r"E = m c ^ { 2 }", "E equals m c squared"),
    (r"x = \frac { - b \pm \sqrt { b ^ { 2 } - 4 a c } } { 2 a }",
     "x equals the fraction minus b plus or minus the square root of b squared minus 4 a c, "
     "end root over 2 a, end fraction"),
    (r"\frac { a } { b }", "a over b"),
    (r"\sum _ { i = 1 } ^ { n } i", "the sum from i equals 1 to n of i"),
    (r"\int _ { 0 } ^ { \infty } e ^ { - x } d x",
     "the integral from 0 to infinity of e to the power of minus x, end power d x"),
    (r"\lim _ { x \to 0 } \frac { \sin x } { x }",
     "the limit as x goes to 0 of the fraction sine x over x, end fraction"),
    (r"f ^ { \prime \prime } ( x )", "f double prime open paren x close paren"),
    (r"c o s ( \omega t )", "cosine of omega t"),
    (r"\sqrt [ 3 ] { x }", "the cube root of x"),
    (r"\sqrt [ n ] { x }", "the nth root of x"),
    (r"\pi \approx 3 . 1 4", "pi is approximately equal to 3.14"),
    (r"\alpha _ { 1 0 } \le \Omega", "alpha sub 10 is less than or equal to capital omega"),
    (r"\begin{array}{cc} x & y \\ \end{array} ~ ~", "x y"),
    (r"\mathrm { d } x / \mathrm { d } t", "d x divided by d t"),
])
def test_latex_is_put_into_words_a_person_would_say(latex: str, words: str):
    assert speak(latex) == words


def test_nothing_to_say_says_nothing():
    assert speak("") == "" and speak("  ") == ""


def test_an_equation_number_set_apart_is_cropped_away():
    # Ink for the equation at the left, a wide gap, then "(3)" at the far right.
    image = np.full((40, 400, 3), 255, dtype=np.uint8)
    image[10:30, 20:180] = 0
    image[12:28, 370:390] = 0
    kept = _Reader._equation_only(image)
    assert kept.shape[1] < 200 and (kept < 128).any()


def test_an_equation_with_no_number_is_left_whole():
    image = np.full((40, 400, 3), 255, dtype=np.uint8)
    image[10:30, 20:180] = 0
    image[10:30, 190:300] = 0           # a gap narrower than the ink is tall: one equation
    assert _Reader._equation_only(image).shape == image.shape


@pytest.mark.formula_reader
def test_a_formula_gets_a_drafted_description_that_never_reaches_the_document_on_its_own(
        tmp_path: Path, monkeypatch):
    """The draft is offered to the editor, and the document's /Formula carries no /Alt until the
    person accepts one: a machine's reading of an equation never reaches a reader unseen."""
    from rebind import layout_model
    from rebind.extract import extract_pages
    from rebind.layout_model import Region
    from rebind.remediate import remediate

    source = born_digital_pdf(
        "<p>Mass and energy are related by the equation below.</p>"
        "<p style='text-align:center;font-size:20pt;font-style:italic'>E = mc<sup>2</sup></p>"
        "<p>It is the best-known equation in physics.</p>", tmp_path / "in.pdf")
    page = next(iter(extract_pages(source)))
    equation = [ln for ln in page.lines if ln.text.strip().startswith("E")]
    x0 = min(ln.bbox[0] for ln in equation)
    y0 = min(ln.bbox[1] for ln in equation)
    x1 = max(ln.bbox[2] for ln in equation)
    y1 = max(ln.bbox[3] for ln in equation) + 8      # the superscript sits above the line
    monkeypatch.setattr(layout_model, "page_regions", lambda *args, **kwargs: (
        Region("display_formula", (x0 - 4, y0 - 4, x1 + 4, y1 + 4), 0.9),))

    out = tmp_path / "out.pdf"
    result = remediate(source, out, title="T")
    formula = next(e for e in result.elements if e["kind"] == "Formula")
    assert "E equals m c" in formula.get("alt_guess", ""), formula
    with pikepdf.open(out) as pdf:
        tagged = [e for e in pdf.Root.StructTreeRoot.K[0].K if str(e.S) == "/Formula"]
    assert tagged and all("/Alt" not in e for e in tagged)
