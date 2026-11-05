"""What an uploaded PDF already says: descriptions in its tags, and Rebind's own earlier layer.

A document can arrive already described -- by a person in Acrobat, or by a person in Rebind, saving
and coming back to it later. Those descriptions are somebody's work. They are read here from the
source's structure tree and matched to the pictures Rebind finds by where they sit on the page,
because nothing else connects the two: element ids are Rebind's own and marked-content ids are
renumbered by every producer.

A file Rebind wrote also holds Rebind's invisible text over whatever the page already showed, and a
picture of each figure drawn over the figure. `original_of` takes those back off, so the document is
read as what it was before rather than with every word doubled.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name

from .geometry import _area
from .pagecache import PageCache, file_key

# The structure types whose description is carried over. A table's /Alt is not: it is a summary of
# rows and columns, written again from the table as it now stands.
DESCRIBED_TYPES = ("Figure", "Formula")
# A described picture is the one Rebind found when they share this much of the smaller box.
SAME_PICTURE_OVERLAP = 0.5
# Not a description: what authoring tools and publishers' pipelines fill /Alt with when nobody wrote
# one -- a file name, or a real journal's "Image of Fig. 1". Taken for a description, it would stop
# the figure being asked about and read a screen-reader user nothing about the picture.
PLACEHOLDER_ALT = re.compile(
    r"\S+\.(png|jpe?g|gif|tiff?|bmp|emf|wmf|svg|pdf)"
    r"|((image|picture|graphic|photo|figure|fig\.?)\s*(of\s*)?)+"
    r"((fig(ure)?\.?|scheme|plate|table)\s*)?[\w.-]{0,6}\.?",
    re.IGNORECASE)
# The font name Rebind draws its invisible text in, which marks a page as Rebind's work.
OWN_FONT = "/RebindF"
OWN_FIGURE = re.compile(r"/Fig\d+")

_existing = PageCache()


@dataclass(frozen=True)
class Described:
    kind: str       # "Figure" or "Formula"
    alt: str
    bbox: tuple[float, float, float, float]
    # Rebind wrote this box, and it may be one a person drew. Another producer's can be a fragment
    # of the picture (a publisher's covered a tenth of one), so it only places the description.
    exact: bool = False


Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _times(m: Matrix, n: Matrix) -> Matrix:
    """`m` applied first, then `n`."""
    return (m[0] * n[0] + m[1] * n[2], m[0] * n[1] + m[1] * n[3],
            m[2] * n[0] + m[3] * n[2], m[2] * n[1] + m[3] * n[3],
            m[4] * n[0] + m[5] * n[2] + n[4], m[4] * n[1] + m[5] * n[3] + n[5])


def _point(m: Matrix, x: float, y: float) -> tuple[float, float]:
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def _numbers(operands) -> list[float]:
    return [float(v) for v in operands]


def _mcid_of(operands, properties) -> int | None:
    """The marked-content id a BDC opens: in its own dictionary, or a named one in the resources."""
    if len(operands) < 2:
        return None
    props = operands[1]
    if isinstance(props, Name) and properties is not None:
        props = properties.get(str(props))
    if isinstance(props, Dictionary) and "/MCID" in props:
        return int(props["/MCID"])
    return None


def _marked_content_boxes(page: pikepdf.Page) -> dict[int, tuple[float, float, float, float]]:
    """Where each marked-content sequence on the page draws, as the box around what it draws.

    Text is taken at each string's origin and font height, which is plenty to say which element it
    is in; the pictures this is mostly for are exact.
    """
    resources = page.obj.get("/Resources") or Dictionary()
    properties = resources.get("/Properties")
    xobjects = resources.get("/XObject") or Dictionary()
    points: dict[int, list[tuple[float, float]]] = {}
    open_ids: list[int | None] = []
    ctm, saved = IDENTITY, []
    text_line, font_size = IDENTITY, 1.0

    def mark(*pairs: tuple[float, float]) -> None:
        for mcid in open_ids:
            if mcid is not None:
                points.setdefault(mcid, []).extend(_point(ctm, x, y) for x, y in pairs)

    for ins in pikepdf.parse_content_stream(page):
        op, args = str(ins.operator), ins.operands
        if op == "BDC":
            open_ids.append(_mcid_of(args, properties))
        elif op == "BMC":
            open_ids.append(None)
        elif op == "EMC":
            if open_ids:
                open_ids.pop()
        elif not any(mcid is not None for mcid in open_ids) and op not in ("q", "Q", "cm"):
            continue
        elif op == "q":
            saved.append(ctm)
        elif op == "Q":
            if saved:
                ctm = saved.pop()
        elif op == "cm":
            ctm = _times(tuple(_numbers(args)), ctm)
        elif op == "Do":
            xobject = xobjects.get(str(args[0]))
            if xobject is not None and xobject.get("/Subtype") == Name.Form and "/BBox" in xobject:
                x0, y0, x1, y1 = _numbers(xobject["/BBox"])
                inner = tuple(_numbers(xobject["/Matrix"])) if "/Matrix" in xobject else IDENTITY
                mark(*(_point(inner, x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))))
            else:
                mark((0.0, 0.0), (1.0, 1.0))
        elif op == "re":
            x, y, w, h = _numbers(args)
            mark((x, y), (x + w, y + h))
        elif op in ("m", "l", "c", "v", "y"):
            values = _numbers(args)
            mark(*zip(values[0::2], values[1::2]))
        elif op == "BT":
            text_line = IDENTITY
        elif op == "Tf":
            font_size = float(args[1])
        elif op == "Tm":
            text_line = tuple(_numbers(args))
        elif op in ("Td", "TD"):
            text_line = _times((1.0, 0.0, 0.0, 1.0, float(args[0]), float(args[1])), text_line)
        elif op in ("Tj", "TJ", "'", '"'):
            mark(_point(text_line, 0.0, 0.0), _point(text_line, 0.0, font_size))
    return {mcid: (min(x for x, _ in found), min(y for _, y in found),
                   max(x for x, _ in found), max(y for _, y in found))
            for mcid, found in points.items()}


def _own_mcids(elem, inherited_page) -> list[tuple[object, int]]:
    """The `(page object, mcid)` of an element's content, without any caption inside it."""
    page = elem.get("/Pg", inherited_page)
    kids = elem.get("/K")
    kids = list(kids) if isinstance(kids, Array) else ([] if kids is None else [kids])
    out: list[tuple[object, int]] = []
    for kid in kids:
        if isinstance(kid, Dictionary):
            if "/MCID" in kid:
                out.append((kid.get("/Pg", page), int(kid["/MCID"])))
            elif "/S" in kid and str(kid["/S"]) != "/Caption":
                out.extend(_own_mcids(kid, page))
        elif not isinstance(kid, (Array, str)):
            try:
                out.append((page, int(kid)))
            except (TypeError, ValueError):
                continue
    return out


