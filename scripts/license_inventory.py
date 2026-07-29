"""Generate the per-DLL license inventory for the vendored GTK3 runtime.

Rebind's frozen bundle vendors the entire `bin\\` directory of the GTK-for-Windows Runtime
Environment (see `packaging/rebind.spec`). Redistributing those DLLs carries license
obligations that must be discharged at the point of distribution -- the installer.

The upstream packager's own `gtk3-runtime/license.txt` is NOT sufficient for this: it names
13 projects while the runtime ships 80 DLLs, omitting GnuTLS, Nettle, GMP, SQLite, libtiff,
JasPer, librsvg, gtksourceview, libsoup and others entirely. Several of the omitted ones carry
*heavier* obligations than anything it does list (GMP/Nettle/libidn2/libunistring are LGPL-3,
not LGPL-2.1). So the mapping below was built independently, per DLL.

This script is the source of truth for that mapping. It reads the DLLs actually present in a
built bundle and fails if the set on disk and the set mapped here disagree in either
direction. That matters because the vendored set is expected to shrink (most of these DLLs are
never loaded -- WeasyPrint dlopens six libraries; see `docs/decisions/`): after any change to
what is vendored, re-running this regenerates the inventory and refuses to emit a document that
silently under- or over-claims.

    uv run python scripts/license_inventory.py            # verify + rewrite the inventory
    uv run python scripts/license_inventory.py --check    # verify only, non-zero on drift
"""

from __future__ import annotations

import argparse
import importlib.metadata as _md
import sys
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLED_BIN = REPO_ROOT / "packaging/dist/rebind/_internal/gtk3-runtime/bin"
LICENSES_DIR = REPO_ROOT / "packaging/licenses"
INVENTORY = LICENSES_DIR / "DLL-INVENTORY.md"
PYTHON_INVENTORY = LICENSES_DIR / "PYTHON-INVENTORY.md"
PYTHON_LICENSES_DIR = LICENSES_DIR / "python"

# The bundle also freezes the runtime Python dependency closure (the OCR engine, the PDF renderer's
# Python layer, the web server, and everything they pull in) -- these are redistributed too and
# were historically not in the notice. The closure is resolved from these top-level runtime deps.
RUNTIME_ROOTS = [
    "weasyprint", "pikepdf", "fastapi", "uvicorn", "pdfminer.six",
    "rapidocr-onnxruntime", "pypdfium2",
]
# Distributions whose wheel ships no license file of its own -> a canonical fallback text.
PY_FALLBACK: dict[str, tuple[str, str]] = {
    "flatbuffers": ("Apache-2.0", "LICENSE-Apache-2.0.txt"),
    "rapidocr-onnxruntime": ("Apache-2.0", "LICENSE-Apache-2.0.txt"),
    # webencodings ships no LICENSE in its wheel; this is its actual upstream notice (BSD-3 with
    # Simon Sapin's copyright), not a generic SPDX template -- see test_license_inventory.
    "webencodings": ("BSD-3-Clause", "LICENSE-webencodings.txt"),
}
# Redistributed data that is not a Python distribution: the OCR models bundled inside RapidOCR.
BUNDLED_MODELS: list[tuple[str, str, str]] = [
    ("PP-OCRv4 detection / recognition / classification models (PaddleOCR)", "Apache-2.0",
     "shipped inside rapidocr_onnxruntime/models/*.onnx"),
]
# Shown in the Inno Setup wizard before install proceeds (`LicenseFile` in packaging/rebind.iss)
# and installed into {app}\licenses\ so it remains available afterwards.
THIRD_PARTY = LICENSES_DIR / "LICENSE-THIRD-PARTY.txt"

# Every DLL -> (upstream project, license expression, license text file(s) in packaging/licenses).
#
# License expressions use SPDX operators. "OR" means the recipient may choose either license;
# Rebind does not need to pick one, but must supply the texts of the options it relies on, so
# both files are listed. A "+" suffix means "or any later version".
#
# Where a project is dual-licensed and only one option is practical for redistribution in a
# proprietary-compatible bundle, that is noted in NOTES below rather than silently resolved.
LGPL21 = ("LGPL-2.1-or-later", ["LICENSE-LGPL-2.1.txt"])
LGPL3 = ("LGPL-3.0-or-later", ["LICENSE-LGPL-3.0.txt", "LICENSE-GPL-3.0.txt"])
LGPL3_OR_GPL2 = (
    "LGPL-3.0-or-later OR GPL-2.0-or-later",
    ["LICENSE-LGPL-3.0.txt", "LICENSE-GPL-3.0.txt", "LICENSE-GPL-2.0.txt"],
)
GCC_RUNTIME = (
    "GPL-3.0-or-later WITH GCC-exception-3.1",
    ["LICENSE-GPL-3.0.txt", "LICENSE-GCC-Runtime-Exception-3.1.txt"],
)
CAIRO = ("LGPL-2.1-only OR MPL-1.1", ["LICENSE-LGPL-2.1.txt", "LICENSE-MPL-1.1.txt"])

