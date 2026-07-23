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
- **Inno Setup is NOT installed**, and building the installer needs admin rights.

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

**Next: Phase 1 — the pipeline spine.** Ingest → text acquisition → minimal document model → tagged
PDF → veraPDF, end to end. Start with the born-digital branch: it has a real text layer, needs no
OCR or restoration, and therefore delivers a genuinely useful feature rather than scaffolding.

Full progress ledger, including every deferred finding: `.superpowers/sdd/progress.md`.

## Skills

Follow the global workflow in `C:\Users\thoma\CLAUDE.md`: brainstorming before creative work,
writing-plans for multi-step tasks, TDD, systematic-debugging before proposing fixes, and
verification-before-completion before claiming anything works.
