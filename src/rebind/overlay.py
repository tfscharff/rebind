"""Write the page: the invisible tagged text layer, figure crops, and pages rebuilt from renders."""

from __future__ import annotations

import io
from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from .extract import TextLine

# The invisible font's metrics, per 1000 of its size: every character the same width, and a descent
# under the baseline. Neither is ever seen; they place the text over the print (`_text_placement`).
OVERLAY_WIDTH = 500
OVERLAY_DESCENT = 200
# A typesetter's ligature is two or three letters drawn as one glyph. WinAnsi has none of them, and
# replaced with '?' a screen reader said "ef?cient" on any page set in a font that uses them.
LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi",
                           "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"})


def _crop_data_uri(page_image, bbox, page_width, page_height, max_side: int = 220) -> str:
    """A small PNG preview (data URI) of the figure region, cropped from the rendered page."""
    import base64

    png = _png(page_image, bbox, page_width, page_height, max_side)
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _png(page_image, bbox, page_width, page_height, max_side: int) -> bytes:
    """A region of the rendered page as PNG bytes, scaled down to fit `max_side`."""
    from PIL import Image

    height_px, width_px = page_image.shape[:2]
    sx, sy = width_px / page_width, height_px / page_height
    x0, y0, x1, y1 = bbox
    # PDF y is bottom-up; image y is top-down.
    box = (int(x0 * sx), int((page_height - y1) * sy), int(x1 * sx), int((page_height - y0) * sy))
    crop = Image.fromarray(page_image).convert("RGB").crop(box)
    crop.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG")
    return buffer.getvalue()


def _figure_xobject(pdf: pikepdf.Pdf, page_image, bbox, page_width, page_height) -> pikepdf.Object:
    """A JPEG image XObject of the figure region, for redrawing it inside a tagged /Figure."""
    from PIL import Image

    height_px, width_px = page_image.shape[:2]
    sx, sy = width_px / page_width, height_px / page_height
    x0, y0, x1, y1 = bbox
    box = (int(x0 * sx), int((page_height - y1) * sy), int(x1 * sx), int((page_height - y0) * sy))
    crop = Image.fromarray(page_image).convert("RGB").crop(box)
    buffer = io.BytesIO()
    crop.save(buffer, format="JPEG", quality=90)
    image_obj = pdf.make_stream(buffer.getvalue())
    image_obj.Type = Name.XObject
    image_obj.Subtype = Name.Image
    image_obj.Width = crop.width
    image_obj.Height = crop.height
    image_obj.ColorSpace = Name.DeviceRGB
    image_obj.BitsPerComponent = 8
    image_obj.Filter = Name.DCTDecode
    return image_obj


def _overlay_text(text: str) -> str:
    """Text as the invisible layer draws it.

    C0 control characters (tab, newline, ...) become spaces: a real born-digital source carried a
    literal tab used for visual alignment ("II.\tImaging...", 52 instances), and a control
    character is no character to read -- it maps to nothing a checker accepts (8.4.5.8). A
    typesetter's ligature is spelled out, so a reader meets "efficient", not one glyph for "fi". A
    private-use character (an icon font's glyph) means nothing to anyone but its font, and PDF/UA
    fails it without an /ActualText (8.4.3), so it is read as unknown, U+FFFD.
    """
    return "".join(" " if ord(ch) < 0x20 else "�" if 0xE000 <= ord(ch) <= 0xF8FF else ch
                   for ch in text).translate(LIGATURES)


def readable(text: str) -> String:
    """A string a person reads (a description, a bookmark's title), made as the text layer's text
    is (`_overlay_text`): PDF/UA-2 fails a private-use character in one too (8.6)."""
    return String(_overlay_text(text))


