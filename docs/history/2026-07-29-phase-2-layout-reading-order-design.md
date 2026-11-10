# Phase 2, slice 1 — layout analysis and reading order

**Status:** designed 2026-07-29. Not yet implemented.

Governing design: `2026-07-22-rebind-design.md`. Predecessor slice: `2026-07-23-phase-1-born-digital-spine.md`.
This spec refines the "layout analysis and reading order" portion of Phase 2 and does not supersede
the governing design.

## 1. Goal

Turn the born-digital branch's `multi-column-suspected` *flag* into actual reconstruction: a
multi-column page is segmented into columns and blocks, and its text is emitted in correct reading
order — left-to-right across columns, top-to-bottom within them. The mechanism is a new pipeline
stage that both branches will share, so the reading-order interface the scanned branch needs is
designed against real born-digital input rather than scaffolding.

Deliberately **out of scope**, unchanged from Phase 1: tables, figures, captions, sidebars, pull
quotes, marginalia, OCR, and image restoration. Only the column-block region type is built.

## 2. Why this slice first

Layout analysis and reading order operate on *positioned text regions*, which both the born-digital
and scanned branches produce. The born-digital spine already emits positioned lines and already
*flags* multi-column pages without reconstructing them, so this slice needs no CV or OCR stack, is
testable against samples we hold today (the 1905 Wheaton Bulletin is multi-column; the Bullock
thesis is a real long single-column document), and immediately upgrades the shipping output. It also
builds the reading-order interface that OCR will plug into, satisfying the governing design's
principle of designing interfaces against something real.

## 3. Architecture

One new module. `extract.py`, `profile.py`, `emit.py`, `pipeline.py`, `render.py`, `validate.py`,
`pagelabels.py` keep their responsibilities; `assemble.py` loses two responsibilities it should not
have had.

| Module | Change |
|---|---|
| `layout.py` | **New.** Per page: exclude artifact lines, run recursive XY-cut on the body lines, produce a region tree and a reading-ordered line sequence. |
| `assemble.py` | Loses the naive top-to-bottom/left-to-right sort and the `multi-column-suspected` heuristic. Now consumes an already-reading-ordered line stream plus region boundaries. |
| `pipeline.py` | Inserts the `layout` stage between `profile` and `assemble`. |

Flow:

```
extract → profile ─┐
                   ├→ layout → assemble → emit → render_html_to_pdf → validate_pdf_ua
                   │
       (profile stays document-global; layout is per page)
```

`profile.py` is unchanged: pass one collects document-global style statistics and does not depend on
reading order, so it still runs on raw extract output.

### 3.1 Branch-agnostic interface

`layout` consumes a generic positioned-line abstraction — a bounding box plus a minimal style handle
— which the born-digital extractor produces today and the Phase 2 OCR stage will produce later.
`layout` therefore has no dependency on pdfminer or on any born-digital specifics. This is the
interface the scanned branch will reuse.

No new dependency. XY-cut is pure geometry over the line boxes already extracted.

## 4. The algorithm: recursive XY-cut

Recursive whitespace segmentation. At each region:

