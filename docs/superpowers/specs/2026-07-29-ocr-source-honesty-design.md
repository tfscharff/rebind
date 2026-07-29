# Phase 2 slice 2 — honest handling of hidden OCR text layers

**Status:** designed 2026-07-29. Not yet implemented.

Governing design: `2026-07-22-rebind-design.md`. Related: the Phase 1 spine and the layout slice.

## 1. Problem

Many real interlibrary-loan scans arrive as a **scanned page image with an invisible OCR text
layer on top** (the 1905 Wheaton Bulletin and Chapter 14-ocr in `samples/` are both like this).
Rebind currently treats any page with a text layer as born-digital, so it emits that OCR text at
**confidence 1.0** with no indication the text is recognizer output. The text is frequently garbled
("TllE WI1EA1\"0.1Y BUT,LETIN" for "THE WHEATON BULLETIN"), yet the output looks as confident as a
clean born-digital document. That quietly violates the project's "know what you don't know"
principle: a reader is handed confident-looking output that is full of recognition errors, with
nothing flagging it.

Additionally, the page-covering scan image is currently emitted as a `Placeholder` "unmodelled
image region" — but it is not a figure, it is the scanned page Rebind already transcribed. Emitting
it as an undescribed figure is misleading noise.

## 2. Goal

Detect pages that are OCR-over-scan and handle their text honestly: mark it as recognizer output,
cap its confidence to reflect that its accuracy is unknown, suppress the redundant background scan
image, and report the affected pages. No OCR engine is involved — this slice only recognizes and
honestly labels an OCR layer that is *already present*. Re-recognizing text (the OCR branch for
pages with no text layer at all, e.g. `Failure.pdf`) is a later, larger slice with its own
dependency decisions.

## 3. Detection signal

A page is **OCR-over-scan** when it has a text layer **and** at least one raster image covers at
least `OCR_SCAN_COVERAGE` of the page area. Measured against the four samples: real OCR'd scans
have a 100%-coverage page image; the true born-digital thesis has none (0%); a born-digital poster
with decorative graphics tops out at 4%. `OCR_SCAN_COVERAGE = 0.6` separates these with wide
margin and is a named, tunable constant.

An OCR-font-name heuristic (font names containing "OCR"/"GlyphLess") was considered as a secondary
signal but is not needed and is unreliable — pdfminer surfaced ordinary font names for both OCR
samples. Image coverage is the sole signal.

## 4. Behavior when detected

- **Text nodes** on an OCR-over-scan page carry an `ocr-source` flag, and their confidence is
  capped at `OCR_SOURCE_CONFIDENCE` (0.5). Confidence has meant style-match cleanliness (Phase 1
  §5.2); for OCR-sourced text that number is not trustworthy as a correctness signal, so it is
  lowered to a single documented placeholder value meaning "recognizer output, accuracy unknown."
  This is deliberately coarse: a calibrated per-character confidence only becomes possible when
  Rebind runs its own OCR (a later slice). It is never fabricated upward — capping only ever
  lowers confidence.
- **The background scan image** (any image covering ≥ `OCR_SCAN_COVERAGE` on such a page) is **not**
  emitted as a `Placeholder`. It is the scanned page itself, already represented by the recovered
  text; emitting it as an undescribed figure is misleading. Genuinely smaller embedded images on
  the same page (a photo within an article) are still emitted as placeholders as before.
- **Reporting.** The CLI derives the OCR-source page set from the `ocr-source` node flags — the
  same pattern it already uses for the multi-column note, so no new model or result field is added —
  and prints: "N page(s) look like OCR'd scans; their text is recognizer output and may contain
  errors."

Nothing about reading order, headings, lists or artifacts changes — those still run. An OCR-over-
scan page is still fully assembled; it is just labelled honestly.

## 5. What this deliberately does not do

- **No re-OCR.** Pages with *no* text layer (`Failure.pdf`) remain refused/placeholdered exactly as
  today. Running an OCR engine is a separate slice gated on a bundle-able-on-Windows,
  no-GPU/no-network engine decision (invariants 4 and 6).
- **No text-quality judgement.** Rebind does not try to score how garbled the OCR is (that needs a
  lexicon and is locale-specific). It reports *that* the text is OCR-sourced, not *how bad* it is.
- **No confidence calibration.** The 0.5 cap is a placeholder, not a measured probability.

## 6. Testing

1. **Unit** (`extract` or `assemble`): a synthetic `Page` with a text layer and a full-page
   `ImageRegion` is classified OCR-over-scan; one with small images or none is not.
2. **Assemble**: text nodes on an OCR-over-scan page carry `ocr-source` and confidence ≤ 0.5; the
   page-covering image yields no `Placeholder`; a smaller co-located image still does.
3. **Assemble negative**: a born-digital page (no page-covering image) is unaffected — no
   `ocr-source` flag, confidence unchanged, images still placeholdered.
4. **Pipeline/CLI**: `ocr_source_pages` is populated and the note is printed.

Fixtures are generated with WeasyPrint (a full-bleed background image over text reproduces the
OCR-over-scan shape) — no real sample enters the suite (`samples/` is gitignored).

## 7. Invariants upheld

1. **Never fabricate** — confidence is only ever lowered; OCR text is labelled, never presented as
   exact.
2. **Everything has provenance** — text nodes keep page and bbox; the suppressed background image
   is redundant with the transcribed text, not dropped information.
3. **Determinism scoped to the model** — detection is a deterministic area comparison.
4. **No API key, GPU or network** — pure geometry; no OCR engine.
5. **No arbitrary limits** — per-page, independent of document length.
6. **Bundle-able on Windows** — no new dependency.
