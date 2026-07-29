"""Tests for image restoration (deskew + denoise). Synthetic images only."""

from __future__ import annotations

import numpy as np

from rebind.restoration import deskew, denoise, restore


def _text_image(width=800, height=300):
    """A white image with a few solid black horizontal bars standing in for lines of text."""
    image = np.full((height, width, 3), 255, dtype=np.uint8)
    for row in (80, 140, 200):
        image[row:row + 18, 60:width - 60] = 0
    return image


def _rotate(image, degrees):
    import cv2

    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), degrees, 1.0)
    return cv2.warpAffine(image, matrix, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))


def test_deskew_corrects_a_known_rotation():
    skewed = _rotate(_text_image(), 7.0)
    corrected, angle = deskew(skewed)
    # The applied correction should be close to undoing the 7 degree rotation.
    assert abs(abs(angle) - 7.0) < 1.5, f"estimated angle {angle} not near 7"
    assert corrected.shape == skewed.shape


def test_deskew_leaves_a_straight_page_alone():
    straight = _text_image()
    corrected, angle = deskew(straight)
    assert abs(angle) < 0.3, f"a straight page should not be rotated, got {angle}"
    # Below the threshold the image is returned unchanged (not needlessly resampled).
    assert np.array_equal(corrected, straight)


def test_denoise_removes_speckle_but_keeps_text():
    image = _text_image()
    rng = np.random.default_rng(0)
    speckled = image.copy()
    # scatter black specks on the white background
    ys = rng.integers(0, image.shape[0], size=400)
    xs = rng.integers(0, image.shape[1], size=400)
    speckled[ys, xs] = 0

    cleaned = denoise(speckled)

    # Far fewer stray black pixels than before, but the text bars survive.
    speck_before = int((speckled == 0).sum())
    speck_after = int((cleaned == 0).sum())
    assert speck_after < speck_before
    # The text region is still mostly black (bars preserved).
    assert (cleaned[80:98, 100:700] == 0).mean() > 0.9


def test_restore_is_deterministic():
    image = _rotate(_text_image(), 4.0)
    a = restore(image)
    b = restore(image)
    assert np.array_equal(a.image, b.image)
    assert a.skew_angle == b.skew_angle
