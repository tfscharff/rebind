"""The local Rebind service.

Rebind runs as a local web service driven from a browser tab — the OpenRefine pattern. This
avoids bundling a native GUI toolkit and gives librarians a familiar interface.
"""

from __future__ import annotations

import os
import sys
import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI

HOST = "127.0.0.1"
PORT = 8756


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

    return app


def main() -> None:
    import uvicorn

    threading.Timer(1.5, lambda: webbrowser.open(f"http://{HOST}:{PORT}/health")).start()
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
