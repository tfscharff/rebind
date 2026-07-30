# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Rebind.

The bundle is the remediation runtime only: pikepdf (PDF surgery), pypdfium2 (rasterize scanned
pages), and the OCR engine (rapidocr_onnxruntime + onnxruntime + cv2, ADR 0005). There is no
HTML renderer -- Rebind remediates the source PDF in place and never renders HTML, so WeasyPrint
and its GTK3 native stack are neither imported nor bundled. (WeasyPrint remains a dev-only
dependency: the test suite renders synthetic born-digital PDFs with it. It is excluded below so
a stray import can never pull the 40 MB GTK stack back into the bundle.)

`collect_all` pulls each package's native binaries AND data -- the ONNX models and YAML config
live inside rapidocr_onnxruntime and must be collected as data or the frozen exe finds no models.
The real bundle is guarded by `pytest -m packaging` (it OCRs through the shipping exe).
"""
import os

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for package in (
    "pikepdf", "fastapi", "uvicorn",
    "rapidocr_onnxruntime", "onnxruntime", "cv2", "pypdfium2",
):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Modules that must never enter the bundle: the WeasyPrint HTML-rendering stack (dev/test only)
# and its GTK/Cairo/Pango bindings. Excluding them keeps a stray or transitive import from
# dragging the whole native stack back in.
_EXCLUDES = [
    "weasyprint", "pydyf", "cssselect2", "tinycss2", "cairocffi", "cairosvg",
    "pyphen", "fontTools", "lxml",
]

a = Analysis(
    ["../src/rebind/app.py"],
    pathex=["../src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["rebind"],
    hookspath=[],
    runtime_hooks=[],
    excludes=_EXCLUDES,
    noarchive=False,
)
# Drop OpenCV's FFmpeg videoio DLL (~30 MB): it backs cv2.VideoCapture, which nothing here uses.
# RapidOCR and restoration only touch cv2 core/imgproc. This must run on `a.binaries` after
# Analysis, because PyInstaller's own cv2 hook re-adds the DLL after collect_all -- filtering the
# collect_all output alone does not remove it. Match by name for robustness across OpenCV versions
# (opencv_videoio_ffmpeg<ver>_64.dll).
a.binaries = [b for b in a.binaries if "opencv_videoio_ffmpeg" not in b[0].lower()]
a.datas = [d for d in a.datas if "opencv_videoio_ffmpeg" not in d[0].lower()]

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
          console=False, icon="rebind.ico", version="version_info.txt")
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="rebind")
