# Third-party license texts to bundle in the installer

This directory is installed into the app's `licenses\` folder (see `rebind.iss`'s `[Files]`
section) and its `LICENSE-THIRD-PARTY.txt` is wired up as the installer's `LicenseFile`. Neither
is complete yet -- this is the structure the license work lands in, not the finished work
itself (see `docs/decisions/0002-phase-0-findings.md`, "Per-DLL license inventory not done").

## What must be filled in before public release

The frozen bundle vendors ~80 DLLs from the GTK3-Runtime-Win64 distribution
(`packaging/rebind.spec`), which is itself an aggregation of several upstream projects under
different licenses, at least:

| Component | License | Notes |
|---|---|---|
| GTK, GLib, Pango, GdkPixbuf, ATK | LGPL-2.1+ | Core GTK3 stack |
| Cairo | LGPL-2.1 / MPL-1.1 (dual) | |
| HarfBuzz | MIT | Text shaping; also the suspected source of the ADR-0003 nondeterminism |
| FreeType | FTL (or GPLv2, dual-licensed) | Confirm which FreeType chose for this build |
| libpng | libpng license | |
| zlib | zlib license | |
| expat | MIT | |
| PCRE2 | BSD-3-Clause | |

Before a public release:

1. Enumerate the actual DLLs in a built `dist/rebind/gtk3-runtime/bin/` (the exact file list,
   not the table above, which is illustrative) and map each to its upstream project.
2. Obtain the canonical license text for each distinct license identified (not a paraphrase),
   and place each as its own file in this directory (e.g. `LICENSE-LGPL-2.1.txt`,
   `LICENSE-HarfBuzz-MIT.txt`).
3. Write `LICENSE-THIRD-PARTY.txt` (referenced by `rebind.iss`'s `LicenseFile`) as an index:
   which DLL/component uses which license file in this directory, plus attribution text any
   license requires (e.g. copyright notices).
4. LGPL specifically requires either dynamic linking (already true here -- these are separate
   DLLs, not statically linked) or an offer to allow relinking against a different version;
   confirm rebind's distribution mechanism satisfies this (dynamic linking generally does) and
   document that conclusion here rather than leaving it implicit.
5. Rebind's own `LICENSE` (repo root) should also be included in the installed payload for
   completeness, separate from the third-party notices.

## Current state

`LICENSE-THIRD-PARTY.txt` in this directory is a placeholder that names the obligation and
points back to this README; it is not a substitute for doing the above.
