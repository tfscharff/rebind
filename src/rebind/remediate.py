"""Remediate a PDF: keep the original appearance, add accessibility (PDF/UA structure).

Each source page is rebuilt as its own rendered image, marked as an *artifact* (the picture the
reader sees), with an *invisible*, *tagged* text layer placed over it -- the words drawn in render
mode 3 (never printed) at their positions, wrapped in marked content and referenced from a PDF/UA
structure tree. Rendering at 300 DPI keeps a scanned page visually identical to the original; the
text comes from the page's own text layer where it has one, or from OCR where it does not.

The output looks like the input but is now readable by assistive technology, with document
language, title, tags and reading order set. This is remediation, not reconstruction: the page is
never reflowed or restyled.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlsplit

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from . import contrast, recolor, review
from .extract import Page, TextLine, extract_pages
from .layout import COLUMN_ALIGN_TOLERANCE_PT, detect_table_lines, order_page
from .ocr import OcrEngine, recognize, render_page_to_image
from .profile import build_profile, style_of
from .validate import self_check_pdf_ua2

# --- Line/page classification (relocated from the retired reconstruction pipeline) --------------
# A page with a text layer AND a raster image covering at least this fraction of its area is an
# OCR-over-scan page: the image is the scanned page, the text on top is recognizer output.
OCR_SCAN_COVERAGE = 0.6
BULLET_PREFIXES = ("•", "‣", "◦", "-", "*")
# An ordered-list marker, with or without trailing content: "1. first", "1.", "2)". The digit run
# is capped at 3 so a year like "1996. It was..." never matches (a longer run leaves a digit before
# the required "[.)]"). A heuristic disambiguator, not a size limit (invariant 5).
ORDERED_RE = re.compile(r"^(\d{1,3})[.)](?:\s+(.*))?$")


def _image_covers_page(image, page: Page) -> bool:
    """Whether an image covers enough of the page to be its background scan rather than a figure."""
    page_area = page.width * page.height
    if page_area <= 0:
        return False
    x0, y0, x1, y1 = image.bbox
    return (x1 - x0) * (y1 - y0) >= page_area * OCR_SCAN_COVERAGE


def _is_ocr_over_scan(page: Page) -> bool:
    """A text layer sitting on top of a page-covering scan image (a hidden OCR layer)."""
    return page.has_text_layer and any(_image_covers_page(im, page) for im in page.images)


def _list_item_text(text: str) -> tuple[str, bool] | None:
    """Return (item text, ordered) if the line looks like a list item, else None.

    The item text is empty when the line is only the marker glyph (a bare bullet or bare "1.") --
    a renderer that boxes the marker separately; the caller merges it with the next line.
    """
    for bullet in BULLET_PREFIXES:
        if text.startswith(bullet):
            return text[len(bullet):].strip(), False
    match = ORDERED_RE.match(text)
    if match:
        content = match.group(2)
        return (content.strip() if content is not None else ""), True
    return None

MIN_LIST_ITEMS = 2   # a run of at least this many marked lines becomes a list, not paragraphs
# An image covering between these fractions of the page is a figure worth describing. Smaller is a
# rule/icon/bullet; a page-covering image is the scan itself, not a figure.
FIGURE_MIN_COVERAGE = 0.01
FIGURE_MAX_COVERAGE = 0.6

# Figure/caption association. A caption sits directly adjacent to its figure (below it, the
# standard convention; above, as a fallback -- some journals set table-style captions above
# figures too) with only a small gap -- confirmed against a real sample (1429254.pdf): observed
# gaps of ~11-14pt between an image and its "Fig. N" caption. A genuinely long caption wraps
# across several physical lines with a much smaller line-to-line gap than that (~2-10pt observed);
# the cap on line count is just a safety backstop against ever running into unrelated body text.
# That cap has to be generous: the real sample's Fig. 8 caption is 15 wrapped lines, and an
# earlier cap of 8 silently truncated its alt text mid-sentence ("...connected via silicone tubing
# through two"), which reads worse to a screen reader than no caption at all. The continuation-gap
# rule, not the line count, is what actually bounds a caption block.
CAPTION_MARKER_RE = re.compile(r"^fig(?:ure)?s?\.?\s*(\d+)", re.IGNORECASE)
CAPTION_MAX_GAP_PT = 20.0
CAPTION_CONTINUATION_GAP_PT = 10.0
CAPTION_MAX_LINES = 24
# A caption that's just its own label ("Fig. 8", or "Fig. 8 (Continued)" -- a page-break artifact)
# isn't usable as alt text on its own (WCAG 1.1.1: a figure's bare label never conveys its actual
# content) and signals _document_captions should look for a fuller caption elsewhere instead.
MIN_CAPTION_WORDS = 3

# Vector (line-art) figure recovery -- see `_vector_figures`. A drawing sits a little further from
# its caption than a raster image does (the caption marker's own top edge vs the drawing's lowest
# ink): 11-23pt observed on the real sample, hence a wider tolerance than CAPTION_MAX_GAP_PT. The
# path-count floor is what keeps a lone rule above a caption from being promoted to a figure --
# observed exactly that, a 1-path horizontal rule directly above a "Fig. 8" continuation caption.
VECTOR_CAPTION_MAX_GAP_PT = 30.0
MIN_VECTOR_PATHS = 6
RULE_MAX_THICKNESS_PT = 3.0
RULE_MIN_LENGTH_RATIO = 0.5

# OCR heading recovery. A single OCR line's box height is noisy (a body line can crop tall), so no
# one signal is trusted: a heading must be markedly taller than the page's body text AND set apart
# by whitespace AND not fill the column -- the combination an inflated body line, which still sits
# inside its paragraph spanning the full width, cannot fake. Tuned conservatively: a missed heading
# stays an honest paragraph, which is safe; a fabricated one is not.
OCR_HEADING_HEIGHT_RATIO = 1.35     # a heading is at least this much taller than the body median
OCR_HEADING_MAX_WIDTH_RATIO = 0.75  # a line filling more than this of the widest line is body text
OCR_HEADING_ISOLATION_RATIO = 0.8   # whitespace above/below is at least this * the body height
OCR_HEADING_TIER_TOLERANCE = 0.15   # heading heights within this fraction are the same level

# Born-digital heading candidacy (profile.py) is style-only -- larger or bolder than body text,
# with no check on the line's actual content. A figure-panel callout label ("A", "B", "C", ...) is
# routinely bold for emphasis, which alone satisfies that test (confirmed on a real sample: a
# 28-page document's recovered outline surfaced bare single-letter "headings" from exactly this).
# No real document heading is a bare 1-2 character label, so a minimum content length costs
# nothing on genuine headings -- "Abstract" (8 characters) is the shortest real heading seen in
# that same sample and must still be promoted; this only excludes what could never be one.
MIN_HEADING_CHARS = 3

# A run of more than this many CONSECUTIVE born-digital heading-candidate lines is demoted back to
# paragraphs. A genuine document heading is essentially never immediately adjacent to another
# heading-styled line except a real title+subtitle pair (exactly 2 in a row, e.g. a chapter title
# followed directly by its own subtitle); a longer run is something else entirely -- an author
# byline broken into fragments around superscript affiliation markers, or a diagram's callout
# labels ("Ventral", "Baffles", "Air pump", ...) -- confirmed on the real 28-page sample, whose
# recovered outline surfaced both a 4-fragment byline and a 20+-label diagram burst this way.
MAX_HEADING_BURST_RUN = 2

# A table spans from its first detected row to its last; lines in between that were not themselves
# detected as table rows are sparse rows (a subtotal, or a row with an empty cell -- too few
# side-by-side cells to detect alone) and are kept so no row is dropped. The gap is bounded so prose
# between two separate tables cannot merge them: at most this many consecutive undetected lines.
MAX_INTERNAL_TABLE_GAP = 2

# Reading-order review thumbnails: big enough to recognize the page's shape and read the block
# numbers laid over it, small enough that a dozen of them stay a page the browser renders at once.
REVIEW_THUMB_DPI = 80
REVIEW_THUMB_PX = 420


@dataclass
class RemediationResult:
    pdf_path: Path
    page_count: int
    ocr_pages: tuple[int, ...] = ()          # pages we recognized (text may contain OCR errors)
    empty_pages: tuple[int, ...] = ()         # scanned pages where OCR recovered nothing
    added_text_layer: bool = False
    # Figures with no description yet: each is {"id", "page", "thumb"} (a small preview data URI).
    # Give a figure a description and re-run with alt_texts to promote it to a tagged /Figure.
    figures: tuple[dict, ...] = ()
    # A fast, dependency-free structural sanity check on the output (validate.self_check_pdf_ua2)
    # -- not independent conformance validation (that's veraPDF, dev/CI-only). Lets the app show an
    # honest "PDF/UA-2 tagged" badge without a JVM runtime dependency.
    structure_ok: bool = True
    structure_issues: tuple[str, ...] = ()
    # Evidence for the two checks Adobe's checker always leaves to a human (see `review`). Rebind
    # cannot make either pass -- no tool can -- but it can show its work: `reading_order` is the
    # order it chose, with the pages where that was a real decision flagged for an eye;
    # `contrast` is a measurement of the rendered page, not a claim about it.
    reading_order: dict = field(default_factory=dict)
    contrast: dict = field(default_factory=dict)


def _page_figures(src_page) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Embedded images on the page that are figures worth describing -- not the full-page scan,
    not tiny rules/icons. Each is (stable id, bbox in PDF points)."""
    page_area = src_page.width * src_page.height
    if page_area <= 0:
        return []
    figures = []
    for index, image in enumerate(src_page.images):
        x0, y0, x1, y1 = image.bbox
        coverage = (x1 - x0) * (y1 - y0) / page_area
        if FIGURE_MIN_COVERAGE <= coverage <= FIGURE_MAX_COVERAGE:
            figures.append((f"p{src_page.number}f{index}", image.bbox))
    return figures


