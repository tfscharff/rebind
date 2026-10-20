"""Small box arithmetic shared by the page-structure modules. Boxes are in PDF points,
(x0, y0, x1, y1), y upwards.
"""

from __future__ import annotations

from .extract import TextLine


def _overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _horizontally_overlaps(a: tuple[float, float, float, float],
                           b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and b[0] < a[2]


def _center_in_box(bbox: tuple, box: tuple) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def _boxes_overlap(a: tuple, b: tuple) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _area(bbox: tuple) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _box(members: list[int], lines: list[TextLine]) -> tuple[float, float, float, float]:
    return (min(lines[i].bbox[0] for i in members), min(lines[i].bbox[1] for i in members),
            max(lines[i].bbox[2] for i in members), max(lines[i].bbox[3] for i in members))