def _layout_bbox(elem) -> tuple[float, float, float, float] | None:
    attributes = elem.get("/A")
    for attr in (list(attributes) if isinstance(attributes, Array) else [attributes]):
        if isinstance(attr, Dictionary) and "/BBox" in attr:
            return tuple(_numbers(attr["/BBox"]))
    return None


def _described_elements(pdf: pikepdf.Pdf) -> list[tuple[str, str, object, object]]:
    """Every described figure and formula in the tree: `(kind, alt, element, inherited page)`."""
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return []
    roles = root.get("/RoleMap") or Dictionary()
    out: list[tuple[str, str, object, object]] = []
    seen: set = set()
    stack = [(root.get("/K"), None)]
    while stack:
        node, page = stack.pop()
        if node is None:
            continue
        if isinstance(node, Array):
            stack.extend((kid, page) for kid in reversed(list(node)))
            continue
        if not isinstance(node, Dictionary) or "/S" not in node:
            continue
        if node.is_indirect:
            if node.objgen in seen:
                continue
            seen.add(node.objgen)
        page = node.get("/Pg", page)
        kind = str(node["/S"])
        kind = str(roles.get(kind, kind)).lstrip("/")
        alt = str(node.get("/Alt", "")).strip()
        # A control character is binary junk that landed in the string, never words.
        if (kind in DESCRIBED_TYPES and alt and not PLACEHOLDER_ALT.fullmatch(alt)
                and not any(ord(ch) < 0x20 and ch not in "\t\r\n" for ch in alt)):
            out.append((kind, alt, node, page))
        stack.append((node.get("/K"), page))
    return out


def existing_descriptions(source: Path) -> dict[int, list[Described]]:
    """The descriptions already in `source`'s tags, by page number, in the document's order."""
    stamp = file_key(source)
    key = stamp and stamp + ("descriptions",)
    known = _existing.get(key)
    if known is not None:
        return known
    found: dict[int, list[Described]] = {}
    with pikepdf.open(source) as pdf:
        number_of = {page.obj.objgen: number for number, page in enumerate(pdf.pages, start=1)}
        boxes: dict[int, dict] = {}
        for kind, alt, elem, page in _described_elements(pdf):
            mcids = _own_mcids(elem, page)
            at = next((mp for mp, _mcid in mcids if mp is not None), page)
            number = number_of.get(at.objgen) if at is not None else None
            if number is None:
                continue
            bbox = _layout_bbox(elem)
            if bbox is None:
                if number not in boxes:
                    try:
                        boxes[number] = _marked_content_boxes(pdf.pages[number - 1])
                    except Exception:  # noqa: BLE001 -- a page we cannot read tells us nothing
                        boxes[number] = {}
                held = [boxes[number][m] for _p, m in mcids if m in boxes[number]]
                if not held:
                    continue
                bbox = (min(b[0] for b in held), min(b[1] for b in held),
                        max(b[2] for b in held), max(b[3] for b in held))
            found.setdefault(number, []).append(
                Described(kind, alt, bbox, exact=_is_own_page(pdf.pages[number - 1])))
    return _existing.put(key, found)


