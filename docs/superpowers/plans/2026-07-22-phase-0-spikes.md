# Rebind Phase 0 — De-risking Spikes: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove — or disprove — that Rebind can generate WCAG/PDF-UA-conformant tagged PDFs using only
libraries that can be bundled into a double-click Windows installer requiring no system Python.

**Architecture:** Two independent risks are being retired. First, the *output* risk: HTML → tagged
PDF/UA via WeasyPrint, verified by veraPDF, with pikepdf post-processing for page labels and metadata.
Second, the *distribution* risk: PyInstaller + Inno Setup packaging a Python service with native
dependencies. Every task ends with a verifiable assertion, not an opinion.

**Tech Stack:** Python 3.12 (via uv), WeasyPrint, pikepdf, pytest, veraPDF (Java CLI), PyInstaller,
Inno Setup.

## Global Constraints

Copied from `docs/superpowers/specs/2026-07-22-rebind-design.md`. Every task's requirements implicitly
include this section.

- **Python 3.12.** Not 3.14 — parts of the CV/ML stack lack wheels. The machine currently has 3.14
  only; uv installs 3.12 alongside it without disturbing the system install.
- **Windows-first.** macOS support is acceptable if free, never a goal, never a blocker.
- **No API key, no GPU, no network access at runtime.** Network use during build is fine.
- **Every dependency must be bundle-able on Windows.** A library that requires a system-wide native
  install the user must perform is disqualified regardless of other merits. This is the whole point of
  Phase 0.
- **Deterministic.** Same input at the same version produces the same output. PDF generation embeds
  timestamps and IDs by default; these must be pinned for tests to be meaningful.
- **Never fabricate.** No spike code may invent document content. Not exercised much in Phase 0, but
  the invariant holds from commit one.
- **License:** MIT for Rebind's own code. Record the license of every dependency added.

## Success criteria for Phase 0

Phase 0 succeeds if, at the end, all of these are true:

1. A generated PDF passes veraPDF PDF/UA-1 validation with zero failed checks.
2. That PDF contains headings, a list, a table with header associations, and a figure with alt text —
   all correctly tagged.
3. Original page labels survive into the output.
4. The whole thing runs from a PyInstaller build on a machine with no Python installed.
5. We have a written decision record naming the chosen generation library and packaging toolchain,
   with evidence.

If task 6 (mathematics) fails, Phase 0 still succeeds — the fallback is documented and math moves to
Phase 3. If tasks 3–5 fail, the spec's core assumption is wrong and we redesign before writing pipeline
code. **That is the point of doing this first.**

---

### Task 1: Project skeleton and toolchain

**Files:**
- Create: `pyproject.toml`
- Create: `src/rebind/__init__.py`
- Create: `tests/test_smoke.py`
- Create: `.python-version`

**Interfaces:**
- Consumes: nothing.
- Produces: a working `uv run pytest` command and the `rebind` package root that every later task
  imports from.

- [ ] **Step 1: Install uv**

uv manages both the Python version and dependencies. It is not currently installed.

Run in PowerShell:
```powershell
irm https://astral.sh/uv/install.ps1 | iex
```

Verify:
```bash
uv --version
```
Expected: a version string, e.g. `uv 0.5.x`.

- [ ] **Step 2: Pin Python 3.12**

```bash
cd "C:/Users/thoma/Documents/rebind"
uv python install 3.12
echo "3.12" > .python-version
```

Verify:
```bash
uv run python --version
```
Expected: `Python 3.12.x` (NOT 3.14).

- [ ] **Step 3: Create pyproject.toml**

```toml
[project]
name = "rebind"
version = "0.0.1"
description = "Accessible PDF reconstruction for damaged library scans"
readme = "README.md"
license = { file = "LICENSE" }
authors = [{ name = "Thomas Scharff" }]
requires-python = ">=3.12,<3.13"
dependencies = [
    "weasyprint>=62",
    "pikepdf>=9",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "ruff>=0.6",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/rebind"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v"

[tool.ruff]
line-length = 100
target-version = "py312"
```

- [ ] **Step 4: Create the package root**

`src/rebind/__init__.py`:
```python
"""Rebind — accessible PDF reconstruction for damaged library scans."""

__version__ = "0.0.1"
```

- [ ] **Step 5: Write the smoke test**

`tests/test_smoke.py`:
```python
import sys

import rebind


def test_package_imports():
    assert rebind.__version__ == "0.0.1"


def test_python_version_is_pinned():
    """Phase 0 constraint: 3.12 only. 3.14 lacks wheels for the CV/ML stack."""
    assert sys.version_info[:2] == (3, 12), f"expected 3.12, got {sys.version_info[:2]}"
```

