"""The layout model's regions as hints: what they decide, and what they are never trusted with.

Most of these run on hand-made regions, so they test Rebind's use of the model rather than the
model. The ones marked `layout_model` run the real one and need `models/` (scripts/fetch_models.py).
"""

from pathlib import Path

import pikepdf
import pytest

from rebind.extract import TextLine
from rebind.layout_model import PageHints, Region, hints_for
from rebind.remediate import _caption_groups, plan_page, remediate
from tests.fixtures import born_digital_pdf


def line(text: str, box: tuple, *, font: str = "F", size: float = 10.0,
         bold: bool = False) -> TextLine:
    return TextLine(text=text, page=1, bbox=box, font=font, size=size, bold=bold, italic=False)


def test_without_a_model_there_are_no_hints():
    hints = hints_for([line("a", (0, 0, 10, 10))], None)
    assert hints.tables is None and not hints.furniture and not hints.blocks


def test_lines_take_the_region_their_centre_is_in():
    head = line("Journal of Things 12:3", (72, 760, 300, 770))
    body = line("Body text.", (72, 600, 300, 610))
    cell = line("42", (72, 400, 90, 410))
    regions = (Region("header", (70, 755, 310, 775), 0.9),
               Region("text", (70, 590, 310, 620), 0.95),
               Region("table", (70, 380, 310, 420), 0.97))
    hints = hints_for([head, body, cell], regions)
    assert hints.furniture == {id(head)}
    assert hints.blocks == {id(body): 1}
    assert hints.tables == {id(cell): 2}


def test_unsure_furniture_is_not_taken_out_of_the_reading():
    """Furniture is not read at all, so a hesitant call is ignored rather than acted on."""
    folio = line("12", (300, 20, 310, 30))
    hints = hints_for([folio], (Region("number", (295, 15, 315, 35), 0.6),))
    assert not hints.furniture


def test_a_lone_line_in_a_text_box_can_still_be_a_heading():
    """On a sparse page the model calls a real chapter title "text" as often as not; only a line
    run in with others in one block of text is kept from being a heading."""
    title = line("Chapter Two", (72, 700, 250, 724), size=24)
    lead = line("Run-in Heading set large", (72, 600, 300, 614), size=14)
    rest = line("and the paragraph it opens carries on here.", (72, 586, 300, 598))
    regions = (Region("text", (70, 695, 260, 730), 0.6),
               Region("text", (70, 580, 310, 620), 0.95))
    hints = hints_for([title, lead, rest], regions)
    assert id(title) not in hints.not_headings
    assert {id(lead), id(rest)} <= hints.not_headings


def test_a_title_next_to_a_table_captions_it_but_one_next_to_a_picture_does_not():
    table_title = line("Table 1 Results", (72, 500, 300, 510))
    figure_title = line("Fig. 1 A coin", (320, 500, 540, 510))
    regions = (Region("figure_title", (70, 495, 305, 515), 0.9),
               Region("table", (70, 400, 305, 490), 0.97),
               Region("figure_title", (315, 495, 545, 515), 0.9),
               Region("image", (315, 520, 545, 700), 0.9))
    hints = hints_for([table_title, figure_title], regions)
    assert hints.captions == {id(table_title): 0}


def test_one_block_is_one_paragraph_even_where_the_margins_say_otherwise():
    """A born-digital paragraph whose lines each name a different font subset was one element per
    line: the margin rules refuse to join lines set in different faces."""
    lines = [line(f"Line {k} of the introduction runs the full measure.", (72, 600 - 14 * k,
                                                                          300, 610 - 14 * k),
                  font=f"Subset{k}") for k in range(4)]
    roles = ["P"] * 4
    assert len(plan_page(lines, roles)) == 4, "the geometric rules alone split it"
    hints = PageHints(tables={}, blocks={id(ln): 0 for ln in lines})
    assert plan_page(lines, roles, None, hints) == [{"kind": "P", "first": 0, "last": 3}]


