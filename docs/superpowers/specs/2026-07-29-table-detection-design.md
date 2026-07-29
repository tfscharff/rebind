# Phase 2 slice 5 — table detection and honest flagging

**Status:** designed 2026-07-29. Not yet implemented.

Governing design: `2026-07-22-rebind-design.md`. Builds on the layout slice.

## 1. Problem

Rebind does not detect tables at all. A table's cells are ordinary text lines that share the body
paragraph style, so they are emitted as confident, unflagged paragraphs in whatever order the naive
top-to-bottom / left-to-right (now XY-cut) sort produces — which for a multi-column table is
frequently the wrong order, **with nothing in the output to warn a reviewer.** This is the single
loudest silent-failure gap in the current output (Phase 1 spec §9.1) and it is exercised by a real
sample: `samples/Failure.pdf` contains "Table 7.5 Productive Responses to Different Types of
Failure", a 3-column grid whose OCR'd cells currently reorder silently.

## 2. Goal and non-goal

**Goal:** detect table-like regions geometrically and flag their lines `table-suspected`, so a human
reviewer is told the reading order in that region may be wrong — exactly as `multi-column-suspected`
does for columns. Content is never dropped or reordered *differently*; only a flag is added.

**Explicit non-goal: no reconstruction.** Real tables in the samples are messy — multi-line cells
that wrap, row-label columns, stacked header rows. Reconstructing them into an HTML grid would
*fabricate* structure and mis-order cells worse than today, violating the never-fabricate invariant
to satisfy a validator. Reconstruction is deferred to a later slice, gated on tables that can be
rebuilt safely. This slice only makes the existing gap honest.

## 3. Detection signal

Operates per page on the body lines (artifacts already held out by the layout stage). A region is
table-like when its lines form a **grid**: rows that each split into the same recurring column
positions. Concretely:

1. Group lines into **rows** by clustering their vertical centers into y-bands (a band is lines
   whose centers fall within `ROW_BAND_FRACTION` of the median line height).
2. A **candidate row** is a band containing at least `MIN_CELLS_PER_ROW` (2) lines at distinct,
   non-overlapping x-positions — i.e. multiple short cells side by side, not one continuous line.
3. The **column x-positions** (each candidate cell's left edge) are collected across all candidate
   rows and clustered (tolerance `COLUMN_ALIGN_TOLERANCE_PT`). A column is *recurring* if cells land
   on it in at least `MIN_ROWS_FOR_TABLE` (2) distinct rows.
4. A region is a table when there are at least `MIN_COLUMNS_FOR_TABLE` (2) recurring columns and at
   least `MIN_ROWS_FOR_TABLE` (2) rows participating in them. The table's lines are exactly the
   cells in those participating rows and columns.

This is conservative by construction: ordinary prose (one long line per row) never produces
multiple side-by-side cells, and a two-column page *layout* is already separated by XY-cut into
single-column regions before this runs, so flowing columns do not look like a table. The thresholds
are named constants, expected to be tuned against `Failure.pdf`'s real table.

### 3.1 Why not reuse the multi-column detector

The old `_looks_multi_column` heuristic (removed in the layout slice) asked "are there disjoint
vertical clusters of lines?" — true for both columns and tables. A table is distinguished by the
*grid*: cells recurring at the same x across multiple rows, and short cells rather than full-height
column runs. Column layout is now handled structurally by XY-cut; table detection is the residual
"grid inside a single column region" signal.

## 4. Behavior when detected

- Every paragraph whose source line is a detected table cell is flagged `table-suspected`.
- Nothing else changes: the cells are still emitted as paragraphs in the same order, with the same
  confidence and provenance. The flag is the entire deliverable — an honest warning, not a
  transformation.
- The CLI reports the count: "N page(s) contain a suspected table; cell reading order may be wrong —
  check them by hand", mirroring the multi-column note.

## 5. Testing

Synthetic fixtures (no real sample in CI):

1. **Unit** (`layout`): synthetic `TextLine`s laid out as a 3×3 grid are detected; the participating
   cell lines are returned.
2. **Negative — prose**: ordinary single-line-per-row paragraphs are *not* detected as a table.
3. **Negative — list**: a bulleted/numbered list (one item per row) is not a table.
4. **Negative — two-column flowing text**: within a single column region, flowing prose is not a
   table (guards the false positive that would turn columns into tables).
5. **Assemble**: paragraphs on the grid rows carry `table-suspected`; surrounding prose does not.
6. **CLI**: the suspected-table note is printed for a document containing a grid.

Real-sample check (manual, out of CI): `Failure.pdf`'s Table 7.5 region is flagged.

## 6. Invariants upheld

1. **Never fabricate** — no structure is invented; a flag is added, content and order are unchanged.
2. **Everything has provenance** — flagged lines keep their page and bbox.
3. **Determinism scoped to the model** — detection is a deterministic geometric test.
4. **No API key, GPU or network** — pure geometry, no new dependency.
5. **No arbitrary limits** — per page, independent of document length.
6. **Bundle-able on Windows** — no new dependency.
