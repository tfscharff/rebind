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
from dataclasses import dataclass, replace
from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from .extract import Page, TextLine, extract_pages
from .layout import COLUMN_ALIGN_TOLERANCE_PT, detect_table_lines
from .ocr import OcrEngine, recognize, render_page_to_image
from .profile import build_profile, style_of

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

# OCR heading recovery. A single OCR line's box height is noisy (a body line can crop tall), so no
# one signal is trusted: a heading must be markedly taller than the page's body text AND set apart
# by whitespace AND not fill the column -- the combination an inflated body line, which still sits
# inside its paragraph spanning the full width, cannot fake. Tuned conservatively: a missed heading
# stays an honest paragraph, which is safe; a fabricated one is not.
OCR_HEADING_HEIGHT_RATIO = 1.35     # a heading is at least this much taller than the body median
OCR_HEADING_MAX_WIDTH_RATIO = 0.75  # a line filling more than this of the widest line is body text
OCR_HEADING_ISOLATION_RATIO = 0.8   # whitespace above/below is at least this * the body height
OCR_HEADING_TIER_TOLERANCE = 0.15   # heading heights within this fraction are the same level

# A table spans from its first detected row to its last; lines in between that were not themselves
# detected as table rows are sparse rows (a subtotal, or a row with an empty cell -- too few
# side-by-side cells to detect alone) and are kept so no row is dropped. The gap is bounded so prose
# between two separate tables cannot merge them: at most this many consecutive undetected lines.
MAX_INTERNAL_TABLE_GAP = 2


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
        out.write(f"/P <</MCID {mcid}>> BDC\n".encode())
        out.write(b"q BT 3 Tr /" + font_name.encode() + b" 1 Tf\n")
        out.write(
            f"{size:.2f} 0 0 {size:.2f} {x0:.2f} {y0:.2f} Tm ({_escape(line.text)}) Tj\n".encode()
        )
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


