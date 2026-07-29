"""Image restoration: condition a scanned page before OCR.

Deskew (correct in-plane rotation) and a gentle denoise, run between rasterization and recognition
in the OCR branch. Restoration only conditions pixels for the recognizer; the text still traces to
OCR with real confidence, so this introduces no fabrication surface (invariant 1). Pure OpenCV /
NumPy on CPU -- no new dependency (cv2 arrived with the OCR engine, ADR 0005).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Below this estimated skew (degrees) a page is left untouched -- a near-straight page must not be
# needlessly resampled, which would soften text for no benefit.
DESKEW_MIN_ANGLE_DEG = 0.3
# A skew estimate larger than this is not believed: text-mask minAreaRect can latch onto a figure
# or table border and report a wild angle. Real page skew is small; refuse to "correct" beyond this.
DESKEW_MAX_ANGLE_DEG = 15.0
# Median-filter kernel for speckle removal. 3x3 clears isolated specks without the edge-blurring a
# Gaussian or non-local-means would inflict on small text.
DENOISE_KERNEL = 3


@dataclass
class RestorationResult:
    image: np.ndarray
    skew_angle: float   # degrees applied (0.0 if none)
    denoised: bool


def _estimate_skew(image: np.ndarray) -> float:
    """Dominant text skew in degrees, signed, in roughly (-45, 45].

    Otsu-threshold to a foreground mask, then fit a minimum-area rectangle over the foreground
    pixels. Its orientation is the dominant direction of the text block. OpenCV reports the angle
    in [-90, 0); normalize so a small clockwise/counter-clockwise skew maps to a small signed value.
    """
    import cv2

    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
    # Text is dark on light; invert so foreground (text) is white for THRESH_BINARY + Otsu.
    _threshold, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = cv2.findNonZero(mask)
    if coords is None:
        return 0.0
    angle = cv2.minAreaRect(coords)[-1]
    # Normalize OpenCV's [-90, 0) (older) / [0, 90) (newer) convention to a small signed skew.
    if angle < -45:
        angle += 90
    elif angle > 45:
        angle -= 90
    return float(angle)


def deskew(image: np.ndarray) -> tuple[np.ndarray, float]:
    """Return (possibly-rotated image, angle applied in degrees).

    The angle is applied only when it is both large enough to matter (>= DESKEW_MIN_ANGLE_DEG) and
    small enough to be believable (<= DESKEW_MAX_ANGLE_DEG); otherwise the image is returned
    unchanged and the angle is 0.0.
    """
    import cv2

    skew = _estimate_skew(image)
    if abs(skew) < DESKEW_MIN_ANGLE_DEG or abs(skew) > DESKEW_MAX_ANGLE_DEG:
        return image, 0.0
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), skew, 1.0)
    border = (255, 255, 255) if image.ndim == 3 else 255
    rotated = cv2.warpAffine(image, matrix, (w, h), flags=cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=border)
    return rotated, skew


def denoise(image: np.ndarray) -> np.ndarray:
    """Gentle speckle removal (3x3 median). Close to a no-op on a clean scan."""
    import cv2

    return cv2.medianBlur(image, DENOISE_KERNEL)


def restore(image: np.ndarray) -> RestorationResult:
    """Deskew then denoise. Deterministic on fixed input."""
    deskewed, angle = deskew(image)
    cleaned = denoise(deskewed)
    return RestorationResult(image=cleaned, skew_angle=angle, denoised=True)
