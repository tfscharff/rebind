"""Page layout regions from a document-layout model, as hints for Rebind's structural rules.

PP-DocLayoutV3 (PaddleOCR, Apache-2.0, run on onnxruntime through rapid-layout) looks at a picture
of the page and boxes its regions: running heads and folios, paragraphs, section titles, tables and
their captions, footnotes. That is the judgement the geometric rules find hardest, and it fails in
ways a person then has to fix by hand: a page's furniture tagged as content, a paragraph broken
into one element per line, a table on one column of a two-column page swallowing the lines of the
other column beside it.

What the model contributes is *grouping*, never text. Every word still comes from the text layer
or the recognizer, so a wrong box can only group lines wrongly -- which the editor corrects -- and
cannot put a word on the page that was not there (invariant 1). Regions below MIN_REGION_SCORE are
ignored, and with no model at all (`page_regions` returns None) every rule falls back to what it
did before.

The model file is not in the repository (130 MB, over GitHub's file limit); it is fetched by
`scripts/fetch_models.py` and bundled into the installer. Nothing is downloaded at runtime.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

MODEL_FILE = "pp_doc_layoutv3.onnx"
MODEL_URL = ("https://www.modelscope.cn/models/RapidAI/RapidLayout/resolve/v1.2.0/onnx/"
             "pp_doc_layout/pp_doc_layoutv3.onnx")
MODEL_SHA256 = "250dbad1dfb9e4983fab75e1bf5085cd56ec3f41d5c7d0f8623ec74856e7aa67"

# The model resizes every page to 800 x 800 itself, so a modest render loses nothing.
REGION_DPI = 150
# A box the model is less sure of than this is not used for anything.
MIN_REGION_SCORE = 0.5
# Calling a line page furniture takes it out of what a screen reader reads, so that needs more.
MIN_FURNITURE_SCORE = 0.7

# Running heads, running feet, folios and the logos printed in them.
FURNITURE = frozenset({"header", "footer", "number", "header_image", "footer_image"})
# Blocks of running text, each one paragraph (or one footnote, one caption).
PROSE = frozenset({"text", "abstract", "footnote", "vision_footnote", "reference_content",
                   "aside_text", "content", "algorithm"})
TITLES = frozenset({"doc_title", "paragraph_title"})
CAPTION = "figure_title"
TABLE = "table"
PICTURES = frozenset({"image", "chart"})

_CACHE_PAGES = 5000


@dataclass(frozen=True)
class Region:
    label: str
    bbox: tuple[float, float, float, float]     # PDF points, origin bottom-left
    score: float


@dataclass
class PageHints:
    """What the regions say about one page's lines, keyed by `id(line)`.

    `tables` is None when there is no model, which means "ask the geometric table detector", as
    opposed to an empty dict, which means the model looked and found no table.
    """
    furniture: set[int] = field(default_factory=set)
    blocks: dict[int, int] = field(default_factory=dict)        # prose/title/caption region
    tables: dict[int, int] | None = None                        # table region
    captions: dict[int, int] = field(default_factory=dict)      # a table's caption region
    not_headings: set[int] = field(default_factory=set)


def model_path() -> Path | None:
    """Where the model is: inside the frozen bundle, or in the clone's `models/` folder."""
    if getattr(sys, "frozen", False):
        candidate = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "models"
    else:
        candidate = Path(__file__).resolve().parents[2] / "models"
    path = candidate / MODEL_FILE
    return path if path.is_file() else None


_engine = None
_engine_lock = threading.Lock()
_engine_failed = False
_cache: OrderedDict[tuple, tuple[Region, ...]] = OrderedDict()


def _get_engine():
    global _engine, _engine_failed
    with _engine_lock:
        if _engine is None and not _engine_failed:
            path = model_path()
            if path is None:
                log.warning("layout model %s not found; using geometric rules only", MODEL_FILE)
                _engine_failed = True
                return None
            # rapid-layout announces every step at INFO on a console handler of its own, which
            # it re-arms each time one of its classes is built.
            logging.disable(logging.INFO)
            try:
                from rapid_layout import RapidLayout
                _engine = RapidLayout(model_type="pp_doc_layoutv3", model_dir_or_path=str(path),
                                      conf_thresh=MIN_REGION_SCORE)
            except Exception:
                log.exception("layout model could not be loaded; using geometric rules only")
                _engine_failed = True
            finally:
                logging.disable(logging.NOTSET)
        return _engine


def enabled() -> bool:
    """Whether to use the model at all. REBIND_LAYOUT_MODEL=off turns it off (the test suite does,
    outside the tests that are about it)."""
    return os.environ.get("REBIND_LAYOUT_MODEL", "").strip().lower() not in {"0", "off", "false"}