- [ ] **Step 6: Run the tests**

```bash
uv sync --extra dev
uv run pytest
```
Expected: `2 passed`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .python-version src/rebind/__init__.py tests/test_smoke.py
git commit -m "Add project skeleton pinned to Python 3.12"
git push origin main
```

---

### Task 2: veraPDF wrapper

Validation comes first because it is the measuring instrument for every task after it. A wrapper we
don't trust makes every later "it passes" meaningless.

**Files:**
- Create: `src/rebind/validate.py`
- Create: `tests/test_validate.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Consumes: `rebind` package root from Task 1.
- Produces:
  - `ValidationResult` dataclass with fields `compliant: bool`, `flavour: str`,
    `failed_rules: list[FailedRule]`, `raw: dict`.
  - `FailedRule` dataclass with fields `clause: str`, `test_number: int`, `description: str`,
    `failed_checks: int`.
  - `validate_pdf_ua(pdf_path: Path, verapdf_exe: Path | None = None) -> ValidationResult`
  - `VeraPdfNotFound` exception.

- [ ] **Step 1: Install veraPDF**

Java 23 is already present, so the greenfield installer will run. Download the installer from
https://verapdf.org/software/ and install to a known path. Record the path to `verapdf.bat`.

Verify:
```bash
"/c/Program Files/veraPDF/verapdf.bat" --version
```
Expected: a version banner. If the install path differs, use the actual path in the next steps.

- [ ] **Step 2: Write the failing test**

`tests/conftest.py`:
```python
import os
import shutil
from pathlib import Path

import pikepdf
import pytest


@pytest.fixture(scope="session")
def verapdf_exe() -> Path:
    """Locate verapdf.bat, preferring the REBIND_VERAPDF env var."""
    env = os.environ.get("REBIND_VERAPDF")
    if env and Path(env).exists():
        return Path(env)
    for candidate in (
        Path(r"C:\Program Files\veraPDF\verapdf.bat"),
        Path(r"C:\veraPDF\verapdf.bat"),
    ):
        if candidate.exists():
            return candidate
    found = shutil.which("verapdf")
    if found:
        return Path(found)
    pytest.skip("veraPDF not installed; set REBIND_VERAPDF to verapdf.bat")


@pytest.fixture
def untagged_pdf(tmp_path: Path) -> Path:
    """A minimal PDF with no tags at all. Must fail PDF/UA validation."""
    target = tmp_path / "untagged.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.save(target)
    return target
```

`tests/test_validate.py`:
```python
from pathlib import Path

from rebind.validate import ValidationResult, validate_pdf_ua


def test_untagged_pdf_is_not_compliant(untagged_pdf: Path, verapdf_exe: Path):
    result = validate_pdf_ua(untagged_pdf, verapdf_exe=verapdf_exe)

    assert isinstance(result, ValidationResult)
    assert result.compliant is False
    assert result.flavour == "ua1"
    assert len(result.failed_rules) > 0


def test_failed_rules_are_parsed_with_detail(untagged_pdf: Path, verapdf_exe: Path):
    """A wrapper that returns 'False' but can't say why is useless for debugging."""
    result = validate_pdf_ua(untagged_pdf, verapdf_exe=verapdf_exe)

    rule = result.failed_rules[0]
    assert rule.clause, "clause must be populated"
    assert rule.description, "description must be populated"
    assert rule.failed_checks >= 1
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
uv run pytest tests/test_validate.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'rebind.validate'`.

- [ ] **Step 4: Inspect veraPDF's actual JSON shape**

Do not guess at the schema. Generate a report and read it:

```bash
"/c/Program Files/veraPDF/verapdf.bat" --flavour ua1 --format json tests/fixtures/any.pdf > /tmp/report.json
python -c "import json;print(json.dumps(json.load(open('/tmp/report.json')),indent=2)[:3000])"
```

Expected: JSON containing a jobs array, each with a `validationResult` object holding a compliance
boolean and rule summaries. **Note the exact key names** — they vary between veraPDF major versions,
and the implementation in the next step must match what you actually observe. If the observed keys
differ from the implementation below, change the implementation, not the test.

- [ ] **Step 5: Write the implementation**