1. Find the widest *valid* vertical gutter (whitespace band running the region's height) and the
   widest *valid* horizontal gap (whitespace band running the region's width).
2. Cut on whichever is more significant.
3. Recurse into the resulting sub-regions.
4. Stop when neither a valid vertical nor a valid horizontal cut exists. That region is a leaf
   block; its lines are ordered top-to-bottom.

Reading order is the depth-first traversal of the resulting tree: across vertical cuts
left-to-right, across horizontal cuts top-to-bottom. Segmentation and reading order are one
algorithm, not two.

### 4.1 Guards

All thresholds are named constants, expected to need tuning against the 1905 bulletin — that tuning
is planned work, not a defect. Positional thresholds are fractions of the *current region's* box, so
they behave consistently regardless of page size or recursion depth.

- **Vertical cut (column gutter).** Accept only if the gutter width ≥ `GUTTER_MIN_FRACTION` of the
  region width *and* it spans ≥ `GUTTER_MIN_HEIGHT_FRACTION` of the region height *and* there is
  substantial text on both sides. The both-sides requirement prevents a centered title or a lone
  page number from manufacturing a false column.
- **Horizontal cut (block gap).** Accept only if the vertical gap ≥ `BLOCK_GAP_MIN_FRACTION` of the
  region height.
- **Determinism.** When a vertical and a horizontal cut are equally significant, cut vertically
  first; among candidate cuts on the same axis, take the one at the smallest coordinate first. This
  is the tie-break discipline Phase 1 already tests. Same input yields an identical tree and order.
- **Graceful degradation.** A region with no qualifying cut is a leaf. A single-column page therefore
  produces one leaf block and the natural top-to-bottom order — the common case, handled for free.

### 4.2 Artifacts are excluded before the cut

Running headers, footers and page numbers are identified by the profile's position-plus-recurrence
rule (Phase 1 §5.3). Those lines sit at the top and bottom of the page box and would otherwise
create spurious horizontal cuts or be mistaken for column content. `layout` excludes artifact lines
from the XY-cut input; they are emitted as `Artifact` nodes exactly as in Phase 1, outside the
reading order. The two mechanisms stay cleanly separated.

### 4.3 Low-confidence fallback

When a vertical cut is marginal — near the width or height threshold — the affected page keeps a
`multi-column-suspected` flag in addition to being reconstructed, so a reviewer is warned that the
interleaving may be wrong rather than being given falsely-confident order. A clean cut carries no
such flag; an absent cut is the single-column case and carries no flag either.

## 5. Model and provenance

- **The region tree is intermediate, not a document node type.** It lives inside `layout.py`
  (`Region(kind=column|block, bbox, children | lines)`) and is never added to the semantic document
  model. No new node types are introduced — consistent with Phase 1's refusal to stub shapes that
  have not been designed.
- **Reading-order provenance.** Each body node gains a lightweight record of the source page and the
  index of the column it came from, so a reviewer can trace why a line landed where it did. Page and
  bbox are already carried by every node.
- **Confidence is untouched.** Phase 1's contract holds: `confidence` means style-match cleanliness
  and nothing else. Reading-order uncertainty is expressed only as a flag (§4.3), never folded into
  the number — that number must stay meaningful when OCR confidence arrives.

## 6. Testing

The suite cannot depend on any real document (`samples/` is gitignored; those are copyrighted
third-party scans in a public repo). Fixtures are generated with WeasyPrint at test time.

1. **Unit tests on XY-cut** against synthetic line-box lists — no PDF involved:
   - single column → no cut, natural order;
   - clean two-column and three-column → correct interleaving;
   - full-width header above two columns → header isolated by a horizontal cut *first*, not absorbed
     into a column;
   - narrow gutter below threshold → no false cut;
   - bottom page number → excluded as an artifact, not treated as a block.
2. **Round-trip fidelity.** The Phase 1 two-column CSS adversarial fixture must now yield **correct
   interleaved reading order**, not merely a flag.
3. **Golden-file test** on the serialized model of a two-column document — diffable JSON, never PDF
   bytes (ADR 0003).
4. **Determinism.** Identical input yields an identical region tree and reading order.
5. **veraPDF gate** on the generated output, reusing `validate.py`.
6. **Real-sample smoke**, run manually and never in CI: the 1905 Wheaton Bulletin converts with sane
   reading order. This is where heuristic tuning happens; it lives outside the repository.

### 6.1 What the suite does not prove

As in Phase 1, WeasyPrint-generated PDFs are unusually well-formed and do not exercise the column
irregularity of real scans and real page layout software. The suite proves the XY-cut logic is
correct; only the 1905 bulletin and the eventual scanned corpus can show the thresholds are
well-tuned. Tuning against them is expected work, not a sign something is wrong.

## 7. Invariants this design upholds

1. **Never fabricate** — reading order is derived from geometry; nothing is invented. A marginal cut
   is flagged, not guessed past.
2. **Everything has provenance** — every node still carries page and bbox, and now also its column
   index.
3. **Determinism scoped to the document model** — XY-cut is deterministic by construction and tested
   for it; golden files test model JSON, never PDF bytes.
4. **No API key, GPU or network** — pure geometry, no new dependency, no ML.
5. **No arbitrary limits** — segmentation is per page and independent of document length.
6. **Bundle-able on Windows** — no new dependency to bundle.
