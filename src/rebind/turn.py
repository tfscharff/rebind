"""Pages printed sideways: a landscape table set on a portrait page, read with the page turned.

Every judgement Rebind makes about a page -- what is a line, which column comes first, where a
table's rows and cells are -- is made in the page's own coordinates, assuming its text runs left to
right. On a sideways page it runs bottom to top, so a table's rows arrived as columns and its
cells as a scatter of fragments read in no useful order.

Rather than teach every stage about turned text, a sideways page is read from a copy of the
document in which that page's drawing is turned upright (`upright`). Everything downstream sees an
ordinary landscape page; the editor shows it the right way up, too, which is how the person
checking it would want to read it. The page Rebind writes keeps that upright drawing and is given a
`/Rotate` that turns it back (`restore_turns`), so it is shown exactly as the original was. A page
that arrived with a `/Rotate` of its own is put upright the same way, since its text layer would
otherwise be drawn in the page's coordinates while everything was measured as the page is shown.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import replace
from pathlib import Path

import pikepdf
from pikepdf import Array

from .extract import Page, extract_pages
from .glyphs import corrections as glyph_corrections
from .glyphs import same_fonts
from .pagecache import file_key

Matrix = tuple[float, float, float, float, float, float]

# Turning a drawing clockwise by this many degrees, before the move that puts it back on the page.
_TURNS: dict[int, tuple[float, float, float, float]] = {
    0: (1.0, 0.0, 0.0, 1.0),
    90: (0.0, -1.0, 1.0, 0.0),
    180: (-1.0, 0.0, 0.0, -1.0),
    270: (0.0, 1.0, -1.0, 0.0),
}
_BOXES = ("/BleedBox", "/TrimBox", "/ArtBox")


def _rotate_of(page: pikepdf.Page) -> int:
    """The page's /Rotate, inherited from the page tree if the page does not say."""
    node = page.obj
    while node is not None:
        if "/Rotate" in node:
            return int(node.Rotate) % 360
        node = node.get("/Parent")
    return 0


def _apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def _box_through(m: Matrix, box) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = (float(v) for v in box)
    points = [_apply(m, x, y) for x in (x0, x1) for y in (y0, y1)]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def turning(degrees: int, box) -> Matrix:
    """The matrix that turns a page's drawing clockwise by `degrees` and moves `box` (the part of
    the page that is shown) to the origin."""
    a, b, c, d = _TURNS[degrees]
    x0, y0, _x1, _y1 = _box_through((a, b, c, d, 0.0, 0.0), box)
    return (a, b, c, d, -x0, -y0)


def _visible_box(page: pikepdf.Page) -> tuple[float, float, float, float]:
    """The part of the page a viewer shows: its crop box, within its media box."""
    media = [float(v) for v in page.mediabox]
    crop = [float(v) for v in page.cropbox]
    box = (max(min(media[0], media[2]), min(crop[0], crop[2])),
           max(min(media[1], media[3]), min(crop[1], crop[3])),
           min(max(media[0], media[2]), max(crop[0], crop[2])),
           min(max(media[1], media[3]), max(crop[1], crop[3])))
    return box if box[2] > box[0] and box[3] > box[1] else tuple(media)


def _square(page: pikepdf.Page) -> bool:
    """Whether the page is shown exactly as its coordinates run: from the origin, uncropped."""
    media = [float(v) for v in page.mediabox]
    return media[0] == 0 and media[1] == 0 and _visible_box(page) == tuple(media)


def _clamp(box, within) -> tuple[float, float, float, float] | None:
    out = (max(box[0], within[0]), max(box[1], within[1]),
           min(box[2], within[2]), min(box[3], within[3]))
    return out if out[2] > out[0] and out[3] > out[1] else None


