"""On-device OCR for pages with no text layer.

Turns a scanned page into the same `TextLine` records the born-digital path produces, so
`profile`, `layout` and `assemble` consume OCR output unchanged (the branch-agnostic interface the
layout slice was built against). RapidOCR (onnxruntime) runs on CPU with in-package models, so this
needs no API key, GPU or network at runtime -- see docs/decisions/0005-ocr-engine-selection.md.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .extract import TextLine


class OcrEngine:
    """Holds the RapidOCR handle so its (expensive) model load happens once per run.

    RapidOCR is imported lazily inside `__init__` so that importing `rebind.ocr` -- and therefore
    the born-digital path, which never touches OCR -- does not pay the onnxruntime import cost.
    """

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._engine = RapidOCR()

    def __call__(self, image: np.ndarray):
        result, _elapsed = self._engine(image)
        return result or []


def render_page_to_image(source: Path, page_number: int, *, dpi: int = 200) -> np.ndarray:
    """Rasterize one page (1-based) of `source` to an RGB array at `dpi`.

    A scanned page is not always a single extractable image stream (CCITT G4, JBIG2 and tiled
    strips are common), so the whole page is rendered rather than one image XObject pulled out.
    """
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(source))
    try:
        page = document[page_number - 1]
        bitmap = page.render(scale=dpi / 72.0)
        return np.asarray(bitmap.to_pil().convert("RGB"))
    finally:
        document.close()


def recognize(
    image: np.ndarray,
    *,
    page_number: int,
    page_width: float,
    page_height: float,
    engine: OcrEngine,
) -> list[TextLine]:
    """Recognize `image` and return `TextLine`s in PDF-point coordinates.

    RapidOCR yields `(quad, text, confidence)` per line in top-left-origin pixel coordinates. Each
    quad is reduced to its axis-aligned box and mapped to PDF points (y flipped to origin
    bottom-left). `size` is the box height in points, so the typographic profile can still rank
    headings; `font` is empty and bold/italic False because OCR yields no font metrics.
    """
    height_px, width_px = image.shape[:2]
    if width_px == 0 or height_px == 0:
        return []
    scale_x = page_width / width_px
    scale_y = page_height / height_px

    lines: list[TextLine] = []
    for quad, text, confidence in engine(image):
        cleaned = text.strip()
        if not cleaned:
            continue
        xs = [point[0] for point in quad]
        ys = [point[1] for point in quad]
        x0 = min(xs) * scale_x
        x1 = max(xs) * scale_x
        # y flip: the smallest pixel-y is the visual top, which is the largest PDF-y.
        y_top = page_height - min(ys) * scale_y
        y_bottom = page_height - max(ys) * scale_y
        lines.append(
            TextLine(
                text=cleaned,
                page=page_number,
                bbox=(x0, y_bottom, x1, y_top),
                font="",
                size=max(y_top - y_bottom, 1.0),
                bold=False,
                italic=False,
                ocr_confidence=float(confidence),
            )
        )
    return lines
