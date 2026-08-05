"""Tests for the browser UI: the review summary and the HTTP flow."""

from __future__ import annotations

import time
from pathlib import Path

from starlette.testclient import TestClient

from rebind.app import create_app
from rebind.ui import build_review
from tests.fixtures import born_digital_pdf


def test_build_review_reports_what_happened():
    r = build_review(page_count=10, ocr_pages=(3, 4), empty_pages=())
    assert "10 pages" in r["summary"]
    assert "2 scanned" in r["summary"]
    assert r["items"] == [] and r["clean"] is True


def test_build_review_clean_document_says_nothing_needed_recognizing():
    r = build_review(page_count=5, ocr_pages=(), empty_pages=())
    assert "already had text" in r["summary"]
    assert r["clean"] is True


def test_build_review_flags_empty_pages():
    r = build_review(page_count=6, ocr_pages=(1,), empty_pages=(5, 6))
    assert r["clean"] is False
    item = r["items"][0]
    assert item["kind"] == "no-text" and item["pages"] == [5, 6]


def test_index_page_serves_accessible_html():
    client = TestClient(create_app())
    body = client.get("/").text
    assert "<!doctype html>" in body.lower()
    assert "<main" in body and 'lang="en"' in body
    assert "Rebind" in body
    assert 'rel="icon"' in body and "data:image/png;base64," in body
    assert "model" not in body.lower() or "/model" not in body  # JSON download is gone


def test_convert_flow_end_to_end(tmp_path: Path):
    client = TestClient(create_app())
    pdf = born_digital_pdf("<h1>Hello</h1><p>Body text.</p>", tmp_path / "in.pdf")

    resp = client.post("/convert?filename=in.pdf", content=pdf.read_bytes(),
                       headers={"content-type": "application/pdf"})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    for _ in range(80):
        status = client.get(f"/jobs/{job_id}").json()
        if status["status"] in {"done", "error"}:
            break
        time.sleep(0.2)
    assert status["status"] == "done", status.get("error")
    assert "review" in status
    # The structural self-check badge: no veraPDF/JVM needed, just remediate()'s own guarantees.
    assert status["structure_ok"] is True
    assert status["structure_issues"] == []

    pdf_resp = client.get(f"/jobs/{job_id}/pdf")
    assert pdf_resp.status_code == 200
    assert pdf_resp.content[:5] == b"%PDF-"

    # The JSON model download no longer exists.
    assert client.get(f"/jobs/{job_id}/model").status_code == 404


def test_describe_flow_promotes_figure(tmp_path: Path):
    from tests.fixtures import born_digital_pdf_with_image
    client = TestClient(create_app())
    pdf = born_digital_pdf_with_image(tmp_path / "in.pdf")

    job_id = client.post("/convert?filename=in.pdf", content=pdf.read_bytes(),
                         headers={"content-type": "application/pdf"}).json()["job_id"]
    for _ in range(80):
        s = client.get(f"/jobs/{job_id}").json()
        if s["status"] == "done":
            break
        time.sleep(0.2)
    assert s["figures"], "expected an undescribed figure"
    fid = s["figures"][0]["id"]

    r = client.post(f"/jobs/{job_id}/describe", json={"alts": {fid: "A red chart."}})
    assert r.status_code == 200
    for _ in range(80):
        s2 = client.get(f"/jobs/{job_id}").json()
        if s2["status"] == "done":
            break
        time.sleep(0.2)
    assert s2["figures"] == []   # described, no longer outstanding


def test_describe_with_no_text_is_rejected(tmp_path: Path):
    client = TestClient(create_app())
    pdf = born_digital_pdf("<p>no images here</p>", tmp_path / "in.pdf")
    job_id = client.post("/convert?filename=in.pdf", content=pdf.read_bytes(),
                         headers={"content-type": "application/pdf"}).json()["job_id"]
    for _ in range(80):
        if client.get(f"/jobs/{job_id}").json()["status"] == "done":
            break
        time.sleep(0.2)
    assert client.post(f"/jobs/{job_id}/describe", json={"alts": {}}).status_code == 400