def _is_rule(bbox: tuple[float, float, float, float], page_width: float) -> bool:
    """A long, hairline-thin path: a horizontal rule, a table border, an underline. Page furniture,
    never a figure, and it must not stretch a real figure's box to the full column width."""
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    return (min(width, height) <= RULE_MAX_THICKNESS_PT
            and max(width, height) >= page_width * RULE_MIN_LENGTH_RATIO)


def _vector_figures(src_page, lines: list[TextLine],
                    taken: list[tuple]) -> list[tuple[str, tuple, str]]:
    """Line-art figures -- schematics, charts, labelled diagrams -- drawn with path operators
    rather than placed as an image, found by anchoring on their captions.

    A raster image is self-evidently a figure: something is there, and no text describes it. Vector
    paths are not, because *everything* is vector paths -- a table's rules, a header underline, a
    footer barcode and a hand-drawn apparatus diagram are indistinguishable as geometry. Clustering
    them bottom-up does not resolve this either: on the real sample it splits a single captioned
    figure into its lettered panels (Fig. 5 becomes eight separate blobs) while happily promoting a
    page's decorative rules into a ninth.

    So detection runs the other way round, from a signal the author left behind: a "Fig. N ..."
    caption. Everything drawn in the band above a caption -- bounded above by the previous caption
    on the page, so two figures stacked on one page stay separate -- is that caption's figure. This
    cannot invent a figure where the author named none (invariant 1: when in doubt, nothing), and
    it hands back the caption as the figure's alt text for free. The cost is the honest one: an
    uncaptioned chart is still missed, and stays a decorative artifact rather than a wrong guess.

    Each result is (stable id, bbox, the anchoring caption's text). The caption travels with the
    figure rather than being rediscovered by proximity later: this function already knows exactly
    which caption it matched, and a drawing sits further from its caption than a raster image does,
    so re-deriving it downstream loses figures that were found perfectly well here.
    """
    page_area = src_page.width * src_page.height
    if page_area <= 0 or not src_page.drawings:
        return []
    paths = [d.bbox for d in src_page.drawings if not _is_rule(d.bbox, src_page.width)]
    if not paths:
        return []

    # Caption marker lines, top of page downwards. Each one closes off the band below the previous.
    markers = sorted((ln for ln in lines if _caption_number(ln.text)),
                     key=lambda ln: -ln.bbox[3])
    figures: list[tuple[str, tuple, str]] = []
    band_top = src_page.height
    for index, marker in enumerate(markers):
        band_bottom = marker.bbox[3]
        inside = [p for p in paths if p[1] >= band_bottom and p[3] <= band_top]
        band_top = _caption_block_bottom(lines, marker)
        caption = _caption_block(sorted((ln for ln in lines if ln.bbox[3] <= marker.bbox[3]),
                                        key=lambda ln: -ln.bbox[3])) or ""
        if len(inside) < MIN_VECTOR_PATHS:
            continue
        bbox = (min(p[0] for p in inside), min(p[1] for p in inside),
                max(p[2] for p in inside), max(p[3] for p in inside))
        if bbox[1] - band_bottom > VECTOR_CAPTION_MAX_GAP_PT:
            continue    # drawn far above the caption: unrelated page furniture, not its figure
        coverage = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) / page_area
        if not (FIGURE_MIN_COVERAGE <= coverage <= FIGURE_MAX_COVERAGE):
            continue
        if any(_overlaps(bbox, other) for _fid, other in taken):
            continue    # already found as a raster image; never describe the same region twice
        figures.append((f"p{src_page.number}v{index}", bbox, caption))
    return figures


def _caption_block_bottom(lines: list[TextLine], marker: TextLine) -> float:
    """The y of the bottom of a caption's full wrapped block -- the ceiling for the next figure
    down the page, which must not reach up into the caption above it."""
    ordered = sorted((ln for ln in lines if ln.bbox[3] <= marker.bbox[3]),
                     key=lambda ln: -ln.bbox[3])
    bottom = marker.bbox[1]
    for prev, nxt in zip(ordered, ordered[1:]):
        if prev.bbox[1] - nxt.bbox[3] > CAPTION_CONTINUATION_GAP_PT:
            break
        bottom = nxt.bbox[1]
    return bottom


