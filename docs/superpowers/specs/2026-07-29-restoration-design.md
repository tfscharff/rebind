# Phase 2 slice 4 — image restoration (deskew / denoise) before OCR

**Status:** designed 2026-07-29. Not yet implemented.

Governing design: `2026-07-22-rebind-design.md`. Builds on the OCR branch
(`2026-07-29-ocr-branch-design.md`).

## 1. Goal

Preprocess a scanned page image before OCR so recognition is more accurate on degraded scans:
correct page skew and remove speckle. Restoration runs between rasterization and recognition in the
OCR branch and is otherwise invisible — it improves the recognized text, changes no interface.

This first restoration slice covers **deskew** (the highest-value, lowest-risk correction) and a
gentle **denoise**. Full **dewarp** of page curvature (a book scanned flat at the spine, a warped
bitmap) is deliberately deferred: it needs either a learned dewarping model or a page-grid estimator
and is a later slice. Deskew corrects in-plane rotation, which is the most common scan defect and is
a well-posed, deterministic geometric problem.

## 2. Dependency

**None new.** OpenCV (`cv2`) is already bundled — it arrived with RapidOCR (ADR 0005) and is in the
license inventory. Restoration is pure OpenCV/NumPy.

## 3. Architecture

New module **`restoration.py`**.

- `deskew(image: ndarray) -> tuple[ndarray, float]` — estimate the dominant text skew angle and, if
  it exceeds a threshold, rotate the image to correct it. Returns the corrected image and the angle
  applied (0.0 when no correction was made), so the angle can be recorded as provenance/diagnostics.
- `denoise(image: ndarray) -> ndarray` — a gentle speckle removal that does not blur text.
- `restore(image: ndarray) -> RestorationResult` — runs the pipeline and returns the restored image
  plus what it did (angle, whether denoise ran).

`ocr.ocr_pages` calls `restore` on the rendered page image before `recognize`. The skew angle is
attached to that page's OCR provenance (a page-level note), so a reviewer can see the page was
rotated N degrees.

### 3.1 Deskew algorithm

1. Grayscale, then Otsu-threshold to a binary text mask (text foreground).
2. Take the coordinates of foreground pixels and fit `cv2.minAreaRect`; its angle is the dominant
   orientation of the text block. Normalize OpenCV's `[-90, 0)` convention to a signed skew in
   `(-45, 45]`.
3. If `|angle| >= DESKEW_MIN_ANGLE_DEG` (default 0.3°), rotate the *original* (not the binarized)
   image by `-angle` with `cv2.warpAffine`, white border fill, cubic interpolation. Otherwise return
   the image untouched — a near-straight page must not be needlessly resampled.

minAreaRect over the text mask is the classic, dependency-free deskew estimator and is fully
deterministic. Hough-line estimation was considered and rejected: it needs a line-length threshold
that is document-dependent, whereas minAreaRect has none.

### 3.2 Denoise

A 3×3 median filter (`cv2.medianBlur`) removes isolated speckle without the edge-blurring that a
Gaussian blur or non-local-means would inflict on small text. It is applied gently and always; on a
clean scan a 3×3 median is close to a no-op. Anything heavier (morphological opening, adaptive
thresholding to pure black-and-white) is deferred — RapidOCR's own preprocessing already binarizes
internally, and over-cleaning risks erasing thin strokes, which would *cause* the fabrication the
project forbids (a recognizer guessing at a stroke Rebind deleted).

## 4. Confidence and honesty

Restoration changes pixels, not text — it feeds OCR, and OCR still reports its own confidence, which
already governs the never-fabricate placeholder rule (OCR-branch spec §4). Restoration therefore
introduces no new fabrication surface. The skew angle applied is recorded as diagnostics so a
reviewer can tell a page was rotated; nothing about restoration is silent.

Restoration must never *reduce* recognizability on a good scan. The deskew threshold and the
gentleness of the denoise are chosen so a clean page is left essentially unchanged; §5 tests this
directly (a clean synthetic scan's recovered text is unchanged with restoration on).

## 5. Testing

1. **Deskew estimation** — a synthetic text image rotated by a known angle (e.g. 7°) is deskewed to
   within a small tolerance of 0°.
2. **Deskew improves OCR** — a synthetic scan rotated enough to hurt recognition is recovered
   correctly after `restore`, and the same text is *not* fully recovered without it (guarding that
   the step does real work).
3. **Clean scan is left alone** — an unrotated synthetic scan runs through `restore` with a
   near-zero reported angle, and OCR still recovers the known text (no regression).
4. **Denoise keeps text** — speckle added to a synthetic scan is reduced without losing the text
   (recovered text unchanged).
5. **Determinism** — `restore` on the same input yields an identical array.

Fixtures reuse the synthetic-scan generator from the OCR branch, adding a rotation/speckle helper.
Real degraded scans (out of CI) are the eventual tuning ground; the samples on hand are relatively
clean, so this slice is validated primarily on synthetic degradation and checked not to regress the
real samples.

## 6. Invariants upheld

1. **Never fabricate** — restoration only conditions pixels for the recognizer; text still traces to
   OCR with real confidence, and over-cleaning that could erase strokes is deliberately avoided.
2. **Everything has provenance** — the skew angle applied is recorded per page.
3. **Determinism scoped to the model** — the CV pipeline is deterministic on fixed input.
4. **No API key, GPU or network** — pure OpenCV/NumPy on CPU.
5. **No arbitrary limits** — per-page, independent of document length.
6. **Bundle-able on Windows** — no new dependency; cv2 is already bundled.