DLLS: dict[str, tuple[str, tuple[str, list[str]]]] = {}


def _add(project: str, license_: tuple[str, list[str]], *names: str) -> None:
    for name in names:
        DLLS[name] = (project, license_)


# --- GNOME/GTK stack: uniformly LGPL-2.1-or-later ---------------------------------------------
_add("GLib", LGPL21, "libglib-2.0-0.dll", "libgio-2.0-0.dll", "libgmodule-2.0-0.dll",
     "libgobject-2.0-0.dll", "libgthread-2.0-0.dll")
_add("GTK", LGPL21, "libgtk-3-0.dll", "libgdk-3-0.dll", "libgailutil-3-0.dll")
_add("ATK", LGPL21, "libatk-1.0-0.dll")
_add("gdk-pixbuf", LGPL21, "libgdk_pixbuf-2.0-0.dll")
_add("Pango", LGPL21, "libpango-1.0-0.dll", "libpangocairo-1.0-0.dll", "libpangoft2-1.0-0.dll",
     "libpangowin32-1.0-0.dll")
_add("gtkmm / glibmm C++ bindings", LGPL21, "libatkmm-1.6-1.dll", "libcairomm-1.0-1.dll",
     "libgdkmm-3.0-1.dll", "libgiomm-2.4-1.dll", "libglibmm-2.4-1.dll",
     "libglibmm_generate_extra_defs-2.4-1.dll", "libgtkmm-3.0-1.dll", "libpangomm-1.4-1.dll",
     "libgtksourceviewmm-3.0-0.dll")
_add("libsigc++", LGPL21, "libsigc-2.0-0.dll")
_add("libxml++", LGPL21, "libxml++-2.6-2.dll", "libxml++-3.0-1.dll")
_add("gobject-introspection", LGPL21, "libgirepository-1.0-1.dll")
_add("JSON-GLib", LGPL21, "libjson-glib-1.0-0.dll")
_add("GtkSourceView", LGPL21, "libgtksourceview-3.0-1.dll", "libgtksourceview-4-0.dll")
_add("libpeas", LGPL21, "libpeas-1.0-0.dll", "libpeas-gtk-1.0-0.dll")
_add("libsoup", LGPL21, "libsoup-2.4-1.dll", "libsoup-gnome-2.4-1.dll")
_add("librsvg", LGPL21, "librsvg-2-2.dll")
_add("libcroco", LGPL21, "libcroco-0.6-3.dll")
_add("GNU FriBidi", LGPL21, "libfribidi-0.dll")
_add("libthai", LGPL21, "libthai-0.dll")
_add("libdatrie", LGPL21, "libdatrie-1.dll")
_add("libproxy", LGPL21, "libproxy-1.dll")
_add("GnuTLS", LGPL21, "libgnutls-30.dll")
_add("GNU libtasn1", LGPL21, "libtasn1-6.dll")
_add("GNU libiconv", LGPL21, "libiconv-2.dll")
_add("GNU gettext (libintl)", LGPL21, "libintl-8.dll")
_add("Graphite2", LGPL21, "libgraphite2.dll")

# --- LGPL-3 components: heavier obligations than the rest of the bundle ------------------------
_add("GNU MP (GMP)", LGPL3_OR_GPL2, "libgmp-10.dll")
_add("Nettle", LGPL3_OR_GPL2, "libnettle-8.dll", "libhogweed-6.dll")
_add("GNU libidn2", LGPL3_OR_GPL2, "libidn2-0.dll")
_add("GNU libunistring", LGPL3, "libunistring-2.dll")

# --- Toolchain runtime --------------------------------------------------------------------------
_add("GCC runtime (TDM-GCC/MinGW-w64)", GCC_RUNTIME, "libgcc_s_seh-1.dll", "libstdc++-6.dll",
     "libssp-0.dll")
