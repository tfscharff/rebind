"""The local Rebind service.

Rebind runs as a local web service driven from a browser tab — the OpenRefine pattern. This
avoids bundling a native GUI toolkit and gives librarians a familiar interface.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import traceback
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

# Explicit, even though other imports below would eventually pull `rebind` in transitively:
# when PyInstaller freezes this file as its Analysis entry script, it is executed directly as
# `__main__`, not reached via `import rebind.app`.
import rebind  # noqa: F401

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Route

HOST = "127.0.0.1"
PORT = 8756


@dataclass
class _Job:
    """One conversion, run on a background thread. Rebind is a single-user local app, so an
    in-memory job store is enough -- there is no second user and no persistence requirement."""

    id: str
    filename: str
    status: str = "running"           # running | done | error
    stage: str = "Reading the document..."
    started: float = field(default_factory=time.monotonic)
    workdir: Path | None = None
    source_path: Path | None = None   # kept so descriptions can re-run remediation
    pdf_path: Path | None = None
    review: dict | None = None
    # Evidence for the two checks Adobe always leaves to a human (see rebind.review/rebind.contrast)
    reading_order: dict = field(default_factory=dict)
    contrast: dict = field(default_factory=dict)
    figures: list = field(default_factory=list)   # figures still needing a description
    alt_texts: dict = field(default_factory=dict)  # descriptions the user has supplied so far
    # Fixes the user has asked for from the report, each kept so it survives every later re-run.
    title_override: str | None = None
    lang: str = "en"
    strip_scripts: bool = False
    # Adobe's own rule list, judged against the produced PDF (see rebind.checklist)
    checklist: list = field(default_factory=list)
    elements: list = field(default_factory=list)     # every tagged element, for the page editor
    page_images: dict = field(default_factory=dict)  # page number -> data URI of the page
    edits: dict = field(default_factory=dict)        # the user's corrections, kept across re-runs
    structure_ok: bool = True
    structure_issues: tuple = ()
    error: str | None = None


class _JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()

    def create(self, filename: str) -> _Job:
        job = _Job(id=uuid.uuid4().hex, filename=filename)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> _Job | None:
        with self._lock:
            return self._jobs.get(job_id)


def _run_conversion(job: _Job, source: Path) -> None:
    """Convert `source` on a worker thread, recording the outcome on `job`.

    Everything is caught and turned into an honest job error rather than crashing the worker
    thread silently, so the UI always gets a status it can show the librarian.
    """
    # Absolute imports, not relative: when PyInstaller freezes app.py as the __main__ entry
    # script, a relative import has no parent package and raises ImportError (see /render-smoke,
    # which uses the same absolute form for the same reason).
    from rebind.checklist import build_checklist
    from rebind.extract import ExtractionError
    from rebind.remediate import Edits, remediate
    from rebind.ui import build_review

    try:
        job.stage = "Making it accessible (scanned pages are read page by page)..."
        stem = Path(job.filename).stem
        result = remediate(source, job.workdir / (stem + ".accessible.pdf"),
                           title=job.title_override or stem, lang=job.lang,
                           alt_texts=job.alt_texts, strip_scripts=job.strip_scripts,
                           edits=Edits.from_payload(job.edits))
        job.pdf_path = result.pdf_path
        job.figures = list(result.figures)
        job.structure_ok = result.structure_ok
        job.structure_issues = result.structure_issues
        job.review = build_review(
            page_count=result.page_count, ocr_pages=result.ocr_pages,
            empty_pages=result.empty_pages,
        )
        job.reading_order = result.reading_order
        job.contrast = result.contrast
        job.elements = list(result.elements)
        job.page_images = result.page_images
        # Read off the finished document, not off what remediation meant to do: this is the list
        # the librarian will be judged by, so every tick has to be earned by the actual bytes.
        job.checklist = build_checklist(
            result.pdf_path, page_count=result.page_count, empty_pages=result.empty_pages,
            undescribed_figures=tuple(result.figures), reading_order=result.reading_order,
            contrast=result.contrast)
        job.status = "done"
    except ExtractionError as exc:
        job.status = "error"
        job.error = str(exc)
    except Exception as exc:  # noqa: BLE001 -- surface any failure honestly, never hang the job
        job.status = "error"
        job.error = f"Rebind could not process this document: {exc}"


# How long the server keeps running with nobody watching. The page sends a heartbeat every few
# seconds; when the tab closes, the heartbeats stop and this is how long until the process exits.
# Generously longer than a browser's background-tab timer throttling (Chrome slows an idle tab's
# setInterval to roughly once a minute), so a minimised window is not mistaken for a closed one.
IDLE_SHUTDOWN_SECONDS = 150.0
HEARTBEAT_CHECK_SECONDS = 5.0
# How long after a page says it is going away before the process follows it out. Long enough that a
# reload's first heartbeat (sent as the script runs) arrives first and cancels it; short enough
# that closing the tab still feels like quitting the app.
CLOSING_GRACE_SECONDS = 4.0
# The watchdog has to tick faster than that grace period, or the grace period is whatever the tick
# happens to round it up to.
WATCHDOG_TICK_SECONDS = 1.0


def create_app(*, exit_when_idle: bool = False) -> Starlette:
    """The Rebind ASGI app. Starlette (not FastAPI): the routes take raw bodies and return plain
    dicts, so FastAPI's pydantic layer bought nothing and only added weight to the bundle.

    `exit_when_idle` makes the process exit once the browser stops sending heartbeats. That is
    what the installed app wants: Rebind has no window of its own, so closing the tab is the only
    "quit" gesture a user has, and without this the server stayed running invisibly -- discovered
    the hard way, as an instance that had to be killed by hand before an upgrade could install.
    Off by default so `TestClient` and a developer's `rebind serve` are never killed by a heartbeat
    that no one is sending.
    """
    jobs = _JobStore()
    watching = {"last_seen": time.monotonic(), "armed": False, "closing": 0.0}

    async def index(request: Request) -> HTMLResponse:
        from rebind.ui import index_html  # absolute: relative imports fail in the frozen __main__

        return HTMLResponse(index_html())

    async def convert_endpoint(request: Request) -> JSONResponse:
        """Accept the PDF as the raw request body (no multipart dependency) and start a job."""
        data = await request.body()
        if not data:
            return JSONResponse({"error": "No file was received. Choose a PDF and try again."},
                                status_code=400)
        filename = request.query_params.get("filename", "document.pdf")
        job = jobs.create(filename=filename)
        job.workdir = Path(tempfile.mkdtemp(prefix="rebind-job-"))
        source = job.workdir / "source.pdf"
        source.write_bytes(data)
        job.source_path = source
        threading.Thread(target=_run_conversion, args=(job, source), daemon=True).start()
        return JSONResponse({"job_id": job.id})

    async def job_status(request: Request) -> JSONResponse:
        job = jobs.get(request.path_params["job_id"])
        if job is None:
            return JSONResponse({"error": "No such job."}, status_code=404)
        body: dict = {"status": job.status, "stage": job.stage,
                      "elapsed": round(time.monotonic() - job.started, 1)}
        if job.status == "done":
            body["review"] = job.review
            body["figures"] = job.figures
            body["structure_ok"] = job.structure_ok
            body["structure_issues"] = list(job.structure_issues)
            body["reading_order"] = job.reading_order
            body["contrast"] = job.contrast
            body["checklist"] = job.checklist
        if job.status == "error":
            body["error"] = job.error
        return JSONResponse(body)

    async def job_describe(request: Request) -> JSONResponse:
        """Accept {alts: {figure_id: description}} and re-run remediation so each described figure
        becomes a tagged /Figure with that alt text."""
        job = jobs.get(request.path_params["job_id"])
        if job is None or job.source_path is None:
            return JSONResponse({"error": "No such job."}, status_code=404)
        payload = await request.json()
        alts = {str(k): str(v).strip() for k, v in (payload.get("alts") or {}).items()
                if str(v).strip()}
        if not alts:
            return JSONResponse({"error": "No descriptions were provided."}, status_code=400)
        job.alt_texts.update(alts)
        job.status = "running"
        job.stage = "Applying your descriptions..."
        job.started = time.monotonic()
        threading.Thread(target=_run_conversion, args=(job, job.source_path), daemon=True).start()
        return JSONResponse({"job_id": job.id})

    async def job_elements(request: Request) -> JSONResponse:
        """Every tagged element with its page picture -- what the page editor draws and edits.

        Kept off the job-status payload deliberately: the page images make this large, and the
        status endpoint is polled every second while a conversion runs.
        """
        job = jobs.get(request.path_params["job_id"])
        if job is None:
            return JSONResponse({"error": "No such job."}, status_code=404)
        from rebind.remediate import (
            ARTIFACT_KEY,
            ARTIFACT_LABEL,
            ARTIFACT_WHAT,
            EDITABLE_TAGS,
            TAG_KEYS,
        )

        return JSONResponse({
            "elements": job.elements, "pages": job.page_images,
            "tags": list(EDITABLE_TAGS), "edits": job.edits,
            "keys": [{"key": key, "tag": tag, "label": label, "what": what}
                     for key, tag, label, what in TAG_KEYS],
            # Sent alongside rather than among them: taking an element out of the reading order is
            # an action, not one more type to choose between.
            "artifact": {"key": ARTIFACT_KEY, "tag": "Artifact", "label": ARTIFACT_LABEL,
                         "what": ARTIFACT_WHAT},
        })

    async def job_edits(request: Request) -> JSONResponse:
        """Accept the user's corrections and rebuild the document with them applied.

        Edits replace rather than merge, so the editor's state is the whole truth: un-deleting an
        element or reverting a tag is just sending the corrected set again.
        """
        job = jobs.get(request.path_params["job_id"])
        if job is None or job.source_path is None:
            return JSONResponse({"error": "No such job."}, status_code=404)
        payload = await request.json()
        job.edits = {
            "tags": payload.get("tags") or {},
            "removed": payload.get("removed") or [],
            "alts": payload.get("alts") or {},
        }
        job.status = "running"
        job.stage = "Applying your changes to the tags..."
        job.started = time.monotonic()
        threading.Thread(target=_run_conversion, args=(job, job.source_path), daemon=True).start()
        return JSONResponse({"job_id": job.id})

    async def job_fix(request: Request) -> JSONResponse:
        """Apply one of the fixes the accessibility report offers, then rebuild the document.

        A report that names a fault without offering the fix leaves a librarian to work out the
        remedy themselves, so every check that can be fixed from here has a `fix` id it sends.
        Faults that can only be fixed by retagging are not handled here -- the report points at
        the element in the document instead, which is the fix for those.
        """
        job = jobs.get(request.path_params["job_id"])
        if job is None or job.source_path is None:
            return JSONResponse({"error": "No such job."}, status_code=404)
        payload = await request.json()
        fix, value = str(payload.get("fix") or ""), str(payload.get("value") or "").strip()
        if fix == "set-title":
            if not value:
                return JSONResponse({"error": "Type a title first."}, status_code=400)
            job.title_override = value
            job.stage = "Setting the document title..."
        elif fix == "set-language":
            if not value:
                return JSONResponse({"error": "Type a language first."}, status_code=400)
            job.lang = value
            job.stage = "Setting the document language..."
        elif fix == "strip-scripts":
            job.strip_scripts = True
            job.stage = "Removing the document's scripts..."
        else:
            return JSONResponse({"error": f"Rebind has no fix called {fix!r}."}, status_code=400)
        job.status = "running"
        job.started = time.monotonic()
        threading.Thread(target=_run_conversion, args=(job, job.source_path), daemon=True).start()
        return JSONResponse({"job_id": job.id})

    async def job_pdf(request: Request):
        job = jobs.get(request.path_params["job_id"])
        if job is None or job.pdf_path is None or not job.pdf_path.exists():
            return JSONResponse({"error": "That result is not ready."}, status_code=404)
        return FileResponse(job.pdf_path, media_type="application/pdf",
                            filename=Path(job.filename).stem + ".accessible.pdf")

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def heartbeat(request: Request) -> JSONResponse:
        """The open page saying it is still there. The first one arms the idle watchdog, so the
        server never exits during the second or two before the browser has finished starting."""
        watching["last_seen"] = time.monotonic()
        watching["armed"] = True
        watching["closing"] = 0.0   # somebody is still here: cancel any pending exit
        return JSONResponse({"status": "ok"})

    async def shutdown(request: Request) -> JSONResponse:
        """A page going away, reported by `navigator.sendBeacon`. This is only a courtesy -- it
        makes quitting quick -- and the heartbeat watchdog is what actually guarantees the process
        goes away, since a beacon is not delivered from a crashed or killed browser.

        It asks, it does not order. `pagehide` fires on every reload and every navigation, not only
        on the close of the last tab, so exiting on the beacon alone meant Rebind killed itself
        whenever the page was refreshed -- and, worse, whenever a stale tab left over from an
        earlier run was closed, which took the *new* server down with it. That is the
        "Could not reach the converter" nobody could reproduce. Instead a short grace period
        starts, and any heartbeat inside it cancels the exit: a reload beats it comfortably, and a
        genuinely closed last tab has nothing left to send one.
        """
        if exit_when_idle:
            watching["closing"] = time.monotonic()
        return JSONResponse({"status": "ok"})

    def _watchdog() -> None:
        while True:
            time.sleep(WATCHDOG_TICK_SECONDS)
            now, last = time.monotonic(), float(watching["last_seen"])
            if not watching["armed"]:
                continue
            closing = watching["closing"]
            if closing and last <= float(closing) and now - float(closing) > CLOSING_GRACE_SECONDS:
                os._exit(0)     # asked to go, and nothing spoke up in the meantime
            if now - last > IDLE_SHUTDOWN_SECONDS:
                os._exit(0)     # nobody has been watching for a long time

    if exit_when_idle:
        threading.Thread(target=_watchdog, daemon=True).start()

    async def ocr_smoke(request: Request) -> JSONResponse:
        """Recognize known text through the real OCR path, from the frozen bundle.

        The OCR engine (RapidOCR + onnxruntime + the bundled ONNX models) is the heaviest native
        dependency in the bundle and is never touched by server startup (its import is lazy).
        ADR 0005 proved a standalone frozen probe OCRs offline; this proves the *shipping* bundle
        does too. Text is drawn with Pillow and recognized; the endpoint reports what it read.
        """
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont

        from rebind.ocr import OcrEngine, recognize

        expected = "REBIND OCR SMOKE 12345"
        try:
            image = Image.new("RGB", (760, 120), "white")
            ImageDraw.Draw(image).text(
                (20, 30), expected, fill="black", font=ImageFont.load_default(size=48)
            )
            lines = recognize(
                np.asarray(image), page_number=1, page_width=760.0, page_height=120.0,
                engine=OcrEngine(),
            )
            recovered = " ".join(line.text for line in lines)
            return JSONResponse({"status": "ok", "success": "REBIND" in recovered.upper(),
                                 "recovered": recovered, "error": None})
        except Exception as exc:  # noqa: BLE001 -- surface the real error, do not mask it
            return JSONResponse({"status": "error", "success": False, "recovered": None,
                                 "error": f"{type(exc).__name__}: {exc}",
                                 "traceback": traceback.format_exc()})

    return Starlette(routes=[
        Route("/", index),
        Route("/convert", convert_endpoint, methods=["POST"]),
        Route("/jobs/{job_id}", job_status),
        Route("/jobs/{job_id}/describe", job_describe, methods=["POST"]),
        Route("/jobs/{job_id}/fix", job_fix, methods=["POST"]),
        Route("/jobs/{job_id}/elements", job_elements),
        Route("/jobs/{job_id}/edits", job_edits, methods=["POST"]),
        Route("/jobs/{job_id}/pdf", job_pdf),
        Route("/health", health),
        Route("/heartbeat", heartbeat, methods=["GET", "POST"]),
        Route("/shutdown", shutdown, methods=["POST"]),
        Route("/ocr-smoke", ocr_smoke, methods=["GET", "POST"]),
    ])


def _log_file_path() -> Path:
    """Where to write rebind's own log when there is no console to print to.

    `packaging/rebind.spec` builds the frozen exe with `console=False` (no console window),
    which means stdout/stderr -- and therefore uvicorn's default logging -- go nowhere on
    Windows; they are not merely hidden, they are discarded. Writing to a file next to the
    frozen executable (or the current working directory, unfrozen) is the minimum needed so a
    librarian who hits a crash has something to send back other than "it didn't work."
    """
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    return base / "rebind.log"


def _hold_single_instance_mutex() -> None:
    """Create a named Win32 mutex and hold it for the process's lifetime (never closed -- the OS
    releases it automatically on exit).

    This is what lets `packaging/rebind.iss`'s `AppMutex=RebindSingleInstanceMutex` detect a
    running instance during install/upgrade and offer to close it first, so re-running the
    installer over an existing install just works -- no manual "quit Rebind, then install" step.
    Windows-only; a no-op everywhere else (dev on macOS/Linux, if that ever happens).
    """
    if sys.platform != "win32":
        return
    import ctypes

    ctypes.windll.kernel32.CreateMutexW(None, False, "RebindSingleInstanceMutex")


def main() -> None:
    import uvicorn

    _hold_single_instance_mutex()
    log_config = uvicorn.config.LOGGING_CONFIG
    # use_colors=None makes uvicorn's formatters call sys.stdout.isatty() at construction time.
    # In the console=False frozen build sys.stdout is None whenever the process is launched
    # without an inherited handle -- i.e. every real launch: double-click, Start menu shortcut,
    # Start-Process. That raises AttributeError before the server ever starts, so the app dies
    # instantly with a dialog no librarian can act on. Pinning use_colors=False keeps the
    # formatters from touching stdout at all; the handlers below already write to a file.
    log_config["formatters"]["default"]["use_colors"] = False
    log_config["formatters"]["access"]["use_colors"] = False
    log_config["handlers"]["default"]["class"] = "logging.FileHandler"
    log_config["handlers"]["default"]["filename"] = str(_log_file_path())
    log_config["handlers"]["default"].pop("stream", None)
    log_config["handlers"]["access"]["class"] = "logging.FileHandler"
    log_config["handlers"]["access"]["filename"] = str(_log_file_path())
    log_config["handlers"]["access"].pop("stream", None)

    threading.Timer(1.5, lambda: webbrowser.open(f"http://{HOST}:{PORT}/")).start()
    uvicorn.run(create_app(exit_when_idle=True), host=HOST, port=PORT, log_level="info",
                log_config=log_config)


if __name__ == "__main__":
    main()
