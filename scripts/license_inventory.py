"""Generate the third-party license inventory for the bundled Python distributions.

Rebind's frozen bundle freezes its runtime Python dependency closure -- the OCR engine
(rapidocr_onnxruntime + onnxruntime + opencv), pikepdf, pypdfium2, Pillow, the local web server,
and everything they pull in. These are redistributed unmodified under their own licenses, and the
installer must carry those notices. (There is no longer any vendored native GTK stack: Rebind
remediates PDFs in place and no longer renders HTML, so WeasyPrint and its GTK/Cairo DLLs are not
bundled -- that whole per-DLL licensing burden is gone.)

This script is the source of truth for the notice. It resolves the runtime closure from the
top-level runtime dependencies, writes each distribution's own license text, and fails if any
bundled distribution has no discoverable license text.

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
LICENSES_DIR = REPO_ROOT / "packaging/licenses"
PYTHON_INVENTORY = LICENSES_DIR / "PYTHON-INVENTORY.md"
PYTHON_LICENSES_DIR = LICENSES_DIR / "python"
THIRD_PARTY = LICENSES_DIR / "LICENSE-THIRD-PARTY.txt"

# The runtime dependency closure the frozen bundle vendors is resolved from these top-level deps
# (they mirror pyproject's runtime `dependencies`; PIL/Pillow arrives transitively via pikepdf and
# rapidocr, cv2/onnxruntime via rapidocr).
RUNTIME_ROOTS = [
    "pikepdf", "fastapi", "uvicorn", "pdfminer.six",
    "rapidocr-onnxruntime", "pypdfium2",
]
# Distributions whose wheel ships no license file of its own -> a canonical fallback text.
PY_FALLBACK: dict[str, tuple[str, str]] = {
    "flatbuffers": ("Apache-2.0", "LICENSE-Apache-2.0.txt"),
    "rapidocr-onnxruntime": ("Apache-2.0", "LICENSE-Apache-2.0.txt"),
}
# Redistributed data that is not a Python distribution: the OCR models bundled inside RapidOCR.
BUNDLED_MODELS: list[tuple[str, str, str]] = [
    ("PP-OCRv4 detection / recognition / classification models (PaddleOCR)", "Apache-2.0",
     "shipped inside rapidocr_onnxruntime/models/*.onnx"),
]


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


_THIRD_PARTY_HEADER = """\
================================================================================
Rebind -- Third-Party License Notices
================================================================================

Rebind itself is distributed under the MIT License. Its full text is installed
alongside this file as LICENSE-Rebind.txt.

Rebind is not, however, only Rebind. To work offline without requiring you to
install anything else, it bundles its runtime Python dependencies -- the OCR
engine, the PDF libraries, the local web server, and everything they depend on.
Those are the work of other people, distributed under their own licenses, and
this file discharges the obligation to tell you so.

{count} bundled distributions are covered, listed below. The full text of each
distribution's license is installed in the python/ subfolder of this directory.
None of these libraries have been modified. Nothing in this file modifies the
terms of any license it points to; where they differ, the license text governs.

================================================================================
"""


def _render_third_party(rows: list[tuple[str, str, str, str | None]]) -> str:
    out = [_THIRD_PARTY_HEADER.format(count=len(rows)), ""]
    for name, version, spdx, _text in rows:
        out.append(f"{name} {version}")
        out.append(f"    License:  {spdx}")
        out.append(f"    Text:     python/{name}.txt")
        out.append("")
    out.append("Bundled OCR models:")
    for component, spdx, location in BUNDLED_MODELS:
        out.append(f"    {component}")
        out.append(f"        License: {spdx} ({location})")
    out += ["", "=" * 80, "", "REQUIRED CREDITS", ""]
    # The FreeType License, section 3 ("Credits"), asks distributors to carry this exact sentence.
    # FreeType ships in the bundle inside Pillow's wheel (PIL/.libs), so the obligation still
    # applies even though FreeType is not itself a top-level distribution.
    out.append("    Portions of this software are copyright (c) 2006-2021 The FreeType")
    out.append("    Project (www.freetype.org). All rights reserved.")
    out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="verify only; do not rewrite the inventory")
    args = parser.parse_args()

    rows = _python_inventory()
    problems = _check_python(rows)
    if problems:
        print("License inventory is out of sync with the bundled distributions:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        return 1

    if args.check:
        print(f"OK: {len(rows)} bundled Python distributions, all license texts present.")
        return 0

    _write_python_inventory(rows)
    THIRD_PARTY.write_text(_render_third_party(rows), encoding="utf-8")
    print(f"Wrote {PYTHON_INVENTORY.relative_to(REPO_ROOT)} and "
          f"{THIRD_PARTY.relative_to(REPO_ROOT)} ({len(rows)} Python distributions).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
