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

from .extract import TextLine, extract_pages
from .ocr import OcrEngine, recognize, render_page_to_image


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


def _page_image_stream(pdf: pikepdf.Pdf, pil_image) -> pikepdf.Object:
    buffer = io.BytesIO()
    pil_image.save(buffer, format="JPEG", quality=90)
    image_obj = pdf.make_stream(buffer.getvalue())
    image_obj.Type = Name.XObject
    image_obj.Subtype = Name.Image
    image_obj.Width = pil_image.width
    image_obj.Height = pil_image.height
    image_obj.ColorSpace = Name.DeviceRGB
    image_obj.BitsPerComponent = 8
    image_obj.Filter = Name.DCTDecode
    return image_obj


def remediate(source: Path, target: Path, *, title: str | None = None, lang: str = "en",
              dpi: int = 300) -> RemediationResult:
    """Write `target`: the source made accessible, looking like the original."""
    from PIL import Image

    source, target = Path(source), Path(target)
    source_pages = list(extract_pages(source))

    pdf = pikepdf.Pdf.new()
    engine = OcrEngine()
    ocr_pages: list[int] = []
    empty_pages: list[int] = []
    added_layer = False

    struct_root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    document_elem = pdf.make_indirect(
        Dictionary(Type=Name.StructElem, S=Name.Document, P=struct_root, K=Array([]))
    )
    struct_root.K = Array([document_elem])
    struct_root.RoleMap = Dictionary()
    parent_tree_nums = Array([])
    font = pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica, Encoding=Name.WinAnsiEncoding))

    for struct_parent, src_page in enumerate(source_pages):
        width_pt, height_pt = src_page.width, src_page.height
        image = render_page_to_image(source, src_page.number, dpi=dpi)
        image_obj = _page_image_stream(pdf, Image.fromarray(image).convert("RGB"))

        lines, used_ocr = _lines_for(source, src_page, engine, dpi)
        if used_ocr and lines:
            ocr_pages.append(src_page.number)
            added_layer = True
        elif used_ocr:
            empty_pages.append(src_page.number)

        # `/Artifact BMC` (not BDC): a bare artifact marked-content sequence takes only a tag, no
        # properties dictionary. `BDC` would expect two operands and the sequence would not open,
        # leaving the image read as untagged content (veraPDF clause 7.1).
        content = (
            f"/Artifact BMC q {width_pt:.2f} 0 0 {height_pt:.2f} 0 0 cm /Im0 Do Q EMC\n".encode()
            + _tagged_text_stream(lines, "RebindF")
        )
        page_obj = pdf.make_indirect(Dictionary(
            Type=Name.Page,
            MediaBox=Array([0, 0, width_pt, height_pt]),
            Resources=Dictionary(XObject=Dictionary(Im0=image_obj), Font=Dictionary(RebindF=font)),
            Contents=pdf.make_stream(content),
            StructParents=struct_parent,
            Tabs=Name.S,
        ))
        pdf.pages.append(pikepdf.Page(page_obj))

        page_elems = [
            pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name.P, P=document_elem, Pg=page_obj, K=mcid))
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
