# Rebind — project instructions

Accessible PDF reconstruction for catastrophically bad library scans. Public repo:
`tfscharff/rebind` (MIT). Primary user is Allie, an ILL/accessibility librarian at Wheaton College;
secondary user is Thomas, who needs very long documents (a 300-page course catalog that broke Yuja
at its 999-structure-element limit).

## The thesis — do not lose this

**Rebind remediates the source PDF in place. It preserves the original page exactly and adds only
the accessibility it is missing.** (This reverses the original "reconstruct from scratch" thesis,
which Thomas rejected 2026-07-30: reconstruction reflowed a centered scanned title page into a
left-justified wall of text — it can never look like the original. See `src/rebind/remediate.py`.)

The pipeline: render each page to an image (marked as an artifact — the picture), lay an
*invisible*, *tagged* OCR/existing-text layer over it (render mode 3), and build a PDF/UA structure
tree with reading order, language and title. The output looks like the input (a scan stays visually
identical) but validates as **PDF/UA-1** (veraPDF, 0 failures) — the standard behind WCAG 2.1 AA for
PDFs. Text comes from the page's own text layer where it has one, or from OCR where it does not, so
a document is never re-recognized unnecessarily.

Goal, in Thomas's words: *create a WCAG 2.1 AA accessible PDF from any uploaded PDF as quickly and
accurately as possible, looking as close to the original as possible, intervening only where
necessary.* No JSON is exposed to the user; the app fixes what it can and, for the one thing a
machine can't decide — figure descriptions — lets the user type them in-app, never a passive
homework list.

**What the tag tree now contains** (`remediate.py`): headings (`/H1`–`/H6`, level-normalized),
paragraphs (`/P`), lists (`/L`→`/LI`→`/LBody`, with a bare-marker-merge for renderers that box the
bullet separately), tables (fully tagged `/Table`→`/TR`→`/TD` via `layout.detect_table_lines`, with
the top row as header cells `/TH` scoped to their column and empty cells filling gaps so the grid is
regular — `_tagged_table`), and figures (`/Figure` with `/Alt`). Figures default to decorative artifacts (compliant); the app surfaces each
with a thumbnail and the user's description promotes it to a tagged `/Figure` (`/jobs/{id}/describe`
re-runs remediation with `alt_texts`). Born-digital pages are kept **verbatim** (crisp vector);
only a page that already carries marked content (a scan with a hidden OCR layer) is rebuilt from a
300-DPI render. Every case validates as PDF/UA-1 (veraPDF, 0 failures) — there's a compliance test.

**Reuse note:** heading/list/table detection reuses `profile`, `assemble._list_item_text`,
`assemble._is_ocr_over_scan` and `layout.detect_table_lines` — the analysis was always good; only
the render-from-scratch was wrong. The old reconstruction pipeline (`assemble`/`emit`/
`pipeline.convert` and their tests) still exists but is unused by the entry points — a later cleanup.

## Invariants — reject changes that violate these

1. **Never fabricate.** Every text node traces to recognizer output with a confidence score. Below
   threshold it becomes an honest placeholder (`[text not recoverable from source scan, p. 214]`),
   never a plausible guess.
2. **Everything has provenance.** Every node knows its source page and bounding box.
3. **Deterministic — scoped to the document model.** PDF bytes are NOT reproducible (see ADR 0003).
   Never write byte-comparison tests against PDFs.
4. **No API key, no GPU, no network at runtime.** Libraries that need this tool cannot obtain API
   keys and do not know what one is. This is a hard product constraint, not a preference.
5. **No arbitrary limits** on structure elements, pages, or document size.
6. **Every dependency must be bundle-able on Windows.** A library needing a user-performed
   system-wide native install is disqualified regardless of merit.

## Environment

- **Python 3.12 via uv** — the machine's system Python is 3.14, which lacks wheels for parts of the
  CV/ML stack. Always `uv run ...`, never bare `python` or `pytest`.
- **veraPDF 1.30.2** at `C:\veraPDF\verapdf.bat` (needs Java; Java 23 is installed).
- **GTK3 runtime** installed system-wide at `C:\Program Files\GTK3-Runtime Win64` — required for
  WeasyPrint in development. The frozen build vendors its own copy.