class OverlayFont:
    """The font the invisible text layer is drawn in: one code per character the document uses,
    and a ToUnicode map from each code to its character, so any character survives.

    It used to be Helvetica in WinAnsi, which holds about 220 characters. Everything else -- Greek
    letters, arrows, the minus sign, every subscript digit -- became "?", which is what a screen
    reader then read out of a chemistry paper. A font drawn in rendering mode 3 is never rendered,
    so it needs no font program: only a width (`OVERLAY_WIDTH`, the same for every code, which
    `_text_placement` scales to the print) and the map. `finish` writes the map once the whole
    document has been drawn.
    """

    name = "RebindF"

    def __init__(self, pdf: pikepdf.Pdf):
        self._codes: dict[str, int] = {}
        descriptor = pdf.make_indirect(Dictionary(
            Type=Name.FontDescriptor, FontName=Name.RebindUnicode, Flags=4,
            FontBBox=Array([0, -OVERLAY_DESCENT, 1000, 1000 - OVERLAY_DESCENT]), ItalicAngle=0,
            Ascent=1000 - OVERLAY_DESCENT, Descent=-OVERLAY_DESCENT, CapHeight=700, StemV=80))
        glyphs = pdf.make_indirect(Dictionary(
            Type=Name.Font, Subtype=Name.CIDFontType2, BaseFont=Name.RebindUnicode,
            CIDSystemInfo=Dictionary(Registry=String("Adobe"), Ordering=String("Identity"),
                                     Supplement=0),
            FontDescriptor=descriptor, DW=OVERLAY_WIDTH))
        self.font = pdf.make_indirect(Dictionary(
            Type=Name.Font, Subtype=Name.Type0, BaseFont=Name.RebindUnicode,
            Encoding=Name("/Identity-H"), DescendantFonts=Array([glyphs])))
        self._pdf = pdf

    def encode(self, text: str) -> bytes:
        """`text` as a hex string operand: two bytes per character."""
        codes = []
        for ch in _overlay_text(text):
            if ch not in self._codes:
                self._codes[ch] = len(self._codes) + 1      # code 0 is left unused
            codes.append(self._codes[ch])
        return b"<" + "".join(f"{code:04X}" for code in codes).encode("ascii") + b">"

    def finish(self) -> None:
        entries = sorted((code, ch) for ch, code in self._codes.items())
        chunks = [entries[k:k + 100] for k in range(0, len(entries), 100)]
        cmap = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
                "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
                "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def",
                "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange"]
        for chunk in chunks:
            cmap.append(f"{len(chunk)} beginbfchar")
            cmap.extend(f"<{code:04X}> <{ch.encode('utf-16-be').hex().upper()}>"
                        for code, ch in chunk)
            cmap.append("endbfchar")
        cmap += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
        self.font.ToUnicode = self._pdf.make_stream("\n".join(cmap).encode("ascii"))


def _text_placement(line: TextLine) -> bytes:
    """Set the text matrix and horizontal scale so the invisible line covers its printed one.

    Drawn at its own natural width, the text at the line's full box height runs well past the print
    or falls short of it: selecting it highlights the wrong place, and a two-column page read back
    from it has lines crossing the gutter. The baseline is lifted by the font's descent so its box
    sits where the printed line's does.
    """
    x0, y0, x1, y1 = line.bbox
    size = max(y1 - y0, 1.0)
    natural = len(_overlay_text(line.text)) * OVERLAY_WIDTH
    scale = 100.0
    if natural > 0 and x1 > x0:
        scale = min(max(100.0 * (x1 - x0) / (natural * size / 1000.0), 1.0), 1000.0)
    return (f"{size:.2f} 0 0 {size:.2f} {x0:.2f} {y0 + OVERLAY_DESCENT / 1000 * size:.2f} Tm "
            f"{scale:.2f} Tz ").encode("ascii")


def _draw_line(out: io.BytesIO, line: TextLine, font: OverlayFont) -> None:
    """The invisible (render mode 3) text for one line, with no marked-content wrapper of its own."""
    out.write(b"q BT 3 Tr /" + font.name.encode("ascii") + b" 1 Tf\n")
    out.write(_text_placement(line))
    out.write(font.encode(line.text))
    out.write(b" Tj\nET Q\n")


