import time
from pathlib import Path

from starlette.testclient import TestClient

from rebind.app import create_app
from tests.fixtures import born_digital_pdf


def test_health_endpoint_reports_ready():
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ocr_smoke_endpoint_recognizes_text():
    """Fast in-process check of the OCR smoke path; the frozen-bundle version lives in
    test_packaging.py and proves the shipping bundle can OCR."""
    client = TestClient(create_app())

    body = client.post("/ocr-smoke").json()

    assert body["success"] is True, body.get("error")
    assert "REBIND" in (body["recovered"] or "").upper()


def test_a_finished_job_reports_the_two_manual_check_findings(tmp_path: Path):
    # Adobe's checker always defers "Logical Reading Order" and "Colour contrast" to a human. The
    # app has to hand that human the evidence, so a finished job must carry both -- with a real
    # contrast failure surfaced rather than quietly passed.
    source = born_digital_pdf(
        "<h1>Title</h1><p>Ordinary black body text.</p>"
        "<p style='color:#a8a8a8'>Pale grey small print that fails contrast.</p>",
        tmp_path / "in.pdf")
    client = TestClient(create_app())

    # /convert takes the PDF as the raw request body (no multipart dependency in the bundle).
    job_id = client.post("/convert?filename=in.pdf", content=source.read_bytes()).json()["job_id"]
    for _ in range(120):
        status = client.get(f"/jobs/{job_id}").json()
        if status["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    assert status["status"] == "done", status.get("error")

    assert status["reading_order"]["checked"] == 1
    contrast = status["contrast"]
    assert contrast["measured"] > 0
    assert contrast["ok"] is False
    assert any("grey small print" in f["text"] for f in contrast["failures"])
    assert contrast["lowest"]["ratio"] < 4.5

    # The correction is opt-in: it changes how the document looks, so it only happens on request.
    client.post(f"/jobs/{job_id}/contrast")
    for _ in range(120):
        status = client.get(f"/jobs/{job_id}").json()
        if status["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    assert status["status"] == "done", status.get("error")
    assert status["contrast"]["ok"] is True, status["contrast"]["failures"]
    assert status["contrast"]["darkened"] > 0
