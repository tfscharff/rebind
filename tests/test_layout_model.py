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
