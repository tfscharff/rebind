"""The local Rebind service.

Rebind runs as a local web service driven from a browser tab — the OpenRefine pattern. This
avoids bundling a native GUI toolkit and gives librarians a familiar interface.
"""

from __future__ import annotations

import tempfile
import threading
import traceback
import webbrowser
from pathlib import Path

# Explicit, even though other imports below would eventually pull `rebind` in transitively:
# when PyInstaller freezes this file as its Analysis entry script, it is executed directly as
# `__main__`, not reached via `import rebind.app` -- so nothing guarantees this package's
# `__init__.py` (which registers the bundled GTK3 DLL directory before WeasyPrint is ever
# imported) has already run, unless something in this module says so explicitly.
import rebind  # noqa: F401

from fastapi import FastAPI

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


def create_app() -> FastAPI:
    app = FastAPI(title="Rebind", version="0.0.1")

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

    threading.Timer(1.5, lambda: webbrowser.open(f"http://{HOST}:{PORT}/health")).start()
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="info", log_config=log_config)


if __name__ == "__main__":
    main()