def adopt_described_figures(boxes: list[tuple[str, tuple]], described: list[Described],
                            page_number: int) -> tuple[list[tuple[str, tuple]], dict[str, str]]:
    """The page's pictures with those already described taken up, and those descriptions by id.

    A box Rebind itself wrote replaces its guess for the same picture, since it may be one a person
    drew. One Rebind did not find at all is a picture all the same: the document says so.
    """
    boxes = list(boxes)
    alts: dict[str, str] = {}
    fresh = 0
    for item in described:
        if item.kind != "Figure":
            continue
        best, best_share = None, SAME_PICTURE_OVERLAP
        for index, (fid, bbox) in enumerate(boxes):
            if fid in alts:
                continue
            across = min(bbox[2], item.bbox[2]) - max(bbox[0], item.bbox[0])
            down = min(bbox[3], item.bbox[3]) - max(bbox[1], item.bbox[1])
            smaller = min(_area(bbox), _area(item.bbox))
            share = across * down / smaller if across > 0 and down > 0 and smaller > 0 else 0.0
            if share >= best_share:
                best, best_share = index, share
        if best is None:
            boxes.append((f"p{page_number}e{fresh}", item.bbox))
            fresh += 1
            best = len(boxes) - 1
        elif item.exact:
            boxes[best] = (boxes[best][0], item.bbox)
        alts[boxes[best][0]] = item.alt
    return boxes, alts


def formula_description(box: tuple, described: list[Described]) -> str:
    """The description already written for the formula whose text starts inside `box`."""
    for item in described:
        if item.kind != "Formula":
            continue
        x, y = (item.bbox[0] + item.bbox[2]) / 2, (item.bbox[1] + item.bbox[3]) / 2
        if box[0] - 1.0 <= x <= box[2] + 1.0 and box[1] - 1.0 <= y <= box[3] + 1.0:
            return item.alt
    return ""


# Where a file Rebind wrote keeps what it needs to be opened again as it was left: the person's
# corrections in the document's page-piece dictionary, and the original drawing of any page that had
# to be redrawn from a picture in the page's own. A page-piece dictionary is the place ISO 32000
# gives a producer for private data of its own; nothing else reads it.
SAVED_KEY = "/Rebind"


def _piece(data: Dictionary) -> Dictionary:
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("D:%Y%m%d%H%M%SZ")
    return Dictionary(LastModified=pikepdf.String(stamp), Private=data)


def save_corrections(pdf: pikepdf.Pdf, payload: dict) -> None:
    """Keep the person's corrections (`Edits.to_payload`, descriptions included) in the file."""
    import json

    data = Dictionary(Corrections=pikepdf.String(json.dumps(payload, sort_keys=True)))
    pdf.Root.PieceInfo = Dictionary({SAVED_KEY: _piece(data)})


def page_drawing(page: pikepdf.Page) -> tuple:
    """A page's content and a copy of its resources, as they stand: what `keep_original_drawing`
    keeps. The resources are copied because changing a page changes them in place."""
    resources = page.obj.get("/Resources") or Dictionary()
    copy = Dictionary({key: Dictionary(dict(value.items())) if isinstance(value, Dictionary)
                       else value for key, value in resources.items()})
    return page.obj.get("/Contents"), copy


def keep_original_drawing(pdf: pikepdf.Pdf, page: pikepdf.Page, drawing: tuple | None = None) -> None:
    """Keep a page's own drawing (`page_drawing`, taken before it was changed) so the page can be
    put back exactly when the file is opened again: one redrawn from a picture of it, or a scan
    whose own invisible text layer was taken off."""
    contents, resources = drawing or page_drawing(page)
    original = Dictionary(Contents=contents, Resources=resources)
    page.obj.PieceInfo = Dictionary({SAVED_KEY: _piece(Dictionary(Original=original))})
    page.obj.LastModified = page.obj.PieceInfo[SAVED_KEY].LastModified