def _overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _horizontally_overlaps(a: tuple[float, float, float, float],
                           b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and b[0] < a[2]


def _caption_number(text: str) -> str | None:
    """The figure number a line's caption marker names ("Fig. 8" -> "8"), or None if the line
    doesn't start with one."""
    match = CAPTION_MARKER_RE.match(text.strip())
    return match.group(1) if match else None


def _caption_block(ordered: list[TextLine]) -> str | None:
    """`ordered[0]` must be a caption-marker line; concatenate it with any tightly-following
    continuation lines (small vertical gap -- the same paragraph, not unrelated content) into the
    full caption text, capped at CAPTION_MAX_LINES as a safety backstop."""
    if not ordered or _caption_number(ordered[0].text) is None:
        return None
    block = [ordered[0]]
    for prev, nxt in zip(ordered, ordered[1:]):
        if len(block) >= CAPTION_MAX_LINES:
            break
        gap = prev.bbox[1] - nxt.bbox[3]   # y-up: prev is above nxt once sorted top-to-bottom
        if gap > CAPTION_CONTINUATION_GAP_PT:
            break
        block.append(nxt)
    # `ordered` may walk outward from a figure bottom-to-top (the "caption above" fallback) rather
    # than top-to-bottom -- re-sort by position so the joined text always reads in the document's
    # true top-to-bottom order regardless of which direction found it.
    block.sort(key=lambda ln: -ln.bbox[3])
    return " ".join(ln.text.strip() for ln in block)


def _caption_is_substantial(text: str) -> bool:
    """Whether a caption says more than just its own label. WCAG 1.1.1 is explicit that a
    figure's bare label ("Figure 8") never serves as its text alternative on its own -- it must
    convey the image's actual content. A local match that's just the marker (or the marker plus a
    page-break artifact like "(Continued)") isn't usable as-is; _document_captions is what finds
    the real caption elsewhere in that case.
    """
    remainder = CAPTION_MARKER_RE.sub("", text.strip(), count=1)
    return len(remainder.split()) >= MIN_CAPTION_WORDS


def _figure_caption(lines: list[TextLine], bbox: tuple[float, float, float, float]) -> str | None:
    """The figure's own caption, if the document has one directly adjacent to it -- reusing the
    author's own words is more accurate than anything Rebind could invent, and skips the app's
    manual describe step entirely for a figure that already names itself. Conservative by
    construction, like every other heuristic in this module: no adjacent "Fig. N" line means no
    caption, not a guess. May return a bare/thin match (just the marker); the caller decides
    whether that's substantial enough or should fall back to _document_captions.
    """
    fx0, fy0, fx1, fy1 = bbox

    below = sorted(
        (ln for ln in lines if ln.bbox[3] <= fy0 and _horizontally_overlaps(ln.bbox, bbox)),
        key=lambda ln: -ln.bbox[3],   # closest to the figure (highest y1) first
    )
    if below and (fy0 - below[0].bbox[3]) <= CAPTION_MAX_GAP_PT:
        found = _caption_block(below)
        if found is not None:
            return found

    above = sorted(
        (ln for ln in lines if ln.bbox[1] >= fy1 and _horizontally_overlaps(ln.bbox, bbox)),
        key=lambda ln: ln.bbox[1],   # closest to the figure (lowest y0) first
    )
    if above and (above[0].bbox[1] - fy1) <= CAPTION_MAX_GAP_PT:
        return _caption_block(above)
    return None


def _nearby_caption_number(lines: list[TextLine],
                           bbox: tuple[float, float, float, float]) -> str | None:
    """A more lenient search than _figure_caption: the closest "Fig. N" line within vertical range
    of the figure, WITHOUT requiring horizontal alignment -- used only to identify which figure
    this is when nothing adjacent forms a full, usable caption block itself. Confirmed necessary on
    a real sample: the marker line ("Fig. 8") sits at a different indent than the image itself, so
    it fails _figure_caption's (deliberately stricter, for building an actual caption block)
    horizontal-overlap check entirely -- but it still reliably names which figure this is.
    """
    fx0, fy0, fx1, fy1 = bbox
    candidates: list[tuple[float, TextLine]] = []
    for ln in lines:
        if ln.bbox[3] <= fy0:
            gap = fy0 - ln.bbox[3]
        elif ln.bbox[1] >= fy1:
            gap = ln.bbox[1] - fy1
        else:
            continue   # vertically overlaps the figure itself -- not a caption candidate
        if gap <= CAPTION_MAX_GAP_PT:
            candidates.append((gap, ln))
    for _gap, line in sorted(candidates, key=lambda item: item[0]):
        number = _caption_number(line.text)
        if number:
            return number
    return None


def _document_captions(per_page: list[tuple]) -> dict[str, str]:
    """Map figure number -> the fullest caption text found ANYWHERE in the document, regardless of
    proximity to any image. Backs the fallback for a multi-part figure whose image sits on one
    page while its real caption sits on another -- confirmed on a real sample: an image with only
    a bare "Fig. 8 (Continued)" nearby (a page-break artifact, no descriptive content), while the
    real, substantial caption is on the following page. `lines` is already in reading order, so a
    caption block is built by walking forward from each marker line found -- correctly stopping at
    a large geometric gap (e.g. a column break), which is safe: worse case is an early-terminated
    block, never a wrong one.
    """
    best: dict[str, str] = {}
    for _src_page, lines, _used_ocr in per_page:
        for i, line in enumerate(lines):
            number = _caption_number(line.text)
            if number is None:
                continue
            block = _caption_block(lines[i:])
            if block and (number not in best or len(block) > len(best[number])):
                best[number] = block
    return best


def _crop_data_uri(page_image, bbox, page_width, page_height, max_side: int = 220) -> str:
    """A small PNG preview (data URI) of the figure region, cropped from the rendered page."""
    import base64

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
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


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


def _tagged_text_stream(lines: list[TextLine], font_name: str) -> bytes:
    """Invisible text (render mode 3), one marked-content paragraph per line.

    Each line is wrapped in `/P <</MCID n>> BDC ... EMC` so it can be referenced from the structure
    tree; the MCID is the line index. Drawn after the artifact-marked page image, so it never
    changes the visible page.
    """
    out = io.BytesIO()
    for mcid, line in enumerate(lines):
        x0, y0, x1, y1 = line.bbox
        size = max(y1 - y0, 1.0)
        out.write(f"/P <</MCID {mcid}>> BDC\n".encode("ascii"))
        out.write(b"q BT 3 Tr /" + font_name.encode("ascii") + b" 1 Tf\n")
        out.write(f"{size:.2f} 0 0 {size:.2f} {x0:.2f} {y0:.2f} Tm (".encode("ascii"))
        out.write(_encode_winansi(_escape(line.text)))
        out.write(b") Tj\n")
        out.write(b"ET Q\nEMC\n")
    return out.getvalue()


def _merge_bare_markers(lines: list[TextLine]) -> list[TextLine]:
    """Merge a lone list-marker line ('•', '-', '1.') into the item text that follows it.

    Some renderers (WeasyPrint's <ul>, Word) draw the bullet in its own text box, so pdfminer
    yields the marker and its content as separate lines. Merging them (when they sit on the same
    row) lets the list detector see a real list item rather than a stray marker plus a paragraph.
    """
    out: list[TextLine] = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        item = _list_item_text(cur.text)
        bare = item is not None and not item[0]
        if bare and i + 1 < len(lines):
            nxt = lines[i + 1]
            same_row = min(cur.bbox[3], nxt.bbox[3]) - max(cur.bbox[1], nxt.bbox[1]) > 0
            if same_row and nxt.bbox[0] >= cur.bbox[0]:
                out.append(replace(
                    nxt, text=f"{cur.text} {nxt.text}",
                    bbox=(min(cur.bbox[0], nxt.bbox[0]), min(cur.bbox[1], nxt.bbox[1]),
                          max(cur.bbox[2], nxt.bbox[2]), max(cur.bbox[3], nxt.bbox[3]))))
                i += 2
                continue
        out.append(cur)
        i += 1
    return out


def _lines_for(source: Path, src_page, engine: OcrEngine, dpi: int,
               profile) -> tuple[list[TextLine], bool, object]:
    """The page's text lines in reading order, whether OCR was used, and the page's layout.

    Reading order comes from `layout.order_page`'s recursive XY-cut, NOT from a top-to-bottom sort.
    On a single-column page the two agree. On a two-column page they emphatically do not: a
    top-to-bottom sort interleaves the columns line by line, so a screen reader reads "left line 1,
    right line 1, left line 2, ..." -- word salad, and precisely the failure Adobe's "Logical
    Reading Order" check exists to catch. The cut was written and tested long before this, but the
    pipeline was still sorting by y; that gap is what `review.page_order` now reports on.
    """
    if src_page.has_text_layer:
        lines = list(src_page.lines)
        used_ocr = False
    else:
        image = render_page_to_image(source, src_page.number, dpi=dpi)
        lines = recognize(image, page_number=src_page.number, page_width=src_page.width,
                          page_height=src_page.height, engine=engine)
        used_ocr = True
    page = replace(src_page, lines=tuple(lines))
    # Figure regions are found before ordering, not after, so a diagram's callout labels can be
    # held out of the column cut (see order_page) instead of being read as column structure.
    boxes = _page_figures(page)
    boxes += [(fid, bbox) for fid, bbox, _cap in _vector_figures(page, lines, boxes)]
    layout = order_page(page, profile, tuple(bbox for _fid, bbox in boxes))
    ordered = [placed.line for placed in layout.lines]
    return _merge_bare_markers(ordered), used_ocr, layout


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


def _ocr_heading_heights(lines: list[TextLine]) -> dict[int, float]:
    """For the OCR lines on one page, return {id(line): height} for lines that look like headings.

    A heading is taller than the page's body text (size), set apart by whitespace (isolation), and
    does not fill the text column (shortness). No signal alone is trusted -- a single OCR line's box
    height is noise -- but their conjunction is not something an over-tall body line, which still
    sits inside its paragraph spanning the full width, can produce.
    """
    if len(lines) < 3:
        return {}
    heights = sorted(ln.bbox[3] - ln.bbox[1] for ln in lines)
    body_height = heights[len(heights) // 2] or 1.0
    max_width = max((ln.bbox[2] - ln.bbox[0]) for ln in lines) or 1.0
    ordered = sorted(lines, key=lambda ln: -ln.bbox[3])   # top of page to bottom
    result: dict[int, float] = {}
    for idx, line in enumerate(ordered):
        height = line.bbox[3] - line.bbox[1]
        width = line.bbox[2] - line.bbox[0]
        if height < body_height * OCR_HEADING_HEIGHT_RATIO:
            continue
        if width > max_width * OCR_HEADING_MAX_WIDTH_RATIO:
            continue
        gap_above = (ordered[idx - 1].bbox[1] - line.bbox[3]) if idx > 0 else float("inf")
        gap_below = (line.bbox[1] - ordered[idx + 1].bbox[3]) if idx < len(ordered) - 1 else float("inf")
        if max(gap_above, gap_below) >= body_height * OCR_HEADING_ISOLATION_RATIO:
            result[id(line)] = height
    return result


def _demote_heading_bursts(levels: list[int]) -> list[int]:
    """Demote a run of more than MAX_HEADING_BURST_RUN consecutive lines at the SAME exact raw
    heading level back to paragraphs (0).

    Grouping strictly by the same level (not "any heading, regardless of level") matters: a real
    title immediately followed by its own subtitle uses two DIFFERENT levels and must never be
    treated as a burst just for being adjacent, no matter how many levels are involved -- only a
    run of lines that all share one style is the signature of a byline or diagram-label burst.
    """
    out = list(levels)
    i, n = 0, len(out)
    while i < n:
        if not out[i]:
            i += 1
            continue
        j = i
        while j < n and out[j] == out[i]:
            j += 1
        if j - i > MAX_HEADING_BURST_RUN:
            for k in range(i, j):
                out[k] = 0
        i = j
    return out


def _height_tiers(heights: list[float]) -> list[float]:
    """Cluster heading heights into representative tiers, largest first (a tier per size level)."""
    tiers: list[float] = []
    for height in sorted(set(heights), reverse=True):
        if not tiers or (tiers[-1] - height) > tiers[-1] * OCR_HEADING_TIER_TOLERANCE:
            tiers.append(height)
    return tiers


def _structure_roles(per_page: list[tuple], profile) -> list[list[str]]:
    """A structure type ('P' or 'H1'..'H6') for each line, per page.

    Born-digital headings come from the document-global typographic profile. Recognizer output
    (Rebind's OCR, or a hidden OCR layer over a scan) has no reliable font size, so its headings are
    instead recovered geometrically -- size, isolation and shortness together (`_ocr_heading_heights`)
    -- with heading levels assigned from document-global size tiers. Levels are then normalized so
    the sequence starts at H1 and never skips a level, which PDF/UA requires (7.4.2).
    """
    ocr_headings: list[dict[int, float]] = []   # per page: {id(line): height} for OCR headings
    for src_page, lines, used_ocr in per_page:
        page_is_ocr = used_ocr or _is_ocr_over_scan(src_page)
        ocr_headings.append(_ocr_heading_heights(lines) if page_is_ocr else {})

    tiers = _height_tiers([h for page in ocr_headings for h in page.values()])

    def tier_level(height: float) -> int:
        for i, tier in enumerate(tiers):
            if abs(height - tier) <= tier * OCR_HEADING_TIER_TOLERANCE:
                return i + 1
        return len(tiers) or 1

    raw: list[list[int]] = []   # per page: 0 for paragraph, or the raw heading level (>=1)
    for (src_page, lines, used_ocr), page_headings in zip(per_page, ocr_headings):
        page_is_ocr = used_ocr or _is_ocr_over_scan(src_page)
        page_levels: list[int] = []
        for line in lines:
            if page_is_ocr:
                page_levels.append(tier_level(page_headings[id(line)]) if id(line) in page_headings else 0)
            elif line.ocr_confidence is not None:
                page_levels.append(0)
            elif (profile.role_of(line, page_height=src_page.height) == "heading"
                  and len(line.text.strip()) >= MIN_HEADING_CHARS):
                page_levels.append(profile.heading_level(style_of(line)) or 1)
            else:
                page_levels.append(0)
        if not page_is_ocr:
            # OCR headings already carry their own isolation signal (_ocr_heading_heights);
            # only born-digital candidacy (style + length alone) needs the burst check too.
            page_levels = _demote_heading_bursts(page_levels)
        raw.append(page_levels)

    if not any(lvl for page in raw for lvl in page):
        return [["P"] * len(page_levels) for page_levels in raw]

    # Anchor H1 to whichever heading the reader meets FIRST in reading order, not to the single
    # largest font anywhere in the document. Global size-rank alone can let a heading appearing
    # later -- an emphasized "References" header, say -- usurp H1 out from under the document's
    # real first heading (confirmed on a real sample: raw output was H2, H1, H3...). PDF/UA and
    # Adobe's own checker both require the first heading to be H1. Anything at least as large as
    # that first heading is treated as an equally top-level sibling, not promoted above H1 --
    # there is no level above H1 to fabricate, and real documents legitimately have several
    # top-level headings (chapter titles) of the same or incidentally differing size.
    first_raw = next(lvl for page in raw for lvl in page if lvl)

    # Assign each NEW raw style (in reading-order first-appearance) at most one level deeper than
    # the CURRENTLY OPEN branch -- not the deepest level ever reached historically. This is normal
    # outline-nesting semantics (same as HTML/EPUB headings): returning to a shallower, previously
    # -seen level CLOSES any deeper branch that was open, so a brand-new style encountered after
    # that can only go one level past wherever the outline currently sits, not past its historical
    # peak. Getting this wrong is the difference between satisfying veraPDF's PDF/UA-2 check and
    # Adobe Acrobat's own stricter "Appropriate nesting" check: veraPDF is satisfied as long as the
    # GLOBAL SET of levels used has no gaps, but Adobe requires the SEQUENCE itself to never skip
    # locally. Confirmed on a real sample that validated PDF/UA-2 compliant (veraPDF, 0 failures)
    # yet still failed Adobe's check: H2, H3, H2, H2, H2, [new style] -- tracking the historical
    # max (3, from the H3) let the new style become H4; tracking the CURRENT depth (2, since the
    # H3 branch had already closed by returning to H2) correctly gives it H3 instead.
    level_map: dict[int, int] = {}
    current_depth = 0
    for page_levels in raw:
        for lvl in page_levels:
            if not lvl:
                continue
            if lvl in level_map:
                current_depth = level_map[lvl]   # back to (or still at) a level already open
                continue
            level_map[lvl] = 1 if lvl <= first_raw else min(current_depth + 1, 6)
            current_depth = level_map[lvl]

    return [
        [f"H{level_map[lvl]}" if lvl else "P" for lvl in page_levels]
        for page_levels in raw
    ]


def _is_list_item(text: str) -> bool:
    item = _list_item_text(text)
    return item is not None and bool(item[0])


def _table_rows(cells: list[tuple[int, TextLine]]) -> list[list[tuple[int, TextLine]]]:
    """Group a table's cells (mcid, line) into rows by vertical band, each row sorted left to right."""
    if not cells:
        return []
    heights = sorted(ln.bbox[3] - ln.bbox[1] for _mcid, ln in cells)
    band = (heights[len(heights) // 2] or 1.0) * 0.6
    ordered = sorted(cells, key=lambda cl: -((cl[1].bbox[1] + cl[1].bbox[3]) / 2))
    rows: list[list[tuple[int, TextLine]]] = []
    current: list[tuple[int, TextLine]] = []
    current_center: float | None = None
    for mcid, line in ordered:
        center = (line.bbox[1] + line.bbox[3]) / 2
        if current_center is None or current_center - center <= band:
            current.append((mcid, line))
            current_center = center if current_center is None else current_center
        else:
            rows.append(current)
            current = [(mcid, line)]
            current_center = center
    if current:
        rows.append(current)
    return [sorted(row, key=lambda cl: cl[1].bbox[0]) for row in rows]


def _tagged_table(pdf: pikepdf.Pdf, cells: list[tuple[int, TextLine]],
                  document_elem: pikepdf.Object, page_obj: pikepdf.Object, leaf) -> pikepdf.Object:
    """Build a fully tagged `/Table` from a run of table cells: a regular grid of `/TR`s whose
    first row is header cells (`/TH` scoped to their column) and the rest data cells (`/TD`).

    Cells are snapped to document-consistent column positions (their clustered left edges), so every
    row emits one cell per column -- an empty cell where a value is missing -- making the grid
    regular, which is what lets assistive technology read a data cell against its column header.
    """
    columns: list[float] = []
    for x in sorted(line.bbox[0] for _mcid, line in cells):
        if not columns or x - columns[-1] > COLUMN_ALIGN_TOLERANCE_PT:
            columns.append(x)

    def column_of(line: TextLine) -> int:
        return min(range(len(columns)), key=lambda c: abs(line.bbox[0] - columns[c]))

    table = pdf.make_indirect(Dictionary(
        Type=Name.StructElem, S=Name.Table, P=document_elem, K=Array([])))
    trs: list[pikepdf.Object] = []
    header_texts: list[str] = []
    row_count = 0
    for row_index, row in enumerate(_table_rows(cells)):
        row_count += 1
        tr = pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=Name.TR, P=table, K=Array([])))
        by_column: dict[int, tuple[int, TextLine]] = {}
        for mcid, line in row:
            by_column.setdefault(column_of(line), (mcid, line))
        is_header = row_index == 0
        cell_type = Name.TH if is_header else Name.TD
        row_cells: list[pikepdf.Object] = []
        for c in range(len(columns)):
            if c in by_column:
                mcid, line = by_column[c]
                extra = {"A": Dictionary(O=Name.Table, Scope=Name.Column)} if is_header else None
                row_cells.append(leaf(mcid, cell_type, tr, extra))
                if is_header:
                    header_texts.append(line.text.strip())
            else:
                # An empty cell keeps the grid regular; it holds no content, so no marked content.
                row_cells.append(pdf.make_indirect(Dictionary(
                    Type=Name.StructElem, S=cell_type, P=tr, K=Array([]))))
        tr.K = Array(row_cells)
        trs.append(tr)
    table.K = Array(trs)
    summary = _table_summary(len(columns), row_count, header_texts)
    table.Alt = String(summary)
    # Also the dedicated /Summary Table attribute (ISO 32000-2's own mechanism for this, the same
    # /A <</O /Table ...>> pattern already used for /Scope on header cells) -- confirmed necessary
    # on a real sample: Adobe Acrobat's "Tables must have a summary" check kept failing with /Alt
    # alone, even though /Alt is the generic PDF/UA alternate-description mechanism and should have
    # been enough on its own.
    table.A = Dictionary(O=Name.Table, Summary=String(summary))
    return table


def _table_summary(column_count: int, row_count: int, header_texts: list[str]) -> str:
    """An honest, non-fabricated /Alt for a /Table (Adobe's 'Tables must have a summary' check).

    Built entirely from structure Rebind already knows -- column/row count and header cell text --
    never a guess at what the table is *about* (invariant 1: never fabricate a table's meaning).
    """
    summary = f"Table with {column_count} columns and {row_count} rows."
    if header_texts:
        summary += f" Column headers: {', '.join(header_texts)}."
    return summary


def _page_structure(pdf: pikepdf.Pdf, lines: list[TextLine], page_roles: list[str],
                    document_elem: pikepdf.Object, page_obj: pikepdf.Object):
    """Build the structure elements for one page from its lines, grouping list and table runs.

    Returns (top-level elements to add under the document, owners) where `owners[mcid]` is the leaf
    element that directly holds that MCID -- what the page's ParentTree entry indexes.
    """
    n = len(lines)
    table_line_ids = detect_table_lines(lines)
    is_table = [id(line) in table_line_ids for line in lines]
    owners: list[pikepdf.Object | None] = [None] * n
    tops: list[pikepdf.Object] = []

    def leaf(mcid: int, structure_type, parent, extra: dict | None = None) -> pikepdf.Object:
        elem = pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=structure_type, P=parent, Pg=page_obj, K=mcid, **(extra or {})))
        owners[mcid] = elem
        return elem

    i = 0
    while i < n:
        if is_table[i]:
            # Extend from the first detected row to the last, absorbing sparse rows in between
            # (see MAX_INTERNAL_TABLE_GAP) so no row is dropped and the table is not fragmented.
            last = i
            j = i + 1
            # (j - last - 1) is the count of undetected lines since the last detected row.
            while j < n and (j - last - 1) <= MAX_INTERNAL_TABLE_GAP:
                if is_table[j]:
                    last = j
                j += 1
            table = _tagged_table(pdf, [(m, lines[m]) for m in range(i, last + 1)],
                                  document_elem, page_obj, leaf)
            tops.append(table)
            i = last + 1
            continue

        if _is_list_item(lines[i].text):
            j = i
            while j < n and not is_table[j] and _is_list_item(lines[j].text):
                j += 1
            if j - i >= MIN_LIST_ITEMS:
                lst = pdf.make_indirect(Dictionary(
                    Type=Name.StructElem, S=Name.L, P=document_elem, K=Array([])))
                lis = []
                for mcid in range(i, j):
                    li = pdf.make_indirect(Dictionary(
                        Type=Name.StructElem, S=Name.LI, P=lst, K=Array([])))
                    li.K = Array([leaf(mcid, Name.LBody, li)])
                    lis.append(li)
                lst.K = Array(lis)
                tops.append(lst)
                i = j
                continue

        tops.append(leaf(i, Name("/" + page_roles[i]), document_elem))
        i += 1

    return tops, owners


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


