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


def test_ocr_over_scan_page_text_is_flagged_and_confidence_capped():
    # A page with a text layer AND a page-covering image is an OCR'd scan: the text is recognizer
    # output, so it must be flagged 'ocr-source' and its confidence capped, not presented as exact.
    lines = [line("body text", y=400.0 - i) for i in range(20)]
    full_page = ImageRegion(page=1, bbox=(0.0, 0.0, 612.0, 792.0))
    small = ImageRegion(page=1, bbox=(100.0, 100.0, 160.0, 160.0))
    pages = [page_of(lines, images=[full_page, small])]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    paragraphs = [n for n in doc.nodes if isinstance(n, Paragraph)]
    assert paragraphs
    assert all("ocr-source" in p.flags for p in paragraphs)
    assert all(p.confidence <= 0.5 for p in paragraphs)

    placeholders = [n for n in doc.nodes if isinstance(n, Placeholder)]
    # The background scan (page-covering image) is NOT emitted as a figure placeholder...
    assert not any(p.bbox == (0.0, 0.0, 612.0, 792.0) for p in placeholders)
    # ...but a genuinely smaller embedded image still is.
    assert any(p.bbox == (100.0, 100.0, 160.0, 160.0) for p in placeholders)


def test_born_digital_page_is_not_treated_as_ocr_source():
    # No page-covering image: ordinary born-digital text is untouched -- no flag, exact confidence,
    # and its small images are still placeholdered.
    lines = [line("body text", y=400.0 - i) for i in range(20)]
    small = ImageRegion(page=1, bbox=(100.0, 100.0, 160.0, 160.0))
    pages = [page_of(lines, images=[small])]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    paragraphs = [n for n in doc.nodes if isinstance(n, Paragraph)]
    assert paragraphs
    assert all("ocr-source" not in p.flags for p in paragraphs)
    assert any(p.confidence == 1.0 for p in paragraphs)
    assert any(isinstance(n, Placeholder) and n.bbox == (100.0, 100.0, 160.0, 160.0)
               for n in doc.nodes)


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


