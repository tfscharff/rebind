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


def _run(client, job_id):
    for _ in range(200):
        status = client.get(f"/jobs/{job_id}").json()
        if status["status"] in ("done", "error"):
            return status
        time.sleep(0.5)
    raise AssertionError("job never finished")


def test_the_page_editor_lists_elements_and_applies_corrections(tmp_path: Path):
    # The editor's whole job: show what Rebind decided, and let a person change it. An element id
    # is keyed to the source line it starts at, so a correction still refers to the same element
    # after an earlier one has been retagged or removed.
    source = born_digital_pdf(
        "<h1>Title</h1><p>First paragraph of the document.</p>"
        "<p>Second paragraph here.</p><p>Third one.</p>", tmp_path / "in.pdf")
    client = TestClient(create_app())
    job_id = client.post("/convert?filename=in.pdf", content=source.read_bytes()).json()["job_id"]
    assert _run(client, job_id)["status"] == "done"

    body = client.get(f"/jobs/{job_id}/elements").json()
    kinds = [(e["id"], e["kind"], e["text"]) for e in body["elements"]]
    assert [k for _i, k, _t in kinds] == ["H1", "P", "P", "P"], kinds
    assert body["pages"]["1"].startswith("data:image/png;base64,"), "the page picture is missing"
    for expected in ("H2", "P", "BlockQuote", "Caption", "Figure", "Table", "L"):
        assert expected in body["tags"], body["tags"]
    keys = {entry["key"] for entry in body["keys"]}
    assert len(keys) == len(body["keys"]), "every hotkey must be unique"
    assert {"p", "1", "q", "c", "f", "t", "x"} <= keys, sorted(keys)
    # Every key carries an explanation: the editor shows what a type *means* when it has focus,
    # because "BlockQuote" tells a librarian nothing on its own.
    for entry in body["keys"]:
        assert entry["what"], f"{entry['tag']} has no explanation"
    for element in body["elements"]:
        assert 0 <= element["left"] <= 100 and 0 <= element["top"] <= 100, element

    ids = [i for i, _k, _t in kinds]
    client.post(f"/jobs/{job_id}/edits", json={"tags": {ids[2]: "H2"}, "removed": [ids[3]]})
    status = _run(client, job_id)
    assert status["status"] == "done", status.get("error")
    assert status["structure_ok"] is True, status["structure_issues"]

    after = {e["id"]: e["kind"] for e in client.get(f"/jobs/{job_id}/elements").json()["elements"]}
    assert after[ids[0]] == "H1"
    assert after[ids[1]] == "P"
    assert after[ids[2]] == "H2", "the retag should have been applied"
    # A removed element is not read, but it is still offered -- listed as untagged, so the same
    # control that removed it can put it back.
    assert after[ids[3]] == "Artifact"


def test_a_finished_job_carries_the_adobe_checklist(tmp_path: Path):
    # The left column of the result view is Adobe's own rule list, judged against the produced
    # document. It has to reach the page from the job, and it has to be honest: the two checks
    # Adobe always defers to a human are reported as manual, never as passes.
    source = born_digital_pdf("<h1>Title</h1><p>Body text here.</p>", tmp_path / "in.pdf")
    client = TestClient(create_app())
    job_id = client.post("/convert?filename=in.pdf", content=source.read_bytes()).json()["job_id"]
    status = _run(client, job_id)
    assert status["status"] == "done", status.get("error")

    checks = {c["title"]: c for c in status["checklist"]}
    assert checks["Tagged PDF"]["status"] == "pass"
    assert checks["Logical reading order"]["status"] == "manual"
    assert checks["Colour contrast"]["status"] == "manual"
    assert {c["group"] for c in status["checklist"]} >= {"Document", "Tables", "Headings"}


def test_the_idle_watchdog_is_off_unless_asked_for():
    # A developer's `rebind serve`, and every test, must never be killed by a heartbeat nobody is
    # sending. Only the installed app (which opens a browser tab) turns this on.
    client = TestClient(create_app())
    assert client.get("/heartbeat").json()["status"] == "ok"
    assert client.post("/shutdown").json()["status"] == "ok"
    # Still alive and serving.
    assert client.get("/health").json()["status"] == "ok"


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
