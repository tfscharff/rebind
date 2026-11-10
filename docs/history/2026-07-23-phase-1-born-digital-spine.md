# Phase 1 — the pipeline spine, born-digital branch

**Status:** implemented 2026-07-23. The born-digital spine is built and passing veraPDF; see
`src/rebind/` (`extract`, `profile`, `model`, `assemble`, `emit`, `pipeline`, `cli`), which is
authoritative where it differs from this document. Known gaps deliberately left open are recorded
in §6 and §9.1 rather than silently.

Governing design: `2026-07-22-rebind-design.md`. This spec refines sections 5.1–5.3 and 9 of that
document for the first implementable slice and does not supersede it.

## 1. Goal

A born-digital PDF goes in; a tagged PDF/UA document comes out that veraPDF passes, with headings,
paragraphs and lists recovered from typography. Every stage of the spine connects end to end on a
document that is genuinely useful rather than on scaffolding.

Deliberately **out of scope**: tables, figures, formulae, chemistry, music, footnote linking, OCR,
image restoration, the corrections diff layer, and the browser UI. Each is a later phase.

## 2. Why the born-digital branch first

It has a real text layer, so it needs neither OCR nor restoration, and heading levels, emphasis and
reading order can be inferred from typography rather than guessed from pixels. It therefore
delivers a working feature at the end of Phase 1 instead of infrastructure waiting on Phase 2. It
also exercises every stage of the spine, so the interfaces the scanned branch will need are
designed against something real.

## 3. Architecture

Seven new modules. `render.py`, `validate.py` and `pagelabels.py` already exist and are unchanged.

| Module | Responsibility |
|---|---|
| `extract.py` | pdfminer.six → per-page `TextLine` and `ImageRegion` records; per-page born-digital/scanned classification |
| `profile.py` | Pass one: style statistics → `TypographicProfile` |
| `model.py` | Document model dataclasses; JSON round-trip |
| `assemble.py` | Pass two: `TextLine`s + profile → document tree |
| `emit.py` | Document model → semantic HTML fragment |
| `pipeline.py` | Stage orchestration; returns model, PDF path, validation report |
| `cli.py` | `rebind convert in.pdf out.pdf` |

Flow:

```
extract → profile → assemble → emit → render_html_to_pdf → validate_pdf_ua
```

### 3.1 New dependency

**pdfminer.six** (MIT, pure Python). Satisfies invariant 6 — no native build, bundle-able on
Windows, no system-wide install. This is the adoption anticipated in `CLAUDE.md`; `inspect.py`'s
ToUnicode extractor remains diagnostic/test-only and is not extended.

## 4. Structure inference: document-global typographic profile

Two passes over the document.

**Pass one** collects style statistics only: `(font, size, bold, italic)` tuples with their
character counts and positional distribution. It does not retain text. Memory is therefore bounded
by the number of distinct styles, not by document length — which is what makes 300- and 1000-page
documents tractable.

From those statistics:

- **Body style** is the style with the greatest character volume. This is definitional, not
  heuristic: the most-set text in a book is its body text.
- **Heading styles** are styles larger or bolder than body, ranked by size then weight, and mapped
  to heading levels in that order.
- **Artifact candidates** are lines that fall in the top or bottom 10% of the page box *and* appear
  at that position on at least half the pages — running headers, footers, page numbers. Both
  thresholds are named constants, not magic numbers, and are expected to need tuning against a real
  document. Requiring recurrence as well as position matters: a first-page title also sits at the
  top of the page and must not be discarded as an artifact.

**Pass two** streams the pages again and emits nodes against the profile.

### 4.1 Why global rather than per-page

Heading styles in a long document are consistent document-wide, so a global profile assigns levels
correctly where a per-page rule cannot. A page containing only a heading and one paragraph has no
usable local baseline, and per-page level assignment drifts across a long document — a silent
failure mode, worst on exactly the 300-page catalog that motivates the project.

The cost is reading the document twice. For a 300-page PDF this is seconds, and correctness does
not degrade with length.

Style clustering was rejected: it introduces a tunable with no principled setting, and clustering
over floats is a determinism risk in the same area where ADR 0003 already found nondeterminism.

## 5. Document model

Node types implemented in Phase 1: `Document`, `Heading`, `Paragraph`, `List`, `ListItem`,
`Artifact`, `Placeholder`, `PageBreak`.

The remaining node types from the governing design (`Section`, `Table`, `Figure`, `Formula`,
`ChemicalStructure`, `Music`, `Footnote`, `FootnoteRef`, `BlockQuote`) are **not stubbed**. A stub
invites code to depend on a shape that has not been designed.

Every node carries:

```python
id: str            # stable, content-derived
page: int          # source page, 1-based
bbox: tuple        # (x0, y0, x1, y1) in PDF points
confidence: float  # 0.0-1.0
stage: str         # producing module
flags: list[str]
```

### 5.1 Stable ids

The id is a hash of page number, normalized bbox, and a content fingerprint — the composite
specified in governing design 5.7.

This is implemented in Phase 1 even though the corrections diff layer is not, because node identity
must survive reprocessing for that layer to be possible at all. It is nearly free now and expensive
to retrofit. The fuzzy re-attachment built on top of it belongs to a later phase.

### 5.2 Confidence

Confidence in Phase 1 means exactly one thing: **how cleanly a line's style matched a profile
entry.** An exact match on the dominant body style is 1.0. A style seen only a few times that is
being guessed as a heading is low.