def remediate(source: Path, target: Path, *, title: str | None = None, lang: str = "en",
              dpi: int = 300, alt_texts: dict[str, str] | None = None,
              darken_contrast: bool = False) -> RemediationResult:
    """Write `target`: the source made accessible, looking exactly like the original.

    The original pages are kept verbatim (vector text stays crisp, a scan stays a scan) and marked
    as an artifact; an invisible, tagged text layer is added over them and referenced from a PDF/UA
    structure tree. Embedded figures are decorative by default (compliant); pass `alt_texts`
    (keyed by the figure ids in a prior result's `.figures`) to promote a figure to a tagged
    `/Figure` with that description.

    `darken_contrast` is the one option that changes how the document *looks*: text failing WCAG
    AA is darkened just enough to pass, keeping its hue (see `recolor`). Off by default, and the
    app only offers it once it has measured a real failure to offer it about.
    """
    source, target = Path(source), Path(target)
    alt_texts = alt_texts or {}
    source_pages = list(extract_pages(source))
    profile = build_profile(source_pages)

    engine = OcrEngine()
    ocr_pages: list[int] = []
    empty_pages: list[int] = []
    added_layer = False

    # Pass one: recover each page's text lines (from its text layer or OCR) in reading order.
    per_page: list[tuple] = []
    layouts: list[tuple] = []
    for src_page in source_pages:
        lines, used_ocr, layout = _lines_for(source, src_page, engine, dpi, profile)
        layouts.append((src_page, layout))
        if used_ocr and lines:
            ocr_pages.append(src_page.number)
            added_layer = True
        elif used_ocr:
            empty_pages.append(src_page.number)
        per_page.append((src_page, lines, used_ocr))
    roles = _structure_roles(per_page, profile)
    document_captions = _document_captions(per_page)

    # Pass two: build the tagged, appearance-preserving output.
    pdf = pikepdf.open(source)
    # Runs before the per-page loop below (not after, as originally written) specifically so that
    # loop only ever sees the annotations that survive -- the external links it goes on to tag into
    # the structure tree -- never the legacy-destination ones this call is about to remove.
    _strip_legacy_destinations(pdf)

    # Recolouring happens before the page's own content is wrapped as an artifact. Anything that
    # rasterizes a page (a rebuilt page, a figure crop, the contrast re-measurement) reads from a
    # *file*, not from this in-memory Pdf, so the corrected document is written out once here and
    # used as the render source from then on -- otherwise those renders would quietly show the
    # original, uncorrected colours.
    render_source = source
    recoloured = 0
    if darken_contrast:
        for page in pdf.pages:
            recoloured += recolor.darken_failing_text(pdf, page)
        if recoloured:
            render_source = target.with_name(target.stem + ".recoloured.tmp.pdf")
            pdf.save(render_source)

    struct_root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    # PDF/UA-2 (ISO 14289-2) namespace. All the structure types below (/P, /H1-/H6, /Table, /TR,
    # /TD, /TH, /L, /LI, /LBody, /Figure) are retained as-is in the PDF 2.0 Standard Structure
    # Namespace -- confirmed against `verapdf -f ua2` (see docs/decisions/ for the spike). Only the
    # root Document element needs /NS set explicitly; every descendant inherits it, so nothing else
    # in this function's tag-building changes. Getting the namespace URI wrong (e.g. the plausible
    # but incorrect "http://iso.org/pdf/ssn") fails clause 8.2.5.2 silently -- confirmed by trial.
    PDF2_SSN_NAMESPACE = "http://iso.org/pdf2/ssn"
    ssn_namespace = pdf.make_indirect(Dictionary(Type=Name.Namespace, NS=String(PDF2_SSN_NAMESPACE)))
    document_elem = pdf.make_indirect(
        Dictionary(Type=Name.StructElem, S=Name.Document, NS=ssn_namespace, P=struct_root, K=Array([]))
    )
    struct_root.K = Array([document_elem])
    struct_root.RoleMap = Dictionary()
    struct_root.Namespaces = Array([ssn_namespace])
    parent_tree_nums = Array([])
    # Page StructParents keys use 0..page_count-1 (the enumerate index below); annotation
    # StructParent keys start right after, so neither numbering ever collides in the flat Nums array.
    next_parent_key = len(source_pages)
    font = pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica, Encoding=Name.WinAnsiEncoding))
    undescribed_figures: list[dict] = []
    figure_boxes: dict[int, tuple] = {}
    heading_entries: list[tuple[int, pikepdf.Object, str]] = []   # (level, struct_elem, title)

    for struct_parent, (page, (src_page, lines, used_ocr), page_roles) in enumerate(
        zip(pdf.pages, per_page, roles)
    ):
        figures = _page_figures(src_page)
        vector = _vector_figures(src_page, list(lines), figures)
        figures += [(fid, bbox) for fid, bbox, _caption in vector]
        anchored = {fid: caption for fid, _bbox, caption in vector}
        # A figure sitting directly under (or, less commonly, above) a "Fig. N ..." caption needs
        # no manual description at all: the author's own words are more accurate than anything
        # Rebind could invent, and this skips the app's describe step entirely. A thin local match
        # (just the marker, e.g. a "(Continued)" page-break artifact -- not substantial per WCAG
        # 1.1.1) falls back to document_captions, which may have found the real caption elsewhere.
        # Explicit user-supplied text (alt_texts) always wins over either -- the user may have
        # deliberately typed something better, or the caption match may be imperfect.
        #
        # If NOTHING substantial turns up anywhere, this returns None on purpose, so the figure
        # lands in the app's describe list and the user is asked. A bare label ("Fig. 8", or the
        # "Fig. 8 (Continued)" page-break artifact) must never be accepted as alt text just because
        # it exists: it satisfies a checker's "has /Alt" box while telling a screen-reader user
        # nothing about the image, which is the exact failure WCAG 1.1.1 is about. Silence that
        # prompts a human beats a placeholder that suppresses the prompt.
        def caption_for(fid: str, bbox: tuple) -> str | None:
            local = anchored.get(fid) or _figure_caption(lines, bbox)
            if local and _caption_is_substantial(local):
                return local
            number = (_caption_number(local) if local else None) or _nearby_caption_number(lines, bbox)
            elsewhere = document_captions.get(number) if number else None
            if elsewhere and _caption_is_substantial(elsewhere):
                return elsewhere
            return None

        effective_alt = {fid: alt_texts.get(fid) or caption_for(fid, bbox)
                         for fid, bbox in figures}
        described = [(fid, bbox) for fid, bbox in figures if effective_alt[fid]]
        undescribed = [(fid, bbox) for fid, bbox in figures if not effective_alt[fid]]
        rebuild = _has_marked_content(page)
        page_image = (render_page_to_image(render_source, src_page.number, dpi=dpi)
                      if rebuild or figures else None)

        # Draw each described figure (a crop of the rendered region) inside a tagged /Figure.
        figure_stream = b""
        extra_xobjects: dict = {}
        figure_specs: list[tuple] = []   # (mcid, alt, bbox)
        mcid = len(lines)
        for k, (fid, bbox) in enumerate(described):
            extra_xobjects[f"Fig{k}"] = _figure_xobject(
                pdf, page_image, bbox, src_page.width, src_page.height)
            x0, y0, x1, y1 = bbox
            figure_stream += (
                f"/Figure <</MCID {mcid}>> BDC q {x1 - x0:.2f} 0 0 {y1 - y0:.2f} "
                f"{x0:.2f} {y0:.2f} cm /Fig{k} Do Q EMC\n").encode()
            figure_specs.append((mcid, effective_alt[fid], bbox))
            mcid += 1

        overlay = _tagged_text_stream(lines, "RebindF") + figure_stream

        if rebuild:
            _rebuild_page(pdf, page, page_image, overlay, extra_xobjects, font,
                          src_page.width, src_page.height)
        else:
            page.contents_add(pdf.make_stream(b"/Artifact BMC\n"), prepend=True)
            page.contents_add(pdf.make_stream(b"EMC\n" + overlay), prepend=False)
            _add_font(pdf, page, font, "RebindF")
            for name, obj in extra_xobjects.items():
                _add_xobject(pdf, page, obj, name)
        page.obj.StructParents = struct_parent
        page.obj.Tabs = Name.S

        tops, owners = _page_structure(pdf, lines, page_roles, document_elem, page.obj)
        for mcid, role in enumerate(page_roles):
            if role.startswith("H") and lines[mcid].text.strip():
                heading_entries.append((int(role[1:]), owners[mcid], lines[mcid].text.strip()))
        for fmcid, alt, bbox in figure_specs:
            figure_elem = pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name.Figure, P=document_elem, Pg=page.obj, K=fmcid,
                Alt=String(alt),
                A=Dictionary(O=Name.Layout, BBox=Array([round(v, 2) for v in bbox]))))
            tops.append(figure_elem)
            owners.append(figure_elem)
        document_elem.K.extend(tops)
        parent_tree_nums.append(struct_parent)
        parent_tree_nums.append(pdf.make_indirect(Array(owners)))

        # Tag every surviving annotation (an external URI/GoToR link -- _strip_legacy_destinations
        # already removed any internal-destination one) into the structure tree: a /Link element
        # whose sole child is an object reference (OBJR) to the annotation, cross-linked via the
        # annotation's own /StructParent key into a NEW slot in the parent tree. Annotation
        # StructParent keys share the same flat Nums numbering space as the page StructParents keys
        # above but must never collide with them, so they start only after every page's is used.
        for annot in page.obj.get("/Annots") or []:
            link_description = _link_alt(annot)
            link_elem = pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name.Link, P=document_elem,
                K=Array([Dictionary(Type=Name.OBJR, Obj=annot, Pg=page.obj)]),
                Alt=String(link_description),
            ))
            # Also set directly on the annotation's own /Contents -- a real sample kept failing
            # Adobe Acrobat's "Tagged annotations" check with /Alt on the struct element alone.
            annot.Contents = String(link_description)
            annot.StructParent = next_parent_key
            parent_tree_nums.append(next_parent_key)
            parent_tree_nums.append(link_elem)
            document_elem.K.append(link_elem)
            next_parent_key += 1

        figure_boxes[src_page.number] = tuple(bbox for _fid, bbox in figures)
        for fid, bbox in undescribed:
            undescribed_figures.append({
                "id": fid, "page": src_page.number,
                "thumb": _crop_data_uri(page_image, bbox, src_page.width, src_page.height)})

    struct_root.ParentTree = pdf.make_indirect(Dictionary(Nums=parent_tree_nums))
    struct_root.ParentTreeNextKey = next_parent_key
    pdf.Root.StructTreeRoot = struct_root
    _build_outline(pdf, heading_entries)
    _set_metadata(pdf, title=title or source.stem, lang=lang)
    pdf.save(target, min_version="2.0")   # PDF/UA-2 requires a PDF 2.0 base (ISO 32000-2)
    pdf.close()
    self_check = self_check_pdf_ua2(target)

    # Evidence for the two checks a machine may never sign off on. Both read the SOURCE: contrast
    # is a property of the page a reader sees, which remediation deliberately leaves untouched, and
    # the reading order is the one just built above.
    orders = [review.page_order(src_page, layout, figure_boxes.get(src_page.number, ()))
              for src_page, layout in layouts]
    # Only the flagged pages are rendered for their thumbnail -- on a 300-page catalogue the whole
    # point is that the review is a handful of pages, and so is the work of preparing it.
    thumbs = {}
    for src_page, _layout in layouts:
        order = next(o for o in orders if o.page == src_page.number)
        if order.needs_review:
            image = render_page_to_image(render_source, src_page.number, dpi=REVIEW_THUMB_DPI)
            thumbs[src_page.number] = _crop_data_uri(
                image, (0.0, 0.0, src_page.width, src_page.height),
                src_page.width, src_page.height, max_side=REVIEW_THUMB_PX)
    # After recolouring, the pages extracted at the top of this function still carry the *old*
    # declared ink colours, and the ink is what `contrast` trusts the declaration for. Re-read them
    # from the corrected document so the report describes what was actually produced.
    measured_pages = list(extract_pages(render_source)) if render_source != source else source_pages
    measured = contrast.measure(render_source, measured_pages, figures=figure_boxes)
    if render_source != source:
        render_source.unlink(missing_ok=True)

    return RemediationResult(
        pdf_path=target, page_count=len(source_pages),
        ocr_pages=tuple(ocr_pages), empty_pages=tuple(empty_pages), added_text_layer=added_layer,
        figures=tuple(undescribed_figures),
        structure_ok=self_check.ok, structure_issues=self_check.issues,
        reading_order=review.summarize(orders, thumbs),
        contrast=contrast.summarize(measured, darkened=recoloured),
    )