def _tagged_text_stream(lines: list[TextLine], font: OverlayFont, mcids: list[int | None],
                        pieces: dict[int, list] | None = None) -> bytes:
    """Invisible text (render mode 3), one marked-content sequence per line.

    A line with an MCID is wrapped in `/P <</MCID n>> BDC ... EMC` so the structure tree can point
    at it. A line with `None` is wrapped in `/Artifact BMC ... EMC` instead: page furniture -- a
    running header, a footer, a folio -- which PDF/UA requires be marked as an artifact rather than
    tagged as content, so a screen reader does not announce "Makoto Kamei et al., 34" in the middle
    of the prose on every single page. The text is still drawn, so it stays selectable and
    searchable; it simply is not part of the document's content.

    A line in `pieces` (by index) is drawn as several sequences instead, one per piece
    (`_draw_pieces`), because it has superscripts or subscripts to tag.
    """
    out = io.BytesIO()
    for index, (line, mcid) in enumerate(zip(lines, mcids)):
        if pieces and index in pieces:
            _draw_pieces(out, line, font, pieces[index])
            continue
        out.write(b"/Artifact BMC\n" if mcid is None
                  else f"/P <</MCID {mcid}>> BDC\n".encode("ascii"))
        _draw_line(out, line, font)
        out.write(b"EMC\n")
    return out.getvalue()


def _pieces(line: TextLine) -> list[tuple[int, int, float]]:
    """The line's text cut where its superscripts and subscripts start and end: `(start, end,
    shift)` for each piece in order, the shift 0 for text on the baseline. Empty for a line with
    nothing raised or lowered, which is drawn whole."""
    if not line.scripts:
        return []
    out: list[tuple[int, int, float]] = []
    at = 0
    for script in line.scripts:
        if script.start > at:
            out.append((at, script.start, 0.0))
        out.append((script.start, script.end, script.shift))
        at = script.end
    if at < len(line.text):
        out.append((at, len(line.text), 0.0))
    return out


def _draw_pieces(out: io.BytesIO, line: TextLine, font: OverlayFont,
                 pieces: list[tuple[int, int, int, float]]) -> None:
    """A line drawn piece by piece, each `(mcid, start, end, shift)` its own marked content, so a
    superscript or subscript can be its own element in the structure tree. Each is drawn raised or
    lowered by its own shift (text rise), as it is printed."""
    size = max(line.bbox[3] - line.bbox[1], 1.0)
    out.write(b"q BT 3 Tr /" + font.name.encode("ascii") + b" 1 Tf\n")
    out.write(_text_placement(line) + b"\n")
    for mcid, start, end, shift in pieces:
        tag = "Span" if shift else "P"
        out.write(f"/{tag} <</MCID {mcid}>> BDC {shift / size:.3f} Ts ".encode("ascii"))
        out.write(font.encode(line.text[start:end]))
        out.write(b" Tj EMC\n")
    out.write(b"ET Q\n")


def _add_font(pdf: pikepdf.Pdf, page: pikepdf.Page, overlay_font: OverlayFont) -> None:
    font, name = overlay_font.font, overlay_font.name
    resources = page.obj.get("/Resources")
    if resources is None:
        resources = pdf.make_indirect(Dictionary())
        page.obj["/Resources"] = resources
    fonts = resources.get("/Font")
    if fonts is None:
        fonts = Dictionary()
        resources["/Font"] = fonts
    fonts[Name("/" + name)] = font


def _has_marked_content(page: pikepdf.Page) -> bool:
    """Whether the page's content already holds tagged content: marked content with an MCID.

    Such a page cannot simply be wrapped in an artifact -- tagged content may not live inside an
    artifact (veraPDF 7.1-2) -- so it is rebuilt from a clean render instead of kept verbatim.

    Marked content with no MCID is not tagged: nothing in a structure tree can refer to it. A real
    journal's pages wrap their drawings in `/Document /MC0 BDC`, and taking any marker for a tag
    flattened every page with a vector diagram on it into a JPEG.
    """
    properties = (page.obj.get("/Resources") or Dictionary()).get("/Properties")
    try:
        for operands, operator in pikepdf.parse_content_stream(page):
            if str(operator) != "BDC" or len(operands) < 2:
                continue
            props = operands[1]
            if isinstance(props, Name) and properties is not None:
                props = properties.get(str(props))
            if isinstance(props, Dictionary) and "/MCID" in props:
                return True
    except (pikepdf.PdfError, Exception):  # noqa: BLE001 -- unparseable content -> rebuild clean
        return True
    return False