`src/rebind/validate.py`:
```python
"""Thin wrapper over the veraPDF CLI.

veraPDF validates PDF/UA structural conformance. It is not a WCAG checker — the criteria
requiring human judgment are reported separately by Rebind. See the design spec, section 2.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class VeraPdfNotFound(RuntimeError):
    """Raised when the veraPDF executable cannot be located."""


@dataclass(frozen=True)
class FailedRule:
    clause: str
    test_number: int
    description: str
    failed_checks: int


@dataclass(frozen=True)
class ValidationResult:
    compliant: bool
    flavour: str
    failed_rules: list[FailedRule] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def summary(self) -> str:
        if self.compliant:
            return f"PDF/UA-1: PASS (0 failed checks)"
        total = sum(r.failed_checks for r in self.failed_rules)
        return f"PDF/UA-1: FAIL ({len(self.failed_rules)} rules, {total} failed checks)"


def _find_verapdf() -> Path:
    import os
    import shutil

    env = os.environ.get("REBIND_VERAPDF")
    if env and Path(env).exists():
        return Path(env)
    found = shutil.which("verapdf")
    if found:
        return Path(found)
    raise VeraPdfNotFound(
        "veraPDF not found. Install it from https://verapdf.org/software/ and set "
        "REBIND_VERAPDF to the full path of verapdf.bat."
    )


def validate_pdf_ua(pdf_path: Path, verapdf_exe: Path | None = None) -> ValidationResult:
    """Validate a PDF against PDF/UA-1 and return a structured result."""
    exe = Path(verapdf_exe) if verapdf_exe else _find_verapdf()

    proc = subprocess.run(
        [str(exe), "--flavour", "ua1", "--format", "json", str(pdf_path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    # veraPDF exits non-zero when a document is non-compliant, which is not an error for us.
    if not proc.stdout.strip():
        raise RuntimeError(f"veraPDF produced no output. stderr: {proc.stderr[:500]}")

    raw = json.loads(proc.stdout)
    job = raw["report"]["jobs"][0] if "report" in raw else raw["jobs"][0]
    validation = job["validationResult"]
    if isinstance(validation, list):
        validation = validation[0]

    rules = [
        FailedRule(
            clause=str(summary.get("clause", "")),
            test_number=int(summary.get("testNumber", 0)),
            description=str(summary.get("description", "")),
            failed_checks=int(summary.get("failedChecks", 0)),
        )
        for summary in validation.get("details", {}).get("ruleSummaries", [])
    ]

    return ValidationResult(
        compliant=bool(validation.get("compliant", False)),
        flavour="ua1",
        failed_rules=rules,
        raw=raw,
    )
```

- [ ] **Step 6: Run the tests**

```bash
uv run pytest tests/test_validate.py -v
```
Expected: `2 passed`. If the JSON keys differ from Step 4's observation, fix `validate.py` and rerun.

- [ ] **Step 7: Commit**

```bash
git add src/rebind/validate.py tests/test_validate.py tests/conftest.py
git commit -m "Add veraPDF validation wrapper"
git push origin main
```

---

### Task 3: Minimal tagged PDF/UA from HTML

**The single highest-risk task in Phase 0.** If WeasyPrint cannot produce a PDF/UA-conformant document
on Windows without a system GTK install, the spec's output strategy changes.

**Files:**
- Create: `src/rebind/render.py`
- Create: `tests/test_render_minimal.py`

**Interfaces:**
- Consumes: `validate_pdf_ua` from Task 2.
- Produces:
  - `render_html_to_pdf(html: str, target: Path, *, title: str, lang: str = "en") -> Path`

- [ ] **Step 1: Confirm WeasyPrint imports on Windows**

WeasyPrint needs Pango/cairo. This step establishes whether it works from a plain `uv sync` or needs
vendored DLLs.

```bash
uv run python -c "import weasyprint; print(weasyprint.__version__)"
```

Expected: a version number ≥ 62.

**If this raises `OSError: cannot load library 'libgobject-2.0-0'`,** WeasyPrint needs the GTK runtime.
Install the MSYS2 GTK3 runtime, then retry. **Record in the task notes whether a system install was
required** — that answer is the entire point of this spike, and it determines whether Task 7 can
succeed.

- [ ] **Step 2: Write the failing test**

`tests/test_render_minimal.py`:
```python
from pathlib import Path

from rebind.render import render_html_to_pdf
from rebind.validate import validate_pdf_ua

MINIMAL_HTML = """
<h1>The Structure of Scientific Revolutions</h1>
<p>Normal science, the activity in which most scientists inevitably spend almost all
their time, is predicated on the assumption that the scientific community knows what
the world is like.</p>
"""


def test_minimal_document_passes_pdf_ua(tmp_path: Path, verapdf_exe: Path):
    target = tmp_path / "minimal.pdf"

    render_html_to_pdf(MINIMAL_HTML, target, title="Scientific Revolutions", lang="en")

    assert target.exists()
    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary() + "\n" + "\n".join(
        f"  {r.clause}: {r.description}" for r in result.failed_rules
    )
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
uv run pytest tests/test_render_minimal.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'rebind.render'`.

- [ ] **Step 4: Write the implementation**