def saved_corrections(source: Path) -> dict | None:
    """The corrections a file Rebind wrote was saved with, or None."""
    import json

    try:
        with pikepdf.open(source) as pdf:
            piece = (pdf.Root.get("/PieceInfo") or Dictionary()).get(SAVED_KEY)
            data = piece.get("/Private") if piece is not None else None
            if data is None or "/Corrections" not in data:
                return None
            payload = json.loads(str(data.Corrections))
    except Exception:  # noqa: BLE001 -- a payload that will not read is no payload
        return None
    return payload if isinstance(payload, dict) else None


def _saved_original(page: pikepdf.Page):
    piece = (page.obj.get("/PieceInfo") or Dictionary()).get(SAVED_KEY)
    data = piece.get("/Private") if piece is not None else None
    return data.get("/Original") if data is not None else None


def _is_own_page(page: pikepdf.Page) -> bool:
    fonts = (page.obj.get("/Resources") or Dictionary()).get("/Font")
    return fonts is not None and OWN_FONT in fonts


def _without_own_layer(page: pikepdf.Page) -> list:
    """The page's drawing without Rebind's marked content or figure pictures, and without its
    invisible text wherever the page shows text of its own. On a page that does not -- a scan --
    that text is the page's only words, and reading it saves recognising the page all over again.
    """
    instructions = list(pikepdf.parse_content_stream(page))
    mode, saved = 0, []
    shows_text = False
    for ins in instructions:
        op = str(ins.operator)
        if op == "Tr":
            mode = int(ins.operands[0])
        elif op == "q":
            saved.append(mode)
        elif op == "Q" and saved:
            mode = saved.pop()
        elif op in ("Tj", "TJ", "'", '"') and mode != 3:
            shows_text = True
            break

    out: list = []
    block: list = []
    in_block = False
    own_block = False
    markers: list[str] = []
    for ins in instructions:
        op = str(ins.operator)
        if op in ("BDC", "BMC"):
            markers.append(str(ins.operands[0]))
            continue
        if op == "EMC":
            if markers:
                markers.pop()
            continue
        if op == "BT":
            in_block, own_block, block = True, False, [ins]
            continue
        if in_block:
            block.append(ins)
            if op == "Tf" and str(ins.operands[0]) == OWN_FONT:
                own_block = True
            elif op == "ET":
                in_block = False
                # A figure's labels are drawn twice, in the figure and as an artifact under it.
                if not (own_block and (shows_text or "/Figure" in markers)):
                    out.extend(block)
                block = []
            continue
        if op == "Do" and OWN_FIGURE.fullmatch(str(ins.operands[0])):
            del out[-1:]            # its `cm`: the `q` and `Q` around it stay, and do nothing
            continue
        out.append(ins)
    out.extend(block)
    return out


def original_of(source: Path) -> Path:
    """`source`, or -- if Rebind wrote it -- a copy with Rebind's own layer taken back off.

    The copy is kept under a name made from the file's identity, so the rebuild after every saved
    edit reuses it, and with it everything already worked out per page of it (`pagecache`).
    """
    stamp = file_key(source)
    with pikepdf.open(source) as pdf:
        if not any(_is_own_page(page) or _saved_original(page) is not None
                   for page in pdf.pages):
            return source
        digest = hashlib.sha256(repr(stamp or source).encode()).hexdigest()[:24]
        copy = Path(tempfile.gettempdir()) / "rebind-originals" / f"{digest}.pdf"
        if stamp is not None and copy.exists():
            return copy
        for page in pdf.pages:
            original = _saved_original(page)
            if original is not None:
                # Redrawn from a picture last time: the page as it was is put back as it was.
                page.obj.Contents = original.Contents
                page.obj.Resources = original.Resources
                for key in ("/PieceInfo", "/LastModified", "/StructParents"):
                    if key in page.obj:
                        del page.obj[key]
                continue
            if not _is_own_page(page):
                continue
            page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(
                _without_own_layer(page)))
            resources = page.obj.Resources
            drawn = {str(ins.operands[0]) for ins in pikepdf.parse_content_stream(page)
                     if str(ins.operator) in ("Tf", "Do")}
            for group in ("/Font", "/XObject"):
                members = resources.get(group)
                if members is None:
                    continue
                for name in [str(k) for k in members.keys()]:
                    if name not in drawn and (name == OWN_FONT or OWN_FIGURE.fullmatch(name)):
                        del members[name]
            if "/StructParents" in page.obj:
                del page.obj["/StructParents"]
        for key in ("/StructTreeRoot", "/Outlines", "/MarkInfo", "/PieceInfo"):
            if key in pdf.Root:
                del pdf.Root[key]
        copy.parent.mkdir(parents=True, exist_ok=True)
        pdf.save(copy)
    return copy