def _has_unmappable_glyphs(lines: list[TextLine]) -> bool:
    """Whether the page draws glyphs its own fonts give no real character for: pdfminer spells a
    missing one `(cid:N)`, and a private-use value is no character anyone can read. PDF/UA-2 fails
    either anywhere on the page (8.4.5.8, 8.4.3), even inside an artifact, so the page is drawn from
    a render instead, where there are no glyphs left to fail."""
    return any("(cid:" in line.text or any(0xE000 <= ord(ch) <= 0xF8FF for ch in line.text)
               for line in lines)


def _font_is_embedded(font) -> bool:
    if font.get("/Subtype") == Name.Type3:
        return True         # its glyphs are drawn by its own content, so nothing is missing
    if font.get("/Subtype") == Name.Type0:
        descendants = font.get("/DescendantFonts")
        return bool(descendants) and all(_font_is_embedded(d) for d in descendants)
    descriptor = font.get("/FontDescriptor")
    return descriptor is not None and any(
        key in descriptor for key in ("/FontFile", "/FontFile2", "/FontFile3"))


def _has_unembedded_font(page: pikepdf.Page) -> bool:
    """Whether the page, or a form drawn on it, has a font whose program is not in the file: clause
    8.4.5.5.1 fails it wherever it is used, and only a render of the page leaves it behind."""
    seen: set = set()
    stack = [page.obj.get("/Resources")]
    while stack:
        resources = stack.pop()
        if resources is None:
            continue
        for name, font in (resources.get("/Font") or Dictionary()).items():
            if name != "/RebindF" and not _font_is_embedded(font):
                return True
        for xobject in (resources.get("/XObject") or Dictionary()).values():
            if xobject.get("/Subtype") == Name.Form and xobject.objgen not in seen:
                seen.add(xobject.objgen)
                stack.append(xobject.get("/Resources"))
    return False


def _text_visibility(page: pikepdf.Page) -> tuple[bool, bool]:
    """(has any text, has any *visible* text) for one page.

    Rendering mode 3 draws nothing: it is how an OCR tool lays a searchable text layer over a scan.
    Text drawn that way is not perceivable, so it is not text a reader can be said to see.
    """
    try:
        instructions = list(pikepdf.parse_content_stream(page))
    except Exception:  # noqa: BLE001 -- an unparseable stream tells us nothing either way
        return (False, False)
    has_text = has_visible = False
    mode = 0
    saved: list[int] = []
    for ins in instructions:
        operator = str(getattr(ins, "operator", ""))
        if operator == "Tr":
            mode = int(ins.operands[0])
        elif operator == "q":
            saved.append(mode)
        elif operator == "Q" and saved:
            mode = saved.pop()
        elif operator in ("Tj", "TJ", "'", '"'):
            has_text = True
            if mode != 3:
                has_visible = True
    return (has_text, has_visible)


def _invisible_text_pages(source: Path) -> set[int]:
    """Page numbers whose text is *entirely* invisible -- a scan with an OCR layer over it.

    Such a page's words are not something a reader perceives: what they see is the picture. Their
    colours are therefore not a colour decision the document made, and measuring the contrast of
    text drawn in rendering mode 3 produces failures for text that is not on the page at all
    (939 lines and 49 "failures" on a real Tesseract-processed scan, every one of them invisible).
    Rebind strips that layer anyway, so the measurement would describe a document that no longer
    exists by the time it is written.
    """
    with pikepdf.open(source) as pdf:
        return {number for number, page in enumerate(pdf.pages, start=1)
                if _text_visibility(page) == (True, False)}