def _turn_page(pdf: pikepdf.Pdf, page: pikepdf.Page, degrees: int) -> Matrix:
    """Turn the page's drawing, its boxes and its annotations clockwise by `degrees`, with the part
    of it that is shown moved to the origin, in place."""
    visible = _visible_box(page)
    m = turning(degrees, visible)
    media = _box_through(m, visible)
    page.obj.MediaBox = Array(list(media))
    if "/CropBox" in page.obj:
        del page.obj["/CropBox"]
    for key in _BOXES:
        box = page.obj.get(key)
        if box is not None:
            moved = _clamp(_box_through(m, box), media)
            if moved is None:
                del page.obj[key]
            else:
                page.obj[key] = Array(list(moved))
    page.contents_add(pdf.make_stream(
        b"q " + " ".join(f"{v:.6f}" for v in m).encode() + b" cm\n"), prepend=True)
    page.contents_add(pdf.make_stream(b"Q\n"), prepend=False)
    for annot in page.obj.get("/Annots") or []:
        if "/Rect" in annot:
            annot.Rect = Array(list(_box_through(m, annot.Rect)))
        quads = annot.get("/QuadPoints")
        if quads is not None:
            values = [float(v) for v in quads]
            annot.QuadPoints = Array([v for k in range(0, len(values) - 1, 2)
                                      for v in _apply(m, values[k], values[k + 1])])
    return m


def upright(source: Path, pages: list[Page]) -> tuple[Path, list[Page], dict[int, int],
                                                       dict[int, Matrix]]:
    """`source` with every sideways page turned upright and every page's shown part moved to the
    origin, the pages read again from it, the `/Rotate` each of those pages needs to be shown as it
    was, and the matrix that took each one's coordinates to the copy's. The source itself,
    unchanged, when every page is upright and square already.

    A page whose media box does not start at the origin (a JSTOR download's starts 8 points below
    it), or that is cropped, was measured from one corner and pictured from another: every frame
    in the editor sat 8 points too high, and the text layer with it."""
    with pikepdf.open(source) as pdf:
        # A page shows its text turned `page.turn` degrees; its drawing is turned `rotate` by the
        # page's own /Rotate. Turning the drawing by the difference puts the text upright with no
        # /Rotate, and a /Rotate of `page.turn` shows the page as it was.
        work = {page.number: ((_rotate_of(pdf.pages[page.number - 1]) - page.turn) % 360,
                              page.turn)
                for page in pages
                if page.turn or _rotate_of(pdf.pages[page.number - 1])
                or not _square(pdf.pages[page.number - 1])}
        if not work:
            return source, pages, {}, {}
        stamp = file_key(source)
        digest = hashlib.sha256(repr((stamp or source, sorted(work.items()))).encode()).hexdigest()
        copy = Path(tempfile.gettempdir()) / "rebind-upright" / f"{digest[:24]}.pdf"
        fresh = stamp is None or not copy.exists()
        matrices: dict[int, Matrix] = {}
        for number, (degrees, _turn) in work.items():
            page = pdf.pages[number - 1]
            matrices[number] = (_turn_page(pdf, page, degrees) if fresh
                                else turning(degrees, _visible_box(page)))
            page.obj.Rotate = 0
        if fresh:
            copy.parent.mkdir(parents=True, exist_ok=True)
            pdf.save(copy)
    try:
        same_fonts(source, copy)
        corrected = glyph_corrections(source)
    except Exception:  # noqa: BLE001 -- as in extract_pages: the reading goes on without it
        corrected = {}
    again = {page.number: page for page in extract_pages(copy, set(work), corrected)}
    pages = [replace(again[page.number], turn=0) if page.number in again else page
             for page in pages]
    return copy, pages, {number: turn for number, (_d, turn) in work.items()}, matrices


def move_box(m: Matrix, box) -> tuple[float, float, float, float]:
    """A box in a page's own coordinates, in the upright copy's."""
    return _box_through(m, box)


def restore_turns(pdf: pikepdf.Pdf, turns: dict[int, int]) -> None:
    """Give each page that was read upright the /Rotate that shows it as the original was."""
    for number, turn in turns.items():
        # Set even when 0: a page tree's own /Rotate would otherwise be inherited again.
        pdf.pages[number - 1].obj.Rotate = turn