def detect_regions(image: np.ndarray, page_width: float, page_height: float
                   ) -> tuple[Region, ...] | None:
    """The regions on a rendered page (an RGB array), in PDF points. None when there is no model."""
    engine = _get_engine()
    if engine is None:
        return None
    height_px, width_px = image.shape[:2]
    if not width_px or not height_px:
        return ()
    with _engine_lock:
        result = engine(np.ascontiguousarray(image[:, :, ::-1]))     # the model expects BGR
    sx, sy = page_width / width_px, page_height / height_px
    regions = []
    for box, label, score in zip(result.boxes or [], result.class_names or [],
                                 result.scores or []):
        x0, y0, x1, y1 = (float(v) for v in box)
        regions.append(Region(label=str(label), score=float(score),
                              bbox=(x0 * sx, page_height - y1 * sy,
                                    x1 * sx, page_height - y0 * sy)))
    return tuple(regions)


def page_regions(source: Path, page_number: int, page_width: float, page_height: float,
                 image: np.ndarray | None = None) -> tuple[Region, ...] | None:
    """`detect_regions` for one page of `source`, remembered for as long as the file is unchanged.

    The app rebuilds the whole document every time a person saves a correction, and the model
    costs about two seconds a page -- too much to pay again for a page nobody touched.
    `image` is a render of the page already made (for OCR, say); otherwise one is made here.
    """
    if not enabled() or _get_engine() is None:
        return None
    source = Path(source)
    try:
        stat = source.stat()
        key = (str(source.resolve()), stat.st_size, stat.st_mtime_ns, page_number)
    except OSError:
        key = None
    if key is not None and key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    if image is None:
        from .ocr import render_page_to_image
        image = render_page_to_image(source, page_number, dpi=REGION_DPI)
    try:
        regions = detect_regions(image, page_width, page_height)
    except Exception:      # a model failure must never cost the person their document
        log.exception("layout model failed on page %d; using geometric rules", page_number)
        return None
    if key is not None and regions is not None:
        _cache[key] = regions
        while len(_cache) > _CACHE_PAGES:
            _cache.popitem(last=False)
    return regions


def _center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    return (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2


def _inside(point: tuple[float, float], box: tuple[float, float, float, float]) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _area(box: tuple[float, float, float, float]) -> float:
    return max(box[2] - box[0], 0.0) * max(box[3] - box[1], 0.0)


def _gap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """Distance between two boxes (0 when they touch or overlap)."""
    dx = max(0.0, max(a[0] - b[2], b[0] - a[2]))
    dy = max(0.0, max(a[1] - b[3], b[1] - a[3]))
    return dx + dy


def hints_for(lines, regions: tuple[Region, ...] | None) -> PageHints:
    """Assign each line to the region its centre falls in (the smallest, where boxes nest).

    A figure title becomes a table's caption only when the nearest picture-or-table region to it is
    a table: figure captions already have their own, stricter route to becoming one (they are
    matched to the picture they describe and used as its alt text).

    A line is kept from being a heading only when the model put it in a block of text *with other
    lines* -- run in with a paragraph, it is part of that paragraph whatever its size. On its own in
    a "text" box, the model's word is not enough: on a sparse page it calls a real chapter title
    text about as often as not, and a lost heading costs the reader their navigation.
    """
    if regions is None:
        return PageHints()
    usable = [(index, region) for index, region in enumerate(regions)
              if region.score >= MIN_REGION_SCORE]
    hints = PageHints(tables={})
    prose: set[int] = set()
    hosts = [region for _index, region in usable
             if region.label == TABLE or region.label in PICTURES]

    def captions_a_table(region: Region) -> bool:
        if not hosts:
            return False
        nearest = min(hosts, key=lambda host: _gap(host.bbox, region.bbox))
        return nearest.label == TABLE

    for line in lines:
        point = _center(line.bbox)
        containing = [(index, region) for index, region in usable
                      if _inside(point, region.bbox)]
        if not containing:
            continue
        index, region = min(containing, key=lambda item: _area(item[1].bbox))
        key = id(line)
        if region.label in FURNITURE:
            if region.score >= MIN_FURNITURE_SCORE:
                hints.furniture.add(key)
        elif region.label == TABLE:
            hints.tables[key] = index
        elif region.label in PROSE:
            hints.blocks[key] = index
            prose.add(key)
        elif region.label in TITLES:
            hints.blocks[key] = index
        elif region.label == CAPTION:
            hints.blocks[key] = index
            prose.add(key)
            if captions_a_table(region):
                hints.captions[key] = index
    members: dict[int, int] = {}
    for key in prose:
        members[hints.blocks[key]] = members.get(hints.blocks[key], 0) + 1
    hints.not_headings = {key for key in prose if members[hints.blocks[key]] > 1}
    return hints
