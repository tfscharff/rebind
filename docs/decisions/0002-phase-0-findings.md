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
   PARTIALLY MET, corrected by final whole-branch review.** Task 4 verified structure tags
   `H1 H2 P L LI Table TR TH TD Figure` all appear, with no `/RoleMap` needed, for a
   *well-formed* heading sequence (H1 then H2). Table header association was confirmed
   *semantically*, not just structurally: WeasyPrint links data cells to header cells via
   `/Headers` + `/ID` (**not** `/Scope`) — `'4.18' -> ['Water', 'c (J/g.K)']` and
   `'0.385' -> ['Copper', ...]` were traced correctly. Any future table QA tooling must resolve
   `/Headers`, or it will false-negative on every table Rebind renders.

   **Correction: the unqualified "headings tag correctly: YES" from Task 4 did not test heading
   *level sequencing*, and that gap fails outright.** Final whole-branch review verified
   directly (`tests/test_heading_levels.py`) that a document with `<h1>` then `<h3>` (skipping
   `<h2>`), and a document using only `<h2>` with no `<h1>` at all, both **fail** veraPDF
   PDF/UA-1 at clause 7.4.2 (heading tags must start at H1 and never skip an intervening
   level). This matters far more than an edge case: Rebind reconstructs scans that routinely
   start mid-chapter or yield irregular recognized heading levels, so an irregular heading
   sequence is a realistic, likely-common Phase 1 input, not a hypothetical one.

   **Fixed, not merely documented.** `rebind.render.render_html_to_pdf` now normalizes heading
   levels before rendering (`_normalize_heading_levels`): headings are renumbered so the first
   heading in a document always becomes H1 and the sequence never skips a level, while
   preserving the source's actual relative nesting (a document with H1/H3/H2/H3 normalizes to
   H1/H2/H2/H3, not to some other collapsed sequence). A document that genuinely starts at H2
   is deliberately renumbered to start at H1 in the output — this is the correct accessible
   representation of "this document's own top-level heading", not a loss of information, and
   is itself asserted by a dedicated test
   (`test_normalize_heading_levels_shifts_a_document_that_starts_at_h2_down_to_h1`). Both
   previously-failing shapes now pass PDF/UA-1 end to end
   (`test_render_html_to_pdf_normalizes_a_heading_skip_to_pdf_ua_compliance` and
   `test_render_html_to_pdf_normalizes_a_document_starting_at_h2`).

   **Nuance carried from ADR 0001 (mathematics), corrected by final whole-branch review:**
   WeasyPrint 69.0 has **no MathML support at all** — it renders the equation's descendant
   characters as flat inline text on a single baseline (no fraction bar, radical, or
   superscript) and drops at least one glyph (√) from the embedded font entirely, in addition
   to tagging the `<math>` subtree as generic `NonStruct`/`Span` and never surfacing the
   `alttext` attribute anywhere in the tag tree. The document still *passes* PDF/UA-1
   validation — this is **not** a PDF/UA failure; it is Rebind's own stricter semantic
   requirement (mathematics must be identifiable to, and correctly represented for, assistive
   technology) that native MathML does not meet, and meets even less well than an earlier
   version of this document recorded. Do not restate this as "WeasyPrint fails PDF/UA on
   MathML" — it does not. See ADR 0001 for the full finding and the resulting decision (render
   equations as tagged `Figure` images with spoken-form alt text, MathML attached as an
   associated file) — a decision this stronger finding supports even more clearly than before.

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
- A frozen, no-system-Python renderer is achievable — proven by the opt-in `packaging`-marked
  test (`uv run pytest -m packaging`), which builds `packaging/dist/rebind/` from
  `packaging/rebind.spec` and exercises the running executable. **Correction:** an earlier
  version of this document said the frozen bundle "already exists in this repo
  (`dist/rebind/`)". `dist/` is gitignored build output, not a committed artifact — someone
  cloning the repository will not find it there; it must be built locally by running the
  `packaging` test (or `pyinstaller packaging/rebind.spec` directly). Phase 1 can build the
  pipeline on top of `render_html_to_pdf` without re-litigating *whether* bundling works, but
  must still run the build itself, not expect a pre-built bundle in the checkout.
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
- **RESOLVED by final whole-branch review: the `test_two_runs_produce_identical_bytes` xfail was
  noise, not signal.** It was a non-strict xfail that XPASSed roughly 85-90% of runs (matching
  the same-process divergence rate found in ADR 0003), so a bare pass/fail read of that one test
  would never reliably alert anyone to an upstream fix. Replaced with
  `test_repeated_builds_still_exhibit_the_known_font_subsetting_nondeterminism`
  (`tests/test_reproducible.py`), a stable characterization test asserting N repeated builds
  yield more than one distinct SHA-256 hash — this fails loudly only if upstream actually
  resolves the nondeterminism, rather than flipping noisily run to run as the old test did.
