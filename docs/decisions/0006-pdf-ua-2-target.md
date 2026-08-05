# 0006 — Target PDF/UA-2 (ISO 14289-2) instead of PDF/UA-1

**Status:** Accepted.

**Date:** 2026-08-05

## Context

Rebind targeted PDF/UA-1 (ISO 14289-1:2014) since the remediation pivot. PDF/UA-2 (ISO 14289-2:2024,
based on PDF 2.0 / ISO 32000-2) is the PDF Association's current standard and adds MathML support,
richer annotation coverage, and stricter navigation requirements. Thomas asked to move the target
directly, having read the PDF Association's own material on it.

## Findings from a spike

Public write-ups of the PDF/UA-1 → PDF/UA-2 migration (blog posts, third-party summaries) describe a
full rename to lowercase HTML5-style role names (`p`, `h1`–`h6`, `table`, …) under a `/Namespaces`
mechanism, RoleMapNS indirection, etc. Rather than trust that secondhand, a minimal tagged PDF was
hand-built with pikepdf and iterated against `verapdf -f ua2` until it reached 0 failures — the same
discipline as every other PDF/UA subtlety in this codebase (e.g. `/Artifact BMC` vs `BDC`).

**The real delta was much smaller than the write-ups suggested:**

- The PDF 2.0 Standard Structure Namespace (`http://iso.org/pdf2/ssn`) retains every familiar
  uppercase role name (`/P`, `/H1`–`/H6`, `/Table`, `/TR`, `/TD`, `/TH`, `/L`, `/LI`, `/LBody`,
  `/Figure`) as a valid type. Lowercase HTML5 names are an *available style*, not a requirement.
- Only the root `Document` structure element needs an explicit `/NS`; every descendant inherits it.
  No change to `_structure_roles`, `_page_structure`, `_tagged_table`, or figure building.
- The namespace URI is easy to get backwards from a validator's own error-message wording — an
  early attempt used the plausible-looking `http://iso.org/pdf/ssn` (no "2") because the error text
  ambiguously echoed it back; the correct value, confirmed against veraPDF's rule text and its own
  wiki, is `http://iso.org/pdf2/ssn`.
- **veraPDF 1.30.2 already validates `ua2`** ("PDF/UA-2 + Tagged PDF validation profile") — the
  "validator support is still catching up" claim found in a general web search was stale for the
  version actually installed here.

So the actual change in `remediate.py`: save with `min_version="2.0"`, one `Namespace` object
referenced by `StructTreeRoot.Namespaces` and the `Document` element's `/NS`, and XMP
`pdfuaid:part="2"` + `pdfuaid:rev="2024"` (was `part="1"`).

## A real regression this surfaced: internal navigation

PDF/UA-2 clause 8.8 requires every internal destination (an outline/bookmark entry, an
`OpenAction`, a `Link` annotation's `GoTo` target) to be a **structure destination** — one that
names a structure element, not a page + coordinates. Nothing before PDF 2.0 could produce that, so
a born-digital source's own navigation (built by whatever authored it — Word, LaTeX, WeasyPrint, or
a publisher's production pipeline) is carried through verbatim on a page kept verbatim, and fails
this clause. Rebind does not yet build its own structure-destination navigation, so
`_strip_legacy_destinations` drops the legacy kind (`/Outlines`, `/OpenAction`, `/Names/Dests`, and
per-page `Link` annotations with an internal `GoTo`/`Dest`) rather than ship a document that claims
PDF/UA-2 and fails it. External links (URI, GoToR) are untouched. **This is real navigation lost**,
not a formality — restoring it properly (bookmarks built from Rebind's own recovered heading
structure, using real structure destinations) is a follow-up feature, not done here.

## Three real bugs found stress-testing a genuinely hard sample

`samples/1429254.pdf` (gitignored; an Elsevier academic-paper scan with an Adobe Accessibility
Checker report showing 6 failing rules) drove the migration from "passes synthetic fixtures" to
"passes a real hard document, 0 veraPDF failures":

1. **Malformed source XMP hides Rebind's own metadata.** The source's XMP carried a stray,
   non-namespaced element (a DRM/fingerprinting artifact from the publisher's own pipeline) — well-
   formed XML, but strict enough to break veraPDF's metadata parser so it stopped seeing Rebind's
   `dc:title`/`pdfuaid:*` entries too. `_set_metadata` now strips any top-level XMP key without a
   namespace before adding its own (a legitimate property always has one).
2. **Link-annotation destinations weren't covered** by the first pass of
   `_strip_legacy_destinations` (only root-level `Outlines`/`OpenAction`/`Dests` were) — 137
   instances in one document. Extended to per-page annotations (see above).
3. **The invisible text overlay had a real encoding bug**, not a UA-2 nuance: `_tagged_text_stream`
   `.encode()`d text as UTF-8 into a font declared `/WinAnsiEncoding`, silently corrupting any
   accented character or curly quote — sometimes landing on one of WinAnsi's five undefined byte
   values, an invalid Unicode mapping regardless of which PDF/UA part is targeted. A second,
   separate defect: a literal tab character (real source text used tabs for heading alignment) has
   no glyph name at all in WinAnsiEncoding's table (PDF spec Annex D defines none for C0 controls),
   so it always encodes to Unicode 0. `_encode_winansi` now encodes as cp1252 (matching the
   declared encoding) and normalizes C0 controls to spaces first.

## Consequences

- `validate_pdf_ua`'s default flavour is now `ua2`; `ua1` remains selectable for validating older
  output.
- A new dependency-free structural self-check (`validate.self_check_pdf_ua2`) backs the in-app
  "PDF/UA-2 tagged" badge — bundling veraPDF itself at runtime would need a JVM, undoing the
  installer-size work from the same week (104 MB → 81.6 MB).
- Future samples should come with an accessibility-checker report (Adobe or otherwise) as a
  baseline, per the workflow Thomas set: it is what surfaced all three bugs above, none of which
  synthetic WeasyPrint fixtures would ever have produced.
