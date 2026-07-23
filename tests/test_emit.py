from rebind.emit import PAGE_ANCHOR_PREFIX, to_html
from rebind.model import (
    Artifact,
    Document,
    Heading,
    ListItem,
    ListNode,
    PageBreak,
    Paragraph,
    Placeholder,
)


def doc(*nodes):
    return Document(title="T", lang="en", nodes=list(nodes))


def node_kwargs(**over):
    base = dict(id="n", page=1, bbox=(0, 0, 1, 1), confidence=1.0, stage="assemble", flags=[])
    base.update(over)
    return base


def test_headings_render_at_their_level():
    html = to_html(doc(Heading(**node_kwargs(), level=2, text="Sub")))

    assert "<h2>Sub</h2>" in html


def test_text_is_escaped():
    html = to_html(doc(Paragraph(**node_kwargs(), text="a < b & c")))

    assert "a &lt; b &amp; c" in html
    assert "a < b" not in html


def test_lists_render_as_ul_or_ol():
    item = ListItem(**node_kwargs(), text="first")
    unordered = to_html(doc(ListNode(**node_kwargs(), ordered=False, items=[item])))
    ordered = to_html(doc(ListNode(**node_kwargs(), ordered=True, items=[item])))

    assert "<ul>" in unordered and "<li>first</li>" in unordered
    assert "<ol>" in ordered


def test_list_item_text_is_escaped():
    item = ListItem(**node_kwargs(), text="a < b & c")
    html = to_html(doc(ListNode(**node_kwargs(), ordered=False, items=[item])))

    assert "a &lt; b &amp; c" in html
    assert "a < b" not in html


def test_artifacts_are_not_in_the_reading_order():
    html = to_html(doc(Artifact(**node_kwargs(), text="Course Catalog")))

    assert "Course Catalog" not in html


def test_placeholder_renders_visible_honest_text_with_page():
    html = to_html(doc(Placeholder(**node_kwargs(page=214), reason="no text layer on page 214")))

    assert "214" in html
    assert "not recoverable" in html or "not available" in html


def test_placeholder_reason_is_escaped():
    html = to_html(doc(Placeholder(**node_kwargs(page=1), reason="bad <tag> & stuff")))

    assert "&lt;tag&gt;" in html
    assert "<tag>" not in html


def test_page_break_emits_an_anchor_for_label_mapping():
    html = to_html(doc(PageBreak(**node_kwargs(page=3), label="3")))

    assert f"{PAGE_ANCHOR_PREFIX}3" in html


def test_page_break_label_is_escaped_as_an_attribute_value():
    # The anchor id is built from page number, not label, but the label is provenance data
    # extracted from the scan and must never be trusted to be attribute-safe if it is ever
    # rendered.
    html = to_html(doc(PageBreak(**node_kwargs(page=3), label='3" onmouseover="x')))

    assert 'onmouseover="x"' not in html


def test_unrecognized_node_kind_raises_instead_of_silently_dropping_content():
    # A bare ListItem at the top level of the document (not wrapped in a ListNode) is a valid
    # Node subclass that to_html has no rendering rule for. Silently skipping it would drop
    # content the reader never gets to see -- exactly the failure mode this project must not
    # ship. It must raise, not vanish.
    import pytest

    with pytest.raises(ValueError):
        to_html(doc(ListItem(**node_kwargs(), text="orphaned item")))


def test_deterministic_output_for_identical_document():
    document = doc(
        Heading(**node_kwargs(id="h1"), level=1, text="Title"),
        Paragraph(**node_kwargs(id="p1"), text="Body"),
    )

    assert to_html(document) == to_html(document)
