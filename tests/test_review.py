from pathlib import Path

from rebind.extract import extract_pages
from rebind.layout import order_page
from rebind.profile import build_profile
from rebind.review import page_order, summarize
from tests.fixtures import born_digital_pdf, born_digital_pdf_two_column


def _order_for(source: Path, figure_boxes: tuple = ()):
    pages = list(extract_pages(source))
    profile = build_profile(pages)
    page = pages[0]
    return page_order(page, order_page(page, profile, figure_boxes), figure_boxes)


def test_a_plain_single_column_page_needs_no_review(tmp_path: Path):
    # There is no decision to second-guess on a page that reads straight down, and asking a human
    # to confirm 300 of them is how a review gets skipped entirely.
    source = born_digital_pdf(
        "<h1>Title</h1>" + "".join(f"<p>Paragraph {i} of ordinary prose.</p>" for i in range(6)),
        tmp_path / "in.pdf")
    order = _order_for(source)
    assert not order.needs_review
    assert order.columns <= 1
    assert [b.number for b in order.blocks] == list(range(1, len(order.blocks) + 1))


def test_a_two_column_page_is_flagged_with_its_blocks_in_order(tmp_path: Path):
    source = born_digital_pdf_two_column(tmp_path / "in.pdf")
    order = _order_for(source)
    assert order.needs_review
    assert order.columns == 2
    assert "columns" in order.reason
    sides = [b.text.split()[0] for b in order.blocks if b.text.split()]
    sides = [s for s in sides if s in ("LEFT", "RIGHT")]
    assert sides == sorted(sides, key=lambda s: 0 if s == "LEFT" else 1), sides


def test_summary_separates_the_clear_pages_from_the_ones_needing_an_eye(tmp_path: Path):
    plain = _order_for(born_digital_pdf("<p>One plain paragraph.</p>", tmp_path / "a.pdf"))
    columns = _order_for(born_digital_pdf_two_column(tmp_path / "b.pdf"))
    summary = summarize([plain, columns])
    assert summary["checked"] == 2
    assert summary["clear"] == 1
    assert [p["page"] for p in summary["pages"]] == [columns.page]
    assert summary["pages"][0]["blocks"], "a flagged page must carry the blocks to display"
