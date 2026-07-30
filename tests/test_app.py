from starlette.testclient import TestClient

from rebind.app import create_app


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
