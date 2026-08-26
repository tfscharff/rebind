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
    assert {"p", "1", "q", "c", "f", "t"} <= keys, sorted(keys)
    # Every key carries an explanation: the editor shows what a type *means* when it has focus,
    # because "BlockQuote" tells a librarian nothing on its own.
    for entry in body["keys"]:
        assert entry["what"], f"{entry['tag']} has no explanation"
    # Taking an element out of the reading order is an action, not one more type to choose
    # between, so it arrives beside the types rather than among them -- with its own key.
    assert body["artifact"]["tag"] == "Artifact"
    assert body["artifact"]["key"] not in keys
    assert "Artifact" not in {entry["tag"] for entry in body["keys"]}
    row_keys = {entry["key"] for entry in body["rowKeys"]}
    assert row_keys == {"h", "b"}
    assert {entry["tag"] for entry in body["rowKeys"]} == {"TH", "TD"}
    assert row_keys.isdisjoint(keys), "row hotkeys must not collide with the whole-element ones"
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


def test_table_rows_reach_the_editor_with_their_own_ids(tmp_path: Path):
    from tests.fixtures import born_digital_pdf_with_table

    source = born_digital_pdf_with_table(tmp_path / "in.pdf")
    client = TestClient(create_app())
    job_id = client.post("/convert?filename=in.pdf", content=source.read_bytes()).json()["job_id"]
    assert _run(client, job_id)["status"] == "done"

    body = client.get(f"/jobs/{job_id}/elements").json()
    table = next(e for e in body["elements"] if e["kind"] == "Table")
    rows = [e for e in body["elements"] if e.get("row")]
    assert len(rows) == 4
    assert all(r["id"].startswith(table["id"] + "r") for r in rows)

    client.post(f"/jobs/{job_id}/edits",
                json={"tags": {f"{table['id']}r0": "TD", f"{table['id']}r1": "TH"}})
    status = _run(client, job_id)
    assert status["status"] == "done", status.get("error")

    after_rows = {e["id"]: e["kind"]
                  for e in client.get(f"/jobs/{job_id}/elements").json()["elements"]
                  if e.get("row")}
    assert after_rows[f"{table['id']}r0"] == "TD"
    assert after_rows[f"{table['id']}r1"] == "TH"


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
    # Reading order is the one verdict no measurement can settle, so it stays with the person --
    # and it is the *only* one that does.
    assert checks["Logical reading order"]["status"] == "manual"
    assert [c["title"] for c in status["checklist"] if c["status"] == "manual"] == \
        ["Logical reading order"]
    assert {c["group"] for c in status["checklist"]} >= {"Document", "Tables", "Headings"}


def test_the_idle_watchdog_is_off_unless_asked_for():
    # A developer's `rebind serve`, and every test, must never be killed by a heartbeat nobody is
    # sending. Only the installed app (which opens a browser tab) turns this on.
    client = TestClient(create_app())
    assert client.get("/heartbeat").json()["status"] == "ok"
    assert client.post("/shutdown").json()["status"] == "ok"
    # Still alive and serving.
    assert client.get("/health").json()["status"] == "ok"


def test_a_reload_does_not_shut_the_server_down():
    """`pagehide` fires on every reload and every navigation, not only on the last tab closing.

    Exiting on that beacon alone meant Rebind killed itself whenever its page was refreshed, and --
    the case that actually bit -- whenever a stale tab left over from a previous run was closed,
    which took the freshly started server down with it. The symptom was "Could not reach the
    converter. Is Rebind still running?" on a server that had been alive seconds earlier.

    The beacon now only starts a grace period, and a heartbeat cancels it. This test drives the
    watchdog's own decision function rather than waiting on real time.
    """
    import rebind.app as app_module

    client = TestClient(create_app(exit_when_idle=True))
    exits: list[int] = []
    monkey = app_module.os._exit
    app_module.os._exit = exits.append
    try:
        client.get("/heartbeat")            # the page arrives and arms the watchdog
        client.post("/shutdown")            # ...then reloads: pagehide fires
        client.get("/heartbeat")            # ...and the new page checks in inside the grace period
        # Give the watchdog several ticks to make its call.
        time.sleep(app_module.WATCHDOG_TICK_SECONDS * 3 + app_module.CLOSING_GRACE_SECONDS)
        assert exits == [], "a reload must not take the server with it"
    finally:
        app_module.os._exit = monkey


def test_a_closed_last_tab_does_shut_the_server_down():
    """The other half: Rebind has no window, so closing the tab is the only way to quit it. If
    nothing checks in after the beacon, the process really does have to go."""
    import rebind.app as app_module

    client = TestClient(create_app(exit_when_idle=True))
    exits: list[int] = []
    monkey = app_module.os._exit
    app_module.os._exit = exits.append
    try:
        client.get("/heartbeat")
        client.post("/shutdown")
        time.sleep(app_module.WATCHDOG_TICK_SECONDS * 2 + app_module.CLOSING_GRACE_SECONDS)
        assert exits, "a closed last tab must still quit the app"
    finally:
        app_module.os._exit = monkey


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
    # Contrast is corrected as part of remediation now, not offered as homework: the pale grey
    # paragraph is darkened, and the verdict is a re-measurement of the corrected document rather
    # than a claim that the correction worked.
    contrast = status["contrast"]
    assert contrast["measured"] > 0
    assert contrast["ok"] is True, contrast["failures"]
    assert contrast["darkened"] > 0
    assert contrast["lowest"]["ratio"] >= 4.5
    # It is on the report and ticked off -- settled by measurement, never put to the reader.
    contrast_check = {c["title"]: c for c in status["checklist"]}["Colour contrast"]
    assert contrast_check["status"] == "pass"
    assert "1 colour was corrected" in contrast_check["detail"], contrast_check["detail"]


def test_a_report_fix_is_applied_and_survives_a_later_rebuild(tmp_path: Path):
    # Every check the report can fix carries a fix id, and applying one rebuilds the document with
    # it. It has to stick: a fix undone by the next retag would be worse than no fix at all.
    source = born_digital_pdf("<h1>Title</h1><p>Body text.</p>", tmp_path / "in.pdf")
    client = TestClient(create_app())
    job_id = client.post("/convert?filename=in.pdf", content=source.read_bytes()).json()["job_id"]
    assert _run(client, job_id)["status"] == "done"

    assert client.post(f"/jobs/{job_id}/fix", json={"fix": "set-title", "value": ""}).status_code \
        == 400
    assert client.post(f"/jobs/{job_id}/fix", json={"fix": "invent"}).status_code == 400

    client.post(f"/jobs/{job_id}/fix", json={"fix": "set-title", "value": "A Better Title"})
    assert _run(client, job_id)["status"] == "done"

    import pikepdf
    from rebind.app import _JobStore  # noqa: F401  (documents where the job lives)
    pdf_bytes = client.get(f"/jobs/{job_id}/pdf").content
    (tmp_path / "out.pdf").write_bytes(pdf_bytes)
    with pikepdf.open(tmp_path / "out.pdf") as pdf:
        assert str(pdf.open_metadata()["dc:title"]) == "A Better Title"

    # A later edit rebuilds from source; the title must come through it unchanged.
    client.post(f"/jobs/{job_id}/edits", json={"tags": {}, "removed": [], "alts": {}})
    assert _run(client, job_id)["status"] == "done"
    (tmp_path / "out2.pdf").write_bytes(client.get(f"/jobs/{job_id}/pdf").content)
    with pikepdf.open(tmp_path / "out2.pdf") as pdf:
        assert str(pdf.open_metadata()["dc:title"]) == "A Better Title"
