# Rebind — project instructions

Accessible PDF **remediation** for catastrophically bad library scans. Public repo:
`tfscharff/rebind` (MIT). Primary user is Allie, an ILL/accessibility librarian at Wheaton College;
secondary user is Thomas, who needs very long documents (a 300-page course catalog that broke Yuja
at its 999-structure-element limit).

## The thesis — do not lose this

**Rebind remediates the source PDF in place. It preserves the original page exactly and adds only
the accessibility it is missing.** (This reversed an original "reconstruct from scratch" thesis
Thomas rejected 2026-07-30: reconstruction reflowed a centered scanned title page into a
left-justified wall of text — it can never look like the original.)

Goal, in Thomas's words: *create a WCAG 2.1 AA accessible PDF from any uploaded PDF as quickly and
accurately as possible, looking as close to the original as possible, intervening only where
necessary.* No JSON is exposed to the user; the app fixes what it can and, for the one thing a
machine can't decide — figure descriptions — lets the user type them in-app, never a passive
homework list.

## Architecture (all live code)

Entry points: `cli.py` (`rebind convert`) and `app.py` (`rebind serve` / the frozen exe's local
browser app). Both drive **`remediate.remediate()`**, the whole pipeline:

- `extract.py` — pdfminer.six pulls born-digital text lines + image boxes per page.
- `ocr.py` + `restoration.py` — pages with no text layer are rasterized (pypdfium2), deskewed +
  denoised (OpenCV), and recognized (RapidOCR / onnxruntime, CPU, models bundled). Real per-line
  confidence; sub-threshold text becomes an honest placeholder.
- `profile.py` — document-global typographic profile (born-digital heading sizes).
- `layout.py` — recursive XY-cut for reading order; `detect_table_lines` flags grid regions.
- `remediate.py` — builds the tagged output: keeps each page's pixels/vector untouched, lays an
  *invisible* (render mode 3) tagged text layer over it, and builds the PDF/UA structure tree.
- `validate.py` — runs veraPDF and parses its report. `ui.py` — the inline browser app.

Per page: a born-digital page is kept **verbatim** (crisp vector); a page that already carries
marked content (a scan with a hidden OCR layer) is rebuilt from a 300-DPI render, because tagged
content cannot nest inside an `/Artifact`. Every case validates as **PDF/UA-1** (veraPDF, 0
failures) — there is a compliance test.

**What the tag tree contains** (`remediate.py`):
- Headings `/H1`–`/H6`, level-normalized (no skips — PDF/UA 7.4.2, in `_structure_roles`).
  Born-digital: from font size. **Scans/OCR: recovered geometrically** — `_ocr_heading_heights`
  promotes a line only when it is markedly taller than the body median AND set apart by whitespace
  AND not filling the column (levels from doc-global size tiers). Conservative: a missed heading
  stays a paragraph; a body-only scan invents none.
- Paragraphs `/P`; lists `/L`→`/LI`→`/LBody` (with a bare-marker-merge for renderers that box the
  bullet separately).
- Tables: fully tagged `/Table`→`/TR`→`/TD` (`_tagged_table`) — a regular grid, top row as `/TH`
  header cells scoped to their column, empty cells filling gaps. A sparse row (subtotal / empty
  cell) is kept, not dropped (`MAX_INTERNAL_TABLE_GAP`).
- Figures `/Figure` with `/Alt`. Decorative artifacts by default (compliant); the app shows each
  with a thumbnail and the user's typed description promotes it (`POST /jobs/{id}/describe`
  re-runs remediation with `alt_texts`).

## Invariants — reject changes that violate these

1. **Never fabricate.** Every text node traces to recognizer output with a confidence score. Below
   threshold it becomes an honest placeholder (`[text not recoverable from source scan, p. 214]`),
   never a plausible guess. Structural inference (heading/list/table) is conservative for the same
   reason — when in doubt, a paragraph.
2. **Everything has provenance.** Every node knows its source page and bounding box.
3. **Deterministic — scoped to the document model.** PDF bytes are NOT reproducible (ADR 0003).
   Never write byte-comparison tests against PDFs.