def _build_outline(pdf: pikepdf.Pdf, heading_entries: list[tuple[int, pikepdf.Object, str]]) -> None:
    """Build a document outline (bookmarks) from the headings remediate() already recovered, nested
    by level, using real PDF 2.0 **structure destinations** -- an `/SD` reference straight into the
    heading's own structure element -- rather than a page/coordinate destination, the kind PDF/UA-2
    clause 8.8 forbids and `_strip_legacy_destinations` removes from the source.

    A source's own outline (built by whatever authored it) always used the legacy kind and is
    stripped; without this, a document that HAD bookmarks would end up with none at all -- a real
    navigation loss for long documents (confirmed: Adobe's checker flags "Bookmarks are present in
    large documents" for exactly this). `heading_entries` is `(level, struct_elem, title)` in
    reading order, collected as headings are built (`remediate()`'s main loop); a heading whose
    level jumps deeper than the currently open ancestor simply nests one level under it (the
    normal case, since `_structure_roles` already guarantees levels never skip).
    """
    if not heading_entries:
        return

    outlines = pdf.make_indirect(Dictionary(Type=Name.Outlines))

    def append_child(parent: pikepdf.Object, item: pikepdf.Object) -> None:
        if "/First" not in parent:
            parent.First = item
            parent.Last = item
        else:
            item.Prev = parent.Last
            parent.Last.Next = item
            parent.Last = item
        parent.Count = int(parent.get("/Count", 0)) + 1

    stack: list[tuple[int, pikepdf.Object]] = []   # (level, item) for each currently open ancestor
    for level, elem, title in heading_entries:
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = stack[-1][1] if stack else outlines
        item = pdf.make_indirect(Dictionary(
            Title=String(title), Parent=parent,
            Dest=Array([Dictionary(S=Name.XYZ, SD=elem)]),
        ))
        append_child(parent, item)
        stack.append((level, item))

    pdf.Root.Outlines = outlines


