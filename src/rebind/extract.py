"""Read a born-digital PDF's text layer with position and font metrics.

pdfminer.six is used rather than `inspect.py`'s ToUnicode parser, which is diagnostic/test-only
by design (see CLAUDE.md). pdfminer.six is MIT, pure Python and has no native build step, so it
satisfies the bundle-able-on-Windows invariant.

Pages are yielded lazily. Nothing here retains more than one page at a time, which is what makes
1,000-page documents tractable.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pikepdf
from pdfminer.high_level import extract_pages as _pdfminer_pages
from pdfminer.layout import LAParams, LTChar, LTFigure, LTImage, LTTextContainer
from pdfminer.pdfdocument import PDFEncryptionError
from pdfminer.pdfparser import PDFSyntaxError


class ExtractionError(RuntimeError):
    """The source PDF cannot be read at all -- missing, malformed, or encrypted."""


@dataclass(frozen=True)
class TextLine:
    """One line of text with the provenance and typography needed to classify it."""

    text: str
    page: int
    bbox: tuple[float, float, float, float]
    font: str
    size: float
    bold: bool
    italic: bool


@dataclass(frozen=True)
class ImageRegion:
    """A non-text region. Phase 1 records its existence and location, nothing more."""

    page: int
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class Page:
    number: int
    width: float
    height: float
    lines: tuple[TextLine, ...]
    images: tuple[ImageRegion, ...]

    @property
    def has_text_layer(self) -> bool:
        """False means the page is a scan and belongs to the (unbuilt) OCR branch."""
        return bool(self.lines)


def _dominant_font(chars: list[LTChar]) -> tuple[str, float]:
    """The font and size covering the most characters in a line.

    A line is rarely all one font -- a bold run inside a sentence, a footnote marker. Taking the
    most common rather than the first avoids classifying a paragraph as a heading because its
    first character happened to be styled.
    """
    counts: dict[tuple[str, float], int] = {}
    for char in chars:
        key = (char.fontname, round(char.size, 1))
        counts[key] = counts.get(key, 0) + 1
    return max(counts.items(), key=lambda item: item[1])[0]


def _line_from_container(container, page_number: int) -> TextLine | None:
    text = container.get_text().strip()
    if not text:
        return None
    chars = [obj for obj in container if isinstance(obj, LTChar)]
    if not chars:
        return None
    font, size = _dominant_font(chars)
    lowered = font.lower()
    return TextLine(
        text=text,
        page=page_number,
        bbox=(container.x0, container.y0, container.x1, container.y1),
        font=font,
        size=size,
        # Font names carry weight and slant as a naming convention, not as metadata; there is no
        # reliable structured source for either in a PDF. This substring check is what every PDF
        # tool does and it is wrong for fonts that do not follow the convention.
        bold="bold" in lowered or "black" in lowered or "heavy" in lowered,
        italic="italic" in lowered or "oblique" in lowered,
    )


def extract_pages(source: Path) -> Iterator[Page]:
    """Yield one `Page` per page of the source, lazily."""
    source = Path(source)
    if not source.is_file():
        raise ExtractionError(f"no such file: {source}")

    try:
        layouts = _pdfminer_pages(str(source), laparams=LAParams())
        for index, layout in enumerate(layouts, start=1):
            lines: list[TextLine] = []
            images: list[ImageRegion] = []
            for element in layout:
                if isinstance(element, LTTextContainer):
                    for container in element:
                        line = _line_from_container(container, index)
                        if line is not None:
                            lines.append(line)
                elif isinstance(element, (LTImage, LTFigure)):
                    images.append(
                        ImageRegion(
                            page=index,
                            bbox=(element.x0, element.y0, element.x1, element.y1),
                        )
                    )
            yield Page(
                number=index,
                width=layout.width,
                height=layout.height,
                lines=tuple(lines),
                images=tuple(images),
            )
    except PDFSyntaxError as exc:
        raise ExtractionError(f"{source} is not a readable PDF: {exc}") from exc
    except PDFEncryptionError as exc:
        # Covers both PDFEncryptionError and its subclass PDFPasswordIncorrect: either way the
        # PDF is password-protected and Rebind has no mechanism to supply a password.
        raise ExtractionError(
            f"{source} is password-protected; Rebind cannot read encrypted PDFs"
        ) from exc


def source_is_tagged(source: Path) -> bool:
    """Whether the source already declares a structure tree.

    Rebind should not churn documents that are already accessible (governing design 5.1). This
    only reports the claim; it does not validate that the tagging is any good.
    """
    try:
        with pikepdf.open(source) as pdf:
            return "/StructTreeRoot" in pdf.Root
    # Narrowed to the exceptions that genuinely mean "this file cannot be opened" -- PdfError for
    # malformed PDFs, PasswordError for encrypted ones, OSError for filesystem failures. A bare
    # `except Exception` would also swallow programmer errors (e.g. an internal AttributeError)
    # and misreport them as extraction failures, masking real bugs.
    except (pikepdf.PdfError, pikepdf.PasswordError, OSError) as exc:
        if isinstance(exc, pikepdf.PasswordError):
            raise ExtractionError(
                f"{source} is password-protected; Rebind cannot read encrypted PDFs"
            ) from exc
        raise ExtractionError(f"cannot open {source}: {exc}") from exc
