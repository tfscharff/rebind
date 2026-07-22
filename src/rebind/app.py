"""The local Rebind service.

Rebind runs as a local web service driven from a browser tab — the OpenRefine pattern. This
avoids bundling a native GUI toolkit and gives librarians a familiar interface.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import traceback
import webbrowser
from pathlib import Path

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


def _bootstrap_bundled_dll_directory() -> None:
    """Point WeasyPrint's native loader at the bundled GTK3 DLLs, not the system install.

    WeasyPrint loads libgobject/libpango/etc. via ``LOAD_LIBRARY_SEARCH_DEFAULT_DIRS`` and
    ignores ``PATH``. When frozen by PyInstaller, the bundled DLLs live next to this
    executable under ``gtk3-runtime\\bin`` (see packaging/rebind.spec). We must register that
    directory — via ``os.add_dll_directory`` (Windows-only, Python 3.8+) and the
    ``WEASYPRINT_DLL_DIRECTORIES`` env var WeasyPrint itself consults — *before* WeasyPrint
    (or anything importing it) is imported anywhere in the process, or the OS loader will fall
    back to whatever copy it finds via its own default search (which includes a system-wide
    GTK3 install, if one is present, and would defeat this whole check on a dev machine).
    """
    if not getattr(sys, "frozen", False):
        return
    bundle_root = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    gtk_bin = bundle_root / "gtk3-runtime" / "bin"
    if not gtk_bin.is_dir():
        return
    os.environ["WEASYPRINT_DLL_DIRECTORIES"] = str(gtk_bin)
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(gtk_bin))

    fonts_conf = bundle_root / "gtk3-runtime" / "etc" / "fonts" / "fonts.conf"
    if fonts_conf.is_file():
        os.environ["FONTCONFIG_PATH"] = str(fonts_conf.parent)


_bootstrap_bundled_dll_directory()


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


def main() -> None:
    import uvicorn

    threading.Timer(1.5, lambda: webbrowser.open(f"http://{HOST}:{PORT}/health")).start()
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