_add("MinGW-w64 winpthreads", ("MIT AND BSD-3-Clause", ["LICENSE-mingw-w64-winpthreads.txt"]),
     "libwinpthread-1.dll")

# --- Permissive graphics/text stack ------------------------------------------------------------
_add("cairo", CAIRO, "libcairo-2.dll", "libcairo-gobject-2.dll",
     "libcairo-script-interpreter-2.dll")
_add("pixman", ("MIT", ["LICENSE-pixman.txt"]), "libpixman-1-0.dll")
_add("HarfBuzz", ("MIT", ["LICENSE-HarfBuzz.txt"]), "libharfbuzz-0.dll")
_add("FreeType", ("FTL OR GPL-2.0-or-later", ["LICENSE-FreeType-FTL.txt", "LICENSE-GPL-2.0.txt"]),
     "libfreetype-6.dll")
_add("fontconfig", ("MIT", ["LICENSE-fontconfig.txt"]), "libfontconfig-1.dll")
_add("libpng", ("Libpng", ["LICENSE-libpng.txt"]), "libpng16-16.dll")
_add("libepoxy", ("MIT", ["LICENSE-libepoxy.txt"]), "libepoxy-0.dll")

# --- Compression / parsing / misc ---------------------------------------------------------------
_add("zlib", ("Zlib", ["LICENSE-zlib.txt"]), "zlib1.dll")
_add("bzip2", ("bzip2-1.0.6", ["LICENSE-bzip2.txt"]), "libbz2-1.dll")
_add("Brotli", ("MIT", ["LICENSE-brotli.txt"]), "libbrotlicommon.dll", "libbrotlidec.dll")
_add("XZ Utils (liblzma)", ("LicenseRef-public-domain", ["LICENSE-xz-liblzma.txt"]),
     "liblzma-5.dll")
_add("Expat", ("MIT", ["LICENSE-expat.txt"]), "libexpat-1.dll")
_add("libxml2", ("MIT", ["LICENSE-libxml2.txt"]), "libxml2-2.dll")
_add("libxslt", ("MIT", ["LICENSE-libxslt.txt"]), "libxslt-1.dll", "libexslt-0.dll")
_add("libffi", ("MIT", ["LICENSE-libffi.txt"]), "libffi-7.dll")
_add("PCRE", ("BSD-3-Clause", ["LICENSE-PCRE.txt"]), "libpcre-1.dll")
_add("libjpeg (IJG)", ("IJG", ["LICENSE-IJG-libjpeg.txt"]), "libjpeg-8.dll")
_add("libtiff", ("libtiff", ["LICENSE-libtiff.txt"]), "libtiff-5.dll")
_add("JasPer", ("JasPer-2.0", ["LICENSE-JasPer-2.0.txt"]), "libjasper-4.dll")
_add("SQLite", ("blessing", ["LICENSE-SQLite-blessing.txt"]), "libsqlite3-0.dll")
_add("libpsl", ("MIT", ["LICENSE-libpsl.txt"]), "libpsl-5.dll")
_add("p11-kit", ("BSD-3-Clause", ["LICENSE-p11-kit.txt"]), "libp11-kit-0.dll")


