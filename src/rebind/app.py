"""The local Rebind service.

Rebind runs as a local web service driven from a browser tab — the OpenRefine pattern. This
avoids bundling a native GUI toolkit and gives librarians a familiar interface.
"""

from __future__ import annotations

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
    figures: list = field(default_factory=list)   # figures still needing a description
    alt_texts: dict = field(default_factory=dict)  # descriptions the user has supplied so far
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
    from rebind.extract import ExtractionError
    from rebind.remediate import remediate
    from rebind.ui import build_review

    try:
        job.stage = "Making it accessible (scanned pages are read page by page)..."
        stem = Path(job.filename).stem
        result = remediate(source, job.workdir / (stem + ".accessible.pdf"), title=stem,
                           alt_texts=job.alt_texts)
        job.pdf_path = result.pdf_path
        job.figures = list(result.figures)
        job.structure_ok = result.structure_ok
        job.structure_issues = result.structure_issues
        job.review = build_review(
            page_count=result.page_count, ocr_pages=result.ocr_pages,
            empty_pages=result.empty_pages,
        )
        job.status = "done"
    except ExtractionError as exc:
        job.status = "error"
        job.error = str(exc)
    except Exception as exc:  # noqa: BLE001 -- surface any failure honestly, never hang the job
        job.status = "error"
        job.error = f"Rebind could not process this document: {exc}"


def create_app() -> Starlette:
    """The Rebind ASGI app. Starlette (not FastAPI): the routes take raw bodies and return plain
    dicts, so FastAPI's pydantic layer bought nothing and only added weight to the bundle."""
    jobs = _JobStore()

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

    async def job_pdf(request: Request):
        job = jobs.get(request.path_params["job_id"])
        if job is None or job.pdf_path is None or not job.pdf_path.exists():
            return JSONResponse({"error": "That result is not ready."}, status_code=404)
        return FileResponse(job.pdf_path, media_type="application/pdf",
                            filename=Path(job.filename).stem + ".accessible.pdf")

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

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
        Route("/jobs/{job_id}/pdf", job_pdf),
        Route("/health", health),
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
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="info", log_config=log_config)


if __name__ == "__main__":
    main()