def _link_alt(annot: pikepdf.Object) -> str:
    """An honest, non-fabricated /Alt for a /Link structure element (Adobe's 'Other elements
    alternate text' check). Built entirely from the annotation's own action -- a mechanical fact
    ("Link to <uri>"), never a guess at the link's purpose. A source PDF's own broken/malformed
    URI (confirmed in a real sample: a publisher production defect, not something Rebind
    introduces) is reported as-is -- honestly stating a broken thing is broken, not fabricating a
    plausible-looking replacement.
    """
    action = annot.get("/A")
    if action is not None:
        if action.get("/S") == Name.URI and "/URI" in action:
            return f"Link to {action.URI}"
        if action.get("/S") == Name.GoToR and "/F" in action:
            return f"Link to {action.F}"
    return "Link"


def _strip_legacy_destinations(pdf: pikepdf.Pdf) -> None:
    """Remove document navigation that PDF/UA-2 clause 8.8 forbids.

    PDF/UA-2 requires every internal destination (an outline/bookmark entry, a named destination,
    an OpenAction, or a Link annotation's GoTo target) to be a *structure destination* -- one that
    names a structure element, not a page + coordinates. Nothing before PDF 2.0 could produce that,
    so a born-digital source PDF's own navigation (an outline built by whatever authored it -- Word,
    LaTeX, WeasyPrint -- or in-text cross-reference/TOC links) is carried through verbatim on a page
    kept verbatim, and it fails this clause (confirmed on a real publisher sample: 137 Link-
    annotation and Outline destinations in one document). Rebind does not yet build its own
    structure-destination navigation (a real feature, tracked separately), so the safe choice is to
    drop the legacy kind rather than ship a document that claims PDF/UA-2 and fails it. This is real
    navigation lost, not merely a formality -- worth restoring properly, see progress notes.

    A *working* external link (a URI action, or GoToR into another file) is untouched -- clause 8.8
    is only about destinations *within* this document. A URI link whose target is unusable is
    dropped too, for a different reason entirely; see `_is_usable_uri`.
    """
    root = pdf.Root
    if "/Outlines" in root:
        del root.Outlines
    if "/OpenAction" in root:
        del root.OpenAction
    names = root.get("/Names")
    if names is not None and "/Dests" in names:
        del names.Dests

    for page in pdf.pages:
        annots = page.obj.get("/Annots")
        if annots is None:
            continue
        keep = []
        for annot in annots:
            is_link = annot.get("/Subtype") == Name.Link
            action = annot.get("/A") or Dictionary()
            is_internal_link = is_link and ("/Dest" in annot or action.get("/S") == Name.GoTo)
            is_broken_link = (
                is_link
                and action.get("/S") == Name.URI
                and not _is_usable_uri(str(action.get("/URI", "")))
            )
            if not is_internal_link and not is_broken_link:
                keep.append(annot)
        if len(keep) != len(annots):
            page.obj.Annots = Array(keep)