def test_two_blocks_are_two_paragraphs_even_where_the_margins_would_join_them():
    lines = [line("A paragraph that ends at the full measure of the col-", (72, 600, 300, 610)),
             line("umn and the next one starting flush beneath it.", (72, 586, 300, 596))]
    roles = ["P", "P"]
    assert len(plan_page(lines, roles)) == 1
    hints = PageHints(tables={}, blocks={id(lines[0]): 0, id(lines[1]): 1})
    assert len(plan_page(lines, roles, None, hints)) == 2


def test_a_table_is_exactly_the_lines_in_its_region():
    """On a two-column page, prose in the other column that happens to line up with a table's
    columns was swallowed into the table. With a table region, only its own lines are cells."""
    cells = [line(text, (x, y, x + 30, y + 10))
             for y in (500, 486, 472) for x, text in ((72, "A"), (140, "1"), (210, "2"))]
    beside = line("prose in the next column", (320, 486, 540, 496))
    lines = cells[:3] + [beside] + cells[3:]
    roles = ["P"] * len(lines)
    hints = PageHints(tables={id(cell): 7 for cell in cells})
    plan = plan_page(lines, roles, None, hints)
    tables = [entry for entry in plan if entry["kind"] == "Table"]
    assert [(t["first"], t["last"]) for t in tables] == [(0, 2), (4, 9)]
    assert {"kind": "P", "first": 3, "last": 3} in plan


def test_a_caption_does_not_reach_across_a_gutter_into_a_boxed_block():
    title = line("Table 1 The descriptive statistics", (72, 400, 290, 410))
    level = line("30 min), a small percentage (5%) of", (306, 400, 540, 410))
    roles = ["Caption", "P"]
    groups = _caption_groups([title, level], roles, {id(title): 0}, [[title]],
                             boxed={id(level)})
    assert groups == [0, None] and roles[1] == "P"


@pytest.mark.layout_model
def test_the_model_runs_and_its_regions_land_on_the_page(tmp_path: Path):
    from rebind import layout_model
    from rebind.extract import extract_pages

    source = born_digital_pdf(
        "<h1>A Report</h1>"
        + "".join(f"<p>{'Paragraph text that runs on for a while. ' * 12}</p>" for _ in range(3)),
        tmp_path / "in.pdf")
    page = next(iter(extract_pages(source)))
    regions = layout_model.page_regions(source, 1, page.width, page.height)
    assert regions, "the model found nothing on a page of plain prose"
    for region in regions:
        x0, y0, x1, y1 = region.bbox
        assert 0 <= x0 < x1 <= page.width + 1 and 0 <= y0 < y1 <= page.height + 1
    assert any(region.label == "text" for region in regions)
    # Asked again for the unchanged file, it is not run again.
    assert layout_model.page_regions(source, 1, page.width, page.height) is regions


