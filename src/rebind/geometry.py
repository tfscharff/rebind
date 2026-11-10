"""Small box arithmetic shared by the page-structure modules. Boxes are in PDF points,
(x0, y0, x1, y1), y upwards.
"""

from __future__ import annotations

from .extract import TextLine

BBox = tuple[float, float, float, float]


def _overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _horizontally_overlaps(a: tuple[float, float, float, float],
                           b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and b[0] < a[2]


def _center_in_box(bbox: tuple, box: tuple) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def _center_in_any(bbox: tuple, boxes) -> bool:
    return any(_center_in_box(bbox, box) for box in boxes)


def _bounds(boxes) -> tuple[float, float, float, float]:
    """The box around a set of boxes."""
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _on_page(bbox: tuple, width: float, height: float) -> dict:
    """A box as the editor places it: left, top, width and height as percentages of the page,
    measured from its top-left corner."""
    x0, y0, x1, y1 = bbox
    return {"left": round(100 * x0 / width, 2), "top": round(100 * (height - y1) / height, 2),
            "width": round(100 * (x1 - x0) / width, 2), "height": round(100 * (y1 - y0) / height, 2)}


def _area(bbox: tuple) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _box(members: list[int], lines: list[TextLine]) -> tuple[float, float, float, float]:
    return _bounds(lines[i].bbox for i in members)
