"""On-device OCR for pages with no text layer.

Turns a scanned page into the same `TextLine` records the born-digital path produces, so
`profile`, `layout` and `assemble` consume OCR output unchanged (the branch-agnostic interface the
layout slice was built against). RapidOCR (onnxruntime) runs on CPU with in-package models, so this
needs no API key, GPU or network at runtime -- see docs/decisions/0005-ocr-engine-selection.md.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from .extract import Page, Script, TextLine


class OcrEngine:
    """The RapidOCR handle, loaded once per process and shared.

    The handle is created on first use, not at construction, so a born-digital document -- which
    never OCRs a page -- pays neither the onnxruntime import nor the model-load cost. Constructing
    an `OcrEngine` is therefore cheap; the pipeline can always make one and only pages that need it
    trigger the load. It is shared because the app rebuilds the document on every saved
    correction, and loading the models each time was a second or two of every save.
    """

    _shared = None
    _lock = threading.Lock()

    def __call__(self, image: np.ndarray, **options):
        with OcrEngine._lock:
            if OcrEngine._shared is None:
                from rapidocr_onnxruntime import RapidOCR

                OcrEngine._shared = RapidOCR()
            result, _elapsed = OcrEngine._shared(image, **options)
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
    bottom-left). `size` is the height of the line's body text in points, so the typographic
    profile can still rank headings; `font` is empty and bold/italic False because OCR yields no
    font metrics.

    Two passes over the page: the recognizer's own, then a second over any band of ink it left
    unread (`_read_missed`). Pieces of one line read separately are then put back together, with
    their superscripts and subscripts (`_join_rows`).
    """
    height_px, width_px = image.shape[:2]
    if width_px == 0 or height_px == 0:
        return []
    scale_x = page_width / width_px
    scale_y = page_height / height_px

    ink = _Ink(image)
    reads = _read(engine, image, ink)
    reads += _read_missed(engine, image, ink, reads)
    lines: list[TextLine] = []
    for read in _join_rows(reads):
        x0, top, x1, bottom = read.box
        lines.append(
            TextLine(
                text=read.text,
                page=page_number,
                # y flip: the smallest pixel-y is the visual top, which is the largest PDF-y.
                bbox=(x0 * scale_x, page_height - bottom * scale_y,
                      x1 * scale_x, page_height - top * scale_y),
                font="",
                size=max(read.body * scale_y, 1.0),
                bold=False,
                italic=False,
                ocr_confidence=read.confidence,
                scripts=tuple(Script(start, end, round(float(shift) * scale_y, 2))
                              for start, end, shift in read.scripts),
            )
        )
    return lines


@dataclass
class _Read:
    """One line, or piece of a line, as the recognizer read it, in page pixels (y down)."""

    box: tuple[float, float, float, float]          # left, top, right, bottom
    text: str
    confidence: float
    # Superscripts and subscripts: (start, end, shift) -- the run's characters in `text`, and how
    # far it is raised (positive) or lowered, in pixels.
    scripts: list[tuple[int, int, float]] = field(default_factory=list)
    body: float = 0.0       # the height of the line's body text; its box's height by default
    # The box drawn tight around the ink inside `box`. The recognizer's own box runs well past its
    # text on every side, so it cannot say how big the text is or how far apart two pieces are.
    tight: tuple[float, float, float, float] | None = None

    def __post_init__(self):
        self.body = self.body or self.box[3] - self.box[1]
        self.tight = self.tight or self.box

    @property
    def middle(self) -> float:
        return (self.tight[1] + self.tight[3]) / 2

    @property
    def height(self) -> float:
        return self.tight[3] - self.tight[1]


def _tight(ink: np.ndarray, box: tuple[float, float, float, float]):
    x0, y0 = max(int(box[0]), 0), max(int(box[1]), 0)
    x1, y1 = min(int(np.ceil(box[2])), ink.shape[1]), min(int(np.ceil(box[3])), ink.shape[0])
    region = ink[y0:y1, x0:x1] if x1 > x0 and y1 > y0 else np.zeros((0, 0), bool)
    rows, columns = np.flatnonzero(region.any(axis=1)), np.flatnonzero(region.any(axis=0))
    if rows.size == 0:
        return None
    return (x0 + columns[0], y0 + rows[0], x0 + columns[-1] + 1, y0 + rows[-1] + 1)