# Schemes whose targets are meaningless without an authority ("//host"). A URI in one of these
# with no host is not a link anyone can follow, whatever the producer meant by it.
_AUTHORITY_SCHEMES = frozenset({"http", "https", "ftp", "ftps"})


def _is_usable_uri(uri: str) -> bool:
    """Whether a Link annotation's URI target is something a reader could actually follow.

    A publisher's own auto-linker can fire on text that merely *looks* like a URL and emit a target
    that resolves to nothing -- confirmed in a real sample (1429254.pdf), where all four URI links
    are production defects: "http:0.5<en-dash>0.75" over a numeric range, "http:theminplace.We"
    over a sentence boundary. Rebind cannot repair those (there is no correct target to guess), and
    keeping them costs twice: a screen-reader user is announced a link that goes nowhere, and Adobe
    Acrobat's "Tagged annotations" / "Other elements alternate text" checks fail on them regardless
    of how the tagging is done. So they are dropped, exactly as legacy internal destinations are --
    removing a thing that never worked, never inventing a plausible-looking replacement.

    Deliberately permissive: anything with a scheme this can't reason about (doi:, urn:, tel:, a
    custom handler) is kept. Only a clearly unusable target is removed.
    """
    uri = uri.strip()
    if not uri or any(ch.isspace() or ord(ch) < 0x20 for ch in uri):
        return False
    parsed = urlsplit(uri)
    if not parsed.scheme:
        return False
    if parsed.scheme.lower() in _AUTHORITY_SCHEMES:
        # A bare host is legal DNS but never how a real document links out; requiring a dot is what
        # separates "example.com" from the auto-linker's "0.5<en-dash>0.75" garbage... which also
        # has dots, hence the hostname character check.
        host = parsed.hostname or ""
        return "." in host and all(c.isalnum() or c in "-." for c in host)
    if parsed.scheme.lower() == "mailto":
        return "@" in parsed.path
    return True


