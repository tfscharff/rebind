from rebind.assemble import assemble
from rebind.extract import ImageRegion, Page, TextLine
from rebind.model import Artifact, Heading, ListNode, PageBreak, Paragraph, Placeholder
from rebind.profile import build_profile


def line(text, *, page=1, size=11.0, bold=False, y=400.0):
    return TextLine(text=text, page=page, bbox=(72.0, y, 400.0, y + size),
                    font="DejaVuSerif", size=size, bold=bold, italic=False)


def page_of(lines, number=1, images=()):
    return Page(number=number, width=612.0, height=792.0, lines=tuple(lines), images=tuple(images))


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
    pages = [page_of([line("body") for _ in range(10)],
                     images=[ImageRegion(page=1, bbox=(100.0, 100.0, 300.0, 300.0))])]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    placeholders = [n for n in doc.nodes if isinstance(n, Placeholder)]
    assert placeholders
    assert "image" in placeholders[0].reason.lower()
    assert "Figure" not in kinds(doc)


def test_every_node_has_provenance_and_an_id():
    pages = [page_of([line("body") for _ in range(10)])]
    doc = assemble(pages, build_profile(pages), title="T")

    for node in doc.nodes:
        assert node.id
        assert node.page >= 1
        assert len(node.bbox) == 4
