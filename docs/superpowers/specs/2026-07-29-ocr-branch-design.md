# Phase 2 slice 3 — the OCR branch (no-text-layer pages)

**Status:** designed 2026-07-29. Not yet implemented.

Engine decision: `docs/decisions/0005-ocr-engine-selection.md` (RapidOCR / onnxruntime, spiked and
proven to bundle into the frozen build and run offline). Governing design:
`2026-07-22-rebind-design.md`.

## 1. Goal

A page with no text layer is recognized with on-device OCR and enters the existing spine as
ordinary positioned lines, so `profile` → `layout` (XY-cut reading order) → `assemble` → `emit` →
render → validate all run unchanged. `samples/Failure.pdf` (a pure image scan) **converts** instead
of being refused. OCR text carries real per-line confidence, and text the recognizer is not
confident about becomes an honest placeholder, never a guess.

## 2. Dependencies adopted

- **`rapidocr-onnxruntime`** — the OCR engine (ADR 0005). Models ship in-package; CPU; offline.
- **`pypdfium2`** — renders a PDF page to a raster at a chosen DPI. A scanned page is not always a
  single JPEG XObject (CCITT G4, JBIG2, tiled strips are common), so rendering the whole page is
  the robust way to get pixels, rather than extracting one image stream. `pypdfium2` is a
  self-contained wheel (vendored pdfium), bundle-able with no system install.

`packaging/rebind.spec` gains `--collect-all` equivalents for `rapidocr_onnxruntime`, `onnxruntime`,
`cv2`, and `pypdfium2`; `scripts/license_inventory.py` gains their licenses. The `-m packaging`
test is extended to prove the frozen bundle OCRs a scanned page (ADR 0005 proved this for a
standalone probe; the real bundle must show it too).

## 3. Architecture

New module **`ocr.py`**. One responsibility: turn a scanned page into `TextLine` records.

- `render_page_to_image(source, page_number, dpi) -> ndarray` — pypdfium2 rasterization.
- `recognize(image, *, page_number, page_width, page_height) -> list[TextLine]` — runs RapidOCR and
  maps each `(quad, text, confidence)` to a `TextLine`.
- `OcrEngine` — wraps the RapidOCR handle so the (expensive) model load happens once per run.

### 3.1 Coordinate mapping

RapidOCR returns pixel coordinates with the origin at the top-left; the document model uses PDF
points with the origin at the bottom-left. Each quad is reduced to its axis-aligned bounding box and
scaled by `page_width / image_width` (x) and `page_height / image_height` (y), with y flipped
(`y_pt = page_height - y_px * scale`). The rendered image's pixel size is known from the DPI, so the
mapping is exact and deterministic.

### 3.2 TextLine gains an OCR confidence

`TextLine` gains `ocr_confidence: float | None = None` (default `None` for born-digital text, which
is exact by construction). OCR lines set it to RapidOCR's per-line score. `size` is set from the
box height in points, so `profile` can still rank headings by size; `font` is `""` and
`bold`/`italic` are `False` (OCR yields no font metrics — the profile falls back to size-only
heading ranking, which is acceptable and honest).

### 3.3 OCR runs once, streaming preserved for born-digital

The pipeline runs two passes over the pages (profile, then assemble). OCR is expensive (~13s/page),
so it must not run twice, but the born-digital 300-page path must keep its streaming, bounded-memory
property (invariant 5). Resolution: an `OcrCache` created by the pipeline memoizes a scanned page's
recognized `TextLine`s by page number. A generator `ocr_scanned_pages(pages, engine, cache)` yields
each page unchanged if it has a text layer, or with OCR'd lines (from the cache, filling it on first
sight) if it does not. Pass one fills the cache; pass two reuses it. The cache holds only text (no
images), and only for scanned pages — a born-digital document leaves it empty and streams exactly as
before.

## 4. Confidence and the never-fabricate invariant

- A recognized line's node confidence is its OCR confidence (not style-match — style-match is
  meaningless for OCR). The line is flagged `ocr-source` (the same flag slice 2 introduced), so a
  reviewer knows the text is recognizer output.
- A line whose OCR confidence is below `OCR_TEXT_MIN_CONFIDENCE` is **not emitted as text**. It
  becomes a `Placeholder` reading `[text not recoverable from source scan, p. N]`, retaining page
  and bbox — the honest gap the thesis promises, never a low-confidence guess presented as content.
- A scanned page on which OCR finds **no** text at all remains a `no-text-layer` placeholder for
  that page (a blank or unrecoverable scan), exactly as today — OCR is attempted, and its finding
  nothing is reported honestly rather than hidden.

## 5. What changes downstream (and what does not)

- `extract.py` is unchanged: it still yields scanned pages with empty `lines`. OCR is a separate
  stage so extraction keeps no heavy dependency.
- `profile.py`, `layout.py`, `emit.py`, `render.py`, `validate.py` are unchanged — they consume
  `TextLine`s and nodes regardless of origin. This is the branch-agnostic interface the layout
  slice was built against.
- `assemble.py` uses `ocr_confidence` when present (node confidence + the below-threshold
  placeholder rule) and otherwise behaves exactly as now.
- `pipeline.py` no longer refuses a document with no text layer anywhere; it OCRs it. The
  `NoTextLayerError` path is now reached only when OCR also recovers nothing from any page.

## 6. Testing

Fixtures generate a **synthetic scan**: render known text with WeasyPrint, rasterize it, and embed
the raster in a new PDF with **no text layer** (`pdf_image_only_scan`). RapidOCR is deterministic on
fixed input, so the recovered text is asserted to contain the known words.

1. **`ocr.recognize`** on a synthetic scan of known text → `TextLine`s whose text contains the known
   words, with `ocr_confidence` set and bboxes inside the page box.
2. **Coordinate mapping** — a line's bbox is in PDF points, y-up, within `(0,0,width,height)`.
3. **Pipeline** — a synthetic image-only scan (the `Failure.pdf` shape) converts to a tagged PDF/UA
   document instead of raising `NoTextLayerError`; recovered paragraphs carry `ocr-source`.
4. **Below-threshold** — a `TextLine` with a low `ocr_confidence` becomes a `[text not recoverable
   …]` placeholder, not a paragraph (unit test on `assemble`, no OCR needed).
5. **Born-digital unaffected** — a page with a text layer never invokes OCR and its confidence is
   unchanged (the cache stays empty).
6. **Packaging** (`-m packaging`) — the frozen bundle OCRs a scanned page.

Real-sample check (manual, out of CI): `Failure.pdf` converts; the recovered text is compared by
eye to the page.

## 7. Invariants upheld

1. **Never fabricate** — below-threshold recognition becomes an honest placeholder; confidence is
   the real OCR score, never inflated.
2. **Everything has provenance** — every OCR line carries page and bbox (mapped to PDF points).
3. **Determinism scoped to the model** — RapidOCR is deterministic on fixed input; golden/text
   assertions are on the model, never PDF bytes.
4. **No API key, GPU or network** — RapidOCR is CPU/offline with in-package models; pypdfium2 is
   offline (ADR 0005).
5. **No arbitrary limits** — OCR is per page; the cache holds text only, so long scans stay
   tractable and born-digital streaming is untouched.
6. **Bundle-able on Windows** — both dependencies are self-contained wheels, proven to bundle
   (ADR 0005; the packaging test guards it).