It is explicitly *not* a text-accuracy score. Born-digital text is exact by construction. Conflating
the two would make the number meaningless as soon as OCR confidence arrives in Phase 2.

### 5.3 Artifacts

Running headers, footers and page numbers are emitted as `Artifact` nodes and excluded from the
reading order, so assistive technology does not announce the document title on every page.

### 5.4 Page breaks

`PageBreak` nodes feed the existing `pagelabels.py`. As implemented, `PageBreak.label` holds the
source page's sequential ordinal (`str(page.number)`), not the source's own printed pagination --
so a viewer shows page 1, 2, 3, ... rather than the roman numerals or plate numbers the source
document may actually use. Extracting and preserving the source's own labels is later work; see
§9.1.

## 6. Handling content Phase 1 does not model

Split by region type:

- **Text-bearing regions that cannot be structured** keep their text, emitted in reading order as
  paragraphs, with provenance retained. Nothing recoverable is discarded.
- **Non-text regions** (images, figures) become `Placeholder` nodes retaining page and bbox.

Of the regions above, only multi-column text is actually flagged in Phase 1: `assemble.py` has a
cheap heuristic (disjoint, vertically-overlapping horizontal clusters of lines) that marks
suspect pages `multi-column-suspected`, precisely so scrambled reading order is surfaced rather
than silently trusted. **Tables have no equivalent heuristic.** A table's cells share the body
paragraph style, so they score the same confidence as ordinary prose and carry no flag at all --
cell text is preserved but may read out of order, indistinguishable in the output from a correctly
ordered paragraph. Table detection is Phase 2 work; see §9.1.

`Placeholder`, not `Figure`, is used for images deliberately. PDF/UA 7.3 requires `/Alt` on every
figure, and Phase 1 has no honest way to produce alt text. Emitting a `Figure` with invented alt
text would break the never-fabricate invariant in order to satisfy a validator — the exact trade
Rebind exists to refuse.

## 7. Input that is not born-digital

Per-page classification, and the run continues:

- **Scanned pages** produce `Placeholder` nodes flagged `no-text-layer`, recorded per page so a
  later OCR pass knows precisely which pages to revisit. Mixed documents therefore convert, with
  honest holes, rather than being refused.
- **A document with no extractable text on any page** is a scan, and Phase 1 stops with a clear
  report rather than emitting hundreds of placeholders.
- **Already-tagged PDFs** are detected and reported in the result object. This does not block
  conversion; the governing design's position that Rebind should not churn already-accessible
  documents is surfaced as information, and acting on it is a later decision.
- **Encrypted, malformed, or zero-page files** stop with a clear error.

## 8. Interface

`rebind.pipeline` is a library, driven by a CLI:

```
rebind convert input.pdf output.pdf
```

The browser UI is deferred to the end of Phase 1 or later, and is a thin shell over the library.
Building it earlier would couple pipeline design to HTTP shapes before the pipeline exists.

## 9. Testing

The suite cannot depend on any real document: `samples/` is gitignored and must stay so, because
those are copyrighted third-party scans in a public repo.

**Fixtures are generated with WeasyPrint at test time.** Known HTML → PDF → back through Rebind →
assert the recovered model matches the structure that went in.

1. **Unit tests on `profile.py`** against synthetic `TextLine` lists — no PDF involved. Body-style
   detection, heading ranking and artifact detection are tested in isolation.
2. **Round-trip fidelity** — headings and their levels, paragraphs and lists survive the round trip.
3. **Golden-file tests on the serialized model** — diffable JSON, per governing design section 9.
   Never PDF bytes (ADR 0003).
4. **veraPDF gate** on generated output, reusing `validate.py`.
5. **Long-document test** — a generated 300-page fixture converts with bounded memory and no
   structure-element ceiling. This tests invariant 5 directly.

Adversarial fixtures are included: skipped heading levels, two-column CSS, `@page` margin-box
running headers.

### 9.1 What the suite does not prove

WeasyPrint-generated PDFs are unusually well-formed. They do not exercise what InDesign, Word or
LaTeX actually emit — inconsistent font naming, text split mid-word across spans, headers placed in
margin boxes.

**The suite proves the logic is correct. It does not prove the heuristics are well-tuned.** Only
real documents can do that, and the first of those is the 300-page catalog, which lives outside the
repository. Tuning against it is expected work at the end of Phase 1, not a sign something went
wrong.

**Table cell text has no ordering guarantee and no flag.** Phase 1 has no table detection at all;
a table's cells are ordinary text lines that happen to share the body paragraph style, so they are
emitted as confident, unflagged paragraphs in whatever order the naive top-to-bottom /
left-to-right sort produces -- which for a multi-column table is frequently the wrong order, with
nothing in the output to warn a reviewer. This is a known Phase 1 gap, not a bug to fix here; real
table structuring is Phase 2 work.

**Page labels are sequential ordinals, not the source's own pagination.** `PageBreak.label` is
`str(page.number)` today. A source with roman-numeral front matter or non-arabic pagination gets
plain sequential numbering in the output instead of its own scheme; extracting real source labels
is later work (see §5.4).

## 10. Invariants this design upholds

1. **Never fabricate** — no invented alt text; unrecoverable content becomes an honest placeholder.
2. **Everything has provenance** — every node carries page and bbox, including placeholders.
3. **Determinism scoped to the document model** — golden files test model JSON, never PDF bytes.
4. **No API key, GPU or network** — pdfminer.six is pure Python and offline.
5. **No arbitrary limits** — tested directly by the 300-page fixture.
6. **Bundle-able on Windows** — pdfminer.six has no native build step.