`src/rebind/render.py`:
```python
"""HTML to tagged PDF/UA rendering.

Rebind generates its output rather than patching a source PDF, which is what makes most of
WCAG 2.1 AA achievable by construction. See the design spec, section 2.
"""

from __future__ import annotations

from pathlib import Path

from weasyprint import HTML

_DOCUMENT_TEMPLATE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  @page {{ size: letter; margin: 1in; }}
  body {{ font-family: "DejaVu Serif", Georgia, serif; font-size: 11pt; line-height: 1.45;
          color: #111; background: #fff; }}
  h1, h2, h3 {{ line-height: 1.2; }}
  table {{ border-collapse: collapse; }}
  th, td {{ border: 1px solid #444; padding: 4pt 6pt; text-align: left; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def render_html_to_pdf(html: str, target: Path, *, title: str, lang: str = "en") -> Path:
    """Render an HTML body fragment to a tagged PDF/UA-1 file.

    The document colours are fixed to guarantee WCAG 1.4.3 contrast, which we can do
    precisely because we generate the output rather than inherit it.
    """
    document = _DOCUMENT_TEMPLATE.format(lang=lang, title=_escape(title), body=html)
    HTML(string=document).write_pdf(
        target,
        pdf_variant="pdf/ua-1",
        uncompressed_pdf=False,
    )
    return target


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
```

- [ ] **Step 5: Run the test**

```bash
uv run pytest tests/test_render_minimal.py -v
```
Expected: PASS.

**If it fails,** print the failed rules — the assertion message already does this — and work through
them one at a time. Common first failures are a missing document title in XMP metadata, a missing
`/Lang`, or a missing PDF/UA identification schema. If `pdf_variant` is not accepted by the installed
WeasyPrint version, check the version's documentation for the correct parameter name; older releases
used `pdf_identifier`/`pdf_variant` inconsistently.

- [ ] **Step 6: Commit**

```bash
git add src/rebind/render.py tests/test_render_minimal.py
git commit -m "Render minimal tagged PDF/UA from HTML"
git push origin main
```

---

### Task 4: Structure matrix — headings, lists, tables, figures

Proves the tagger emits the structure types Rebind's document model will actually produce.

**Files:**
- Create: `tests/test_render_structure.py`
- Create: `src/rebind/inspect.py`

**Interfaces:**
- Consumes: `render_html_to_pdf` (Task 3), `validate_pdf_ua` (Task 2).
- Produces:
  - `structure_element_types(pdf_path: Path) -> set[str]` — the set of structure-element tag names
    present in the document's tag tree.

- [ ] **Step 1: Write the failing test**

`tests/test_render_structure.py`:
```python
from pathlib import Path

from rebind.inspect import structure_element_types
from rebind.render import render_html_to_pdf
from rebind.validate import validate_pdf_ua

STRUCTURED_HTML = """
<h1>Chapter 4: Thermodynamics</h1>
<h2>4.1 The First Law</h2>
<p>Energy is conserved in an isolated system.</p>
<ul>
  <li>Heat added to the system</li>
  <li>Work done by the system</li>
</ul>
<table>
  <caption>Specific heat capacities</caption>
  <thead>
    <tr><th scope="col">Substance</th><th scope="col">c (J/g&#183;K)</th></tr>
  </thead>
  <tbody>
    <tr><th scope="row">Water</th><td>4.18</td></tr>
    <tr><th scope="row">Copper</th><td>0.385</td></tr>
  </tbody>
</table>
<figure>
  <img src="data:image/gif;base64,R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw=="
       alt="Diagram of a piston compressing gas in a cylinder" width="120" height="80">
  <figcaption>Figure 4.1 Isothermal compression.</figcaption>
</figure>
"""


def test_structured_document_passes_pdf_ua(tmp_path: Path, verapdf_exe: Path):
    target = tmp_path / "structured.pdf"
    render_html_to_pdf(STRUCTURED_HTML, target, title="Thermodynamics", lang="en")

    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary() + "\n" + "\n".join(
        f"  {r.clause}: {r.description}" for r in result.failed_rules
    )


def test_expected_structure_elements_are_present(tmp_path: Path):
    target = tmp_path / "structured.pdf"
    render_html_to_pdf(STRUCTURED_HTML, target, title="Thermodynamics", lang="en")

    types = structure_element_types(target)

    for expected in {"H1", "H2", "P", "L", "LI", "Table", "TR", "TH", "TD", "Figure"}:
        assert expected in types, f"{expected} missing from structure tree; found {sorted(types)}"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_render_structure.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'rebind.inspect'`.

- [ ] **Step 3: Write the implementation**

