from rebind.assemble import assemble
from rebind.extract import ImageRegion, Page, TextLine
from rebind.model import Artifact, Heading, ListNode, PageBreak, Paragraph, Placeholder
from rebind.profile import build_profile


def line(text, *, page=1, size=11.0, bold=False, y=400.0):
    return TextLine(text=text, page=page, bbox=(72.0, y, 400.0, y + size),
                    font="DejaVuSerif", size=size, bold=bold, italic=False)


def page_of(lines, number=1, images=()):
    return Page(number=number, width=612.0, height=792.0, lines=tuple(lines), images=tuple(images))


def custom_line(text, x0, x1, y, *, page=1, size=11.0, bold=False):
    """A line with an explicit horizontal extent, for exercising the column heuristic.

    `line()` always uses a fixed x-range, which is fine for reading-order tests but cannot
    represent columns or a narrower centered heading -- both need lines at distinct x-positions.
    """
    return TextLine(text=text, page=page, bbox=(x0, y, x1, y + size),
                     font="DejaVuSerif", size=size, bold=bold, italic=False)


def kinds(doc):
    return [node.kind for node in doc.nodes]


def test_headings_and_paragraphs_are_recovered():
    lines = [line("Chapter One", size=24.0, bold=True, y=700.0)]
    lines += [line("body text", y=400.0 - i) for i in range(20)]
    pages = [page_of(lines)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    assert isinstance(doc.nodes[0], PageBreak)
    heading = next(n for n in doc.nodes if isinstance(n, Heading))
    assert heading.text == "Chapter One"
    assert heading.level == 1
    assert any(isinstance(n, Paragraph) for n in doc.nodes)


def test_bulleted_lines_become_a_list():
    lines = [line("body", y=500.0 - i) for i in range(20)]
    lines += [line("• first", y=300.0), line("• second", y=280.0)]
    pages = [page_of(lines)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    lists = [n for n in doc.nodes if isinstance(n, ListNode)]
    assert len(lists) == 1
    assert [item.text for item in lists[0].items] == ["first", "second"]
    assert lists[0].ordered is False


def test_numbered_lines_become_an_ordered_list():
    lines = [line("body", y=500.0 - i) for i in range(20)]
    lines += [line("1. first", y=300.0), line("2. second", y=280.0)]
    pages = [page_of(lines)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    lists = [n for n in doc.nodes if isinstance(n, ListNode)]
    assert lists[0].ordered is True
    assert [item.text for item in lists[0].items] == ["first", "second"]


def test_running_headers_become_artifacts_not_paragraphs():
    pages = []
    for number in range(1, 11):
        lines = [line("Course Catalog", page=number, y=760.0, size=9.0)]
        lines += [line("body text", page=number, y=400.0 - i) for i in range(10)]
        pages.append(page_of(lines, number=number))
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    artifacts = [n for n in doc.nodes if isinstance(n, Artifact)]
    assert artifacts, "the running header should be an Artifact"
    assert all("Course Catalog" not in n.text for n in doc.nodes if isinstance(n, Paragraph))


def test_scanned_page_yields_a_flagged_placeholder_and_is_recorded():
    pages = [page_of([line("body") for _ in range(10)], number=1),
             page_of([], number=2)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    placeholder = next(n for n in doc.nodes if isinstance(n, Placeholder))
    assert placeholder.page == 2
    assert "no-text-layer" in placeholder.flags
    assert doc.scanned_pages == (2,)


def test_image_region_becomes_a_placeholder_never_a_figure():
    """PDF/UA requires /Alt on figures and Phase 1 cannot produce it honestly."""
    image_bbox = (100.0, 100.0, 300.0, 300.0)
    pages = [page_of([line("body") for _ in range(10)],
                     images=[ImageRegion(page=1, bbox=image_bbox)])]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    placeholders = [n for n in doc.nodes if isinstance(n, Placeholder)]
    assert placeholders
    image_placeholder = next(p for p in placeholders if "image" in p.reason.lower())
    assert image_placeholder.bbox == image_bbox
    # model.py defines no Figure class at all today, so this can never fail; it is a future
    # regression guard, not live protection -- do not mistake it for one.
    assert "Figure" not in kinds(doc)


def test_scanned_page_with_images_gets_both_the_page_and_image_placeholders():
    pages = [page_of([], number=1, images=[ImageRegion(page=1, bbox=(50.0, 60.0, 200.0, 300.0))])]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    placeholders = [n for n in doc.nodes if isinstance(n, Placeholder)]
    page_placeholder = next(p for p in placeholders if "no-text-layer" in p.flags)
    assert page_placeholder.bbox == (0.0, 0.0, 612.0, 792.0)
    image_placeholder = next(p for p in placeholders if "image" in p.reason.lower())
    assert image_placeholder.bbox == (50.0, 60.0, 200.0, 300.0)
    assert image_placeholder is not page_placeholder


def test_every_node_has_provenance_and_an_id():
    pages = [page_of([line("body") for _ in range(10)])]
    doc = assemble(pages, build_profile(pages), title="T")

    for node in doc.nodes:
        assert node.id
        assert node.page >= 1
        assert len(node.bbox) == 4
        if isinstance(node, ListNode):
            for item in node.items:
                assert item.id
                assert item.page >= 1
                assert len(item.bbox) == 4


def test_list_bbox_is_the_union_of_its_items():
    lines = [line("body", y=500.0 - i) for i in range(20)]
    lines += [line("• first", y=300.0), line("• second", y=100.0)]
    pages = [page_of(lines)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    list_node = next(n for n in doc.nodes if isinstance(n, ListNode))
    item_bboxes = [item.bbox for item in list_node.items]
    assert list_node.bbox[0] == min(b[0] for b in item_bboxes)
    assert list_node.bbox[1] == min(b[1] for b in item_bboxes)
    assert list_node.bbox[2] == max(b[2] for b in item_bboxes)
    assert list_node.bbox[3] == max(b[3] for b in item_bboxes)


def test_two_column_page_paragraphs_are_flagged_multi_column_suspected():
    left = [custom_line(f"left {i}", 72.0, 250.0, 300.0 + i * 20.0) for i in range(5)]
    right = [custom_line(f"right {i}", 300.0, 480.0, 300.0 + i * 20.0) for i in range(5)]
    pages = [page_of(left + right)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    paragraphs = [n for n in doc.nodes if isinstance(n, Paragraph)]
    assert paragraphs
    assert all("multi-column-suspected" in p.flags for p in paragraphs)


def test_single_column_page_paragraphs_are_not_flagged_multi_column():
    lines = [line("body text", y=400.0 - i) for i in range(20)]
    pages = [page_of(lines)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    paragraphs = [n for n in doc.nodes if isinstance(n, Paragraph)]
    assert paragraphs
    assert all("multi-column-suspected" not in p.flags for p in paragraphs)


def test_centered_heading_above_full_width_body_is_not_flagged_multi_column():
    heading = [custom_line("Chapter One", 250.0, 350.0, 700.0, size=24.0, bold=True)]
    body = [custom_line("body text", 72.0, 540.0, 400.0 - i * 20.0) for i in range(10)]
    pages = [page_of(heading + body)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    assert any(isinstance(n, Heading) for n in doc.nodes)
    paragraphs = [n for n in doc.nodes if isinstance(n, Paragraph)]
    assert paragraphs
    assert all("multi-column-suspected" not in p.flags for p in paragraphs)


def test_list_id_is_deterministic_and_content_derived():
    def make_lines():
        lines = [line("body", y=500.0 - i) for i in range(20)]
        lines += [line("• first", y=300.0), line("• second", y=280.0)]
        return lines

    pages_a = [page_of(make_lines())]
    pages_b = [page_of(make_lines())]
    profile_a = build_profile(pages_a)
    profile_b = build_profile(pages_b)

    list_a = next(n for n in assemble(pages_a, profile_a, title="T").nodes
                  if isinstance(n, ListNode))
    list_b = next(n for n in assemble(pages_b, profile_b, title="T").nodes
                  if isinstance(n, ListNode))
    assert list_a.id == list_b.id

    lines_c = [line("body", y=500.0 - i) for i in range(20)]
    lines_c += [line("• third", y=300.0), line("• fourth", y=280.0)]
    pages_c = [page_of(lines_c)]
    profile_c = build_profile(pages_c)
    list_c = next(n for n in assemble(pages_c, profile_c, title="T").nodes
                  if isinstance(n, ListNode))
    assert list_c.id != list_a.id