class _Ink:
    """Where the page has ink -- dark against the paper, by Otsu's threshold on the grey page --
    and, worked out the first time they are asked for, its separate marks (connected components),
    which are what a character's shape is measured from."""

    def __init__(self, image: np.ndarray):
        import cv2

        gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        _threshold, dark = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        self.mask = dark > 0
        self._marks = None

    def marks(self) -> tuple[np.ndarray, np.ndarray]:
        """Each pixel's mark (0 for paper), and each mark's left, top, width, height and area."""
        if self._marks is None:
            import cv2

            _count, labels, stats, _centres = cv2.connectedComponentsWithStats(
                self.mask.astype(np.uint8), connectivity=8)
            self._marks = labels, stats
        return self._marks


def _read(engine: OcrEngine, image: np.ndarray, ink: _Ink, *, dx: int = 0, dy: int = 0,
          **options) -> list[_Read]:
    """The recognizer's lines on `image`, which sits at (dx, dy) on the page `ink` covers."""
    out: list[_Read] = []
    for row in engine(image, return_word_box=True, **options):
        quad, text, confidence = row[0], row[1], row[2]
        cleaned = text.strip()
        if not cleaned:
            continue
        xs = [point[0] + dx for point in quad]
        ys = [point[1] + dy for point in quad]
        scripts: list[tuple[int, int, float]] = []
        # Each character's own column on the page, where the recognizer reports them.
        if len(row) >= 5 and len(row[3]) == len(row[4]) and "".join(row[4]) == text:
            lead = len(text) - len(text.lstrip())
            cells = [[(x + dx, y + dy) for x, y in cell] for cell in row[3]]
            scripts = [(start - lead, end - lead, shift)
                       for start, end, shift in _inline_scripts(ink, list(row[4]), cells)
                       if start >= lead and end - lead <= len(cleaned)]
        box = (min(xs), min(ys), max(xs), max(ys))
        out.append(_Read(box, cleaned, float(confidence), scripts, tight=_tight(ink.mask, box)))
    return out


# Characters whose position decides whether they are a superscript or subscript. Letters are left
# alone: an "x" or a "p" set small is too easily a letter misjudged, and in chemistry the scripts
# are overwhelmingly counts and charges.
SCRIPT_DIGITS = frozenset("0123456789")
SCRIPT_SIGNS = frozenset("+-−–")
# Letters that sit on the baseline with nothing below it, and letters that reach the cap height:
# the line's own measure of where its baseline and top are.
ON_BASELINE = frozenset("acemnorsuvwxzhkbdlftABCDEFGHIKLMNOPRSTUVWXYZ")
AT_CAP_HEIGHT = frozenset("bdfhkltABCDEFGHIKLMNOPRSTUVWXYZ")
# A figure at least this share of the line's cap height is full size.
SCRIPT_MAX_HEIGHT = 0.8
# A smaller figure is a subscript when its foot is below the baseline by this share of the cap
# height, and a superscript when its foot is above it by this share.
SUBSCRIPT_DROP = 0.12
SUPERSCRIPT_RISE = 0.3


def _extent(ink: _Ink, cell) -> tuple[float, float] | None:
    """The top and bottom of a character's shape: the one mark whose middle is nearest the middle
    of the character's column. A whole mark, so a figure is measured as the figure it is and not as
    a strip of it; the nearest, because the recognizer's columns drift a little off the glyphs."""
    labels, stats = ink.marks()
    xs = [x for x, _y in cell]
    ys = [y for _x, y in cell]
    left, right = min(xs), max(xs)
    y0, y1 = max(int(min(ys)), 0), min(int(np.ceil(max(ys))), labels.shape[0])
    x0, x1 = max(int(left), 0), min(int(np.ceil(right)), labels.shape[1])
    if x1 <= x0 or y1 <= y0:
        return None
    middle = (left + right) / 2
    best = None
    for mark in np.unique(labels[y0:y1, x0:x1]):
        if mark == 0:
            continue
        mx, my, mw, mh, _area = stats[mark]
        centre = mx + mw / 2
        if not left <= centre <= right or mh > 2 * (y1 - y0):
            continue
        if best is None or abs(centre - middle) < best[0]:
            best = (abs(centre - middle), my, my + mh)
    return None if best is None else (best[1], best[2])


