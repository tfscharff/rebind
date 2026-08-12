"""Find the pictures on a page that has no pictures in it.

A born-digital page places its images as objects, and `_page_figures` reads them straight off the
page. A scan has none: the whole sheet is one photograph, and a diagram printed on that sheet is
not a separate thing in the file at all -- it is a patch of the same raster. So every illustration
in a scanned book was invisible to Rebind, which is the honest reason figures were being missed.

They are found here the way a reader finds them: as a region of the page carrying ink that is not
text. OCR already says where the words are; what is left is masked, closed up into blobs, and any
blob big and solid enough to be a picture is one.

Deliberately conservative, for the usual reason -- a missed figure is a figure the user marks by
hand, and a fabricated one is a picture that does not exist being announced to a screen reader:

* it has to be **big** (`MIN_REGION_COVERAGE` of the page), so specks, staple marks, dust and
  scanner noise cannot become figures;
* it has to be **solid** -- at least `MIN_INK_DENSITY` of its own box inked, or it is a stray mark
  or the ruled lines of a table. There is deliberately no upper bound: a photograph is *supposed*
  to be solid ink, and an earlier limit of 0.95 rejected every one of them;
* it must not be **mostly text**: a region overlapping recognized lines by more than
  `MAX_TEXT_OVERLAP` is a paragraph the mask failed to cover, not a diagram;
* it must not hug the **page edge**, where a scan's dark border lives.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# A region smaller than this fraction of the page is not a figure worth announcing; larger than
# this is the scan itself (the page's own background), not something printed on it.
MIN_REGION_COVERAGE = 0.015
MAX_REGION_COVERAGE = 0.85
# How much of its own bounding box a region must ink to be a picture rather than a stray mark.
MIN_INK_DENSITY = 0.04
# A near-solid block pressed against three or more page edges is the scan's own border -- the
# shadow at the spine, the dark strip past the edge of the platen -- not something printed on the
# sheet. Density alone cannot tell the two apart, because a photograph is solid ink too.
BORDER_EDGES = 3
BORDER_MIN_DENSITY = 0.9
# A region this much covered by recognized text is text, whatever shape its ink makes.
MAX_TEXT_OVERLAP = 0.30
# Text boxes are grown by this (in page points) before masking, so ascenders, descenders and the
# recognizer's slightly tight boxes do not leak ink into the picture hunt.
TEXT_MASK_PAD_PT = 2.5
# Ink is closed up by roughly this fraction of the page's smaller side, which bridges the white
# gaps inside a line drawing without welding two separate figures into one.
CLOSE_FRACTION = 0.02
# Work at this width; a scan renders far larger than anything this needs, and the cost is real.
WORK_WIDTH_PX = 900
# Anything whose box comes within this fraction of the page edge is the scan's own border.
EDGE_MARGIN_FRACTION = 0.02


@dataclass(frozen=True)
class Region:
    """A picture found on the page, in PDF points, with the fraction of its box that is inked."""

    bbox: tuple[float, float, float, float]
    density: float


def _binary_ink(gray: np.ndarray) -> np.ndarray:
    """Ink as a boolean mask, thresholded against the page's own paper rather than a fixed value:
    a grey photocopy and a clean scan have very different ideas of what "white" is."""
    import cv2

    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _level, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return binary


def find_picture_regions(page_image: np.ndarray, text_boxes: list[tuple], *,
                         page_width: float, page_height: float) -> list[Region]:
    """Regions of `page_image` that carry ink but are not text, as boxes in PDF points.

    `text_boxes` are the recognized lines' boxes in PDF points (PDF's y-up convention, as
    everywhere else in Rebind). The image is top-down, so the two are reconciled here.
    """
    import cv2

    if page_image is None or page_width <= 0 or page_height <= 0:
        return []
    height_px, width_px = page_image.shape[:2]
    if height_px < 10 or width_px < 10:
        return []

    scale = min(1.0, WORK_WIDTH_PX / float(width_px))
    work = cv2.resize(page_image, (max(int(width_px * scale), 1), max(int(height_px * scale), 1)),
                      interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(work, cv2.COLOR_RGB2GRAY) if work.ndim == 3 else work
    h, w = gray.shape[:2]
    ink = _binary_ink(gray)

    # Mask the words out. What remains is everything printed on the page that is not text.
    sx, sy = w / page_width, h / page_height
    text_mask = np.zeros_like(ink)
    for x0, y0, x1, y1 in text_boxes:
        left = int(max((x0 - TEXT_MASK_PAD_PT) * sx, 0))
        right = int(min((x1 + TEXT_MASK_PAD_PT) * sx, w))
        top = int(max((page_height - y1 - TEXT_MASK_PAD_PT) * sy, 0))
        bottom = int(min((page_height - y0 + TEXT_MASK_PAD_PT) * sy, h))
        if right > left and bottom > top:
            text_mask[top:bottom, left:right] = 255
    ink = cv2.bitwise_and(ink, cv2.bitwise_not(text_mask))

    span = max(int(min(h, w) * CLOSE_FRACTION), 3)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (span, span))
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, kernel)

    contours, _hierarchy = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    page_area = float(h * w)
    margin_x, margin_y = w * EDGE_MARGIN_FRACTION, h * EDGE_MARGIN_FRACTION
    out: list[Region] = []
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        coverage = (bw * bh) / page_area
        if not (MIN_REGION_COVERAGE <= coverage <= MAX_REGION_COVERAGE):
            continue
        patch = ink[y:y + bh, x:x + bw]
        density = float(np.count_nonzero(patch)) / float(max(bw * bh, 1))
        if density < MIN_INK_DENSITY:
            continue
        edges = ((x <= margin_x) + (y <= margin_y)
                 + (x + bw >= w - margin_x) + (y + bh >= h - margin_y))
        if edges >= BORDER_EDGES and density >= BORDER_MIN_DENSITY:
            continue        # the sheet's own border, or the scan itself -- not a picture on it
        covered = float(np.count_nonzero(text_mask[y:y + bh, x:x + bw])) / float(max(bw * bh, 1))
        if covered > MAX_TEXT_OVERLAP:
            continue
        # Back to PDF points, y-up.
        out.append(Region(
            bbox=(x / sx, page_height - (y + bh) / sy, (x + bw) / sx, page_height - y / sy),
            density=round(density, 3)))
    out.sort(key=lambda r: (-r.bbox[3], r.bbox[0]))
    return out