`src/rebind/inspect.py`:
```python
"""Read-only inspection of a PDF's structure tree, for tests and reporting."""

from __future__ import annotations

from pathlib import Path

import pikepdf


def structure_element_types(pdf_path: Path) -> set[str]:
    """Return every structure-element type name present in the document's tag tree."""
    found: set[str] = set()
    with pikepdf.open(pdf_path) as pdf:
        root = pdf.Root.get("/StructTreeRoot")
        if root is None:
            return found
        _walk(root.get("/K"), found)
    return found


def _walk(node, found: set[str]) -> None:
    if node is None:
        return
    if isinstance(node, pikepdf.Array):
        for child in node:
            _walk(child, found)
        return
    if isinstance(node, pikepdf.Dictionary):
        struct_type = node.get("/S")
        if struct_type is not None:
            found.add(str(struct_type).lstrip("/"))
        _walk(node.get("/K"), found)
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/test_render_structure.py -v
```
Expected: `2 passed`.

**If element names differ** (some generators emit `/TOCI`, `/Sect`, or role-mapped custom names), print
the actual set and adjust the expectation to the real names — but only after confirming the role map
maps them to the standard types. A custom tag with no role map entry is a genuine PDF/UA failure and
task 4's first test will catch it.

- [ ] **Step 5: Commit**

```bash
git add src/rebind/inspect.py tests/test_render_structure.py
git commit -m "Verify headings, lists, tables, and figures tag correctly"
git push origin main
```

---

### Task 5: Original page labels

Citation depends on this. A reconstructed document whose page 12 was the source's page 47 must present
itself as page 47.

**Files:**
- Create: `src/rebind/pagelabels.py`
- Create: `tests/test_pagelabels.py`
- Modify: `src/rebind/inspect.py` (add `page_labels`)

**Interfaces:**
- Consumes: `render_html_to_pdf` (Task 3), `validate_pdf_ua` (Task 2).
- Produces:
  - `set_page_labels(pdf_path: Path, labels: list[str]) -> None` — writes a `/PageLabels` number tree.
  - `page_labels(pdf_path: Path) -> list[str]` in `inspect.py` — reads them back.

- [ ] **Step 1: Write the failing test**

`tests/test_pagelabels.py`:
```python
from pathlib import Path

from rebind.inspect import page_labels
from rebind.pagelabels import set_page_labels
from rebind.render import render_html_to_pdf
from rebind.validate import validate_pdf_ua

TWO_PAGE_HTML = """
<h1>Front matter</h1>
<p>First page content.</p>
<p style="break-before: page;">Second page content.</p>
"""


def test_page_labels_round_trip(tmp_path: Path):
    target = tmp_path / "labelled.pdf"
    render_html_to_pdf(TWO_PAGE_HTML, target, title="Labelled", lang="en")

    set_page_labels(target, ["47", "48"])

    assert page_labels(target) == ["47", "48"]


def test_page_labels_do_not_break_conformance(tmp_path: Path, verapdf_exe: Path):
    target = tmp_path / "labelled.pdf"
    render_html_to_pdf(TWO_PAGE_HTML, target, title="Labelled", lang="en")
    set_page_labels(target, ["47", "48"])

    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()


def test_roman_and_arabic_labels_are_both_supported(tmp_path: Path):
    """Front matter is numbered i, ii; the body restarts at 1."""
    target = tmp_path / "mixed.pdf"
    render_html_to_pdf(TWO_PAGE_HTML, target, title="Mixed", lang="en")

    set_page_labels(target, ["ix", "1"])

    assert page_labels(target) == ["ix", "1"]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_pagelabels.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'rebind.pagelabels'`.

- [ ] **Step 3: Write the implementation**

`src/rebind/pagelabels.py`:
```python
"""Write original source pagination into the reconstructed PDF.

Rebind reflows the document, so output page N rarely equals source page N. Page labels keep
citation working: the viewer's page field shows the source's number. See design spec 5.3.

Every label is written as an explicit prefix with no numeric style. This is verbose but exact,
and it handles arbitrary source pagination (roman numerals, plate numbers, "A-17") without
Rebind having to infer a numbering scheme it cannot know.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf


def set_page_labels(pdf_path: Path, labels: list[str]) -> None:
    """Replace the document's page labels. One label per page, in order."""
    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        if len(labels) != len(pdf.pages):
            raise ValueError(
                f"got {len(labels)} labels for {len(pdf.pages)} pages; they must correspond"
            )

        nums = pikepdf.Array()
        for index, label in enumerate(labels):
            nums.append(index)
            nums.append(pdf.make_indirect(pikepdf.Dictionary(P=pikepdf.String(label))))

        pdf.Root["/PageLabels"] = pdf.make_indirect(pikepdf.Dictionary(Nums=nums))
        pdf.save()
```