- **Inno Setup 6.7.3** installed **per-user** at `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe` —
  no admin rights needed, contrary to the earlier assumption. Installed via
  `winget install --id JRSoftware.InnoSetup -e --scope user --override "/VERYSILENT /CURRENTUSER"`.
  It is not on `PATH`; invoke it by full path.

## Commands

```bash
uv run pytest              # default suite, fast
uv run pytest -m packaging # opt-in: rebuilds the frozen bundle and renders through the .exe (~1 min)
uv run ruff check .
uv run python scripts/determinism_probe.py   # reproduces the nondeterminism finding
```

## Workflow

- **Always edit the local clone, commit, and push.** Never write to GitHub through `gh api`
  contents or any server-side edit — it leaves the working copy behind and Thomas will not
  remember to pull.
- Commit and push after every change. Concise, imperative commit messages.
- `samples/` is gitignored and must stay that way — those are copyrighted third-party scans in a
  public repo. Same for `*.pdf` and `.superpowers/`.
- Design docs live in `docs/superpowers/specs/`, plans in `docs/superpowers/plans/`, decision
  records in `docs/decisions/`.

## Gotchas learned the hard way in Phase 0

- **WeasyPrint tables use `/Headers` + `/ID`, never `/Scope`.** Table QA that checks `/Scope` will
  false-negative on every table Rebind renders.
- **WeasyPrint 69 has no MathML support at all.** `<math>` character data leaks out as flat
  single-baseline text and the √ glyph is missing from the font. Equations must be rendered as
  images with spoken-form alt text (ADR 0001).
- **Heading-level skips fail PDF/UA** at clause 7.4.2 — `h1`→`h3`, or a document starting at `h2`.
  `render._normalize_heading_levels` handles this; do not remove it.
- **`WEASYPRINT_DLL_DIRECTORIES` is silently ignored in frozen builds** (`ffi.py` guards it with
  `not hasattr(sys, 'frozen')`). We call `os.add_dll_directory` ourselves before importing
  weasyprint. Unreported upstream bug.
- **Font subsetting works; `libharfbuzz-subset-0.dll` being absent is a red herring.** WeasyPrint
  falls back to `Font._fonttools_subset`, and the bundled HarfBuzz is older than 4.1.0, so the
  HarfBuzz subsetter would be declined even if the DLL were supplied. Guarded by
  `tests/test_font_subsetting.py`. This also rules out the native-subsetter hypothesis in ADR 0003.
- **`sys.stdout` is `None` in the `console=False` frozen build** whenever the process is launched
  without an inherited handle — i.e. every real launch: double-click, Start menu, `Start-Process`.
  Anything touching `sys.stdout` at import or config time crashes before the server starts.
  uvicorn's formatters do exactly this via `use_colors=None`; `app.main` pins `use_colors=False`.
  **Never launch the frozen exe in a test with `stdout=subprocess.PIPE`** — a pipe is a valid
  handle, so `sys.stdout` is non-None and the test passes while every real launch fails. Use
  `DETACHED_PROCESS` and read `rebind.log` for diagnostics.
- **veraPDF exits non-zero for legitimately non-compliant documents.** Never treat returncode alone
  as failure — check `jobEndStatus`. `rebind.validate` raises `RuntimeError` only for genuine tool
  failures.
- **`inspect.py`'s ToUnicode text extractor is diagnostic/test-only.** When real text extraction is
  needed for born-digital PDFs, adopt `pdfminer.six` (MIT, pure Python, bundle-able) rather than
  hardening that regex parser.

## Where things stand

Phase 0 (feasibility spikes) is complete — see `docs/decisions/0002-phase-0-findings.md`. Tagged
PDF/UA generation works; the frozen bundle renders using vendored DLLs with nothing loading from
outside it. The installer itself has never been built.

