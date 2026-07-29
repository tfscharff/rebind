# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Rebind.

GTK3 bundling
-------------
WeasyPrint needs the GTK3 native libraries (gobject, pango, harfbuzz, fontconfig, ...) that
Task 3 found are not installed by `uv sync` on Windows -- they come from a separate
GTK3-Runtime-Win64 install. A librarian cannot be asked to install that separately, so this
spec vendors the DLLs WeasyPrint's ffi loader dlopens plus their transitive dependencies --
computed from PE import tables at build time, see below -- along with `etc\fonts` (the
fontconfig configuration WeasyPrint needs to locate usable fonts), into the frozen bundle under
`gtk3-runtime\`.

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
# rapidocr_onnxruntime + onnxruntime + cv2 are the OCR engine (ADR 0005); pypdfium2 rasterizes
# scanned pages. collect_all pulls their native binaries AND data (the bundled ONNX models and
# YAML config live inside rapidocr_onnxruntime, so they must be collected as data or the frozen
# exe finds no models). ADR 0005 proved a standalone probe bundles and OCRs offline; the real
# bundle is guarded by `pytest -m packaging`.
for package in (
    "weasyprint", "pikepdf", "fastapi", "uvicorn",
    "rapidocr_onnxruntime", "onnxruntime", "cv2", "pypdfium2",
):
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
# Vendor only the transitive dependency closure of the libraries WeasyPrint actually dlopens,
# not the runtime's entire bin\ directory. The full directory is 80 DLLs; the closure is 26.
# What gets dropped is not incidental -- it is GTK itself, cairo, gdk-pixbuf, librsvg,
# gtksourceview, and the whole GnuTLS/Nettle/GMP/libidn2/libunistring TLS stack. That last
# group carries every LGPL-3 obligation in the bundle, so trimming removes the heaviest
# licensing burden along with the bytes.
#
# The closure is computed from PE import tables rather than hardcoded, so a GTK runtime upgrade
# that changes dependencies cannot silently leave a stale list behind. Caveat: import tables
# only capture load-time linkage. A library that dlopens a plugin by name at runtime (GIO
# modules, gdk-pixbuf loaders) would not appear here -- nothing on WeasyPrint's path does, and
# `pytest -m packaging` renders a real PDF through the frozen exe to catch it if that changes.
#
# `scripts/license_inventory.py --check` fails if the vendored set and the license mapping
# disagree, so this trim cannot silently invalidate what the installer claims about licensing.
import pefile  # ships with PyInstaller on Windows

# The names WeasyPrint passes to ffi.dlopen (weasyprint/text/ffi.py). libharfbuzz-subset-0 is
# absent from this GTK runtime and WeasyPrint loads it with allow_fail=True, so it is expected
# to be missing; see docs/NEXT-SESSION.md. Every other root missing is a hard error.
DLOPEN_ROOTS = [
    "libgobject-2.0-0.dll",
    "libpango-1.0-0.dll",
    "libharfbuzz-0.dll",
    "libfontconfig-1.dll",
    "libpangoft2-1.0-0.dll",
]
OPTIONAL_ROOTS = ["libharfbuzz-subset-0.dll"]

_available = {
    name.lower(): os.path.join(gtk_bin, name)
    for name in os.listdir(gtk_bin)
    if name.lower().endswith(".dll")
}

_missing = [r for r in DLOPEN_ROOTS if r not in _available]
if _missing:
    raise SystemExit(f"GTK runtime at {gtk_bin!r} is missing required libraries: {_missing}")


def _imported_dlls(path):
    pe = pefile.PE(path, fast_load=True)
    try:
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]]
        )
        return [
            entry.dll.decode("ascii", "replace").lower()
            for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", None) or []
        ]
    finally:
        pe.close()


_closure = set()
_stack = [r for r in DLOPEN_ROOTS + OPTIONAL_ROOTS if r in _available]
while _stack:
    _name = _stack.pop()
    if _name in _closure:
        continue
    _closure.add(_name)
    # Anything not in the runtime's bin\ resolves to a Windows system DLL, which must not be
    # vendored -- shipping copies of kernel32/user32 would be both wrong and unloadable.
    _stack.extend(dep for dep in _imported_dlls(_available[_name]) if dep in _available)

for _name in sorted(_closure):
    binaries.append((_available[_name], "gtk3-runtime/bin"))

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
# console=False: a librarian double-clicking rebind.exe should not see a console window pop
# up (that reads as "broken" or "malware" to a non-technical user, and this app already opens
# its UI in a browser tab). Consequence: with no console, anything written to stdout/stderr
# (uvicorn's request/access logging, unhandled exception tracebacks that would otherwise print
# before the process exits) has nowhere to go and is silently discarded on Windows -- it is
# NOT captured anywhere by default. `src/rebind/app.py::main()` redirects uvicorn's logging to
# a file next to the executable specifically to compensate for this loss; if that redirection
# is ever removed, a librarian who hits a crash will have no diagnostic output at all to send
# back, only "it didn't work."
# version=: without an explicit version resource a PyInstaller exe has entirely blank Company/
# Product/Description metadata, which is an antivirus heuristic trigger in its own right on top
# of everything else about this binary that already looks suspicious (unsigned, large, packed,
# bundling native DLLs, opens a local listener). See packaging/version_info.txt.
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="rebind",
          console=False, icon=None, version="version_info.txt")
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="rebind")