4. **No API key, no GPU, no network at runtime.** Libraries that need this tool cannot obtain API
   keys and do not know what one is. A hard product constraint, not a preference.
5. **No arbitrary limits** on structure elements, pages, or document size.
6. **Every dependency must be bundle-able on Windows.** A library needing a user-performed
   system-wide native install is disqualified regardless of merit.

## Environment

- **Python 3.12 via uv** — the machine's system Python is 3.14, which lacks wheels for parts of the
  CV/ML stack. Always `uv run ...`, never bare `python` or `pytest`.
- **veraPDF 1.30.2** at `C:\veraPDF\verapdf.bat` (needs Java; Java 23 is installed).
- **Inno Setup 6** at `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe` (per-user, no admin; not on
  `PATH` — invoke by full path).
- **WeasyPrint is a dev/test-only dependency** (it renders the synthetic born-digital PDFs the test
  fixtures need). It is NOT a runtime dependency and is excluded from the frozen bundle — Rebind
  never renders HTML. Do not reintroduce it into `remediate`/`app`/`cli`.

## Commands

```bash
uv run pytest              # default suite, fast (~1 min)
uv run pytest -m packaging # opt-in: rebuilds the frozen bundle and OCRs through the .exe (~1 min)
uv run ruff check .
```

Build the installer (after building the bundle):
```bash
uv run pyinstaller packaging/rebind.spec --noconfirm          # -> packaging/dist/rebind/
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" packaging\rebind.iss  # -> packaging/Output/
```
Thomas installs and tests builds himself; **don't run the installer.**

## Workflow

- **Always edit the local clone, commit, and push.** Never write to GitHub through `gh api`
  contents — it leaves the working copy behind and Thomas will not remember to pull.
- Commit and push after every change. Concise, imperative messages. Version with SemVer 2.0.0 and a
  `vX.Y.Z` tag per release (currently `0.2.0`; single source of truth is `rebind.__version__`).
- `samples/` is gitignored and must stay that way — copyrighted third-party scans in a public repo.
  Same for `*.pdf` and `.superpowers/`.
- Design docs: `docs/superpowers/specs/`; plans: `docs/superpowers/plans/`; decisions:
  `docs/decisions/`. Full progress ledger: `.superpowers/sdd/progress.md`.

## Gotchas (still live)

- **`sys.stdout` is `None` in the `console=False` frozen build** on every real launch (double-click,
  Start menu) — no inherited handle. Anything touching `sys.stdout` at import/config time crashes
  before the server starts; uvicorn's formatters do via `use_colors=None`, so `app.main` pins
  `use_colors=False`. **Never launch the frozen exe in a test with `stdout=subprocess.PIPE`** — a
  pipe is a valid handle, so the bug hides. Use `DETACHED_PROCESS` and read `rebind.log`.
- **Lazy imports inside `app.py` routes must be absolute** (`from rebind.ui import ...`), never
  relative — `app.py` is PyInstaller's `__main__`, so a relative import raises "attempted relative
  import with no known parent package" only in the frozen build (unfrozen tests pass).
- **veraPDF exits non-zero for legitimately non-compliant documents.** Never treat returncode alone
  as failure — check `jobEndStatus`. `rebind.validate` raises only for genuine tool failures.
- **PDF/UA table scope goes on `/TH` as `/A << /O /Table /Scope /Column >>`.** Rebind builds the
  struct tree itself, so it uses `/Scope` directly (unlike a WeasyPrint-rendered table, which would
  use `/Headers`+`/ID`).
- **cv2 (OpenCV) is a hard dependency of RapidOCR**, not just restoration — it cannot be removed.
  The bundle drops only OpenCV's FFmpeg videoio DLL (video decode is never used).
- **App icon:** Pillow's ICO writer ignores `append_images` — save from the 256px master with
  `sizes=[...]` (`packaging/make_icon.py`) or you get a 16px-only icon. Installer is unsigned by
  decision, so SmartScreen warns on first run (accepted).

## Skills

Follow the global workflow in `C:\Users\thoma\CLAUDE.md`: brainstorming before creative work,
writing-plans for multi-step tasks, TDD, systematic-debugging before proposing fixes, and
verification-before-completion before claiming anything works.