Append to `src/rebind/inspect.py`:
```python
def page_labels(pdf_path: Path) -> list[str]:
    """Read back the per-page labels written by rebind.pagelabels.set_page_labels."""
    with pikepdf.open(pdf_path) as pdf:
        tree = pdf.Root.get("/PageLabels")
        if tree is None:
            return []
        nums = tree.get("/Nums")
        if nums is None:
            return []
        labels: list[str] = []
        for i in range(1, len(nums), 2):
            entry = nums[i]
            prefix = entry.get("/P")
            labels.append(str(prefix) if prefix is not None else "")
        return labels
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/test_pagelabels.py -v
```
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/rebind/pagelabels.py src/rebind/inspect.py tests/test_pagelabels.py
git commit -m "Preserve original pagination as PDF page labels"
git push origin main
```

---

### Task 6: Mathematics — expected to be the hardest, allowed to fail

Mathematics is first-class in the spec, and this task determines *how* it must be implemented. A
failure here is a legitimate Phase 0 result, not a blocker: it selects the fallback and defers the work
to Phase 3.

**Files:**
- Create: `tests/test_render_math.py`
- Create: `docs/decisions/0001-math-representation.md`

**Interfaces:**
- Consumes: `render_html_to_pdf` (Task 3), `structure_element_types` (Task 4),
  `validate_pdf_ua` (Task 2).
- Produces: a decision record only. No new runtime interface.

- [ ] **Step 1: Write the test for the preferred outcome**

`tests/test_render_math.py`:
```python
from pathlib import Path

import pytest

from rebind.inspect import structure_element_types
from rebind.render import render_html_to_pdf
from rebind.validate import validate_pdf_ua

MATHML_HTML = """
<p>The quadratic formula is given below.</p>
<math xmlns="http://www.w3.org/1998/Math/MathML" alttext="x equals negative b plus or minus
the square root of b squared minus four a c, all over two a">
  <mrow><mi>x</mi><mo>=</mo>
    <mfrac>
      <mrow><mo>-</mo><mi>b</mi><mo>&#177;</mo>
        <msqrt><mrow><msup><mi>b</mi><mn>2</mn></msup><mo>-</mo>
        <mn>4</mn><mi>a</mi><mi>c</mi></mrow></msqrt></mrow>
      <mrow><mn>2</mn><mi>a</mi></mrow>
    </mfrac>
  </mrow>
</math>
"""

SVG_FALLBACK_HTML = """
<p>The quadratic formula is given below.</p>
<figure role="math" aria-label="x equals negative b plus or minus the square root of
b squared minus four a c, all over two a">
  <img src="data:image/gif;base64,R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw=="
       alt="x equals negative b plus or minus the square root of b squared minus four a c,
       all over two a" width="200" height="60">
</figure>
"""


def test_mathml_produces_a_formula_element(tmp_path: Path, verapdf_exe: Path):
    """Preferred outcome: native MathML tagged as Formula. May legitimately fail."""
    target = tmp_path / "math.pdf"
    render_html_to_pdf(MATHML_HTML, target, title="Quadratic", lang="en")

    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()
    assert "Formula" in structure_element_types(target)


def test_svg_fallback_is_conformant(tmp_path: Path, verapdf_exe: Path):
    """Fallback outcome: equation as an image with a spoken-form alt text. Must pass."""
    target = tmp_path / "math_fallback.pdf"
    render_html_to_pdf(SVG_FALLBACK_HTML, target, title="Quadratic", lang="en")

    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()
```

- [ ] **Step 2: Run both tests and record which pass**

```bash
uv run pytest tests/test_render_math.py -v
```

Expected: uncertain. WeasyPrint's MathML support is limited, so
`test_mathml_produces_a_formula_element` may fail. `test_svg_fallback_is_conformant` **must** pass — if
even the fallback cannot be made conformant, escalate before continuing.

- [ ] **Step 3: If MathML failed, mark it xfail with a reason**

Change the decorator on the failing test rather than deleting it, so a future library upgrade surfaces
the improvement:

```python
@pytest.mark.xfail(reason="WeasyPrint does not tag native MathML as Formula; see ADR 0001")
def test_mathml_produces_a_formula_element(tmp_path: Path, verapdf_exe: Path):
```

- [ ] **Step 4: Write the decision record**

`docs/decisions/0001-math-representation.md`:
```markdown
# ADR 0001: How mathematics reaches the output PDF

**Date:** 2026-07-22
**Status:** Accepted

## Context

The design spec makes mathematics first-class: recognized to LaTeX, converted to MathML for
assistive technology, with a Speech Rule Engine string as the accessible description. This
task tested whether the renderer can carry native MathML into a tagged Formula element.

## Findings