NOTES = """\
## How this was determined

The GTK-for-Windows Runtime Environment's own `gtk3-runtime/license.txt` names 13 projects; the
runtime ships 80 DLLs. Everything it omits was identified per DLL from the file name, the Win32
version resource where one is present (`ProductName`/`ProductVersion`/`CompanyName`), and the
exported symbols where the file name alone is ambiguous.

Two corrections to assumptions recorded earlier in this repository:

- **`libiconv-2.dll` is GNU libiconv, not win-iconv.** The upstream `license.txt` credits
  win-iconv (MIT). The DLL exports `_libiconv_version`, `libiconv_open`, `libiconv_close` and
  its version resource reads "libiconv: character set conversion library / 1.16 / Free Software
  Foundation" -- that is GNU libiconv, which is **LGPL-2.1-or-later**, a materially heavier
  obligation than MIT.
- **`libpcre-1.dll` is PCRE1, not PCRE2.** An earlier draft of this directory's README listed
  "PCRE2 / BSD-3-Clause". The bundle ships PCRE 8.x (PCRE1). Both are BSD-3-Clause, so the
  conclusion is unchanged, but the text supplied is PCRE1's.

## LGPL compliance

Both LGPL-2.1 and LGPL-3 permit distribution alongside a work that uses the library, provided
the user can replace the library with a modified version. Rebind satisfies this by **dynamic
linking**: every component above ships as a separate, unmodified `.dll` in
`{app}\\_internal\\gtk3-runtime\\bin\\`, loaded at runtime via `os.add_dll_directory` (see
`src/rebind/__init__.py`). A user may replace any of these DLLs in place with their own build of
the same library and Rebind will load it. None of these libraries are statically linked into
`rebind.exe`, and none are modified from the upstream binaries shipped by the GTK-for-Windows
Runtime Environment installer.

Corresponding source is available from each project listed above; the runtime's packager states
sources may be obtained from the upstream project URLs.

## GPL-3 components

`libstdc++-6.dll` and `libgcc_s_seh-1.dll` are GPL-3-licensed but carry the **GCC Runtime Library
Exception 3.1**, which explicitly permits distributing them with a program compiled by GCC
without that program becoming subject to the GPL. This is the standard and intended arrangement
for any MinGW-built binary; it does not impose obligations on Rebind's own MIT-licensed code.

These are the only GPL-3-family components in the bundle, and the exception is what makes them
unproblematic. Every library that was LGPL-3 *without* such an exception -- GnuTLS, Nettle,
Hogweed, GMP, libidn2, libunistring, gtksourceview -- reached the bundle only because the whole
GTK3 runtime `bin\\` directory was vendored wholesale. `packaging/rebind.spec` now vendors only
the computed dependency closure of the libraries WeasyPrint dlopens, and none of them survive it.
The heaviest copyleft obligations are therefore no longer present at all rather than being
argued around.
"""


def _load_present() -> set[str]:
    if not BUNDLED_BIN.is_dir():
        sys.exit(
            f"No built bundle at {BUNDLED_BIN}.\n"
            "Build it first: uv run pyinstaller packaging/rebind.spec (or `pytest -m packaging`)."
        )
    return {p.name for p in BUNDLED_BIN.glob("*.dll")}


def _check(present: set[str]) -> tuple[list[str], list[str]]:
    """Return (fatal problems, non-fatal stale mappings).

    The two directions of drift are not symmetric and must not be treated as if they were.

    A DLL in the bundle with no mapping here is **fatal**: the installer would ship a binary
    whose license is undeclared, which is exactly the false claim this script exists to prevent.

    A mapping for a DLL no longer in the bundle is **not** an error. `_render` emits only what is
    actually present, so a stale row cannot over-claim. Keeping these rows is deliberate: DLLS is
    the full catalogue of what the GTK3 runtime can ship, and several rows required reading the
    binary to identify the real upstream project (libiconv, libpcre) -- expensive to redo if the
    vendored set ever grows again. They are reported so the drift stays visible, not silenced.
    """
    problems, stale = [], []
    for name in sorted(present - DLLS.keys()):
        problems.append(f"  DLL present in the bundle but not mapped in this script: {name}")
    for project, (_, files) in DLLS.values():
        for filename in files:
            if not (LICENSES_DIR / filename).is_file():
                problems.append(f"  {project}: missing license text {filename}")
    for name in sorted(DLLS.keys() - present):
        stale.append(f"  mapped but not currently vendored: {name}")
    return problems, stale


def _render(present: set[str]) -> str:
    by_project: dict[str, tuple[tuple[str, list[str]], list[str]]] = {}
    for name in sorted(present):
        project, license_ = DLLS[name]
        by_project.setdefault(project, (license_, []))[1].append(name)

    lines = [
        "# Per-DLL license inventory",
        "",
        "**Generated by `scripts/license_inventory.py` -- do not edit by hand.**",
        "Regenerate after any change to what `packaging/rebind.spec` vendors.",
        "",
        f"Covers the {len(present)} DLLs in `packaging/dist/rebind/_internal/gtk3-runtime/bin/`.",
        "PyInstaller's own dependency analysis additionally copies many of these same files to",
        "`packaging/dist/rebind/_internal/`; those are byte-identical duplicates of the files",
        "below and are covered by the same entries.",
        "",
        "| Project | License | License text | DLLs |",
        "|---|---|---|---|",
    ]
    for project in sorted(by_project, key=str.lower):
        (expression, files), names = by_project[project]
        texts = "<br>".join(f"`{f}`" for f in files)
        dlls = "<br>".join(f"`{n}`" for n in sorted(names))
        lines.append(f"| {project} | {expression} | {texts} | {dlls} |")
    lines += ["", NOTES]
    return "\n".join(lines) + "\n"


