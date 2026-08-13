"""Finding the pictures on a scanned page, where the file itself contains no pictures."""

from __future__ import annotations

import numpy as np

from rebind.regions import find_picture_regions

PAGE_W, PAGE_H = 612.0, 792.0
PX_W, PX_H = 1224, 1584          # 2x, as a 150-dpi-ish render of a Letter page


def _blank_page() -> np.ndarray:
    return np.full((PX_H, PX_W, 3), 255, dtype=np.uint8)


def _draw(image: np.ndarray, box_pt: tuple, value: int = 30) -> None:
    """Ink a rectangle given in PDF points (y-up) onto a top-down image."""
    x0, y0, x1, y1 = box_pt
    sx, sy = PX_W / PAGE_W, PX_H / PAGE_H
    image[int((PAGE_H - y1) * sy):int((PAGE_H - y0) * sy),
          int(x0 * sx):int(x1 * sx)] = value


def _find(image, text_boxes):
    return find_picture_regions(image, text_boxes, page_width=PAGE_W, page_height=PAGE_H)


def test_a_picture_on_a_scanned_page_is_found():
    # The case that was invisible: a diagram printed on a scanned sheet is not an object in the
    # file, it is a patch of the same raster. It has to be found from the pixels or not at all.
    page = _blank_page()
    _draw(page, (100, 500, 500, 700))          # the picture
    _draw(page, (72, 100, 540, 112))           # a line of body text below it

    regions = _find(page, [(72, 100, 540, 112)])

    assert len(regions) == 1, regions
    x0, y0, x1, y1 = regions[0].bbox
    assert 90 < x0 < 110 and 490 < x1 < 510
    assert 490 < y0 < 510 and 690 < y1 < 710


def test_body_text_is_never_mistaken_for_a_picture():
    # A page of prose has plenty of ink. Masking the recognized lines is what keeps it from
    # reading as one big illustration -- getting this wrong would announce a picture on every page.
    page = _blank_page()
    boxes = []
    y = 700.0
    while y > 120:
        box = (72, y, 540, y + 11)
        _draw(page, box)
        boxes.append(box)
        y -= 16

    assert _find(page, boxes) == []


def test_specks_and_scanner_noise_are_not_pictures():
    page = _blank_page()
    for x in range(80, 520, 40):
        _draw(page, (x, 400, x + 3, 403))     # dust

    assert _find(page, []) == []


def test_the_scan_itself_is_not_a_picture_on_the_page():
    # A uniformly grey photocopy inks the whole sheet. That is the page, not something printed on
    # it, and reporting it as a figure would put "describe this" against every scanned page.
    page = np.full((PX_H, PX_W, 3), 210, dtype=np.uint8)
    _draw(page, (72, 100, 540, 112))

    for region in _find(page, [(72, 100, 540, 112)]):
        x0, y0, x1, y1 = region.bbox
        assert (x1 - x0) * (y1 - y0) < 0.85 * PAGE_W * PAGE_H, region


def test_the_scanners_own_dark_edge_never_claims_the_picture_beside_it():
    # The real miss, from page 4 of a scanned book: the dark strip the scanner leaves down the
    # spine and across the top of the sheet is one L-shaped blob. Its ink is thin, so the density
    # rule that catches a solid black border let it through -- and its BOUNDING BOX covered the
    # left 41% of the page, which then vetoed the photograph inside it as an overlap. The first
    # picture on the page disappeared because of a mark that is not on the page at all.
    page = _blank_page()
    _draw(page, (0, 0, 24, PAGE_H))            # the spine shadow, full height
    _draw(page, (0, PAGE_H - 24, 250, PAGE_H))  # ...turning the corner along the top edge
    picture = (100, 300, 400, 650)
    _draw(page, picture)

    regions = _find(page, [])

    assert len(regions) == 1, regions
    x0, y0, x1, y1 = regions[0].bbox
    assert 90 < x0 < 115 and 385 < x1 < 415, "the photograph, not the sheet's edge"


def test_two_separate_pictures_stay_separate():
    page = _blank_page()
    _draw(page, (72, 480, 280, 700))
    _draw(page, (330, 480, 540, 700))

    regions = _find(page, [])

    assert len(regions) == 2, regions
    assert regions[0].bbox[0] < regions[1].bbox[0]


def test_an_empty_page_yields_nothing():
    assert _find(_blank_page(), []) == []
    assert find_picture_regions(None, [], page_width=PAGE_W, page_height=PAGE_H) == []


def test_two_pictures_side_by_side_are_found_left_to_right():
    # Two pictures on one row are never level to the point. On a real scanned page a photograph and
    # the coin printed beside it differed by a few points, and ordering on the top edge alone put
    # the right-hand one first -- so a screen reader met the page's pictures back to front.
    page = _blank_page()
    _draw(page, (72, 480, 280, 700))            # the left-hand picture...
    _draw(page, (330, 484, 540, 704))           # ...and the right-hand one, a shade higher

    regions = _find(page, [])

    assert len(regions) == 2, regions
    assert regions[0].bbox[0] < regions[1].bbox[0], "the left-hand picture is read first"
