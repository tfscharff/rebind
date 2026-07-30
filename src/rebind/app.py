"""The local Rebind service.

Rebind runs as a local web service driven from a browser tab — the OpenRefine pattern. This
avoids bundling a native GUI toolkit and gives librarians a familiar interface.
"""

from __future__ import annotations

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
# `__main__`, not reached via `import rebind.app` -- so nothing guarantees this package's
# `__init__.py` (which registers the bundled GTK3 DLL directory before WeasyPrint is ever
# imported) has already run, unless something in this module says so explicitly.
import rebind  # noqa: F401

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

HOST = "127.0.0.1"
PORT = 8756

# A tiny (2x2, solid red) PNG, embedded as a base64 data URI. Used by the render-smoke endpoint
# to exercise raster image handling -- the path most likely to dlopen a native library (e.g.
# gdk-pixbuf) that a bare `import weasyprint` never touches. Generated with Pillow:
#   Image.new("RGB", (2, 2), (200, 30, 30)) saved as PNG.
_SMOKE_PNG_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGM8ISfHwMDAxAAGAA0EAQijE05aAAAAAElFTkSuQmCC"
)

# A small but representative document: heading, paragraph, table, and an embedded raster image --
# the combination of native rendering paths a real Rebind document exercises, not just text.
_SMOKE_HTML = f"""
<h1>Render Smoke Test</h1>
<p>This paragraph exists to exercise ordinary text layout and font shaping.</p>
<table>
  <tr><th>Column A</th><th>Column B</th></tr>
  <tr><td>1</td><td>2</td></tr>
</table>
<img src="{_SMOKE_PNG_DATA_URI}" width="2" height="2" alt="a two by two red square">
"""


# The DLL bootstrap that used to live here now lives in `rebind/__init__.py` (triggered by the
# explicit `import rebind` above), so every entry point that imports anything under the
# `rebind` package gets it, not just this module.


def _renderer_available() -> bool:
    try:
        import weasyprint  # noqa: F401
    except Exception:
        return False
    return True


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
    pdf_path: Path | None = None
    review: dict | None = None
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
        result = remediate(source, job.workdir / (stem + ".accessible.pdf"), title=stem)
        job.pdf_path = result.pdf_path
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


def create_app() -> FastAPI:
    app = FastAPI(title="Rebind", version="0.0.1")
    jobs = _JobStore()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        from rebind.ui import index_html  # absolute: relative imports fail in the frozen __main__

        return index_html()

    @app.post("/convert")
    async def convert_endpoint(request: Request, filename: str = "document.pdf") -> JSONResponse:
        """Accept the PDF as the raw request body (no multipart dependency) and start a job."""
        data = await request.body()
        if not data:
            return JSONResponse({"error": "No file was received. Choose a PDF and try again."},
                                status_code=400)
        job = jobs.create(filename=filename)
        job.workdir = Path(tempfile.mkdtemp(prefix="rebind-job-"))
        source = job.workdir / "source.pdf"
        source.write_bytes(data)
        threading.Thread(target=_run_conversion, args=(job, source), daemon=True).start()
        return JSONResponse({"job_id": job.id})

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str) -> JSONResponse:
        job = jobs.get(job_id)
        if job is None:
            return JSONResponse({"error": "No such job."}, status_code=404)
        body: dict = {"status": job.status, "stage": job.stage,
                      "elapsed": round(time.monotonic() - job.started, 1)}
        if job.status == "done":
            body["review"] = job.review
        if job.status == "error":
            body["error"] = job.error
        return JSONResponse(body)

    @app.get("/jobs/{job_id}/pdf")
    def job_pdf(job_id: str):
        job = jobs.get(job_id)
        if job is None or job.pdf_path is None or not job.pdf_path.exists():
            return JSONResponse({"error": "That result is not ready."}, status_code=404)
        return FileResponse(job.pdf_path, media_type="application/pdf",
                            filename=Path(job.filename).stem + ".accessible.pdf")

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "renderer": "weasyprint",
            "renderer_available": _renderer_available(),
        }

    @app.post("/render-smoke")
    @app.get("/render-smoke")
    def render_smoke() -> dict:
        """Render a small but representative document through the real render path.

        A bare `import weasyprint` (see /health) proves link-time DLL resolution but not that
        rendering actually works -- raster image handling, fontconfig, and font loading can each
        dlopen native libraries an import never touches. This exercises the real
        `render_html_to_pdf` path (heading, paragraph, table, and an embedded raster PNG) and
        reports what happened, success or failure, rather than a generic message.
        """
        from rebind.render import render_html_to_pdf

        try:
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "render-smoke.pdf"
                render_html_to_pdf(_SMOKE_HTML, target, title="Render Smoke Test", lang="en")
                size_bytes = target.stat().st_size
            return {
                "status": "ok",
                "success": True,
                "size_bytes": size_bytes,
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001 -- the whole point is to surface the real error
            return {
                "status": "error",
                "success": False,
                "size_bytes": None,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }

    @app.post("/ocr-smoke")
    @app.get("/ocr-smoke")
    def ocr_smoke() -> dict:
        """Recognize known text through the real OCR path, from the frozen bundle.

        The OCR engine (RapidOCR + onnxruntime + the bundled ONNX models) is the heaviest native
        dependency in the bundle and, unlike the renderer, is never touched by server startup
        (its import is lazy). ADR 0005 proved a standalone frozen probe OCRs offline; this proves
        the *shipping* bundle does too. Text is drawn with Pillow and recognized; the endpoint
        reports what it read.
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
            return {
                "status": "ok",
                "success": "REBIND" in recovered.upper(),
                "recovered": recovered,
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001 -- surface the real error, do not mask it
            return {
                "status": "error",
                "success": False,
                "recovered": None,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }

    return app


def _log_file_path() -> Path:
    """Where to write rebind's own log when there is no console to print to.

    `packaging/rebind.spec` builds the frozen exe with `console=False` (no console window),
    which means stdout/stderr -- and therefore uvicorn's default logging -- go nowhere on
    Windows; they are not merely hidden, they are discarded. Writing to a file next to the
    frozen executable (or the current working directory, unfrozen) is the minimum needed so a
    librarian who hits a crash has something to send back other than "it didn't work."
    """
    import sys

    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    return base / "rebind.log"


def main() -> None:
    import uvicorn

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