[Record the actual outcome of Step 2 here — which tests passed, which failed, and the exact
veraPDF rule violations if any.]

## Decision

[One of:]
- **Native MathML.** The renderer tags MathML as Formula and validation passes. Phase 3
  converts LaTeX to MathML and emits it directly.
- **SVG with spoken alt text.** The renderer cannot tag MathML. Phase 3 renders each equation
  to SVG, tags it as a Formula (or Figure) with the Speech Rule Engine string as its alt text,
  and attaches MathML as an associated file for readers that can use it.

## Consequences

[What Phase 3 must now build, and what capability is lost.]
```

- [ ] **Step 5: Run the full suite and commit**

```bash
uv run pytest -v
git add tests/test_render_math.py docs/decisions/0001-math-representation.md
git commit -m "Determine how mathematics reaches the output PDF"
git push origin main
```

---

### Task 7: Windows packaging spike

**The second high-risk task.** Determines whether the whole approach can ship as a double-click
installer.

**Files:**
- Create: `src/rebind/app.py`
- Create: `packaging/rebind.spec`
- Create: `packaging/rebind.iss`
- Create: `tests/test_app.py`

**Interfaces:**
- Consumes: `render_html_to_pdf` (Task 3).
- Produces:
  - `create_app() -> FastAPI` — the local service.
  - `main() -> None` — console entry point that starts the server and opens a browser.

- [ ] **Step 1: Add the service dependencies**

Add to `pyproject.toml` under `[project] dependencies`:
```toml
    "fastapi>=0.115",
    "uvicorn>=0.30",
```
Add to `[project.optional-dependencies] dev`:
```toml
    "httpx>=0.27",
    "pyinstaller>=6.10",
```
Then:
```bash
uv sync --extra dev
```

- [ ] **Step 2: Write the failing test**

`tests/test_app.py`:
```python
from fastapi.testclient import TestClient

from rebind.app import create_app


def test_health_endpoint_reports_ready():
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reports_the_rendering_backend():
    """The installer's whole job is shipping a working renderer; the app must confirm it loaded."""
    client = TestClient(create_app())

    body = client.get("/health").json()

    assert body["renderer"] == "weasyprint"
    assert body["renderer_available"] is True
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
uv run pytest tests/test_app.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'rebind.app'`.

- [ ] **Step 4: Write the implementation**

`src/rebind/app.py`:
```python
"""The local Rebind service.

Rebind runs as a local web service driven from a browser tab — the OpenRefine pattern. This
avoids bundling a native GUI toolkit and gives librarians a familiar interface.
"""

from __future__ import annotations

import threading
import webbrowser

from fastapi import FastAPI

HOST = "127.0.0.1"
PORT = 8756


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
```

Add to `pyproject.toml`:
```toml
[project.scripts]
rebind = "rebind.app:main"
```

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/test_app.py -v
```
Expected: `2 passed`.

- [ ] **Step 6: Build a one-folder PyInstaller bundle**

`packaging/rebind.spec`:
```python
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for package in ("weasyprint", "pikepdf", "fastapi", "uvicorn"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

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
```

Build:
```bash
cd "C:/Users/thoma/Documents/rebind/packaging"
uv run pyinstaller rebind.spec --noconfirm
```
Expected: `dist/rebind/rebind.exe` exists.

- [ ] **Step 7: Verify the bundle runs without the project environment**

This is the actual test of the spike. Run the built exe from a directory outside the project, with no
virtualenv active:

```bash
cd "C:/Users/thoma/Documents"
"C:/Users/thoma/Documents/rebind/packaging/dist/rebind/rebind.exe"
```

Expected: a browser opens to `http://127.0.0.1:8756/health` showing
`{"status":"ok","renderer":"weasyprint","renderer_available":true}`.

**`renderer_available: false` means WeasyPrint's native libraries were not bundled** — that is the
critical finding. Record it, then try adding the missing DLLs to the spec's `binaries` list. If they
cannot be bundled, the renderer choice must change and Task 8 records that.

- [ ] **Step 8: Create the Inno Setup script**

`packaging/rebind.iss`:
```
[Setup]
AppName=Rebind
AppVersion=0.0.1
AppPublisher=Thomas Scharff
DefaultDirName={autopf}\Rebind
DefaultGroupName=Rebind
OutputBaseFilename=rebind-setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
DisableProgramGroupPage=yes

[Files]
Source: "dist\rebind\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Rebind"; Filename: "{app}\rebind.exe"
Name: "{autodesktop}\Rebind"; Filename: "{app}\rebind.exe"

[Run]
Filename: "{app}\rebind.exe"; Description: "Start Rebind"; Flags: nowait postinstall skipifsilent
```

