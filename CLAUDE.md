# Rebind — project instructions

Accessible PDF reconstruction for catastrophically bad library scans. Public repo:
`tfscharff/rebind` (MIT). Primary user is Allie, an ILL/accessibility librarian at Wheaton College;
secondary user is Thomas, who needs very long documents (a 300-page course catalog that broke Yuja
at its 999-structure-element limit).

## The thesis — do not lose this

Rebind does **not** remediate the source PDF. It treats the scan as *evidence* and generates a new
born-accessible document: dewarp → OCR → layout → semantic document model → tagged PDF/UA. Because
the output is generated rather than patched, most of WCAG 2.1 AA is satisfied by construction —
including 1.4.5 Images of Text, which no facsimile approach can escape.

The PDF is a **build artifact**. The document model is the source of truth. Human corrections are a
diff layer over the model, so reprocessing with an improved pipeline never discards human work.

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

Follow-ups still open: OCR speed (~13s/page CPU), re-OCR of poor existing OCR layers, and full
dewarp. **Next major piece: full dewarp** for spine-curved book scans, or figure/caption handling.

Full progress ledger, including every deferred finding: `.superpowers/sdd/progress.md`.

## Skills

Follow the global workflow in `C:\Users\thoma\CLAUDE.md`: brainstorming before creative work,
writing-plans for multi-step tasks, TDD, systematic-debugging before proposing fixes, and
verification-before-completion before claiming anything works.