@pytest.mark.layout_model
def test_remediating_with_the_model_still_validates_and_reads_every_word(tmp_path: Path):
    body = "Paragraph text that runs on for a while. " * 12
    source = born_digital_pdf(
        "<h1>A Report</h1>" + "".join(f"<p>{body}</p>" for _ in range(3)), tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    result = remediate(source, out, title="T")
    assert result.structure_ok, result.structure_issues
    with pikepdf.open(out) as pdf:
        kinds = [str(elem.S) for elem in pdf.Root.StructTreeRoot.K[0].K]
    assert kinds.count("/P") == 3, kinds
    read = " ".join(e["text"] for e in result.elements if e["kind"] != "Artifact")
    assert read.count("Paragraph text") >= 3


def test_two_tables_side_by_side_stay_two_tables_in_the_reading_order():
    """On a two-page spread with a table on each page, the rows share their heights. Banded
    together, the two tables' rows interleaved and every switch side started a new table."""
    from rebind.layout import PlacedLine, _splice_tables

    left = [line(f"L{r}{c}", (72 + 60 * c, 500 - 14 * r, 110 + 60 * c, 510 - 14 * r))
            for r in range(3) for c in range(3)]
    right = [line(f"R{r}{c}", (620 + 60 * c, 500 - 14 * r, 660 + 60 * c, 510 - 14 * r))
             for r in range(3) for c in range(3)]
    placed = [PlacedLine(line=ln, column=0) for ln in left + right]
    table_of = {**{id(ln): 0 for ln in left}, **{id(ln): 1 for ln in right}}
    walk = [p.line.text for p in _splice_tables(placed, set(table_of), table_of)]
    assert walk == [ln.text for ln in left + right], walk


def test_a_failed_fetch_leaves_nothing_behind_and_does_not_raise(tmp_path: Path, monkeypatch):
    """Offline on first run, a source checkout carries on without the model and tries again next
    time -- no exception, and no half-written file mistaken for the model."""
    from rebind import weights

    monkeypatch.setattr(weights, "model_dir", lambda: tmp_path / "models")
    monkeypatch.setitem(weights.WEIGHTS, "pp_doc_layoutv3.onnx", weights.Weight(
        (tmp_path / "missing.onnx").as_uri(), "0" * 64, "the page-layout model"))
    said: list[str] = []
    assert weights.fetch("pp_doc_layoutv3.onnx", report=said.append) is None
    assert not list((tmp_path / "models").glob("*"))
    assert said and "continuing without it" in said[-1]


def test_a_download_that_fails_its_checksum_is_discarded(tmp_path: Path, monkeypatch):
    from rebind import weights

    bogus = tmp_path / "bogus.onnx"
    bogus.write_bytes(b"not the model")
    monkeypatch.setattr(weights, "model_dir", lambda: tmp_path / "models")
    monkeypatch.setitem(weights.WEIGHTS, "latex_ocr/encoder.onnx", weights.Weight(
        bogus.as_uri(), "0" * 64, "a model"))
    assert weights.fetch("latex_ocr/encoder.onnx", report=None) is None
    assert not list((tmp_path / "models").rglob("*.onnx*"))


def test_a_displayed_equation_and_its_number_are_one_formula():
    first = line("f(x) = a x^2 + b x", (200, 500, 360, 512))
    second = line("+ c", (260, 484, 290, 496))
    number = line("(3)", (520, 492, 540, 504))
    prose = line("where a, b and c are constants.", (72, 460, 400, 472))
    regions = (Region("display_formula", (195, 480, 365, 515), 0.9),
               Region("formula_number", (515, 488, 545, 508), 0.8),
               Region("text", (70, 455, 405, 476), 0.95))
    lines = [first, second, number, prose]
    hints = hints_for(lines, regions)
    assert hints.formulas == {id(first): 0, id(second): 0, id(number): 0}
    roles = ["Formula", "Formula", "Formula", "P"]
    assert plan_page(lines, roles, None, hints) == [{"kind": "Formula", "first": 0, "last": 2},
                                                    {"kind": "P", "first": 3, "last": 3}]


def test_an_equation_number_with_no_equation_beside_it_is_left_alone():
    number = line("(3)", (520, 492, 540, 504))
    hints = hints_for([number], (Region("formula_number", (515, 488, 545, 508), 0.8),))
    assert not hints.formulas


def test_a_boxed_equation_is_tagged_as_a_formula_and_takes_a_description(
        tmp_path: Path, monkeypatch, verapdf_exe: Path):
    """The model's equation region becomes a /Formula. Undescribed, a screen reader reads its own
    text; described, it reads the words the person gave -- and either way the document conforms."""
    from rebind import layout_model
    from rebind.extract import extract_pages
    from rebind.remediate import Edits
    from rebind.validate import validate_pdf_ua

    source = born_digital_pdf(
        "<p>The curve is given by the equation below, for constants a, b and c.</p>"
        "<p style='text-align:center'>y = ax2 + bx + c</p>"
        "<p>Its turning point lies where the derivative vanishes.</p>", tmp_path / "in.pdf")
    page = next(iter(extract_pages(source)))
    equation = next(ln for ln in page.lines if ln.text.strip().startswith("y = "))
    x0, y0, x1, y1 = equation.bbox
    regions = (Region("display_formula", (x0 - 4, y0 - 4, x1 + 4, y1 + 4), 0.9),)
    monkeypatch.setattr(layout_model, "page_regions", lambda *args, **kwargs: regions)

    plain = remediate(source, tmp_path / "plain.pdf", title="T")
    formulas = [e for e in plain.elements if e["kind"] == "Formula"]
    assert [e["text"] for e in formulas] == ["y = ax2 + bx + c"]

    words = "y equals a x squared plus b x plus c"
    out = tmp_path / "described.pdf"
    remediate(source, out, title="T", edits=Edits(alts={formulas[0]["id"]: words}))
    with pikepdf.open(out) as pdf:
        tagged = [elem for elem in pdf.Root.StructTreeRoot.K[0].K if str(elem.S) == "/Formula"]
    assert [str(elem.Alt) for elem in tagged] == [words]
    for target in (tmp_path / "plain.pdf", out):
        report = validate_pdf_ua(target, verapdf_exe=verapdf_exe)
        assert report.compliant, report.summary()


def test_confident_pictures_are_kept_for_figures_and_text_regions_are_noted():
    caption = line("Fig. 1 A coin", (72, 300, 200, 310))
    label = line("a", (150, 400, 156, 408))
    regions = (Region("image", (70, 320, 300, 500), 0.9),
               Region("image", (320, 320, 500, 500), 0.55),
               Region("figure_title", (70, 295, 205, 315), 0.9))
    hints = hints_for([caption, label], regions)
    assert hints.pictures == [(70, 320, 300, 500)]
    assert id(caption) in hints.in_text and id(label) not in hints.in_text


@pytest.mark.parametrize("text, in_text, modelled, speck", [
    ("|", False, True, True),
    ("~", True, True, True),            # marks alone are never words, wherever they are
    ("—", False, False, True),
    ("e", False, True, True),           # a lone letter where no words were found
    ("e", True, True, False),           # ...but kept inside a block of text
    ("e", False, False, False),         # ...and kept when there is no model to say where words are
    ("Q:", False, True, True),
    ("Fig", False, True, False),
    ("3.", True, True, False),
])
def test_what_counts_as_a_speck(text, in_text, modelled, speck):
    from rebind.remediate import _is_speck

    mark = line(text, (100, 100, 110, 110))
    hints = PageHints(tables={} if modelled else None, in_text={id(mark)} if in_text else set())
    assert _is_speck(mark, hints) is speck


def test_an_uncaptioned_picture_the_model_found_on_a_scan_is_kept_to_be_described(
        tmp_path: Path, monkeypatch):
    """A pixel guess nobody captions is dropped -- on a page of plans it was mostly hatching. A
    picture the layout model boxed is kept, undescribed, so the editor asks for a description."""
    from rebind import layout_model
    from tests.fixtures import pdf_image_only_scan

    source = pdf_image_only_scan(
        "<p style='font-size:16pt'>The temple stood on the hill above the forum.</p>"
        "<div style='margin-top:120pt;width:220pt;height:160pt;background:#444'></div>",
        tmp_path / "scan.pdf")
    with pikepdf.open(source) as pdf:
        width, height = (float(v) for v in pdf.pages[0].mediabox[2:])
    monkeypatch.setattr(layout_model, "page_regions", lambda *args, **kwargs: (
        Region("image", (0.0, 0.0, width, height * 0.6), 0.9),))

    result = remediate(source, tmp_path / "out.pdf", title="T")
    assert [f["id"] for f in result.figures] == ["p1m0"]
    assert any("temple" in e["text"] for e in result.elements if e["kind"] == "P")


def test_a_picture_repeated_in_the_same_place_on_many_pages_is_an_ornament():
    from rebind.layout_model import drop_ornaments

    icon = (605.0, 692.0, 638.0, 724.0)
    photo = (627.0, 481.0, 698.0, 586.0)
    pages = [PageHints(pictures=[icon]), PageHints(pictures=[(606.0, 694.0, 640.0, 727.0), photo]),
             PageHints(pictures=[(603.0, 692.0, 637.0, 726.0)]), PageHints(pictures=[photo])]
    drop_ornaments(pages)
    assert [hints.pictures for hints in pages] == [[], [photo], [], [photo]]