Build it with Inno Setup (install from https://jrsoftware.org/isdl.php if absent):
```bash
"/c/Program Files (x86)/Inno Setup 6/ISCC.exe" "C:/Users/thoma/Documents/rebind/packaging/rebind.iss"
```
Expected: `packaging/Output/rebind-setup.exe` exists.

- [ ] **Step 9: Commit**

```bash
git add src/rebind/app.py packaging/rebind.spec packaging/rebind.iss tests/test_app.py pyproject.toml
git commit -m "Package the local service as a Windows installer"
git push origin main
```

Note: `.gitignore` already excludes `build/` and `dist/`, so build artifacts stay out of the repo.

---

### Task 8: Phase 0 findings and decision record

**Files:**
- Create: `docs/decisions/0002-phase-0-findings.md`
- Modify: `README.md` (status line)

**Interfaces:**
- Consumes: outcomes of all previous tasks.
- Produces: the go/no-go decision that governs Phase 1.

- [ ] **Step 1: Run the entire suite and capture results**

```bash
cd "C:/Users/thoma/Documents/rebind"
uv run pytest -v 2>&1 | tee docs/decisions/phase-0-test-output.txt
```
Expected: all tests pass or xfail. Any hard failure must be explained in the record.

- [ ] **Step 2: Write the findings record**

`docs/decisions/0002-phase-0-findings.md`:
```markdown
# ADR 0002: Phase 0 findings

**Date:** [date completed]
**Status:** Accepted

## Question

Can Rebind generate PDF/UA-conformant tagged PDFs using only libraries bundle-able into a
double-click Windows installer requiring no system Python?

## Results against the Phase 0 success criteria

1. Generated PDF passes veraPDF PDF/UA-1 with zero failed checks: [YES/NO + evidence]
2. Headings, list, table with header associations, figure with alt text all tag correctly:
   [YES/NO + which types were observed]
3. Original page labels survive: [YES/NO]
4. Runs from a PyInstaller build with no system Python: [YES/NO]
5. Decision record written: this document.

## Did WeasyPrint require a system-wide GTK install?

[YES/NO. This is the single most consequential finding for distribution. If YES, record
exactly which DLLs were needed and whether they could be vendored into the PyInstaller
bundle.]

## Decisions

- **Rendering library:** [WeasyPrint / alternative + why]
- **Packaging toolchain:** [PyInstaller + Inno Setup / alternative + why]
- **Mathematics:** [see ADR 0001]

## Consequences for Phase 1

[What Phase 1 can now assume. What it must work around.]
```

- [ ] **Step 3: Update the README status line**

Replace the status blockquote in `README.md`:
```markdown
> ⚠️ **Status: pre-alpha.** Phase 0 (feasibility spikes) complete — see
> [ADR 0002](docs/decisions/0002-phase-0-findings.md). Phase 1 (end-to-end pipeline spine) in progress.
```

- [ ] **Step 4: Commit**

```bash
git add docs/decisions/ README.md
git commit -m "Record Phase 0 findings and go/no-go decision"
git push origin main
```

---

## Self-review

**Spec coverage.** Phase 0 deliberately covers only §5.9 (render), §10 (distribution), and the parts of
§2 that generation must satisfy. The pipeline stages (§5.2), document model (§5.3), recognizers (§5.4),
alt-text derivation (§5.5), round-trip verification (§5.6), diff layer (§5.7), review workflow (§6), and
evaluation (§9) are explicitly out of Phase 0 scope and belong to Phases 1–5. The two spec risks marked
**High** that are testable now — PDF/UA generation bundling cost, and installer engineering — are Tasks
3/7. Reading-order quality and confidence calibration are not testable before a pipeline exists.

**Placeholder scan.** The bracketed sections in Tasks 6 and 8 are decision-record templates to be filled
with observed results, not unspecified implementation work. Every code step contains complete code.
`CONTRIBUTING.md`'s "development setup" section becomes fillable after Task 1 and should be updated
then.

**Type consistency.** `validate_pdf_ua` returns `ValidationResult` (Task 2), consumed with
`.compliant`, `.summary()`, and `.failed_rules` in Tasks 3–6. `structure_element_types` returns
`set[str]` (Task 4), used with `in` in Tasks 4 and 6. `set_page_labels`/`page_labels` are a matched
pair over `list[str]` (Task 5). `render_html_to_pdf(html, target, *, title, lang)` has one signature,
used identically in Tasks 3–6.

**Known risk not covered by a task.** veraPDF requires Java, which is present on the development
machine but cannot be assumed on a librarian's. Bundling a trimmed JRE, or making validation an
optional feature, is a Phase 6 packaging decision. Flagged here so it is not forgotten — it does not
block Phase 0, because validation is a development-time instrument before it is a user-facing feature.
