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

The ink is the exception: where the page declares its text colour, that declaration is used rather
than sampled, because a glyph stroke is only a pixel or two wide and almost every pixel of it is an
anti-aliased blend. Sampling alone read a real sample's pure-black body text as mid-grey and its
pure-blue links as lilac, reporting forty-three failures in a document that has none. An OCR'd page
declares nothing, so there the sample is all there is.

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
# actually sees. Taking an extreme luminance percentile from each end lands on the two real
# populations and steps over the blend between them.
#
# The percentiles are deliberately close to the extremes. Body text is thin -- at the sampling
# resolution a 10pt stroke is only a pixel or two wide -- so *most* of a glyph's pixels are partly
# blended, and a 5th percentile lands inside that blend and reports the text as lighter than it is.
# Measured: #a8a8a8 text read back as #878787, a whole contrast point adrift, which both
# over-reports failures and makes a corrected colour look uncorrected. 1% is far enough into the
# tail to be real ink while still discarding a stray dark speck in a scan.
INK_PERCENTILE = 1
# A crop needs enough pixels for those percentiles to mean anything; below this the line is a stray
# mark, not measurable text.
MIN_SAMPLE_PIXELS = 40
# A background counts as flat when at least this fraction of the line box's pixels sit within
# FLAT_BACKGROUND_TOLERANCE luminance of the background colour. Ordinary text leaves well over half
# its box as untouched paper; a photograph or gradient behind the text does not.
FLAT_BACKGROUND_TOLERANCE = 40.0
MIN_FLAT_BACKGROUND_FRACTION = 0.5
# At or below this ratio the text is the same colour as what is behind it: invisible, not faint.
INVISIBLE_RATIO = 1.1
# 150 DPI rather than 100: at 100 a small glyph is so thin that even its core pixels are blends.
# Still cheap -- a 28-page document measures in about a second.
SAMPLE_DPI = 150


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

    # The paper is the crop's *median* pixel, not its lightest. Text occupies a minority of the
    # pixels in its own box, so the median is whatever it sits on -- and that holds for white text
    # on a dark panel just as well as black text on white. Taking the lightest instead reported the
    # real sample's white figure callouts as white-on-white, a 1:1 "failure" for text that is
    # perfectly legible against the dark artwork behind it.
    paper_index = order[len(order) // 2]
    paper = tuple(int(v) for v in crop[paper_index])

    # And the background has to actually be uniform enough for one colour to describe it. Over a
    # photograph or a gradient no pair of colours says what a reader sees, so the line is left to
    # the human rather than scored against a fiction.
    near_paper = np.abs(luminance - float(luminance[paper_index])) <= FLAT_BACKGROUND_TOLERANCE
    if near_paper.mean() < MIN_FLAT_BACKGROUND_FRACTION:
        return None

    ink_index = order[int(len(order) * INK_PERCENTILE / 100)]
    return (tuple(int(v) for v in crop[ink_index]), paper)


def _inside(bbox: tuple[float, float, float, float],
            boxes: tuple[tuple[float, float, float, float], ...]) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)


def measure(source: Path, pages: list[Page], *, dpi: int = SAMPLE_DPI,
            figures: dict[int, tuple] | None = None) -> ContrastReport:
    """Measure every text line in `pages` against the rendered page behind it.

    Text lying inside a figure is skipped. A panel label burnt into a photograph ("A", "petri
    dish") is part of the image, not of the document's text: it is described by the figure's alt
    text, a reader cannot restyle it, and sampling it measures the photograph's own colours rather
    than any choice the document made. Confirmed on the real sample, where the only three
    implausible failures were exactly that -- labels inside a micrograph.

    Pages with no text are skipped outright (nothing to measure, and no render paid for).
    """
    from .ocr import render_page_to_image

    figures = figures or {}
    measured = 0
    failures: list[LineContrast] = []
    lowest: LineContrast | None = None
    for page in pages:
        # Only text whose colour the page itself declares is measurable. Text recovered by OCR
        # from a scan declares nothing -- it *is* the picture, and sampling it measures the
        # photocopier rather than any colour decision the document made, exactly as for text
        # inside a figure. It also cannot be corrected: repainting it would mean altering the scan.
        # Rendering the page at all is skipped when there is nothing on it to measure.
        measurable = [line for line in page.lines if line.color is not None and line.text.strip()]
        if not measurable:
            continue
        page_image = render_page_to_image(source, page.number, dpi=dpi)
        in_figures = tuple(figures.get(page.number, ()))
        for line in measurable:
            if _inside(line.bbox, in_figures):
                continue
            sampled = _sample_line(page_image, line, page)
            if sampled is None:
                continue
            # The ink is taken from the page's own declaration when it makes one -- exact, where
            # sampling a thin anti-aliased glyph is not. The paper is always sampled, because what
            # is *behind* the text (a filled box, a shaded row, a photograph) is a fact about the
            # rendered page that no colour operator states.
            _sampled_ink, paper = sampled
            ink = line.color
            result = LineContrast(
                page=page.number, text=line.text.strip()[:80], bbox=line.bbox,
                ratio=round(contrast_ratio(ink, paper), 2), ink=ink, paper=paper,
                required=required_ratio(line),
            )
            if result.ratio <= INVISIBLE_RATIO:
                # Ink and paper are the same colour: the text is not rendered at all. Confirmed in
                # a real publisher sample, which carries white-on-white labels above its diagrams.
                # That is not a contrast problem -- there is nothing on the page to perceive, and
                # SC 1.4.3 is about text a reader can see. Reporting it would invite a "fix" that
                # made deliberately hidden text visible, which is a far bigger change than the one
                # being asked for.
                continue
            measured += 1
            if lowest is None or result.ratio < lowest.ratio:
                lowest = result
            if not result.passes:
                failures.append(result)
    return ContrastReport(measured=measured, failures=tuple(failures), lowest=lowest)


def summarize(report: ContrastReport, *, darkened: int = 0) -> dict:
    """The contrast section of the review: the measurement, and what failed it.

    `darkened` is how many text colours were corrected on this run (0 unless the user asked), so
    the app can say what it changed rather than silently showing a document that now passes.
    """
    def entry(line: LineContrast) -> dict:
        return {
            "page": line.page, "text": line.text, "ratio": line.ratio,
            "required": line.required,
            "ink": "#%02x%02x%02x" % line.ink, "paper": "#%02x%02x%02x" % line.paper,
        }

    return {
        "measured": report.measured,
        "ok": report.ok,
        "darkened": darkened,
        "pages": list(report.pages),
        "lowest": entry(report.lowest) if report.lowest else None,
        "failures": [entry(line) for line in report.failures],
    }
