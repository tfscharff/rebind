"""Tests for the browser UI: the review-queue summary and the HTTP flow."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from rebind.app import create_app
from rebind.ui import build_review
from tests.fixtures import born_digital_pdf


def _doc_with(*flag_sets):
    """A tiny stand-in document object exposing `.nodes` and the fields build_review reads."""
    from rebind.model import Document, Paragraph, node_id

    nodes = []
    for page, flags in flag_sets:
        nodes.append(
            Paragraph(id=node_id(page=page, bbox=(0, 0, 1, 1), page_width=1, page_height=1,
                                 text=f"n{page}"),
                      page=page, bbox=(0.0, 0.0, 1.0, 1.0), confidence=1.0, stage="assemble",
                      flags=list(flags), text="x")
        )
    return Document(title="T", lang="en", nodes=nodes)


def test_build_review_groups_flags_by_kind_with_pages():
    doc = _doc_with(
        (1, ["ocr-source"]),
        (1, ["ocr-source", "table-suspected"]),
        (2, ["multi-column-suspected"]),
        (3, []),
    )
    review = build_review(doc, scanned_pages=(), source_was_tagged=False)

    kinds = {item["kind"]: item for item in review["items"]}
    assert "ocr-source" in kinds
    assert kinds["ocr-source"]["pages"] == [1]
    assert "table-suspected" in kinds
    assert kinds["table-suspected"]["pages"] == [1]
    assert kinds["multi-column-suspected"]["pages"] == [2]
    # Every item carries a human title and a plain-language detail, and a severity.
    for item in review["items"]:
        assert item["title"] and item["detail"]
        assert item["severity"] in {"info", "attention"}


def test_build_review_reports_scanned_and_unrecoverable():
    doc = _doc_with((5, ["ocr-source", "text-unrecoverable"]))
    review = build_review(doc, scanned_pages=(9,), source_was_tagged=True)
    kinds = {item["kind"] for item in review["items"]}
    assert "no-text-layer" in kinds       # scanned_pages surfaced
    assert "text-unrecoverable" in kinds
    assert "already-tagged" in kinds       # source_was_tagged surfaced


def test_build_review_clean_document_has_no_items():
    doc = _doc_with((1, []), (2, []))
    review = build_review(doc, scanned_pages=(), source_was_tagged=False)
    assert review["items"] == []
    assert review["clean"] is True


def test_index_page_serves_accessible_html():
    client = TestClient(create_app())
    body = client.get("/").text
    assert "<!doctype html>" in body.lower()
    assert "<main" in body and 'lang="en"' in body
    assert "Rebind" in body
    # A self-contained favicon (inline data URI) so the browser tab is identifiable offline.
    assert 'rel="icon"' in body and "data:image/png;base64," in body


def test_convert_flow_end_to_end(tmp_path: Path):
    client = TestClient(create_app())
    pdf = born_digital_pdf("<h1>Hello</h1><p>Body text.</p>", tmp_path / "in.pdf")

    resp = client.post("/convert?filename=in.pdf", content=pdf.read_bytes(),
                       headers={"content-type": "application/pdf"})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    # The job runs in a background thread; poll until it finishes.
    import time
    for _ in range(60):
        status = client.get(f"/jobs/{job_id}").json()
        if status["status"] in {"done", "error"}:
            break
        time.sleep(0.2)
    assert status["status"] == "done", status.get("error")
    assert "review" in status

    pdf_resp = client.get(f"/jobs/{job_id}/pdf")
    assert pdf_resp.status_code == 200
    assert pdf_resp.content[:5] == b"%PDF-"
    model_resp = client.get(f"/jobs/{job_id}/model")
    assert model_resp.status_code == 200
    assert b'"nodes"' in model_resp.content