_THIRD_PARTY_HEADER = """\
================================================================================
Rebind -- Third-Party License Notices
================================================================================

Rebind itself is distributed under the MIT License. Its full text is installed
alongside this file as LICENSE-Rebind.txt.

Rebind is not, however, only Rebind. To work without requiring you to install
anything else, it bundles the GTK3 native libraries that its PDF renderer
depends on. Those libraries are the work of other people, distributed under
their own licenses, and this file discharges the obligation to tell you so.

{count} bundled libraries are covered, grouped below by project. The full text of
every license referenced here is installed in this same folder. Nothing in this
file modifies the terms of any license it points to; where they differ, the
license text governs.

None of these libraries have been modified. Each ships as a separate DLL and is
loaded dynamically at runtime, which is what satisfies the LGPL's requirement
that you be able to substitute your own build of any of them. To do so, replace
the relevant .dll in this installation and Rebind will load yours instead.

A per-DLL breakdown, including how each mapping was determined, is in
DLL-INVENTORY.md in this folder.

================================================================================
"""


def _render_third_party(present: set[str]) -> str:
    by_project: dict[str, tuple[str, list[str], list[str]]] = {}
    for name in sorted(present):
        project, (expression, files) = DLLS[name]
        entry = by_project.setdefault(project, (expression, files, []))
        entry[2].append(name)

    out = [_THIRD_PARTY_HEADER.format(count=len(present))]
    for project in sorted(by_project, key=str.lower):
        expression, files, names = by_project[project]
        out.append(f"{project}")
        out.append(f"    License:  {expression}")
        out.append(f"    Text:     {', '.join(files)}")
        out.append(f"    Files:    {', '.join(sorted(names))}")
        out.append("")

    out.append("=" * 80)
    out.append("")
    out.append("REQUIRED CREDITS")
    out.append("")
    # The FreeType License, section 3 ("Credits"), asks distributors to carry this exact
    # sentence in their documentation, with the year of the FreeType version actually shipped.
    # The bundled libfreetype-6.dll reports 2.11.1 in its version resource; that release's own
    # copyright line reads 2006-2021. This is an obligation of the license text itself, not
    # boilerplate -- omitting it is the one FTL requirement a notice can silently fail.
    out.append("    Portions of this software are copyright (c) 2006-2021 The FreeType")
    out.append("    Project (www.freetype.org). All rights reserved.")
    out.append("")
    out.append("=" * 80)
    out.append("")
    out.append("The GTK3 libraries above are redistributed from the GTK for Windows Runtime")
    out.append("Environment Installer, packaged by Tom Schoonjans and Alexander Shaduri:")
    out.append("https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer")
    out.append("Corresponding source for each library is available from its upstream project.")
    out.append("")
    return "\n".join(out)


def _spdx(dist: _md.Distribution) -> str:
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License") and "OSI Approved" not in c.split("::")[-1]
    ]
    if classifiers:
        return "; ".join(classifiers)
    first_line = (meta.get("License") or "").strip().splitlines()
    return first_line[0] if first_line else "see license text"


def _dist_license_text(dist: _md.Distribution) -> str | None:
    """The license text a wheel ships in its dist-info, or None if it ships none."""
    for entry in dist.files or []:
        name = str(entry).upper()
        if name.endswith(".PY"):
            continue
        if "LICENSE" in name or "LICENCE" in name or "COPYING" in name:
            try:
                text = dist.read_text(str(entry))
                if not text:
                    text = dist.locate_file(entry).read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeError):
                continue
            if text and len(text) > 50:
                return text
    return None


def _runtime_distributions() -> dict[str, _md.Distribution]:
    """The transitive runtime dependency closure that the frozen bundle vendors.

    Resolved from RUNTIME_ROOTS via each distribution's own Requires-Dist, skipping requirements
    whose environment marker is an extra (test/dev-only), so this matches what PyInstaller freezes
    rather than the whole dev virtualenv.
    """
    seen: dict[str, _md.Distribution] = {}
    stack = list(RUNTIME_ROOTS)
    while stack:
        name = canonicalize_name(stack.pop())
        if name in seen:
            continue
        try:
            dist = _md.distribution(name)
        except _md.PackageNotFoundError:
            continue
        seen[name] = dist
        for req_str in dist.requires or []:
            req = Requirement(req_str)
            # An empty `extra` evaluates a base requirement true and an extras-gated one false.
            if req.marker and not req.marker.evaluate({"extra": ""}):
                continue
            stack.append(req.name)
    return seen