- **RESOLVED by final whole-branch review: `app.py`'s DLL bootstrap was entry-point-fragile.**
  `_bootstrap_bundled_dll_directory()`'s fix for the frozen `WEASYPRINT_DLL_DIRECTORIES` bug
  (see above) worked only because `app.py` was the sole Analysis entry script. Moved to
  `rebind/__init__.py` (which every entry point importing anything under the `rebind` package
  runs first), with `app.py` now containing an explicit `import rebind` so the frozen
  PyInstaller entry point -- which executes `app.py` directly as `__main__`, not via
  `import rebind.app` -- still triggers it.
- **`reproducible.py`'s `/ID[1]` assignment is redundant but harmless.** qpdf always overwrites
  the trailer's second `/ID` element with a content-derived value on save, so the value
  `reproducible.py` assigns to it is discarded. The code comment was corrected to say this
  accurately (Task 5), but the redundant assignment itself was left in place rather than removed.
- **RESOLVED by final whole-branch review: no `.gitattributes`.** CRLF/LF warnings recurred on
  every `git add` on this Windows checkout (first flagged in Task 1's review). Added, normalizing
  text files to LF and marking PDFs/binaries as `-text`.
- **RESOLVED by final whole-branch review: `inspect.py`'s `_node_key` was a garbage-collection
  hazard.** It returned `id(node)` for direct (inline) PDF objects, but `visited` stored only
  the bare integer, so once the wrapper object was collected a later, unrelated direct object
  could reuse the same address and trigger a spurious `StructureTreeError` on a legitimate,
  non-cyclic third-party PDF. Fixed by recognizing that a direct object cannot participate in a
  reference cycle in the first place: `_node_key` now returns `None` for direct objects, and the
  caller skips cycle-tracking for them entirely rather than tracking a collectible identity.
- **`validate.py`'s alternate-JSON-schema branch is under-tested.** It is exercised only against
  an authored fixture, never a real veraPDF sample that happens to emit that schema shape
  (flagged in Task 2's review). The code comment noting this assumption was added; the branch
  itself was not further hardened.
- **`inspect.py`'s hand-rolled `/ToUnicode` CMap text extractor is diagnostic/test-only.** It was
  explicitly marked as such (Task 4) and its surrogate-pair `bfrange` handling is unverified.
  **Correction (final whole-branch review): the "`±` decodes as `U+FFFD`" claim recorded here
  was false and has been removed.** Re-verified directly: the CMap maps `<0073>` to `<00b1>`
  and the decoder returns `±` (`U+00B1`) correctly; the original observation was almost
  certainly a console-encoding artifact from printing the string, not a decoder defect. See
  ADR 0001 for the full correction. When born-digital text extraction becomes a real pipeline
  need (Phase 1+), still adopt `pdfminer.six` (MIT, pure Python, bundle-able) rather than
  hardening this regex-based parser — that recommendation is unaffected by this correction.
- **Root causes not localized, by design of a time-boxed spike:** neither ADR 0001 (why WeasyPrint
  tags MathML generically instead of as `Formula` — HTML5 foreign-content parsing vs. the tagging
  stage was never distinguished) nor ADR 0003 (which of the two live hypotheses explains the
  font-subsetting nondeterminism) was diagnosed to root cause. Both explicitly say so. Anyone
  picking this up for an upstream report should expect to do that diagnostic work themselves.
- **Several fix commits were accepted without a dedicated re-review round** (Task 5's final
  commit `2e051e8`, Task 6's fix commit `999b934`, Task 7's fix commit `4a3df7e`) — each was
  reviewed as part of a later task's overall review rather than in isolation. Flagged for
  whole-branch review rather than silently assumed clean.

## Measured by final whole-branch review, not by a dedicated Phase 0 task

Two things the phase never set out to test, but a reviewer measured directly while verifying
other findings. Recorded here so they are not lost, with the caveat stated plainly: neither has
its own dedicated task, fixture, or regression test the way the findings above do.

**Scale: 200 pages, 2001 structure elements, ~3.0s render + ~5.7s veraPDF validation.** The
Phase 0 spec's motivating comparison was a competing product that aborts entirely once a
document exceeds 999 structure elements. A 200-page synthetic document (verified by review, not
by a dedicated task or committed fixture) produced 2001 structure elements, rendered in
approximately 3.0 seconds, and passed veraPDF PDF/UA-1 validation in approximately 5.7 seconds —
directly answering that comparison in Rebind's favor at roughly double the competing product's
failure threshold, with no sign of degradation. This is a measured result, not an assumption,
but it was verified by review rather than committed as a reproducible test or benchmark; a
dedicated scale test (with the fixture and measurement methodology checked in) would be needed
before citing this number as a guaranteed property rather than a single observed data point.

**Two risks carried into later phases, identified but not mitigated in Phase 0:**

- **An unsigned PyInstaller executable versus Windows SmartScreen and institutional antivirus.**
  `rebind.exe` is not code-signed. On a librarian's managed institutional machine, an unsigned,
  unfamiliar executable downloaded from the internet is exactly the shape SmartScreen and
  endpoint antivirus are designed to block or warn loudly about — independent of whether the
  software itself is safe. This is a larger practical adoption risk than the installer simply
  not existing yet (criterion 4's open item above): even a working, built installer may not run
  at all, or may require an IT department to explicitly whitelist it, on the very institutional
  machines Rebind targets. Code-signing (a paid certificate, plus a signing step in the release
  process) is the standard mitigation and is not yet planned for any phase.
- **Font resolution and glyph coverage inside the frozen bundle are unverified.**
  `src/rebind/render.py`'s template requests `"DejaVu Serif", Georgia, serif` as the body font,
  but nothing confirms *which* font actually resolves once fontconfig is pointed at the bundled
  `gtk3-runtime/etc/fonts/fonts.conf` inside a frozen build (as opposed to development, where a
  system-wide font of the same name may be silently substituting). If DejaVu Serif is not
  actually present in, or discoverable from, the frozen bundle, WeasyPrint will silently
  substitute a fallback font with different glyph coverage, metrics, or Unicode support — which
  could silently break rendering of non-Latin scripts, diacritics, or specific math/IPA symbols
  in a way no current test would catch, since the render-smoke test only exercises ASCII text
  and a raster image. This needs a dedicated frozen-build test that inspects which font actually
  backs the rendered glyphs, not merely that rendering succeeds without error.

## Full test output

Captured in `docs/decisions/phase-0-test-output.txt`
(`uv run pytest -v`): **26 passed, 1 deselected, 3 xfailed, 1 xpassed** as of Task 8's original
close-out. That snapshot is superseded, not deleted, by the final whole-branch review recorded
throughout this document: after the fixes above (heading-level normalization and its new tests,
the noisy xfail's replacement with a stable characterization test, the alt-text reader, the
composed post-processing test, and the rest), `uv run pytest -v` reports **38 passed,
1 deselected, 3 xfailed** — no `xpassed`, because the one XPASS-prone test
(`test_two_runs_produce_identical_bytes`) no longer exists in that noisy form (see the resolved
item above). The 3 remaining xfails are the two `test_reproducible.py` cross-process
nondeterminism tests plus `test_mathml_produces_a_formula_element`, all still expected per ADR
0001/0003 and not a regression signal.