def _set_metadata(pdf: pikepdf.Pdf, *, title: str, lang: str) -> None:
    """Language, title, PDF/UA identifier, and the marked / display-title flags a reader needs."""
    pdf.Root.Lang = String(lang)
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.Root.ViewerPreferences = Dictionary(DisplayDocTitle=True)
    with pdf.open_metadata() as meta:
        # Publisher-produced PDFs (confirmed: Elsevier's own pipeline, in a real sample) can carry
        # a stray, non-namespaced XMP element -- a DRM/fingerprinting artifact. It's well-formed
        # XML, but it breaks veraPDF's strict metadata parser badly enough that it stops seeing
        # OUR dc:title/pdfuaid entries too (clauses 5 and 8.11.1 both fail), even though pikepdf's
        # own reader still finds them fine. A legitimate XMP property always belongs to some
        # namespace, so any top-level key with none is noise, never accessibility-relevant --
        # strip it before adding ours.
        for key in list(meta.keys()):
            if not key.startswith("{"):
                del meta[key]
        meta["dc:title"] = title
        meta["dc:language"] = lang
        meta["pdfuaid:part"] = "2"          # PDF/UA-2 identification (veraPDF clause 5)
        meta["pdfuaid:rev"] = "2024"        # required alongside part for PDF/UA-2
    pdf.docinfo["/Title"] = title
