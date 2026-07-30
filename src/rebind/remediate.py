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
from dataclasses import dataclass
from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from .assemble import _is_ocr_over_scan
from .extract import TextLine, extract_pages
from .ocr import OcrEngine, recognize, render_page_to_image
from .profile import build_profile, style_of


@dataclass
class RemediationResult:
    pdf_path: Path
    page_count: int
    ocr_pages: tuple[int, ...] = ()          # pages we recognized (text may contain OCR errors)
    empty_pages: tuple[int, ...] = ()         # scanned pages where OCR recovered nothing
    added_text_layer: bool = False


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
    return lines, used_ocr


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


def _structure_roles(per_page: list[tuple], profile) -> list[list[str]]:
    """A structure type ('P' or 'H1'..'H6') for each line, per page.

    Headings are detected from the document-global typographic profile -- but only on born-digital
    text: recognizer output (Rebind's OCR, or a hidden OCR layer over a scan) has noisy font sizes
    that manufacture spurious headings, so its lines are all paragraphs. Heading levels are then
    normalized so the sequence starts at H1 and never skips a level, which PDF/UA requires (7.4.2).
    """
    raw: list[list[int]] = []   # per page: 0 for paragraph, or the raw heading level (>=1)
    for src_page, lines, used_ocr in per_page:
        page_is_ocr = used_ocr or _is_ocr_over_scan(src_page)
        page_levels: list[int] = []
        for line in lines:
            if page_is_ocr or line.ocr_confidence is not None:
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


def _rebuild_page_from_image(pdf: pikepdf.Pdf, page: pikepdf.Page, lines: list[TextLine],
                             font: pikepdf.Object, source: Path, page_number: int,
                             width_pt: float, height_pt: float, dpi: int) -> None:
    """Replace the page's content with a rendered image (artifact) + tagged text -- a clean slate
    that discards any pre-existing marked content while looking identical (rendered at `dpi`)."""
    from PIL import Image

    image = render_page_to_image(source, page_number, dpi=dpi)
    pil = Image.fromarray(image).convert("RGB")
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
        + _tagged_text_stream(lines, "RebindF")
    )
    page.obj.Contents = pdf.make_stream(content)
    page.obj.Resources = Dictionary(XObject=Dictionary(Im0=image_obj), Font=Dictionary(RebindF=font))


def remediate(source: Path, target: Path, *, title: str | None = None, lang: str = "en",
              dpi: int = 300) -> RemediationResult:
    """Write `target`: the source made accessible, looking exactly like the original.

    The original pages are kept verbatim (vector text stays crisp, a scan stays a scan) and marked
    as an artifact; an invisible, tagged text layer is added over them and referenced from a PDF/UA
    structure tree.
    """
    source, target = Path(source), Path(target)
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
    document_elem = pdf.make_indirect(
        Dictionary(Type=Name.StructElem, S=Name.Document, P=struct_root, K=Array([]))
    )
    struct_root.K = Array([document_elem])
    struct_root.RoleMap = Dictionary()
    parent_tree_nums = Array([])
    font = pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica, Encoding=Name.WinAnsiEncoding))

    for struct_parent, (page, (src_page, lines, used_ocr), page_roles) in enumerate(
        zip(pdf.pages, per_page, roles)
    ):
        if _has_marked_content(page):
            # Already carries marked content (e.g. a scan with a hidden OCR text layer): rebuild
            # from a clean render so tagged content is never nested inside an artifact.
            _rebuild_page_from_image(pdf, page, lines, font, source, src_page.number,
                                     src_page.width, src_page.height, dpi)
        else:
            # Keep the page verbatim (vector text stays crisp): wrap its content as an artifact,
            # then add the invisible tagged text over it. `/Artifact BMC` is a tag-only
            # marked-content sequence -- `BDC` needs two operands and would leave content untagged.
            page.contents_add(pdf.make_stream(b"/Artifact BMC\n"), prepend=True)
            page.contents_add(pdf.make_stream(b"EMC\n" + _tagged_text_stream(lines, "RebindF")),
                              prepend=False)
            _add_font(pdf, page, font, "RebindF")
        page.obj.StructParents = struct_parent
        page.obj.Tabs = Name.S

        page_elems = [
            pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name("/" + page_roles[mcid]),
                P=document_elem, Pg=page.obj, K=mcid))
            for mcid in range(len(lines))
        ]
        document_elem.K.extend(page_elems)
        parent_tree_nums.append(struct_parent)
        parent_tree_nums.append(pdf.make_indirect(Array(page_elems)))

    struct_root.ParentTree = pdf.make_indirect(Dictionary(Nums=parent_tree_nums))
    struct_root.ParentTreeNextKey = len(source_pages)
    pdf.Root.StructTreeRoot = struct_root
    _set_metadata(pdf, title=title or source.stem, lang=lang)
    pdf.save(target)
    pdf.close()
    return RemediationResult(
        pdf_path=target, page_count=len(source_pages),
        ocr_pages=tuple(ocr_pages), empty_pages=tuple(empty_pages), added_text_layer=added_layer,
    )


def _set_metadata(pdf: pikepdf.Pdf, *, title: str, lang: str) -> None:
    """Language, title, PDF/UA identifier, and the marked / display-title flags a reader needs."""
    pdf.Root.Lang = String(lang)
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.Root.ViewerPreferences = Dictionary(DisplayDocTitle=True)
    with pdf.open_metadata() as meta:
        meta["dc:title"] = title
        meta["dc:language"] = lang
        meta["pdfuaid:part"] = "1"          # PDF/UA-1 identification (veraPDF clause 5)
    pdf.docinfo["/Title"] = title