def test_two_column_page_is_reconstructed_in_column_order():
    # A clean, wide gutter (250->300 on a 612pt page) is reconstructed, not merely flagged: the
    # whole left column reads before the whole right column, and each paragraph records its column.
    left = [custom_line(f"left {i}", 72.0, 250.0, 300.0 + i * 20.0) for i in range(5)]
    right = [custom_line(f"right {i}", 300.0, 480.0, 300.0 + i * 20.0) for i in range(5)]
    pages = [page_of(left + right)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    paragraphs = [n for n in doc.nodes if isinstance(n, Paragraph)]
    texts = [p.text for p in paragraphs]
    # Left column entirely before the right column (top-to-bottom within each, so "left 4" -- the
    # highest y -- comes first among the lefts).
    assert texts.index("left 0") < texts.index("right 0")
    assert texts.index("left 1") < texts.index("right 0")
    left_paras = [p for p in paragraphs if p.text.startswith("left")]
    right_paras = [p for p in paragraphs if p.text.startswith("right")]
    assert all("column-0" in p.flags for p in left_paras)
    assert all("column-1" in p.flags for p in right_paras)
    # A clean cut is confident: it must NOT raise the marginal-gutter warning.
    assert all("multi-column-suspected" not in p.flags for p in paragraphs)


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


def test_marker_and_content_on_separate_lines_merge_into_one_item():
    """WeasyPrint's native <ul>/<ol> markers arrive as their own TextLine, sharing the content
    line's y-range and abutting its left edge -- this is the real-world shape the merge exists
    for, as opposed to the single synthetic "• first" line the older tests exercise."""
    marker1 = custom_line("•", 98.02, 108.0, 613.74)
    content1 = custom_line("alpha", 108.0, 138.75, 613.74)
    marker2 = custom_line("•", 98.02, 108.0, 598.34)
    content2 = custom_line("beta", 108.0, 138.75, 598.34)
    lines = [line("body", y=650.0 - i) for i in range(10)] + [marker1, content1, marker2, content2]
    pages = [page_of(lines)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    lists = [n for n in doc.nodes if isinstance(n, ListNode)]
    assert len(lists) == 1
    assert [item.text for item in lists[0].items] == ["alpha", "beta"]


def test_stray_marker_far_from_unrelated_paragraph_is_not_merged():
    """A decorative bullet or footnote marker must not annex a later, unrelated paragraph into a
    fictional list item with a fabricated bbox (Finding 1). The marker itself must still surface
    somewhere honest (Finding 2) -- not silently dropped."""
    marker = custom_line("•", 98.02, 108.0, 700.0)
    unrelated = custom_line("Some unrelated paragraph.", 72.0, 400.0, 500.0)
    lines = [line("body", y=650.0 - i) for i in range(10)] + [marker, unrelated]
    pages = [page_of(lines)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    paragraphs = [n for n in doc.nodes if isinstance(n, Paragraph)]
    para = next((p for p in paragraphs if p.text == "Some unrelated paragraph."), None)
    assert para is not None, "the unrelated paragraph must not be absorbed into a list item"
    assert para.bbox == unrelated.bbox, "the paragraph's bbox must describe only itself"

    lists = [n for n in doc.nodes if isinstance(n, ListNode)]
    assert lists, "the stray marker must not be silently dropped"
    assert any(item.text == "•" for item in lists[0].items), (
        "the degenerate item must carry the marker's own glyph, not an empty string"
    )


def test_marker_followed_by_heading_is_not_lost():
    lines = [line("body", y=500.0 - i) for i in range(20)]
    lines.append(line("•", y=300.0))
    lines.append(line("Chapter Two", size=24.0, bold=True, y=280.0))
    pages = [page_of(lines)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    heading = next(n for n in doc.nodes if isinstance(n, Heading))
    assert heading.text == "Chapter Two"
    lists = [n for n in doc.nodes if isinstance(n, ListNode)]
    assert lists, "the marker preceding a heading must not be dropped"
    assert any(item.text == "•" for item in lists[0].items)


def test_marker_followed_by_artifact_is_not_lost():
    def footer_page(number, body_y):
        return page_of(
            [line("body", page=number, y=body_y), line("Footer", page=number, y=30.0, size=9.0)],
            number=number,
        )

    page1_lines = [line("body", y=500.0 - i) for i in range(5)]
    page1_lines.append(line("•", y=90.0))
    page1_lines.append(line("Footer", y=30.0, size=9.0))
    pages = [page_of(page1_lines, number=1), footer_page(2, 500.0), footer_page(3, 500.0)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    artifacts = [n for n in doc.nodes if isinstance(n, Artifact)]
    assert any(a.page == 1 for a in artifacts), "the footer must be recognized as an artifact"
    lists = [n for n in doc.nodes if isinstance(n, ListNode)]
    assert lists, "the marker preceding the artifact must not be dropped"
    assert any(item.text == "•" for item in lists[0].items)


def test_marker_followed_immediately_by_another_marker_keeps_both():
    lines = [line("body", y=500.0 - i) for i in range(20)]
    lines.append(line("•", y=300.0))
    lines.append(line("*", y=280.0))
    pages = [page_of(lines)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    lists = [n for n in doc.nodes if isinstance(n, ListNode)]
    assert lists, "both stray markers must surface, not be dropped"
    assert [item.text for item in lists[0].items] == ["•", "*"]


def test_marker_at_end_of_page_with_content_on_next_page_keeps_both():
    page1_lines = [line("body", y=500.0 - i, page=1) for i in range(20)]
    page1_lines.append(line("•", y=100.0, page=1))
    page2_lines = [line("Next page content", y=700.0, page=2)]
    pages = [page_of(page1_lines, number=1), page_of(page2_lines, number=2)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    lists = [n for n in doc.nodes if isinstance(n, ListNode)]
    assert lists, "the marker stranded at end of page must not be dropped"
    assert any(item.text == "•" for item in lists[0].items)
    paragraphs = [n for n in doc.nodes if isinstance(n, Paragraph)]
    assert any(p.text == "Next page content" for p in paragraphs), (
        "content on the following page must not be merged into the previous page's marker"
    )


def test_ordered_marker_only_line_becomes_ordered_list():
    """WeasyPrint's native <ol> marker glyph has no trailing space ("1.", not "1. "), unlike the
    combined form the older test exercises."""
    marker1 = custom_line("1.", 98.02, 108.0, 613.74)
    content1 = custom_line("alpha", 108.0, 138.75, 613.74)
    marker2 = custom_line("2.", 98.02, 108.0, 598.34)
    content2 = custom_line("beta", 108.0, 138.75, 598.34)
    lines = [line("body", y=650.0 - i) for i in range(10)] + [marker1, content1, marker2, content2]
    pages = [page_of(lines)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    lists = [n for n in doc.nodes if isinstance(n, ListNode)]
    assert len(lists) == 1
    assert lists[0].ordered is True
    assert [item.text for item in lists[0].items] == ["alpha", "beta"]


def test_year_starting_a_sentence_is_not_mistaken_for_a_list_marker():
    lines = [line("body", y=500.0 - i) for i in range(20)]
    lines.append(line("1996. It was a good year.", y=300.0))
    pages = [page_of(lines)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    assert not any(isinstance(n, ListNode) for n in doc.nodes)
    paragraphs = [n for n in doc.nodes if isinstance(n, Paragraph)]
    assert any(p.text == "1996. It was a good year." for p in paragraphs)


def test_heading_level_past_six_is_flagged_when_collapsed():
    """Regression test for Finding 4: `emit`/`render` both clamp heading levels to h6, so a
    document with more than six genuinely distinct heading styles (unbounded by design --
    invariant 5, no arbitrary limits) silently collapses levels 7+ into h6 downstream. The model
    itself must keep the true, uncapped level and flag the loss so it is visible, not silent."""
    lines = [line("body", y=780.0 - i) for i in range(30)]
    # Eight distinct heading sizes, strictly decreasing, all larger than body (11pt) -- assigned
    # levels 1 through 8 by build_profile's size-descending ranking.
    sizes = [40.0, 36.0, 32.0, 28.0, 24.0, 20.0, 16.0, 12.0]
    for index, size in enumerate(sizes):
        lines.append(line(f"Heading level {index + 1}", size=size, bold=True, y=100.0 - index))
    pages = [page_of(lines)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    headings = {h.text: h for h in doc.nodes if isinstance(h, Heading)}
    assert headings["Heading level 7"].level == 7
    assert "heading-level-collapsed" in headings["Heading level 7"].flags
    assert headings["Heading level 8"].level == 8
    assert "heading-level-collapsed" in headings["Heading level 8"].flags

    for index in range(1, 7):
        heading = headings[f"Heading level {index}"]
        assert heading.level == index
        assert "heading-level-collapsed" not in heading.flags


def test_stray_numeric_marker_does_not_fabricate_an_ordinal():
    """A bare '7.' with nothing ever arriving to merge with it must not become the sole item of
    an <ol> list -- CSS auto-numbers an empty <ol><li></li></ol> as '1.', reintroducing an
    ordinal the source never had. The degenerate item must fall back to unordered and carry the
    real marker glyph as its text, not an empty string standing in for a fabricated number."""
    lines = [line("body", y=500.0 - i) for i in range(20)]
    lines.append(line("7.", y=280.0))
    pages = [page_of(lines)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    lists = [n for n in doc.nodes if isinstance(n, ListNode)]
    assert lists, "the stray marker must not be silently dropped"
    assert lists[0].ordered is False, (
        "a marker with no content must not decide the list is ordered -- that fabricates '1.'"
    )
    assert [item.text for item in lists[0].items] == ["7."]


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
