from pathlib import Path

import pikepdf
import pytest

from rebind.inspect import (
    StructElement,
    StructureTreeError,
    structure_element_types,
    structure_tree,
    table_header_associations,
)
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


def test_table_headers_resolve_to_the_correct_header_cells(tmp_path: Path):
    """Data cells must reference the *correct* header cells, not merely have /Headers at all.

    WeasyPrint wires this up via /Headers arrays on TD elements pointing at /ID values on
    TH elements (it does not emit /Scope). A regression that silently misassigned headers
    -- e.g. always pointing at the first row header, or dropping the column header -- would
    pass every other test in this file. This asserts the actual resolved text.
    """
    target = tmp_path / "structured.pdf"
    render_html_to_pdf(STRUCTURED_HTML, target, title="Thermodynamics", lang="en")

    associations = table_header_associations(target)
    by_data_text = {data: headers for data, headers in associations}

    assert "4.18" in by_data_text, f"no /Headers resolved for '4.18'; found {associations}"
    assert set(by_data_text["4.18"]) >= {"Water", "c (J/g·K)"}

    assert "0.385" in by_data_text, f"no /Headers resolved for '0.385'; found {associations}"
    assert set(by_data_text["0.385"]) >= {"Copper", "c (J/g·K)"}


def _descendants(elements):
    for element in elements:
        yield element
        yield from _descendants(element.children)


def _assert_th_td_in_table_li_in_l(tree) -> None:
    """Assert per-element containment: every TH/TD is a descendant of *some* Table, and
    every LI is a descendant of *some* L. Uses object identity (`id()`), not type name, so
    a stray TH sitting outside every Table cannot be masked by a correctly-nested sibling
    TH elsewhere in the document contributing the type name "TH" to some vocabulary set.
    """
    all_elements = list(_descendants(tree))
    tables = [e for e in all_elements if e.type == "Table"]
    lists = [e for e in all_elements if e.type == "L"]
    assert tables, "no Table element found in structure tree"
    assert lists, "no L element found in structure tree"

    th_td_ids_in_tables = {
        id(e) for table in tables for e in _descendants(table.children) if e.type in ("TH", "TD")
    }
    li_ids_in_lists = {
        id(e) for lst in lists for e in _descendants(lst.children) if e.type == "LI"
    }

    for element in all_elements:
        if element.type in ("TH", "TD"):
            assert id(element) in th_td_ids_in_tables, (
                f"a {element.type} exists outside of any Table"
            )
        if element.type == "LI":
            assert id(element) in li_ids_in_lists, "an LI exists outside of any L"


def test_th_and_td_are_descendants_of_table_and_li_of_l(tmp_path: Path):
    """A flat type-vocabulary check would pass even if TH/TD lived outside any Table, or LI
    outside any L. Assert actual containment in the tree, not just presence somewhere in it.
    """
    target = tmp_path / "structured.pdf"
    render_html_to_pdf(STRUCTURED_HTML, target, title="Thermodynamics", lang="en")

    tree = structure_tree(target)
    _assert_th_td_in_table_li_in_l(tree)


def test_containment_check_rejects_a_th_outside_any_table():
    """Guard the guard: a synthetic tree with a TH sibling outside the Table (but sharing
    the same document, so "TH" is in the type vocabulary via the correctly-nested TH inside
    the table) must fail `_assert_th_td_in_table_li_in_l`. This is the exact defect a
    type-name-only check would miss -- it proves the containment assertion above can
    actually fail, rather than always passing because the type name is present somewhere.
    """
    stray_th = StructElement(type="TH", id=None, headers=(), page_ref=None, mcids=())
    nested_th = StructElement(type="TH", id=None, headers=(), page_ref=None, mcids=())
    table = StructElement(
        type="Table", id=None, headers=(), page_ref=None, mcids=(), children=(nested_th,)
    )
    the_list = StructElement(type="L", id=None, headers=(), page_ref=None, mcids=())
    tree = [table, the_list, stray_th]

    with pytest.raises(AssertionError, match="TH exists outside of any Table"):
        _assert_th_td_in_table_li_in_l(tree)


def test_cyclic_structure_tree_raises_instead_of_hanging(tmp_path: Path):
    """A self-referential /K (an element whose child is one of its own ancestors) must be
    rejected with a clear error, not recurse until the stack overflows -- rebind will later
    be pointed at arbitrary third-party scanned PDFs, not just its own generated output.
    """
    target = tmp_path / "cyclic.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))

    elem_a = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name("/Sect")))
    elem_b = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name("/P")))
    elem_a.K = elem_b
    elem_b.K = elem_a  # cycle: b's child is a, one of b's own ancestors

    struct_root = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name("/StructTreeRoot"), K=elem_a)
    )
    pdf.Root.StructTreeRoot = struct_root
    pdf.save(target)

    with pytest.raises(StructureTreeError):
        structure_element_types(target)