def _python_inventory() -> list[tuple[str, str, str, str | None]]:
    """(name, version, spdx, license_text|None) for each bundled runtime distribution."""
    rows = []
    for name, dist in sorted(_runtime_distributions().items()):
        text = _dist_license_text(dist)
        spdx = _spdx(dist)
        if text is None and name in PY_FALLBACK:
            spdx, fallback_file = PY_FALLBACK[name]
            text = (LICENSES_DIR / fallback_file).read_text(encoding="utf-8")
        rows.append((name, dist.version, spdx, text))
    return rows


def _check_python(rows: list[tuple[str, str, str, str | None]]) -> list[str]:
    return [
        f"  runtime distribution with no discoverable license text: {name} {version} "
        f"(add a PY_FALLBACK entry)"
        for name, version, _spdx_, text in rows
        if text is None
    ]


def _write_python_inventory(rows: list[tuple[str, str, str, str | None]]) -> None:
    """Write each distribution's license text under packaging/licenses/python/ and an index."""
    PYTHON_LICENSES_DIR.mkdir(parents=True, exist_ok=True)
    for stale in PYTHON_LICENSES_DIR.glob("*.txt"):
        stale.unlink()
    lines = [
        "# Bundled Python distributions -- license inventory",
        "",
        "**Generated by `scripts/license_inventory.py` -- do not edit by hand.**",
        "",
        f"Covers the {len(rows)} runtime Python distributions frozen into the bundle, plus the "
        "bundled OCR models. Each distribution's own license text is written beside this file in "
        "`python/`.",
        "",
        "| Distribution | Version | License | Text |",
        "|---|---|---|---|",
    ]
    for name, version, spdx, text in rows:
        assert text is not None  # _check_python guarantees this before we render
        (PYTHON_LICENSES_DIR / f"{name}.txt").write_text(text, encoding="utf-8")
        lines.append(f"| {name} | {version} | {spdx} | `python/{name}.txt` |")
    lines += ["", "## Bundled models", "", "| Component | License | Location |", "|---|---|---|"]
    for component, spdx, location in BUNDLED_MODELS:
        lines.append(f"| {component} | {spdx} | {location} |")
    lines.append("")
    PYTHON_INVENTORY.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _render_python_third_party(rows: list[tuple[str, str, str, str | None]]) -> str:
    out = [
        "",
        "=" * 80,
        "",
        "BUNDLED PYTHON DISTRIBUTIONS",
        "",
        "Rebind also freezes its runtime Python dependencies -- the OCR engine, the PDF",
        "renderer's Python layer, the local web server, and their dependencies -- into the",
        "application. Each is redistributed unmodified under its own license; the full text of",
        "each is installed in the python/ subfolder of this directory.",
        "",
    ]
    for name, version, spdx, _text in rows:
        out.append(f"{name} {version}")
        out.append(f"    License:  {spdx}")
        out.append(f"    Text:     python/{name}.txt")
        out.append("")
    out.append("Bundled OCR models:")
    for component, spdx, location in BUNDLED_MODELS:
        out.append(f"    {component}")
        out.append(f"        License: {spdx} ({location})")
    out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="verify only; do not rewrite the inventory")
    args = parser.parse_args()

    present = _load_present()
    problems, stale = _check(present)
    python_rows = _python_inventory()
    problems += _check_python(python_rows)
    if problems:
        print("License inventory is out of sync with the built bundle:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        return 1

    if stale:
        print(f"Note: {len(stale)} mapped DLLs are not in the current bundle "
              "(harmless -- only vendored DLLs are written to the inventory):", file=sys.stderr)
        print("\n".join(stale), file=sys.stderr)

    if args.check:
        print(f"OK: {len(present)} vendored DLLs and {len(python_rows)} Python distributions, "
              "all mapped, all license texts present.")
        return 0

    INVENTORY.write_text(_render(present), encoding="utf-8")
    _write_python_inventory(python_rows)
    THIRD_PARTY.write_text(
        _render_third_party(present) + _render_python_third_party(python_rows), encoding="utf-8"
    )
    print(f"Wrote {INVENTORY.relative_to(REPO_ROOT)}, {PYTHON_INVENTORY.relative_to(REPO_ROOT)} "
          f"and {THIRD_PARTY.relative_to(REPO_ROOT)} "
          f"({len(present)} DLLs, {len(python_rows)} Python distributions).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
