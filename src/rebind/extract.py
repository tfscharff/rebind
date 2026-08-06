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
from pdfminer.layout import (
    LAParams,
    LTChar,
    LTCurve,
    LTFigure,
    LTImage,
    LTTextContainer,
    LTTextLine,
)
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
    # Set only for lines recovered by OCR: the recognizer's per-line confidence (0.0-1.0). None for
    # born-digital text, which is exact by construction. `assemble` uses it as the node's confidence
    # and, below a threshold, replaces the line with an honest placeholder rather than a guess.
    ocr_confidence: float | None = None


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
    # Bounding boxes of the page's vector path primitives (strokes, curves, filled shapes), in page
    # coordinates. Not figures on their own -- a table rule, an underline and a hand-drawn diagram
    # are all "vector paths" here, and telling them apart is `remediate`'s job. Recorded because a
    # line-art figure (a schematic, a chart, a labelled diagram) leaves no /Image behind at all:
    # confirmed on a real sample where six of eight figures are drawn purely with curve operators,
    # so an image-only search finds two figures and silently misses the rest.
    drawings: tuple[ImageRegion, ...] = ()

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


def _text_lines(element: LTTextContainer):
    """Yield the LTTextLine objects to turn into `TextLine`s from a top-level text element.

    A top-level text element is usually an `LTTextBox` whose children are `LTTextLine`s. But
    pdfminer also emits a bare `LTTextLine` at the top level on some OCR'd PDFs -- and because
    `LTTextLine` is itself an `LTTextContainer`, iterating it yields `LTChar`s, not lines. Passing
    an `LTChar` to `_line_from_container` (which iterates its argument) crashes with
    "'LTChar' object is not iterable". So a line is yielded whole; only a box is descended into,
    and any stray non-line child of a box (an ungrouped `LTChar`/`LTAnno`) is skipped.
    """
    if isinstance(element, LTTextLine):
        yield element
        return
    for child in element:
        if isinstance(child, LTTextLine):
            yield child


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


def _collect_figure(
    figure: LTFigure, page_number: int, lines: list[TextLine], images: list[ImageRegion],
    drawings: list[ImageRegion],
) -> tuple[bool, bool]:
    """Recurse into a Form XObject, contributing its text lines and embedded images separately.

    Rebind must never report real, extractable text as an unrecoverable image (see the
    docstring on `extract_pages`), so a figure's own text -- grouped into `LTTextContainer`s by
    `LAParams(all_texts=True)` -- is collected as `TextLine`s exactly as it would be at the top
    level, rather than being ignored because it happens to sit inside an XObject.

    The double-counting rule: a figure never contributes a whole-figure `ImageRegion` merely for
    containing text. Text content and embedded raster images are independent signals recorded
    independently -- an `LTImage` found anywhere inside (at any nesting depth) always becomes its
    own `ImageRegion` describing just that image, regardless of whether the figure also holds
    text, so a figure with both a photo and a caption yields both a text line and an image
    region rather than one masking the other. Only a figure that yields neither text nor an
    embedded image anywhere within it (e.g. one containing only vector graphics) falls back to a
    single opaque `ImageRegion` for the whole figure, preserving Phase 1's honest "something is
    here and we cannot describe it" signal for genuinely non-text, non-raster content.

    Returns (found_text, found_image) so a caller nesting figures inside figures can propagate
    whether this level already accounted for the region.
    """
    found_text = False
    found_image = False
    for child in figure:
        if isinstance(child, LTTextContainer):
            for container in _text_lines(child):
                line = _line_from_container(container, page_number)
                if line is not None:
                    lines.append(line)
                    found_text = True
        elif isinstance(child, LTImage):
            images.append(
                ImageRegion(page=page_number, bbox=(child.x0, child.y0, child.x1, child.y1))
            )
            found_image = True
        elif isinstance(child, LTFigure):
            nested_text, nested_image = _collect_figure(
                child, page_number, lines, images, drawings)
            found_text = found_text or nested_text
            found_image = found_image or nested_image
        elif isinstance(child, LTCurve):
            drawings.append(
                ImageRegion(page=page_number, bbox=(child.x0, child.y0, child.x1, child.y1))
            )
    if not found_text and not found_image:
        images.append(
            ImageRegion(page=page_number, bbox=(figure.x0, figure.y0, figure.x1, figure.y1))
        )
    return found_text, found_image


def extract_pages(source: Path) -> Iterator[Page]:
    """Yield one `Page` per page of the source, lazily."""
    source = Path(source)
    if not source.is_file():
        raise ExtractionError(f"no such file: {source}")

    try:
        # `all_texts=True` tells pdfminer to run its layout analysis (grouping characters into
        # lines and boxes) *inside* Form XObjects too, not only at the page's top level. Without
        # it, characters inside a figure arrive as ungrouped `LTChar`s that `_collect_figure`
        # cannot turn into `TextLine`s, and real text is misreported as an unrecoverable image
        # region -- a false provenance claim this project's invariants forbid.
        layouts = _pdfminer_pages(str(source), laparams=LAParams(all_texts=True))
        for index, layout in enumerate(layouts, start=1):
            lines: list[TextLine] = []
            images: list[ImageRegion] = []
            drawings: list[ImageRegion] = []
            for element in layout:
                if isinstance(element, LTTextContainer):
                    for container in _text_lines(element):
                        line = _line_from_container(container, index)
                        if line is not None:
                            lines.append(line)
                elif isinstance(element, LTFigure):
                    _collect_figure(element, index, lines, images, drawings)
                elif isinstance(element, LTImage):
                    images.append(
                        ImageRegion(
                            page=index,
                            bbox=(element.x0, element.y0, element.x1, element.y1),
                        )
                    )
                elif isinstance(element, LTCurve):
                    # LTCurve covers every painted path: LTLine and LTRect are subclasses, so one
                    # branch catches rules, boxes, strokes and Beziers alike. Recorded raw; see
                    # Page.drawings on why no filtering happens here.
                    drawings.append(
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
                drawings=tuple(drawings),
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
