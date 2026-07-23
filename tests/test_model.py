from rebind.model import Document, Heading, Paragraph, Placeholder, node_id


def test_node_id_is_stable_for_identical_input():
    first = node_id(page=3, bbox=(72.0, 700.0, 300.0, 720.0), page_width=612, page_height=792,
                    text="Chapter One")
    second = node_id(page=3, bbox=(72.0, 700.0, 300.0, 720.0), page_width=612, page_height=792,
                     text="Chapter One")

    assert first == second


def test_node_id_differs_on_page_bbox_or_text():
    base = dict(page=3, bbox=(72.0, 700.0, 300.0, 720.0), page_width=612, page_height=792,
                text="Chapter One")

    assert node_id(**{**base, "page": 4}) != node_id(**base)
    assert node_id(**{**base, "text": "Chapter Two"}) != node_id(**base)
    assert node_id(**{**base, "bbox": (72.0, 600.0, 300.0, 620.0)}) != node_id(**base)


def test_node_id_survives_sub_point_position_jitter():
    """Re-extraction can shift a bbox by a fraction of a point. Ids must not churn on that."""
    a = node_id(page=1, bbox=(72.0, 700.0, 300.0, 720.0), page_width=612, page_height=792,
                text="Heading")
    b = node_id(page=1, bbox=(72.02, 700.01, 300.01, 720.02), page_width=612, page_height=792,
                text="Heading")

    assert a == b


def test_document_round_trips_through_json():
    doc = Document(
        title="Test",
        lang="en",
        scanned_pages=(4, 5),
        source_was_tagged=False,
        nodes=[
            Heading(id="h1", page=1, bbox=(1, 2, 3, 4), confidence=1.0, stage="assemble",
                    flags=[], level=1, text="Chapter One"),
            Paragraph(id="p1", page=1, bbox=(1, 2, 3, 4), confidence=1.0, stage="assemble",
                      flags=[], text="Body."),
            Placeholder(id="x1", page=4, bbox=(0, 0, 612, 792), confidence=0.0, stage="assemble",
                        flags=["no-text-layer"], reason="no text layer on source page 4"),
        ],
    )

    restored = Document.from_json(doc.to_json())

    assert restored == doc
    assert restored.nodes[0].level == 1
    assert restored.scanned_pages == (4, 5)


def test_every_node_carries_provenance():
    node = Paragraph(id="p", page=7, bbox=(1, 2, 3, 4), confidence=0.9, stage="assemble",
                     flags=[], text="x")

    assert node.page == 7
    assert node.bbox == (1, 2, 3, 4)