**Phase 1's born-digital spine is complete.** `rebind convert input.pdf output.pdf` takes a
born-digital PDF through `extract` → `profile` → `layout` → `assemble` → `emit` → render → validate
(orchestrated by `pipeline`, driven by `cli`) and produces a tagged PDF/UA document that veraPDF
passes, plus `output.model.json`. Headings, paragraphs and lists are recovered from a
document-global typographic profile; running headers/footers/page numbers are detected and
excluded from reading order; source page labels are preserved. Images become `Placeholder` nodes,
not figures, so images are not reproduced in the output — there is no honest way yet to produce
the alt text PDF/UA requires. Pages with no text layer become placeholders and are reported; a
document with no text layer anywhere is refused as a scan. Tables, figures, formulae, chemistry,
music and footnote linking are not implemented.

**Phase 2 slice 1 — layout analysis and reading order — is complete.** `layout.py` runs recursive
XY-cut over the extracted line boxes: each page is segmented into columns and blocks and its text
emitted in correct reading order, replacing the naive top-to-bottom sort. The gutter detector is a
coverage-valley finder tuned against the real 1905 bulletin (tolerates a few straddling lines,
absolute-point gutter widths); a marginal gutter flags `multi-column-suspected`. Multi-column body
nodes carry `column-{n}` provenance.

**Phase 2 slice 2 — honest hidden-OCR-layer handling — is complete.** A page with a text layer AND
a page-covering raster image is detected as an OCR'd scan (`_is_ocr_over_scan` in `assemble.py`):
its text is flagged `ocr-source` and confidence-capped, the redundant background scan is not
emitted as a figure placeholder, and the CLI reports it. This only labels an OCR layer that is
*already present*.

