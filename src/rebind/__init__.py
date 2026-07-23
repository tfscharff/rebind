"""Rebind — accessible PDF reconstruction for damaged library scans."""

from __future__ import annotations

import os
import sys
from pathlib import Path

__version__ = "0.0.1"


def _bootstrap_bundled_dll_directory() -> None:
    """Point WeasyPrint's native loader at the bundled GTK3 DLLs, not the system install.

    WeasyPrint loads libgobject/libpango/etc. via ``LOAD_LIBRARY_SEARCH_DEFAULT_DIRS`` and
    ignores ``PATH``. When frozen by PyInstaller, the bundled DLLs live next to the frozen
    executable under ``gtk3-runtime\\bin`` (see packaging/rebind.spec). We must register that
    directory — via ``os.add_dll_directory`` (Windows-only, Python 3.8+) and the
    ``WEASYPRINT_DLL_DIRECTORIES`` env var WeasyPrint itself consults — *before* WeasyPrint
    (or anything importing it) is imported anywhere in the process, or the OS loader will fall
    back to whatever copy it finds via its own default search (which includes a system-wide
    GTK3 install, if one is present, and would defeat this whole check on a dev machine).

    This lives in ``rebind/__init__.py`` rather than a specific entry-point module (e.g.
    ``rebind.app``) deliberately: any code that does ``import rebind.something`` runs this
    package's ``__init__.py` first, as a language guarantee, so every current and future
    entry point gets the bootstrap for free, even one that imports ``weasyprint`` directly
    without ever importing ``rebind.app``. Putting this in ``app.py`` instead (the original
    location) worked only by accident, because ``app.py`` happened to be the sole
    PyInstaller Analysis entry script; a second entry point would have silently defeated it.
    A no-op when not frozen (``sys.frozen`` is only set by PyInstaller's bootloader), so this
    has no effect on normal `uv run` development or test invocations.
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