def _strip_invisible_text(pdf: pikepdf.Pdf, page: pikepdf.Page) -> bool:
    """Remove a scan's own invisible OCR text layer, leaving every visible mark untouched.

    A scan that has already been through an OCR tool carries the picture plus a layer of text drawn
    in rendering mode 3 -- invisible, there only to be selectable. Rebind lays its *own* tagged
    invisible layer over the same page, so the original's is a second, untagged copy of the same
    words; and Tesseract's stand-in font ("GlyphLessFont") declares a ToUnicode CMap veraPDF
    rejects, failing clause 8.4.5.8 for a font the document no longer needs. Removing that layer
    (rather than re-rendering the whole page, which would resample the scan) keeps the page's own
    pixels exactly as they were -- nothing that marks the page is touched, so it still looks
    identical -- and takes the unusable font out with it.

    Returns whether anything was removed.
    """
    try:
        instructions = list(pikepdf.parse_content_stream(page))
    except Exception:  # noqa: BLE001 -- unparseable content is left alone, never half-rewritten
        return False

    out: list = []
    block: list = []            # the current BT..ET run, held back until we know if it shows ink
    in_text = False
    mode = 0                    # text rendering mode, part of the graphics state (persists past ET)
    block_mode: int | None = None
    visible = False
    removed = False
    saved: list[int] = []       # `q`/`Q` save and restore the mode along with the rest of the state
    for ins in instructions:
        operator = str(getattr(ins, "operator", ""))
        if operator == "BT":
            in_text, block, visible, block_mode = True, [ins], False, None
            continue
        if not in_text:
            # Tracking q/Q matters: a mode restored by Q and not re-set is 0, and mistaking that
            # for a still-invisible state would delete text the page actually shows.
            if operator == "q":
                saved.append(mode)
            elif operator == "Q" and saved:
                mode = saved.pop()
            elif operator == "Tr":
                mode = int(ins.operands[0])
            out.append(ins)
            continue
        block.append(ins)
        if operator == "Tr":
            mode = int(ins.operands[0])
            block_mode = mode
        elif operator in ("Tj", "TJ", "'", '"') and mode != 3:
            visible = True
        elif operator == "ET":
            in_text = False
            if visible:
                out.extend(block)
            else:
                removed = True
                # A rendering mode set inside the dropped run persists past ET, so it has to be
                # re-stated: dropping it silently would make the *next* run visible or invisible.
                if block_mode is not None:
                    out.append(pikepdf.ContentStreamInstruction([block_mode], pikepdf.Operator("Tr")))
            block = []
    out.extend(block)           # an unterminated BT run: keep it rather than lose content
    if not removed:
        return False

    page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(out))
    # Fonts nothing draws with any more go too -- leaving one behind would keep failing 8.4.5.8.
    used = {str(ins.operands[0]) for ins in out
            if str(getattr(ins, "operator", "")) == "Tf" and ins.operands}
    fonts = (page.obj.get("/Resources") or Dictionary()).get("/Font")
    if fonts is not None:
        for key in [str(k) for k in fonts.keys() if str(k) not in used]:
            del fonts[key]
    return True


def _add_xobject(pdf: pikepdf.Pdf, page: pikepdf.Page, xobject: pikepdf.Object, name: str) -> None:
    resources = page.obj.get("/Resources")
    if resources is None:
        resources = pdf.make_indirect(Dictionary())
        page.obj["/Resources"] = resources
    xobjects = resources.get("/XObject")
    if xobjects is None:
        xobjects = Dictionary()
        resources["/XObject"] = xobjects
    xobjects[Name("/" + name)] = xobject


def _rebuild_page(pdf: pikepdf.Pdf, page: pikepdf.Page, page_image, overlay: bytes,
                  extra_xobjects: dict, font: OverlayFont,
                  width_pt: float, height_pt: float) -> None:
    """Replace the page's content with the rendered page image (artifact) + the tagged overlay --
    a clean slate that discards any pre-existing marked content while looking identical."""
    from PIL import Image

    pil = Image.fromarray(page_image).convert("RGB")
    buffer = io.BytesIO()
    pil.save(buffer, format="JPEG", quality=90)
    image_obj = pdf.make_stream(buffer.getvalue())
    image_obj.Type = Name.XObject
    image_obj.Subtype = Name.Image
    image_obj.Width = pil.width
    image_obj.Height = pil.height
    image_obj.ColorSpace = Name.DeviceRGB
    image_obj.BitsPerComponent = 8
    image_obj.Filter = Name.DCTDecode
    content = (
        f"/Artifact BMC q {width_pt:.2f} 0 0 {height_pt:.2f} 0 0 cm /Im0 Do Q EMC\n".encode()
        + overlay
    )
    page.obj.Contents = pdf.make_stream(content)
    xobjects = Dictionary(Im0=image_obj)
    for name, obj in extra_xobjects.items():
        xobjects[Name("/" + name)] = obj
    page.obj.Resources = Dictionary(XObject=xobjects, Font=Dictionary(RebindF=font.font))