def _fit(points: list[tuple[float, float]]):
    """A straight line through (x, y) points -- a tilted scan's baseline is not level -- or a level
    one through their median when there are too few to fit."""
    ys = sorted(y for _x, y in points)
    if len(points) >= 4:
        slope, intercept = np.polyfit([x for x, _y in points], [y for _x, y in points], 1)
        if abs(slope) < 0.1:
            return lambda x: slope * x + intercept
    middle = ys[len(ys) // 2]
    return lambda _x: middle


def _inline_scripts(ink: _Ink, chars: list[str], cells: list) -> list[tuple[int, int, float]]:
    """The superscripts and subscripts inside one recognized line, from where each character's
    ink actually sits.

    The recognizer reads "H2O" and "10-5" as flat text; the page shows the 2 dropped and the -5
    raised. Each figure and sign is measured against the line's own baseline and cap height, taken
    from the letters around it, and a run of them set small and off the baseline is a script.
    """
    measured = []
    offset = 0
    for char, cell in zip(chars, cells):
        span = _extent(ink, cell) if char.strip() else None
        middle = sum(x for x, _y in cell) / len(cell)
        measured.append((offset, offset + len(char), char, middle, span))
        offset += len(char)
    feet = [(x, span[1]) for _s, _e, char, x, span in measured if span and char in ON_BASELINE]
    tops = [(x, span[0]) for _s, _e, char, x, span in measured if span and char in AT_CAP_HEIGHT]
    if len(feet) < 3 or len(tops) < 3:
        # A run of figures with hardly a letter ("= 4.9 x 10-5"): figures stand on the baseline
        # and reach the cap height too, and most of them are not scripts.
        figures = [span for _s, _e, char, _x, span in measured if span and char in SCRIPT_DIGITS]
        if len(figures) < 3:
            return []
        feet = [(x, span[1]) for _s, _e, char, x, span in measured
                if span and (char in ON_BASELINE or char in SCRIPT_DIGITS)]
        tops = [(x, span[0]) for _s, _e, char, x, span in measured
                if span and (char in AT_CAP_HEIGHT or char in SCRIPT_DIGITS)]
    baseline, cap = _fit(feet), _fit(tops)

    runs: list[list] = []
    for start, end, char, x, span in measured:
        if span is None or (char not in SCRIPT_DIGITS and char not in SCRIPT_SIGNS):
            continue
        foot, height = baseline(x), baseline(x) - cap(x)
        if height <= 0:
            continue
        top, bottom = span
        if char in SCRIPT_DIGITS:
            # A figure set small needs to sit only a little off the line; one at full size -- some
            # typesetters only move it -- has to be moved well off it.
            full = bottom - top >= SCRIPT_MAX_HEIGHT * height
            raised = bottom < foot - (SUPERSCRIPT_RISE + 0.05 * full) * height
            if not raised and bottom <= foot + (0.3 if full else SUBSCRIPT_DROP) * height:
                continue
        else:
            # A sign has no foot of its own; where its middle sits says it. On the line, a plus or
            # a dash is drawn about a third of the way up.
            centre = (top + bottom) / 2
            raised = centre < foot - 0.65 * height
            if not raised and centre <= foot + 0.05 * height:
                continue
        shift = (foot - height / 2) - (top + bottom) / 2     # how far above the line's middle
        run = runs[-1] if runs else None
        if run and run[1] == start and run[3] == raised:
            run[1] = end
            run[2].append(shift)
        else:
            runs.append([start, end, [shift], raised])
    return [(start, end, sorted(shifts)[len(shifts) // 2]) for start, end, shifts, _r in runs]


# A band of ink the recognizer left unread is read again when it is about a line high (between
# these multiples of the page's typical line) and at least `RESCUE_MIN_WIDTH` lines long; taller is
# a picture, and shorter or narrower a speck. What comes back counts only at this confidence.
RESCUE_BAND = (0.5, 2.5)
RESCUE_MIN_WIDTH = 2.0
RESCUE_MIN_CONFIDENCE = 0.6
# The ways a band is read again: with the recognizer's usual threshold for what counts as text and
# a looser one, each on the band cut close and with a margin of paper around it. The text finder is
# unsteady on a line crowded with scripts -- each way finds pieces the others miss -- so all four
# are tried and the one that accounts for the most of the band's ink, read once, is kept.
RESCUE_WAYS = ((0.5, 0.0), (0.3, 0.0), (0.5, 0.6), (0.3, 0.6))     # (box threshold, margin)


def _read_missed(engine: OcrEngine, image: np.ndarray, ink: _Ink,
                 reads: list[_Read]) -> list[_Read]:
    """Read again what the recognizer skipped: text lines it never found.

    Its text finder works on the page as a whole, and where superscripts and subscripts fill the
    space between lines -- a paragraph of chemistry, "SO4 2-" under "HCO3 -" -- the lines run
    together into one blob it cannot take apart, and drops. Whole lines of a page went unread that
    way. Any band of ink left outside every line it did read is cut out on its own and read again,
    where there is nothing for it to run into.
    """
    import cv2

    if not reads:
        return []
    heights = sorted(read.body for read in reads)
    line = heights[len(heights) // 2]
    left_over = ink.mask.copy()
    for read in reads:
        x0, y0, x1, y1 = (int(v) for v in read.box)
        left_over[max(y0, 0):y1 + 1, max(x0, 0):x1 + 1] = False
    _count, labels, stats, _centres = cv2.connectedComponentsWithStats(
        left_over.astype(np.uint8), connectivity=8)
    big = stats[:, cv2.CC_STAT_AREA] >= max(4, (0.1 * line) ** 2)
    big[0] = False
    left_over = big[labels]

    found: list[_Read] = []
    rows = np.flatnonzero(left_over.any(axis=1))
    if rows.size == 0:
        return found
    breaks = np.flatnonzero(np.diff(rows) > 0.3 * line)
    for first, last in zip(np.concatenate(([0], breaks + 1)),
                           np.concatenate((breaks, [rows.size - 1]))):
        top, bottom = int(rows[first]), int(rows[last]) + 1
        if not RESCUE_BAND[0] * line <= bottom - top <= RESCUE_BAND[1] * line:
            continue
        columns = np.flatnonzero(left_over[top:bottom].any(axis=0))
        if columns[-1] - columns[0] < RESCUE_MIN_WIDTH * line:
            continue
        pad = int(0.4 * line)
        y0, y1 = max(top - pad, 0), min(bottom + pad, image.shape[0])
        x0, x1 = max(int(columns[0]) - pad, 0), min(int(columns[-1]) + pad, image.shape[1])
        crop = image[y0:y1, x0:x1].copy()
        # What was read already is blanked out of the cut, so it is not read twice.
        for read in reads:
            bx0, by0, bx1, by1 = read.box
            cx0, cy0 = max(int(bx0) - x0, 0), max(int(by0) - y0, 0)
            cx1, cy1 = min(int(bx1) + 1 - x0, crop.shape[1]), min(int(by1) + 1 - y0, crop.shape[0])
            if cx1 > cx0 and cy1 > cy0:
                crop[cy0:cy1, cx0:cx1] = 255
        band = left_over[y0:y1, x0:x1]
        best, best_score = [], 0
        for threshold, margin in RESCUE_WAYS:
            border = int(margin * line)
            framed = cv2.copyMakeBorder(crop, border, border, border, border, cv2.BORDER_CONSTANT,
                                        value=(255, 255, 255))
            way = [read for read in _read(engine, framed, ink, dx=x0 - border, dy=y0 - border,
                                          box_thresh=threshold)
                   if read.confidence >= RESCUE_MIN_CONFIDENCE]
            score = _accounted_for(band, way, x0, y0)
            if score > best_score:
                best, best_score = way, score
        found += best
    return found


def _accounted_for(band: np.ndarray, reads: list[_Read], x0: int, y0: int) -> int:
    """How much of a band's ink (at x0, y0 on the page) the reads cover exactly once -- ink they
    miss counts for nothing, and ink two of them both claim counts against."""
    cover = np.zeros(band.shape, np.int16)
    for read in reads:
        left, top, right, bottom = read.box
        cover[max(int(top) - y0, 0):max(int(bottom) + 1 - y0, 0),
              max(int(left) - x0, 0):max(int(right) + 1 - x0, 0)] += 1
    return int((band & (cover == 1)).sum()) - int((band & (cover > 1)).sum())


# A piece of a row set this much smaller than the row's body text, and off its middle by this share
# of it, is a superscript or subscript; pieces closer than `ROW_JOIN_GAP` body heights are one line.
SCRIPT_PIECE_HEIGHT = 0.75
SCRIPT_PIECE_CHARS = 4
SCRIPT_PIECE_OFFSET = 0.15
ROW_JOIN_GAP = 0.8
WORD_GAP = 0.25


def _join_rows(reads: list[_Read]) -> list[_Read]:
    """Put a line read in pieces back together.

    A line the recognizer could not take in one go comes back in pieces -- "Sulfate, SO4",
    ", binds Ca", "2+", "strongly; K", "sp" -- the raised and lowered ones on their own, small. A
    row holding such a piece is joined into one line, left to right, with each small piece marked
    as the superscript or subscript it is. A row with nothing small in it is left exactly as read:
    pieces of an ordinary row are kept apart for a reason (columns, table cells).
    """
    rows = _rows(reads)
    out: list[_Read] = []
    for row in rows:
        main = max(row, key=lambda r: r.height)
        # The size of the row's text, from its pieces of words rather than its tallest: a piece
        # with a superscript in it stands taller than the plain words beside it.
        words = sorted(read.height for read in row if len(read.text) > SCRIPT_PIECE_CHARS)
        body = words[len(words) // 2] if words else main.height
        middle = next((read.middle for read in sorted(row, key=lambda r: -len(r.text))), 0)

        def is_script(read: _Read, body: float = body, middle: float = middle) -> bool:
            return (len(read.text) <= SCRIPT_PIECE_CHARS
                    and read.height <= SCRIPT_PIECE_HEIGHT * body
                    and abs(read.middle - middle) >= SCRIPT_PIECE_OFFSET * body)

        if len(row) == 1 or not any(is_script(read) for read in row):
            out += row
            continue
        joined: _Read | None = None
        for read in sorted(row, key=lambda r: r.tight[0]):
            if joined is not None and _gap(joined, read) > ROW_JOIN_GAP * body:
                out.append(joined)
                joined = None
            if joined is None:
                joined = _Read(read.box, read.text, read.confidence, list(read.scripts),
                               main.body, read.tight)
                if is_script(read):
                    joined.scripts = [(0, len(read.text), middle - read.middle)]
                continue
            space = " " if _gap(joined, read) > WORD_GAP * body else ""
            at = len(joined.text) + len(space)
            scripts = ([(at, at + len(read.text), middle - read.middle)] if is_script(read)
                       else [(at + start, at + end, shift) for start, end, shift in read.scripts])
            weight = len(joined.text), len(read.text)
            joined = _Read(
                _union(joined.box, read.box), joined.text + space + read.text,
                (joined.confidence * weight[0] + read.confidence * weight[1]) / sum(weight),
                joined.scripts + scripts, main.body, _union(joined.tight, read.tight))
        out.append(joined)
    return out


def _rows(reads: list[_Read]) -> list[list[_Read]]:
    """The reads grouped into rows: pieces that sit level with each other and close together, each
    row in the order its first piece was read. Level means sharing most of the smaller one's height
    with their middles close, which a superscript tucked under the line above does not."""
    parent = list(range(len(reads)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(reads):
        for j in range(i + 1, len(reads)):
            b = reads[j]
            tall, short = max(a.height, b.height), min(a.height, b.height)
            shared = min(a.tight[3], b.tight[3]) - max(a.tight[1], b.tight[1])
            if (shared >= 0.3 * short and abs(a.middle - b.middle) <= 0.6 * tall
                    and _gap(a, b) <= ROW_JOIN_GAP * tall):
                parent[root(j)] = root(i)
    rows: dict[int, list[_Read]] = {}
    for i, read in enumerate(reads):
        rows.setdefault(root(i), []).append(read)
    return list(rows.values())


def _gap(left: _Read, right: _Read) -> float:
    """The paper between two pieces side by side; zero when they overlap."""
    return max(right.tight[0] - left.tight[2], left.tight[0] - right.tight[2], 0.0)


def _union(a: tuple, b: tuple) -> tuple:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def ocr_pages(
    source: Path,
    pages: Iterable[Page],
    *,
    engine: OcrEngine,
    cache: dict[int, list[TextLine]],
    dpi: int = 200,
    restore_images: bool = True,
) -> Iterator[Page]:
    """Yield each page unchanged if it has a text layer, or with OCR'd lines if it does not.

    A scanned page is rasterized, restored (deskew + gentle denoise, unless `restore_images` is
    False), then recognized. OCR is expensive, and the pipeline reads the pages twice (profile pass,
    then assemble pass), so a page's recognized lines are memoized in `cache` on first sight and
    reused on the second pass. The cache holds only text (never images) and only for scanned pages,
    so a born-digital document leaves it empty and keeps streaming with bounded memory (invariant 5).
    A blank or unrecoverable scan simply yields no lines and stays a no-text-layer page downstream.
    """
    for page in pages:
        if page.has_text_layer:
            yield page
            continue
        if page.number not in cache:
            image = render_page_to_image(source, page.number, dpi=dpi)
            if restore_images:
                from .restoration import restore

                image = restore(image).image
            cache[page.number] = recognize(
                image, page_number=page.number, page_width=page.width,
                page_height=page.height, engine=engine,
            )
        yield replace(page, lines=tuple(cache[page.number]))
