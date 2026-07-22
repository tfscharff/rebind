from fastapi.testclient import TestClient

from rebind.app import create_app


def test_health_endpoint_reports_ready():
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reports_the_rendering_backend():
    """The installer's whole job is shipping a working renderer; the app must confirm it loaded."""
    client = TestClient(create_app())

    body = client.get("/health").json()

    assert body["renderer"] == "weasyprint"
    assert body["renderer_available"] is True