**Phase 2 slice 3 — the OCR branch — is complete.** Pages with no text layer are recognized with
RapidOCR (onnxruntime, CPU, models bundled — ADR 0005); `pypdfium2` rasterizes the page. `ocr.py`
maps recognizer output to `TextLine`s (real per-line confidence), which flow through the unchanged
`profile`/`layout`/`assemble` interface. OCR runs once per page (cache shared across the pipeline's
two passes); a scanned document now converts instead of being refused. Sub-threshold recognition
becomes an honest placeholder. The engine bundles into the frozen build and runs offline (proven in
ADR 0005's spike).

**Phase 2 slice 4 — image restoration — is complete.** `restoration.py` (pure OpenCV, no new
dependency) deskews each scanned page (minAreaRect over an Otsu text mask) and applies a gentle 3×3
median denoise before OCR; `ocr_pages` calls it. Empirically RapidOCR reads moderately rotated text
unaided, so deskew's reliable benefit is *geometry* — a tilted line's axis-aligned box is inflated
by the tilt (52pt vs 18pt at 6°), which would scramble XY-cut reading order; deskew tightens it.
Full page **dewarp** (spine curvature) is deliberately deferred (needs a learned model or grid
estimator).

The `/ocr-smoke` endpoint + packaging test now prove the *shipping* frozen bundle OCRs (not just a
standalone probe). The license inventory (`scripts/license_inventory.py`) now covers all 43 bundled
Python distributions + the PP-OCR models, not only the GTK DLLs; `--check` fails if any bundled
runtime distribution lacks a license text.

**Phase 2 slice 5 — table detection — is complete.** `layout.detect_table_lines` flags grid-shaped
regions `table-suspected` (the cells' reading order may be wrong) without reconstructing them —
detection uses a row fill-fraction gate (table rows are sparse ~0.67, flowing multi-column rows
dense ~0.93) plus a regularity requirement (≥3 rows each spanning ≥3 shared columns), the
combination that separates a real table from dense multi-column text. Validated on real samples
(Failure.pdf's Table 7.5, the bulletin's roster) with no false positives on flowing columns.

**OCR heading recovery — done (the size+isolation+short-line signal).** A single OCR line's "size"
is a noisy box-height crop (on Failure.pdf a body line OCR'd to 40pt while the real heading was
36pt), so no single signal is trusted. `remediate._ocr_heading_heights` recovers a heading only when
its line is *markedly taller* than the page's body median **and** *set apart by whitespace* **and**
*does not fill the column* — the conjunction an over-tall body line (still inside its full-width
paragraph) cannot produce. Levels come from document-global size tiers (`_height_tiers`), then the
usual no-skip normalization. Conservative by design: a missed heading stays an honest paragraph, and
a body-only scan invents none (the old pernambuco/Failure.pdf fabrication is guarded by a test). The
earlier blanket suppression in `assemble` (which flattened all OCR lines to paragraphs) is
superseded for the remediation path.

Measured facts to avoid re-chasing: **OCR is ~4s/page once warm**, not 13s — the 13s was the
one-time RapidOCR model load, amortized across the run since the engine is cached. OCR speed is not
a bottleneck worth optimizing. Downscaling the page image below the 200-DPI render does not speed
recognition and is not worth the accuracy risk.

**The 5 samples no longer ground further structural slices** (none have `/PageLabels`, detected
footer page numbers, real captioned figures, warping, or a clean reconstructable table). Remaining
candidates each need either more representative samples or a product decision: table *reconstruction*
(rebuild the grid into a tagged `<table>` — fabrication risk on messy real tables), figure/caption
association (samples lack real captioned figures), full **dewarp** for spine-curved scans (needs a
learned model or grid estimator — a dependency/approach decision for Thomas), printed page-label
extraction (needs docs that print page numbers in detectable footers). Known minor gap: OCR
fragments beginning with `-` or `N.` can form spurious single-item lists (low harm; a ≥2-item rule
would risk born-digital regressions).

**The browser UI and the installer are built.** `rebind serve` (and the frozen exe on double-click)
now open a real local app at `/` — drop a PDF, convert, download the tagged PDF + model, and see a
review queue of what needs a human's eye (the "know what you don't know" signature). The UI is
inline HTML/CSS/JS in `src/rebind/ui.py` (no static files, no new dependency; upload is a raw
request body, no python-multipart). `build_review` groups node flags into a librarian-facing
condition report. The packaging test now asserts the frozen exe serves `/`. **Gotcha:** lazy imports
inside `app.py` routes must be **absolute** (`from rebind.ui import ...`), never relative — app.py
is PyInstaller's `__main__` entry, so a relative import raises "attempted relative import with no
known parent package" only in the frozen build (unfrozen tests pass). The **Inno Setup installer
builds**: `& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" packaging\rebind.iss` →
`packaging\Output\rebind-setup.exe` (~109MB, LZMA2 from the 306MB bundle). Build the frozen bundle
first (`uv run pytest -m packaging` or `uv run pyinstaller packaging/rebind.spec`). Thomas installs
and tests builds himself; don't run the installer.

**App icon.** `packaging/make_icon.py` generates `packaging/rebind.ico` (16–256px) and the browser
favicon reproducibly (a bound-book mark in buckram teal). It is wired into the frozen exe
(`icon="rebind.ico"` in `rebind.spec`, so shortcuts and the pinned taskbar item show it) and inlined
as a data-URI favicon in `ui.py`. **Gotcha:** Pillow's ICO writer ignores `append_images` — save
from the 256px master with `sizes=[...]` or you get a 16px-only icon. `rebind.ico` is committed as a
source asset. Rerun `make_icon.py` and rebuild the exe if the mark changes. **Signing:** decided
against — the installer is unsigned, so Windows SmartScreen warns on first run (accepted).

**Recognizer output gets minimal structure inference (assemble.py).** Text that is OCR output —
Rebind's own OCR (`ocr_confidence` set) *or* a hidden OCR layer over a page-covering scan
(`page_covered_by_scan`, `ocr_confidence` is None) — has corrupted structure signals, so: heading
inference is suppressed (font-size box-height is noise) and a single-item list falls back to a
paragraph (stray `-`/`+`/mis-read markers). This fixed the pernambuco thesis, a hidden-OCR scan that
had come back as 50 fabricated headings + 28 junk lists. Born-digital docs (real font sizes, real
markers) are unaffected. Recovering *real* headings from a scan (size + isolation + short-line
signal) is still a later slice.

Full progress ledger, including every deferred finding: `.superpowers/sdd/progress.md`.

## Skills

Follow the global workflow in `C:\Users\thoma\CLAUDE.md`: brainstorming before creative work,
writing-plans for multi-step tasks, TDD, systematic-debugging before proposing fixes, and
verification-before-completion before claiming anything works.
