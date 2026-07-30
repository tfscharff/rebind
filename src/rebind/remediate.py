"""Remediate a PDF in place: keep the original appearance exactly, add accessibility.

This is the opposite of reconstruction. The source pages are copied **verbatim** -- every byte of
their visual content is preserved, so the output is visually identical to the input and vector text
stays crisp -- and accessibility is added only where it is missing:

- a page with no text layer (a pure scan) gets an *invisible* OCR text layer (render mode 3) drawn
  over it, so the words are selectable and readable by assistive technology without changing how
  the page looks;
- a page that already has text keeps it untouched;
- document language, title and the "tagged" flag are set.

The intervention is the minimum needed to make the file accessible without reconstructing it. The
structure tree (per-element tags) is added in a later step.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pikepdf
from pikepdf import Dictionary, Name

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


def _invisible_text_stream(lines: list[TextLine], font_name: str) -> bytes:
    """A content stream that draws `lines` as invisible text (render mode 3) at their positions.

    Appended after the page's own content, in default user space, so it never alters the visible
    page -- it only makes the words selectable and available to assistive technology.
    """
    out = io.BytesIO()
    out.write(b"q BT 3 Tr /" + font_name.encode() + b" 1 Tf\n")
    for line in lines:
        x0, y0, x1, y1 = line.bbox
        size = max(y1 - y0, 1.0)
        out.write(
            f"{size:.2f} 0 0 {size:.2f} {x0:.2f} {y0:.2f} Tm "
            f"({_escape(line.text)}) Tj\n".encode()
        )
    out.write(b"ET Q\n")
    return out.getvalue()


def _add_text_layer(pdf: pikepdf.Pdf, page: pikepdf.Page, lines: list[TextLine]) -> None:
    """Append an invisible text layer to an already-copied page, keeping its visual content."""
    resources = page.obj.get("/Resources")
    if resources is None:
        resources = pdf.make_indirect(Dictionary())
        page.obj["/Resources"] = resources
    fonts = resources.get("/Font")
    if fonts is None:
        fonts = Dictionary()
        resources["/Font"] = fonts
    font_name = "RebindOCR"
    fonts[Name("/" + font_name)] = pdf.make_indirect(
        Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                   Encoding=Name.WinAnsiEncoding)
    )
    stream = pdf.make_stream(_invisible_text_stream(lines, font_name))
    page.contents_add(stream, prepend=False)


def remediate(source: Path, target: Path, *, title: str | None = None, lang: str = "en",
              dpi: int = 200) -> RemediationResult:
    """Write `target`: the source made accessible, looking identical to the original."""
    source, target = Path(source), Path(target)
    source_pages = list(extract_pages(source))

    pdf = pikepdf.open(source)
    engine = OcrEngine()
    ocr_pages: list[int] = []
    empty_pages: list[int] = []
    added_layer = False

    for src_page, dst_page in zip(source_pages, pdf.pages):
        if src_page.has_text_layer:
            continue  # already selectable -- leave it exactly as it is
        image = render_page_to_image(source, src_page.number, dpi=dpi)
        lines = recognize(image, page_number=src_page.number, page_width=src_page.width,
                          page_height=src_page.height, engine=engine)
        if lines:
            _add_text_layer(pdf, pikepdf.Page(dst_page), lines)
            ocr_pages.append(src_page.number)
            added_layer = True
        else:
            empty_pages.append(src_page.number)

    _set_metadata(pdf, title=title or source.stem, lang=lang)
    pdf.save(target)
    pdf.close()
    return RemediationResult(
        pdf_path=target, page_count=len(source_pages),
        ocr_pages=tuple(ocr_pages), empty_pages=tuple(empty_pages), added_text_layer=added_layer,
    )


def _set_metadata(pdf: pikepdf.Pdf, *, title: str, lang: str) -> None:
    """Language, title, and the marked / display-title flags an accessible reader needs."""
    pdf.Root.Lang = pikepdf.String(lang)
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.Root.ViewerPreferences = Dictionary(DisplayDocTitle=True)
    with pdf.open_metadata() as meta:
        meta["dc:title"] = title
        meta["dc:language"] = lang
    pdf.docinfo["/Title"] = title
