# ADR 0002: Phase 0 findings

**Date:** 2026-07-22
**Status:** Accepted

## Question

Can Rebind generate PDF/UA-conformant tagged PDFs using only libraries bundle-able into a
double-click Windows installer requiring no system Python?

## Results against the Phase 0 success criteria

1. **Generated PDF passes veraPDF PDF/UA-1 with zero failed checks: YES.** Task 3 rendered a
   minimal `<h1>`/`<p>` document with `WeasyPrint.HTML(...).write_pdf(target,
   pdf_variant="pdf/ua-1")` and it passed veraPDF 1.30.2 validation (`compliant=True,
   failed_rules=[]`) on the first attempt, no iteration required. This positive path is proven
   by a dedicated test (`test_conformant_pdf_is_compliant`), not merely assumed from the
   validator wrapper's negative-path tests.

2. **Headings, list, table with header associations, figure with alt text all tag correctly:
   YES.** Task 4 verified structure tags `H1 H2 P L LI Table TR TH TD Figure` all appear, with
   no `/RoleMap` needed. Table header association was confirmed *semantically*, not just
   structurally: WeasyPrint links data cells to header cells via `/Headers` + `/ID` (**not**
   `/Scope`) — `'4.18' -> ['Water', 'c (J/g.K)']` and `'0.385' -> ['Copper', ...]` were traced
   correctly. Any future table QA tooling must resolve `/Headers`, or it will false-negative on
   every table Rebind renders.

   **Nuance carried from ADR 0001 (mathematics):** WeasyPrint's native MathML output also
   *passes* PDF/UA-1 validation and its glyphs are genuinely present in the content stream
   (verified via MCID/`ToUnicode` text extraction, not assumed) — but WeasyPrint tags the
   `<math>` subtree as generic `NonStruct`/`Span`, never as `Formula`, and does not surface the
   `alttext` attribute anywhere in the tag tree. This is **not** a PDF/UA failure; it is Rebind's
   own stricter semantic requirement (mathematics must be identifiable to assistive technology
   as mathematics) that native MathML does not meet. Do not restate this as "WeasyPrint fails
   PDF/UA on MathML" — it does not. See ADR 0001 for the full finding and the resulting decision
   (render equations as tagged `Figure` images with spoken-form alt text, MathML attached as an
   associated file).

3. **Original page labels survive: YES.** Task 5 proved arabic- and roman-numeral page label
   round-trip through `set_page_labels`/`page_labels`. A real bug was found and fixed in the
   process: `/Kids` hierarchical page-label trees (as opposed to a flat `/Nums` array) now raise
   rather than silently returning wrong data.

