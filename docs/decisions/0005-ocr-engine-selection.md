# 0005 — OCR engine selection (feasibility spike)

**Status:** Proposed. RapidOCR (onnxruntime) passed a Phase-0-style feasibility spike; adopting it
is a pending decision because of its bundle-size cost (see Consequences).

**Date:** 2026-07-29

## Context

The scanned branch needs to recognize text on pages with no text layer (`samples/Failure.pdf`).
The engine is constrained hard by the project invariants:

- **Invariant 4** — no API key, no GPU, no network at runtime. Rules out cloud OCR and any engine
  that downloads models on first use.
- **Invariant 6** — every dependency must bundle on Windows with no user-performed system install.
  Rules out an engine that assumes a system-wide native install the user must perform.

Before committing, we ran a spike to prove a candidate actually bundles into the frozen build and
runs offline on a real scan — the same discipline that de-risked WeasyPrint in Phase 0.

## Candidates considered

- **RapidOCR (`rapidocr-onnxruntime`)** — PP-OCRv4 models run on `onnxruntime` (CPU). pip-only,
  models shipped inside the package. **Spiked.**
- **Tesseract** — mature, but distributed as a native `tesseract.exe` + `tessdata`, not a wheel;
  bundling means vendoring a separate binary. Weaker on degraded scans. Held as fallback.
- **EasyOCR / docTR** — PyTorch/TF backends, large, GPU-oriented. Poor fit for the no-GPU /
  bundle-size constraints. Rejected without spiking.

## The spike (RapidOCR)

Ran against `samples/Failure.pdf` — a 3-page book scan stored as full-page JPEGs (2509×3923,
DCTDecode). The JPEG was pulled straight from the PDF image XObject; no re-rasterization needed.

**Install:** `rapidocr-onnxruntime` pulled 11 packages (onnxruntime, numpy, opencv-python, shapely,
pyclipper, protobuf, …) — **all prebuilt wheels, zero compilation** on Windows/Python 3.12.

**Offline & quality (dev venv):** the three ONNX models (det 4.6MB, rec 10.6MB, cls 0.6MB) ship
*inside* the package, so there is no runtime download. On page 1: **45 lines, mean confidence 0.99**
(min 0.80), ~13.4s on CPU. The recognized text is accurate — "Creating a Fearless Organization",
"Table 7.5 Productive Responses to Different Types of Failure", "Preventable / Complex / Intelligent
Failure" — and dramatically better than the garbage hidden-OCR layer some scans already carry
("TllE WI1EA1\"0.1Y BUT,LETIN" for "THE WHEATON BULLETIN" in the 1905 bulletin).

**Output shape:** each line is `(4-point polygon, text, confidence)`. The quad is skew-aware (a
rotated box), which gives both bounding-box provenance and a skew signal for later dewarping. The
per-line confidence is exactly the real OCR confidence that Phase 1 §5.2 anticipated — it maps
directly onto `Node.confidence`, and text below threshold can become the honest placeholder the
never-fabricate invariant requires.

**Frozen build (the crux):** a minimal PyInstaller onedir bundle
(`--collect-all rapidocr_onnxruntime --collect-all onnxruntime --collect-all cv2`) built cleanly,
and the **frozen exe OCR'd the scan offline** — `FROZEN-OCR-OK lines=45 seconds=13.3`, with the
models present under `_internal/rapidocr_onnxruntime/models/`. No `WEASYPRINT_DLL_DIRECTORIES`-class
surprise appeared: `--collect-all` was sufficient.

## Decision

**Recommend RapidOCR (onnxruntime) as the OCR engine**, with Tesseract as a documented fallback if
its costs prove unacceptable. It satisfies every hard invariant and its output shape (text + box +
confidence) is a clean fit for the document model.

## Consequences

- **Bundle size (+~239MB).** The OCR-only frozen probe is 239MB, dominated by onnxruntime. Added to
  Rebind's existing WeasyPrint+GTK bundle this roughly triples the installer. For libraries on poor
  connections downloading the installer, this is the main cost — and the reason this ADR is
  *Proposed*, not *Accepted*, pending Thomas's call. Mitigations to explore: onnxruntime is
  larger than needed (there may be a slimmer build), and the models can be quantized.
- **Speed (~13s/page CPU).** A 300-page scan is ~1 hour. Acceptable for a batch tool and only paid
  on scanned pages (the born-digital path is untouched), but worth optimizing (downscale before
  detection, smaller detection model).
- **New heavy dependencies** (onnxruntime, opencv, numpy, shapely) enter the dependency and license
  inventory; `packaging/rebind.spec` and `scripts/license_inventory.py` will need updating when
  adopted.
- The spike deps were **not** added to `pyproject.toml`; the venv was restored to committed state.
  Adoption is a separate change (pyproject + spec + license inventory + the OCR branch itself).

## Interface this implies for the OCR branch

`extract.py` gains a scanned-page path that, for a page with no text layer, rasterizes/loads the
page image, runs RapidOCR, and yields the same `TextLine` records the born-digital path produces
(text, page, bbox, style-less) plus a real confidence — so `profile`, `layout` (XY-cut reading
order already works on any positioned lines) and `assemble` consume OCR output unchanged. This is
the branch-agnostic interface the layout slice was designed against.