def _lines_for(source: Path, src_page, engine: OcrEngine, dpi: int) -> tuple[list[TextLine], bool]:
    """The page's text lines in reading order, and whether OCR was used."""
    if src_page.has_text_layer:
        lines = list(src_page.lines)
        used_ocr = False
    else:
        image = render_page_to_image(source, src_page.number, dpi=dpi)
        lines = recognize(image, page_number=src_page.number, page_width=src_page.width,
                          page_height=src_page.height, engine=engine)
        used_ocr = True
    lines.sort(key=lambda ln: (-ln.bbox[3], ln.bbox[0]))     # reading order: top-to-bottom
    return _merge_bare_markers(lines), used_ocr


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
            elif profile.role_of(line, page_height=src_page.height) == "heading":
                page_levels.append(profile.heading_level(style_of(line)) or 1)
            else:
                page_levels.append(0)
        raw.append(page_levels)

    distinct = sorted({lvl for page in raw for lvl in page if lvl})
    level_map = {lvl: i + 1 for i, lvl in enumerate(distinct)}   # -> 1,2,3... no skips, start at 1
    return [
        [f"H{min(level_map[lvl], 6)}" if lvl else "P" for lvl in page_levels]
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
    for row_index, row in enumerate(_table_rows(cells)):
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
                mcid, _line = by_column[c]
                extra = {"A": Dictionary(O=Name.Table, Scope=Name.Column)} if is_header else None
                row_cells.append(leaf(mcid, cell_type, tr, extra))
            else:
                # An empty cell keeps the grid regular; it holds no content, so no marked content.
                row_cells.append(pdf.make_indirect(Dictionary(
                    Type=Name.StructElem, S=cell_type, P=tr, K=Array([]))))
        tr.K = Array(row_cells)
        trs.append(tr)
    table.K = Array(trs)
    return table


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
              dpi: int = 300, alt_texts: dict[str, str] | None = None) -> RemediationResult:
    """Write `target`: the source made accessible, looking exactly like the original.

    The original pages are kept verbatim (vector text stays crisp, a scan stays a scan) and marked
    as an artifact; an invisible, tagged text layer is added over them and referenced from a PDF/UA
    structure tree. Embedded figures are decorative by default (compliant); pass `alt_texts`
    (keyed by the figure ids in a prior result's `.figures`) to promote a figure to a tagged
    `/Figure` with that description.
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
    for src_page in source_pages:
        lines, used_ocr = _lines_for(source, src_page, engine, dpi)
        if used_ocr and lines:
            ocr_pages.append(src_page.number)
            added_layer = True
        elif used_ocr:
            empty_pages.append(src_page.number)
        per_page.append((src_page, lines, used_ocr))
    roles = _structure_roles(per_page, profile)

    # Pass two: build the tagged, appearance-preserving output.
    pdf = pikepdf.open(source)
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
    font = pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica, Encoding=Name.WinAnsiEncoding))
    undescribed_figures: list[dict] = []

    for struct_parent, (page, (src_page, lines, used_ocr), page_roles) in enumerate(
        zip(pdf.pages, per_page, roles)
    ):
        figures = _page_figures(src_page)
        described = [(fid, bbox) for fid, bbox in figures if fid in alt_texts]
        undescribed = [(fid, bbox) for fid, bbox in figures if fid not in alt_texts]
        rebuild = _has_marked_content(page)
        page_image = (render_page_to_image(source, src_page.number, dpi=dpi)
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
            figure_specs.append((mcid, alt_texts[fid], bbox))
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

        for fid, bbox in undescribed:
            undescribed_figures.append({
                "id": fid, "page": src_page.number,
                "thumb": _crop_data_uri(page_image, bbox, src_page.width, src_page.height)})

    struct_root.ParentTree = pdf.make_indirect(Dictionary(Nums=parent_tree_nums))
    struct_root.ParentTreeNextKey = len(source_pages)
    pdf.Root.StructTreeRoot = struct_root
    _strip_legacy_destinations(pdf)
    _set_metadata(pdf, title=title or source.stem, lang=lang)
    pdf.save(target, min_version="2.0")   # PDF/UA-2 requires a PDF 2.0 base (ISO 32000-2)
    pdf.close()
    return RemediationResult(
        pdf_path=target, page_count=len(source_pages),
        ocr_pages=tuple(ocr_pages), empty_pages=tuple(empty_pages), added_text_layer=added_layer,
        figures=tuple(undescribed_figures),
    )


def _strip_legacy_destinations(pdf: pikepdf.Pdf) -> None:
    """Remove document navigation that PDF/UA-2 clause 8.8 forbids.

    PDF/UA-2 requires every internal destination (an outline/bookmark entry, a named destination,
    an OpenAction) to be a *structure destination* -- one that names a structure element, not a
    page + coordinates. Nothing before PDF 2.0 could produce that, so a born-digital source PDF's
    own outline (built by whatever authored it -- Word, LaTeX, WeasyPrint) is carried through
    verbatim on a page kept verbatim, and it fails this clause. Rebind does not yet build its own
    structure-destination outline (a real feature, tracked separately), so the safe choice is to
    drop the legacy one rather than ship a document that claims PDF/UA-2 and fails it. This is a
    real navigation aid lost, not merely a formality -- worth restoring properly, see progress notes.
    """
    root = pdf.Root
    if "/Outlines" in root:
        del root.Outlines
    if "/OpenAction" in root:
        del root.OpenAction
    names = root.get("/Names")
    if names is not None and "/Dests" in names:
        del names.Dests


def _set_metadata(pdf: pikepdf.Pdf, *, title: str, lang: str) -> None:
    """Language, title, PDF/UA identifier, and the marked / display-title flags a reader needs."""
    pdf.Root.Lang = String(lang)
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.Root.ViewerPreferences = Dictionary(DisplayDocTitle=True)
    with pdf.open_metadata() as meta:
        meta["dc:title"] = title
        meta["dc:language"] = lang
        meta["pdfuaid:part"] = "2"          # PDF/UA-2 identification (veraPDF clause 5)
        meta["pdfuaid:rev"] = "2024"        # required alongside part for PDF/UA-2
    pdf.docinfo["/Title"] = title
