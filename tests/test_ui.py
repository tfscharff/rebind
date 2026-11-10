"""Tests for the browser UI: the page and the HTTP flow."""

from __future__ import annotations

import time
from pathlib import Path

from starlette.testclient import TestClient

from rebind.app import create_app
from tests.fixtures import born_digital_pdf


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
    # The structural self-check badge: no veraPDF/JVM needed, just remediate()'s own guarantees.
    assert status["structure_ok"] is True
    assert status["structure_issues"] == []

    pdf_resp = client.get(f"/jobs/{job_id}/pdf")
    assert pdf_resp.status_code == 200
    assert pdf_resp.content[:5] == b"%PDF-"

    # The JSON model download no longer exists.
    assert client.get(f"/jobs/{job_id}/model").status_code == 404
