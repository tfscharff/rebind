# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Rebind.

GTK3 bundling
-------------
WeasyPrint needs the GTK3 native libraries (gobject, pango, harfbuzz, fontconfig, ...) that
Task 3 found are not installed by `uv sync` on Windows -- they come from a separate
GTK3-Runtime-Win64 install. A librarian cannot be asked to install that separately, so this
spec vendors the whole `bin\` directory of that runtime (all the DLLs WeasyPrint's ffi loader
and their transitive dependencies need) plus `etc\fonts` (the fontconfig configuration WeasyPrint
needs to locate usable fonts) into the frozen bundle, under `gtk3-runtime\`.

`src/rebind/app.py` calls `os.add_dll_directory()` on that bundled directory (and sets
`FONTCONFIG_PATH`) before WeasyPrint is imported anywhere in the process -- see
`_bootstrap_bundled_dll_directory()` there for why this must happen before import, and why
WeasyPrint's own `WEASYPRINT_DLL_DIRECTORIES` env var does NOT work here (weasyprint/text/ffi.py
only reads it when `not hasattr(sys, 'frozen')`, i.e. never in a PyInstaller build).

This is what must be proven, not assumed, per Task 7: that the frozen exe loads *these* bundled
DLLs and works even when the system-wide GTK3-Runtime install is absent. See task-7-report.md
for how that was verified.
"""
import os

from PyInstaller.utils.hooks import collect_all

GTK_RUNTIME_ROOT = os.environ.get("REBIND_GTK_RUNTIME", r"C:\Program Files\GTK3-Runtime Win64")

datas, binaries, hiddenimports = [], [], []
for package in ("weasyprint", "pikepdf", "fastapi", "uvicorn"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# --- Vendor the GTK3 runtime -----------------------------------------------------------------
gtk_bin = os.path.join(GTK_RUNTIME_ROOT, "bin")
if not os.path.isdir(gtk_bin):
    raise SystemExit(
        f"GTK3 runtime not found at {gtk_bin!r}. Set REBIND_GTK_RUNTIME to its install root."
    )
for name in os.listdir(gtk_bin):
    if name.lower().endswith(".dll"):
        binaries.append((os.path.join(gtk_bin, name), "gtk3-runtime/bin"))

gtk_fonts_conf = os.path.join(GTK_RUNTIME_ROOT, "etc", "fonts")
if os.path.isdir(gtk_fonts_conf):
    datas.append((gtk_fonts_conf, "gtk3-runtime/etc/fonts"))

a = Analysis(
    ["../src/rebind/app.py"],
    pathex=["../src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["rebind"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="rebind",
          console=True, icon=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="rebind")