4. **Runs from a PyInstaller build with no system Python: PARTIALLY MET.** Two distinct claims
   need to be kept separate, and they are not equally proven:

   - **The frozen application itself: proven.** Task 7's PyInstaller one-folder build
     (`dist/rebind/`, 169 MB, ~53 MB of vendored GTK3 DLLs — all 80 files in the runtime's
     `bin\`, not a hand-picked subset) renders a real PDF, including an embedded raster image,
     using exclusively bundled DLLs. This was verified by direct inspection of the live
     process's loaded module table (`Get-Process rebind | Select -Expand Modules`), not
     inferred from success/failure alone, specifically because the dev machine also has a
     system-wide GTK3 install that could have masked a bundling failure. Every GTK-family
     module loaded from `dist\rebind\_internal\...`; zero modules resolved from
     `C:\Program Files\GTK3-Runtime Win64` after a real render (`Where-Object { $_.FileName
     -like '*Program Files*' }` returned no matches). An opt-in test
     (`uv run pytest -m packaging`) rebuilds the bundle and asserts this from scratch.
   - **The double-click installer: NOT proven.** `packaging/rebind.iss` (Inno Setup script) is
     written but was **never built or run**. Inno Setup is not installed on this machine, and
     the session has no Administrator rights and no interactive UAC path to install it or
     elevate. `packaging/Output/rebind-setup.exe` does not exist. The frozen application working
     is necessary evidence toward criterion 4 but is not the same claim as "a librarian can
     download and double-click an installer and end up with a working app" — that end-to-end
     path remains untested. This is recorded as open, not silently assumed to follow from the
     frozen-build result.

5. **Decision record written: this document, plus ADR 0001 (mathematics) and ADR 0003
   (determinism), both produced during Phase 0 and referenced throughout.**

## Did WeasyPrint require a system-wide GTK install?

**Yes, for development — but Task 7 proved this is a development-time need only, not a
distribution constraint.**

A plain `uv sync` with `weasyprint` installed does not work on Windows: WeasyPrint's `text/ffi.py`
loads Pango/cairo/GObject via cffi at import time and raises `OSError: cannot load library
'libgobject-2.0-0'` without native GTK3 shared libraries, which are not distributed as Python
wheels. Task 3 fixed this for development by installing the community `GTK-for-Windows-Runtime-
Environment-Installer` (tschoonj, 2022-01-04, ~49 MB, LGPL) to WeasyPrint's hardcoded default path
(`C:\Program Files\GTK3-Runtime Win64`). A key trap: WeasyPrint's `dlopen` call uses
`LOAD_LIBRARY_SEARCH_DEFAULT_DIRS`, which does **not** consult `PATH` at all — only the
application directory, `System32`, and `os.add_dll_directory`-registered paths. Adding the GTK
bin directory to `PATH` has no effect.

For **end users**, Task 7 proved this system-wide install is not required: the PyInstaller bundle
vendors all 80 DLLs from the GTK3 runtime's `bin\` directory directly into the frozen app and
resolves them without touching `Program Files` or any system-wide install, verified by live
module-table inspection (see criterion 4 above). So: yes for development, no for distribution —
the DLLs are vendored, not installed.

**A real upstream bug was found and worked around, not merely noted:** `WEASYPRINT_DLL_DIRECTORIES`
is silently ignored in frozen builds. WeasyPrint's `weasyprint/text/ffi.py` guards its own
DLL-directory bootstrap with `not hasattr(sys, 'frozen')`, so PyInstaller's bootloader setting
`sys.frozen` causes that entire code path — including reading the env var — to be skipped. The
brief's suggested fix (set the env var) would have silently done nothing in the frozen build. The
actual fix, implemented in `src/rebind/app.py::_bootstrap_bundled_dll_directory()`, is to call
`os.add_dll_directory()` directly in application code before `weasyprint` is imported anywhere in
the process (imports are cached, so this must happen ahead of the first import). This is a
candidate for an upstream bug report against WeasyPrint (not yet filed; tracked as follow-up
work, not part of this decision).

## Decisions

- **Rendering library: WeasyPrint 69.0.** Zero-iteration PDF/UA-1 compliance for a minimal
  document, generalizes correctly to headings/lists/tables/figures with real header
  associations, and its known gaps (MathML tagging, DLL bootstrap under freezing) both have
  proven, scoped workarounds rather than being blockers.
- **Packaging toolchain: PyInstaller (proven) + Inno Setup (unproven).** PyInstaller one-folder
  freezing with vendored GTK3 DLLs is proven to produce a working, self-contained renderer.
  Inno Setup is the intended installer wrapper (`packaging/rebind.iss` is written) but building
  and running it has not happened in this environment. Phase 1 should not assume the installer
  step is a formality — it is the one criterion-4 sub-claim still open.
- **Mathematics: per ADR 0001.** Render each recognized equation to an image, tag as `Figure`
  with spoken-form alt text as the accessible description; attach MathML as an associated file
  for tooling that can consume it directly. Native MathML is not the accessible-structure path,
  even though it renders and validates.
- **Determinism: per ADR 0003 (retraction, not narrowing).** Rebind originally claimed
  byte-reproducible PDF output. That claim is **false at every granularity tested**, including
  within a single process: repeated measurement found same-process builds of identical input
  diverging inside an embedded font's compressed `FlateDecode` stream in roughly 1 in 7 to 1 in
  12 paired builds (13.3% in one 15-run isolated-test measurement), with the divergence point
  consistent across all reproductions (`Length1` — the uncompressed glyph program size — always
  identical; only the compressed bytes that follow it vary). Pinning `PYTHONHASHSEED` identically
  across 8 separate processes did **not** produce byte-identical output either, ruling out simple
  Python string-hash randomization as a complete explanation. The root cause is unresolved between
  two live hypotheses (ASLR-influenced object-identity hashing inside WeasyPrint's native font
  stack, most likely HarfBuzz's subsetter; or an unrelated small-state decision elsewhere in
  subsetting) and is not part of Rebind's own code — `render.py` calls into WeasyPrint and does
  not construct the responsible container itself. Rebind's determinism guarantee is now scoped
  to the **document model** (structure, tagging, content, and the metadata `reproducible.py`
  pins), not to PDF bytes. `scripts/determinism_probe.py` reproduces the finding independently
  (N=20 → 20 distinct SHA-256 hashes). Upstream reporting to the WeasyPrint project is tracked as
  follow-up work, not yet filed.

## Consequences for Phase 1

**What Phase 1 can assume:**
- WeasyPrint reliably produces PDF/UA-1-conformant output for headings, paragraphs, lists,
  tables (with real `/Headers`-based associations), and figures with alt text, and preserves
  page labels through `pagelabels.py`.
- A frozen, no-system-Python renderer is achievable and already exists in this repo
  (`dist/rebind/`, guarded by the opt-in `packaging` pytest marker) — Phase 1 can build the
  pipeline on top of `render_html_to_pdf` without re-litigating whether bundling works.
- The `WEASYPRINT_DLL_DIRECTORIES`-under-freeze trap and its `os.add_dll_directory` workaround
  are documented and already implemented in `app.py`; future entry points must preserve or
  replicate this bootstrap (see open item below).

**What Phase 1 must work around:**
- Mathematics must be planned as an image-plus-alt-text pipeline (LaTeX → image, LaTeX →
  spoken-form string), per ADR 0001, not as native MathML passed through to structure.
- Golden-file / regression testing must compare the **document model** (tag tree, extracted
  text, page structure, or an equivalent normalized representation) and must **never** compare
  raw PDF bytes, per ADR 0003 — byte comparison will fail intermittently even for two builds in
  the same process, for reasons outside Rebind's own code.
- The double-click installer (`rebind-setup.exe`) still needs to be built and exercised on a
  clean machine before criterion 4 can be called fully met. This requires either Administrator
  rights and Inno Setup installed in a future session, or a CI/build-machine environment that has
  both.
- veraPDF requires a JRE, present on this dev machine but not guaranteed on a librarian's; whether
  to bundle a trimmed JRE or make validation an optional dev-time-only feature is an open Phase 6
  packaging decision (noted in Task 8's brief self-review, not resolved here).

## Known open items deferred during Phase 0 (carried forward, not dropped)

These were deliberately deferred rather than fixed during the phase, per each task's own
"deferred for final review" notes in `progress.md`. Recorded here with enough context that a
reader months later understands each one without re-reading every task report:

- **Per-DLL license inventory not done.** The 80 vendored GTK3-runtime DLLs were labelled
  collectively "GTK3 (LGPL)" for Phase 0's purposes, but the runtime bundles components under
  several different licenses: HarfBuzz (MIT), FreeType (FTL), libpng, zlib, expat, PCRE2, and
  Cairo (LGPL/MPL), in addition to the LGPL GTK/GLib/Pango core. The Phase 0 plan already defers
  bundling-obligation work to pre-release, but a per-file license inventory (and the
  corresponding license-text bundling / attribution the installer must ship) needs doing before
  any public release, not just before "Phase 6."
- **Missing transitive dependencies of `libtiff-5.dll`.** The PyInstaller build warns that
  `liblzo2-2.dll`, `libdeflate.dll`, `libwebp-7.dll`, `libjbig-0.dll`, and `libLerc.dll` are not
  found. These are absent from the source GTK3-Runtime-Win64 distribution itself, not dropped by
  Rebind's spec. Confirmed benign for the current PNG-only rendering path (Task 7's
  render-smoke test exercises an embedded PNG successfully). TIFF is a plausible library-scan
  input format; if TIFF handling is ever added, these dependencies will need sourcing separately
  and this warning revisited.
- **The `test_two_runs_produce_identical_bytes` xfail is noise, not signal.** It is a non-strict
  xfail that XPASSes roughly 85-90% of runs (matching the same-process divergence rate found in
  ADR 0003), so a bare pass/fail read of this one test would never reliably alert anyone to an
  upstream fix. A better long-term test (not yet written) would assert that N repeated builds
  yield more than one distinct hash — a characterization test that fails loudly (signals
  regression-of-the-fix) only if upstream actually resolves the nondeterminism, rather than
  flipping noisily run to run as this one does.
- **`app.py`'s DLL bootstrap is entry-point-fragile.** `_bootstrap_bundled_dll_directory()`'s
  fix for the frozen `WEASYPRINT_DLL_DIRECTORIES` bug (see above) works only because `app.py` is
  currently the sole Analysis entry script, and its module-level code is guaranteed to run before
  anything else in the frozen process gets a chance to `import weasyprint`. A future second entry
  point (e.g. a CLI script, a worker process) that imports `weasyprint` directly, without first
  importing `app.py`, would silently defeat this and reintroduce the frozen-DLL failure with no
  obvious error message pointing at the cause. If/when Phase 1 or later adds another entry point,
  this bootstrap needs to move somewhere both entry points share, or be duplicated deliberately.
- **`reproducible.py`'s `/ID[1]` assignment is redundant but harmless.** qpdf always overwrites
  the trailer's second `/ID` element with a content-derived value on save, so the value
  `reproducible.py` assigns to it is discarded. The code comment was corrected to say this
  accurately (Task 5), but the redundant assignment itself was left in place rather than removed.
- **No `.gitattributes`.** CRLF/LF warnings recur on every `git add` on this Windows checkout
  (first flagged in Task 1's review). Cosmetic, but worth fixing before more contributors join.
- **`validate.py`'s alternate-JSON-schema branch is under-tested.** It is exercised only against
  an authored fixture, never a real veraPDF sample that happens to emit that schema shape
  (flagged in Task 2's review). The code comment noting this assumption was added; the branch
  itself was not further hardened.
- **`inspect.py`'s hand-rolled `/ToUnicode` CMap text extractor is diagnostic/test-only.** It was
  explicitly marked as such (Task 4) and its surrogate-pair `bfrange` handling is unverified; it
  also does not decode the `±` glyph cleanly in ADR 0001's math test (comes back as `U+FFFD`,
  believed to be a limitation of this extractor rather than evidence of a rendering gap, but
  unverified). When born-digital text extraction becomes a real pipeline need (Phase 1+), adopt
  `pdfminer.six` (MIT, pure Python, bundle-able) rather than hardening this regex-based parser.
- **Root causes not localized, by design of a time-boxed spike:** neither ADR 0001 (why WeasyPrint
  tags MathML generically instead of as `Formula` — HTML5 foreign-content parsing vs. the tagging
  stage was never distinguished) nor ADR 0003 (which of the two live hypotheses explains the
  font-subsetting nondeterminism) was diagnosed to root cause. Both explicitly say so. Anyone
  picking this up for an upstream report should expect to do that diagnostic work themselves.
- **Several fix commits were accepted without a dedicated re-review round** (Task 5's final
  commit `2e051e8`, Task 6's fix commit `999b934`, Task 7's fix commit `4a3df7e`) — each was
  reviewed as part of a later task's overall review rather than in isolation. Flagged for
  whole-branch review rather than silently assumed clean.

## Full test output

Captured in `docs/decisions/phase-0-test-output.txt`
(`uv run pytest -v`): **26 passed, 1 deselected, 3 xfailed, 1 xpassed** (the deselected test is
the opt-in `packaging`-marked frozen-build test; the 3 xfailed / 1 xpassed split among the three
`test_reproducible.py` nondeterminism tests is itself expected to vary run to run — see ADR 0003
and the noise finding above — and is not a regression signal on its own).
