"""Write the page: the invisible tagged text layer, figure crops, and pages rebuilt from renders."""

from __future__ import annotations

import io
from pathlib import Path

import pikepdf
from pikepdf import Dictionary, Name

from .extract import TextLine


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


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _encode_winansi(text: str) -> bytes:
    """Encode text for the invisible overlay font (Type1 Helvetica, /Encoding /WinAnsiEncoding).

    The overlay font's declared encoding is WinAnsi (cp1252), so the string bytes must be cp1252,
    not the Python default of UTF-8 -- encoding non-ASCII text (accents, curly quotes, ligatures,
    all routine in real scanned/academic text) as UTF-8 into a WinAnsi string silently corrupts it:
    each UTF-8 byte is reinterpreted as its own WinAnsi codepoint, and some land on one of cp1252's
    five undefined byte values (0x81/0x8D/0x8F/0x90/0x9D) -- an invalid Unicode mapping that fails
    PDF/UA-2 8.4.5.8/8.4.5.9. A character with no cp1252 representation at all (Greek, CJK, ...) is
    replaced with '?' -- a real loss for that one character, but confined to the invisible text
    layer, and an honest substitution beats a silently wrong glyph.

    C0 control characters (0x00-0x1F: tab, newline, ...) get the same treatment even though most
    have a defined *codepoint* -- WinAnsiEncoding's own glyph table (PDF spec Annex D) assigns no
    glyph name to any of them, so they encode to Unicode 0 regardless, the same failure. A real
    born-digital source's own extracted text can carry a literal tab used for visual alignment
    (confirmed on a real sample: a document with "II.\tImaging..."-style headings, 52 instances of
    exactly this) -- replaced with a space, which is what the tab visually was anyway.
    """
    text = "".join(" " if ord(ch) < 0x20 else ch for ch in text)
    return text.encode("cp1252", errors="replace")


def _draw_line(out: io.BytesIO, line: TextLine, font_name: str) -> None:
    """The invisible (render mode 3) text for one line, with no marked-content wrapper of its own."""
    x0, y0, x1, y1 = line.bbox
    size = max(y1 - y0, 1.0)
    out.write(b"q BT 3 Tr /" + font_name.encode("ascii") + b" 1 Tf\n")
    out.write(f"{size:.2f} 0 0 {size:.2f} {x0:.2f} {y0:.2f} Tm (".encode("ascii"))
    out.write(_encode_winansi(_escape(line.text)))
    out.write(b") Tj\nET Q\n")


def _tagged_text_stream(lines: list[TextLine], font_name: str, mcids: list[int | None],
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
            _draw_pieces(out, line, font_name, pieces[index])
            continue
        out.write(b"/Artifact BMC\n" if mcid is None
                  else f"/P <</MCID {mcid}>> BDC\n".encode("ascii"))
        _draw_line(out, line, font_name)
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


def _draw_pieces(out: io.BytesIO, line: TextLine, font_name: str,
                 pieces: list[tuple[int, int, int, float]]) -> None:
    """A line drawn piece by piece, each `(mcid, start, end, shift)` its own marked content, so a
    superscript or subscript can be its own element in the structure tree. Each is drawn raised or
    lowered by its own shift (text rise), as it is printed."""
    x0, y0, _x1, y1 = line.bbox
    size = max(y1 - y0, 1.0)
    out.write(b"q BT 3 Tr /" + font_name.encode("ascii") + b" 1 Tf\n")
    out.write(f"{size:.2f} 0 0 {size:.2f} {x0:.2f} {y0:.2f} Tm\n".encode("ascii"))
    for mcid, start, end, shift in pieces:
        tag = "Span" if shift else "P"
        out.write(f"/{tag} <</MCID {mcid}>> BDC {shift / size:.3f} Ts (".encode("ascii"))
        out.write(_encode_winansi(_escape(line.text[start:end])))
        out.write(b") Tj EMC\n")
    out.write(b"ET Q\n")


def _add_font(pdf: pikepdf.Pdf, page: pikepdf.Page, font: pikepdf.Object, name: str) -> None:
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
    """Whether the page's content already contains marked-content operators.

    Such a page cannot simply be wrapped in an artifact -- tagged content may not live inside an
    artifact (veraPDF 7.1-2) -- so it is rebuilt from a clean render instead of kept verbatim.
    """
    try:
        for _operands, operator in pikepdf.parse_content_stream(page):
            if str(operator) in ("BDC", "BMC"):
                return True
    except (pikepdf.PdfError, Exception):  # noqa: BLE001 -- unparseable content -> rebuild clean
        return True
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
                  extra_xobjects: dict, font: pikepdf.Object,
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
    page.obj.Resources = Dictionary(XObject=xobjects, Font=Dictionary(RebindF=font))
