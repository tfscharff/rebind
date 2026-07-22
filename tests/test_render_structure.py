from pathlib import Path

from rebind.inspect import structure_element_types
from rebind.render import render_html_to_pdf
from rebind.validate import validate_pdf_ua

STRUCTURED_HTML = """
<h1>Chapter 4: Thermodynamics</h1>
<h2>4.1 The First Law</h2>
<p>Energy is conserved in an isolated system.</p>
<ul>
  <li>Heat added to the system</li>
  <li>Work done by the system</li>
</ul>
<table>
  <caption>Specific heat capacities</caption>
  <thead>
    <tr><th scope="col">Substance</th><th scope="col">c (J/g&#183;K)</th></tr>
  </thead>
  <tbody>
    <tr><th scope="row">Water</th><td>4.18</td></tr>
    <tr><th scope="row">Copper</th><td>0.385</td></tr>
  </tbody>
</table>
<figure>
  <img src="data:image/gif;base64,R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw=="
       alt="Diagram of a piston compressing gas in a cylinder" width="120" height="80">
  <figcaption>Figure 4.1 Isothermal compression.</figcaption>
</figure>
"""


def test_structured_document_passes_pdf_ua(tmp_path: Path, verapdf_exe: Path):
    target = tmp_path / "structured.pdf"
    render_html_to_pdf(STRUCTURED_HTML, target, title="Thermodynamics", lang="en")

    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary() + "\n" + "\n".join(
        f"  {r.clause}: {r.description}" for r in result.failed_rules
    )


def test_expected_structure_elements_are_present(tmp_path: Path):
    target = tmp_path / "structured.pdf"
    render_html_to_pdf(STRUCTURED_HTML, target, title="Thermodynamics", lang="en")

    types = structure_element_types(target)

    for expected in {"H1", "H2", "P", "L", "LI", "Table", "TR", "TH", "TD", "Figure"}:
        assert expected in types, f"{expected} missing from structure tree; found {sorted(types)}"
