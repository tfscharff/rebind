"""Measure the colour contrast of a page's text against what is actually behind it.

Adobe's accessibility checker reports "Colour contrast" as *needs manual check* on every document
it ever sees -- it never passes it automatically, because a machine that only reads the file cannot
know what a human perceives. That leaves the person remediating a 300-page catalogue with a check
they are expected to sign off on and no evidence to sign off with.

Rebind can produce that evidence, because contrast is measurable. It is not inferred from the
content stream's colour operators: text can sit on a filled box, a scanned photograph, a shaded
table row or a watermark, and the operator only names the ink. What matters to a reader is the
rendered result, so that is what is sampled -- the actual pixels behind each line of text, from
the same rasterizer already used for OCR.

The measurement follows WCAG 2.1 SC 1.4.3 (Contrast, Minimum): relative luminance per WCAG's own
formula, a 4.5:1 threshold for body text and 3:1 for large text (>= 18pt, or >= 14pt bold).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .extract import Page, TextLine

# WCAG 2.1 SC 1.4.3 thresholds and the definition of "large" text (SC 1.4.3 names 18pt, or 14pt
# bold, measured as the font's own point size).
AA_NORMAL_RATIO = 4.5
AA_LARGE_RATIO = 3.0
LARGE_TEXT_PT = 18.0
LARGE_BOLD_TEXT_PT = 14.0

# Sampling. Text is anti-aliased, so the pixels along a glyph's edge are blends of ink and paper
# and belong to neither: a plain mean of the crop would report the blend, not the colours a reader
# actually sees. Taking a low and a high luminance percentile lands on the two real populations and
# steps over the blend between them.
INK_PERCENTILE = 5
PAPER_PERCENTILE = 95
# A crop needs enough pixels for those percentiles to mean anything; below this the line is a stray
# mark, not measurable text.
MIN_SAMPLE_PIXELS = 40
# 100 DPI is plenty: this measures colour, not shape, and every page gets rendered.
SAMPLE_DPI = 100


@dataclass(frozen=True)
class LineContrast:
    """One line of text, its measured contrast ratio, and the two colours it was measured from."""

    page: int
    text: str
    bbox: tuple[float, float, float, float]
    ratio: float
    ink: tuple[int, int, int]
    paper: tuple[int, int, int]
    required: float

    @property
    def passes(self) -> bool:
        return self.ratio >= self.required


@dataclass(frozen=True)
class ContrastReport:
    measured: int
    failures: tuple[LineContrast, ...]
    lowest: LineContrast | None

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def pages(self) -> tuple[int, ...]:
        return tuple(sorted({line.page for line in self.failures}))


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG 2.1's relative luminance of an 8-bit sRGB colour."""
    channels = []
    for value in rgb:
        c = value / 255.0
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """WCAG 2.1's contrast ratio between two colours -- always >= 1.0, order-independent."""
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def required_ratio(line: TextLine) -> float:
    """The AA threshold this line must meet: 3:1 if it is large text, 4.5:1 otherwise."""
    if line.size >= LARGE_TEXT_PT or (line.bold and line.size >= LARGE_BOLD_TEXT_PT):
        return AA_LARGE_RATIO
    return AA_NORMAL_RATIO


def _sample_line(page_image: np.ndarray, line: TextLine, page: Page
                 ) -> tuple[tuple[int, int, int], tuple[int, int, int]] | None:
    """The (ink, paper) colours behind one line, or None if there is too little to measure."""
    height_px, width_px = page_image.shape[:2]
    sx, sy = width_px / page.width, height_px / page.height
    x0, y0, x1, y1 = line.bbox
    # PDF y is bottom-up, image y is top-down.
    left, right = int(x0 * sx), int(np.ceil(x1 * sx))
    top, bottom = int((page.height - y1) * sy), int(np.ceil((page.height - y0) * sy))
    left, top = max(left, 0), max(top, 0)
    right, bottom = min(right, width_px), min(bottom, height_px)
    if right - left < 1 or bottom - top < 1:
        return None
    crop = page_image[top:bottom, left:right].reshape(-1, 3)
    if len(crop) < MIN_SAMPLE_PIXELS:
        return None
    luminance = crop @ np.array([0.2126, 0.7152, 0.0722])
    order = np.argsort(luminance)
    ink_index = order[int(len(order) * INK_PERCENTILE / 100)]
    paper_index = order[min(int(len(order) * PAPER_PERCENTILE / 100), len(order) - 1)]
    return (tuple(int(v) for v in crop[ink_index]), tuple(int(v) for v in crop[paper_index]))


def measure(source: Path, pages: list[Page], *, dpi: int = SAMPLE_DPI) -> ContrastReport:
    """Measure every text line in `pages` against the rendered page behind it.

    Pages with no text are skipped outright (nothing to measure, and no render paid for).
    """
    from .ocr import render_page_to_image

    measured = 0
    failures: list[LineContrast] = []
    lowest: LineContrast | None = None
    for page in pages:
        if not page.lines:
            continue
        page_image = render_page_to_image(source, page.number, dpi=dpi)
        for line in page.lines:
            if not line.text.strip():
                continue
            sampled = _sample_line(page_image, line, page)
            if sampled is None:
                continue
            ink, paper = sampled
            result = LineContrast(
                page=page.number, text=line.text.strip()[:80], bbox=line.bbox,
                ratio=round(contrast_ratio(ink, paper), 2), ink=ink, paper=paper,
                required=required_ratio(line),
            )
            measured += 1
            if lowest is None or result.ratio < lowest.ratio:
                lowest = result
            if not result.passes:
                failures.append(result)
    return ContrastReport(measured=measured, failures=tuple(failures), lowest=lowest)
