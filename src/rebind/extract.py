"""Read a born-digital PDF's text layer with position and font metrics.

pdfminer.six is used rather than `inspect.py`'s ToUnicode parser, which is diagnostic/test-only
by design (see CLAUDE.md). pdfminer.six is MIT, pure Python and has no native build step, so it
satisfies the bundle-able-on-Windows invariant.

Pages are yielded lazily. Nothing here retains more than one page at a time, which is what makes
1,000-page documents tractable.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, replace
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
    # The ink colour the page itself declares for this line, as 8-bit sRGB. None when the page does
    # not declare a plain device colour (a pattern or an unusual colour space), and always None for
    # OCR'd lines, which have no declared colour to read -- `contrast` samples the render instead.
    color: tuple[int, int, int] | None = None
    # Set on a line `_join_rotated` rebuilt from text printed sideways (a download stamp or an
    # identifier running up the margin), which pdfminer hands over one character per line.
    rotated: bool = False
    # The runs of the line's text set above or below its baseline -- superscripts and subscripts --
    # in the order they occur. Tagged as their own spans so the shift is not lost (see `overlay`).
    scripts: tuple[Script, ...] = ()


@dataclass(frozen=True)
class Script:
    """A run of a line's characters raised or lowered off its baseline: a superscript (a charge,
    an exponent, a note mark) or a subscript (an atom count, an index)."""

    start: int      # the run's characters are text[start:end]
    end: int
    shift: float    # how far the run's baseline sits from the line's, in points; up is positive

    @property
    def raised(self) -> bool:
        return self.shift > 0

    def moved(self, by: int) -> Script:
        """The same run in a line whose text gained `by` characters in front of it."""
        return replace(self, start=self.start + by, end=self.end + by)


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


def _device_rgb(ncolor) -> tuple[int, int, int] | None:
    """A pdfminer non-stroking colour as 8-bit sRGB, or None if it is not a plain device colour."""
    if isinstance(ncolor, (int, float)):
        values = [float(ncolor)]
    elif isinstance(ncolor, (tuple, list)):
        try:
            values = [float(v) for v in ncolor]
        except (TypeError, ValueError):
            return None      # a pattern, or a separation colourant named rather than numbered
    else:
        return None

    def byte(v: float) -> int:
        return round(max(0.0, min(1.0, v)) * 255)

    if len(values) == 1:
        return (byte(values[0]),) * 3
    if len(values) == 3:
        return tuple(byte(v) for v in values)
    if len(values) == 4:
        c, m, y, k = values
        return tuple(byte(1 - min(1.0, channel + k)) for channel in (c, m, y))
    return None


def _dominant_color(chars: list[LTChar]) -> tuple[int, int, int] | None:
    """The colour covering the most characters in a line, as declared by the page itself.

    This is the ink's *exact* value, which sampling the rendered page cannot recover: a glyph stroke
    is a pixel or two wide, so almost every one of its pixels is an anti-aliased blend of ink and
    paper, and even the darkest lands well short of the truth. Measured on a real sample, pure black
    body text sampled back as mid-grey and pure blue as lilac -- enough to report a document with no
    contrast problem at all as having forty-three.
    """
    counts: dict[tuple[int, int, int], int] = {}
    for char in chars:
        rgb = _device_rgb(getattr(getattr(char, "graphicstate", None), "ncolor", None))
        if rgb is not None:
            counts[rgb] = counts.get(rgb, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


def _text_lines(element: LTTextContainer):
    """Yield the LTTextLine objects to turn into `TextLine`s from a top-level text element.

    A top-level text element is usually an `LTTextBox` whose children are `LTTextLine`s. But
    pdfminer also emits a bare `LTTextLine` at the top level on some OCR'd PDFs -- and because
    `LTTextLine` is itself an `LTTextContainer`, iterating it yields `LTChar`s, not lines. Passing
    an `LTChar` to `_raw_line` (which iterates its argument) crashes with
    "'LTChar' object is not iterable". So a line is yielded whole; only a box is descended into,
    and any stray non-line child of a box (an ungrouped `LTChar`/`LTAnno`) is skipped.
    """
    if isinstance(element, LTTextLine):
        yield element
        return
    for child in element:
        if isinstance(child, LTTextLine):
            yield child


@dataclass
class _Raw:
    """A line as pdfminer grouped it, with each character's place in the text, before it is
    finished into a `TextLine`."""

    text: str
    chars: list[tuple[int, int, LTChar]]     # (start, end) of each character within `text`
    bbox: tuple[float, float, float, float]
    turn: int = 0


def _raw_line(container) -> _Raw | None:
    text = ""
    chars: list[tuple[int, int, LTChar]] = []
    for obj in container:
        piece = obj.get_text()
        if isinstance(obj, LTChar):
            chars.append((len(text), len(text) + len(piece), obj))
        text += piece
    lead = len(text) - len(text.lstrip())
    text = text.strip()
    if not text or not chars:
        return None
    chars = [(start - lead, end - lead, char) for start, end, char in chars]
    return _Raw(text, chars, (container.x0, container.y0, container.x1, container.y1),
                _turn(container))


def _upright(char: LTChar) -> bool:
    a, b, c, d = char.matrix[:4]
    return a > 0 and d > 0 and abs(b) < 1e-6 and abs(c) < 1e-6


# A superscript or subscript has its baseline moved off the line's by a share of the type size: at
# least SCRIPT_MIN_SHIFT when it is also set smaller (at most SCRIPT_SIZE_RATIO of the size), at
# least SCRIPT_FULL_SIZE_SHIFT when it is not -- some typesetters raise full-size figures -- and
# never more than SCRIPT_MAX_SHIFT, which is a different line run together with this one.
SCRIPT_SIZE_RATIO = 0.85
SCRIPT_MIN_SHIFT = 0.12
SCRIPT_FULL_SIZE_SHIFT = 0.25
SCRIPT_MAX_SHIFT = 0.7


def _baseline(chars: list[tuple[int, int, LTChar]]) -> float | None:
    """The baseline most of the line's upright characters sit on, read from each character's own
    text matrix -- where the PDF put it, not guessed from boxes."""
    counts = Counter(round(char.matrix[5], 1) for _s, _e, char in chars
                     if _upright(char) and not char.get_text().isspace())
    return counts.most_common(1)[0][0] if counts else None


def _is_script(char: LTChar, baseline: float, size: float) -> bool:
    shift = abs(char.matrix[5] - baseline)
    if not _upright(char) or char.get_text().isspace() or shift > SCRIPT_MAX_SHIFT * size:
        return False
    return shift >= (SCRIPT_MIN_SHIFT if char.size <= SCRIPT_SIZE_RATIO * size
                     else SCRIPT_FULL_SIZE_SHIFT) * size


def _scripts(chars: list[tuple[int, int, LTChar]], size: float) -> tuple[Script, ...]:
    """The runs of characters set above or below the line's baseline, so a subscript "2" and a
    superscript "2" are told apart exactly."""
    baseline = _baseline(chars)
    if baseline is None:
        return ()
    runs: list[list] = []
    for start, end, char in chars:
        if not _is_script(char, baseline, size):
            continue
        shift = char.matrix[5] - baseline
        run = runs[-1] if runs else None
        if run and run[1] == start and (run[2][0] > 0) == (shift > 0):
            run[1] = end
            run[2].append(shift)
        else:
            runs.append([start, end, [shift]])
    return tuple(Script(start, end, round(sorted(shifts)[len(shifts) // 2], 2))
                 for start, end, shifts in runs)


def _finish(raw: _Raw, page_number: int) -> TextLine:
    chars = [char for _s, _e, char in raw.chars]
    font, size = _dominant_font(chars)
    lowered = font.lower()
    return TextLine(
        text=raw.text,
        page=page_number,
        bbox=raw.bbox,
        font=font,
        size=size,
        color=_dominant_color(chars),
        # Font names carry weight and slant as a naming convention, not as metadata; there is no
        # reliable structured source for either in a PDF. This substring check is what every PDF
        # tool does and it is wrong for fonts that do not follow the convention.
        bold="bold" in lowered or "black" in lowered or "heavy" in lowered,
        italic="italic" in lowered or "oblique" in lowered,
        scripts=_scripts(raw.chars, size),
    )


def _join_split_lines(raws: list[_Raw]) -> list[_Raw]:
    """Put back together a line pdfminer broke at a superscript or subscript.

    pdfminer starts a new line wherever a character does not sit level with the one before it,
    and a subscript followed straight by a superscript -- "SO4" then "2-", the shape of every ion
    with a charge -- is exactly that. So "Sulfate, SO42-, binds Ca2+" came out as two lines, and the
    charge on "HCO3-." as a third one of its own at the end of the row, read wherever the column
    cut happened to put it. A line that starts where another ends, on the same row, joined to it by
    a character set small, is the same line.
    """
    raws = list(raws)
    while True:
        ends = sorted(range(len(raws)), key=lambda i: raws[i].bbox[2])
        rights = [raws[i].bbox[2] for i in ends]
        joined = None
        for b, right in enumerate(raws):
            if right.turn:
                continue
            first = right.chars[0][2]
            lo = bisect_left(rights, right.bbox[0] - 0.3 * first.size)
            hi = bisect_right(rights, right.bbox[0] + 0.5)
            for a in (ends[k] for k in range(lo, hi)):
                left = raws[a]
                if a == b or left.turn:
                    continue
                last = left.chars[-1][2]
                shared = min(left.bbox[3], right.bbox[3]) - max(left.bbox[1], right.bbox[1])
                height = min(left.bbox[3] - left.bbox[1], right.bbox[3] - right.bbox[1])
                baseline = _baseline(left.chars)
                size = _dominant_font([c for _s, _e, c in left.chars])[1]
                if (shared >= 0.3 * height and baseline is not None
                        and (_is_script(last, baseline, size) or _is_script(first, baseline, size))):
                    joined = (a, b)
                    break
            if joined:
                break
        if joined is None:
            return raws
        a, b = joined
        left, right = raws[a], raws[b]
        gap = right.bbox[0] - left.bbox[2]
        space = " " if gap > 0.15 * right.chars[0][2].size else ""
        offset = len(left.text) + len(space)
        raws[a] = _Raw(left.text + space + right.text,
                       left.chars + [(s + offset, e + offset, c) for s, e, c in right.chars],
                       (min(left.bbox[0], right.bbox[0]), min(left.bbox[1], right.bbox[1]),
                        max(left.bbox[2], right.bbox[2]), max(left.bbox[3], right.bbox[3])))
        del raws[b]


def _turn(container) -> int:
    """+1 for a one-character line printed turned clockwise (read downwards), -1 turned
    anticlockwise (read upwards), 0 for anything else."""
    chars = [obj for obj in container if isinstance(obj, LTChar)]
    if len(chars) != 1 or len(container.get_text().strip()) != 1:
        return 0
    a, b = chars[0].matrix[:2]
    if abs(b) <= abs(a):
        return 0
    return 1 if b < 0 else -1


# Sideways text comes out one character per line; this many in a column are a line turned on its
# side rather than a column of separate marks.
MIN_ROTATED_CHARS = 4


def _join_rotated(lines: list[TextLine], turns: dict[int, int]) -> list[TextLine]:
    """Put back together a line of text printed sideways.

    pdfminer reads every character of it as its own line, so "01 June 2026 14:25:30" running down
    a journal page's margin reached the page as eighteen one-letter paragraphs, interleaved with the
    body text beside them. Characters stacked in one column, each turned the same way and touching
    the next, are joined in reading order; a gap wider than a character's own advance is a space.
    """
    stacked = [ln for ln in lines if turns.get(id(ln))]
    if len(stacked) < MIN_ROTATED_CHARS:
        return lines
    runs: list[list[TextLine]] = []
    for line in sorted(stacked, key=lambda ln: -ln.bbox[3]):
        for run in runs:
            last = run[-1]
            advance = last.bbox[3] - last.bbox[1]
            if (abs(last.bbox[0] - line.bbox[0]) <= 1 and turns[id(last)] == turns[id(line)]
                    and -1 <= last.bbox[1] - line.bbox[3] <= 3 * advance):
                run.append(line)
                break
        else:
            runs.append([line])
    joined: dict[int, TextLine | None] = {}
    for run in runs:
        if len(run) < MIN_ROTATED_CHARS:
            continue
        if turns[id(run[0])] < 0:
            run = run[::-1]
        text = run[0].text
        for prev, line in zip(run, run[1:]):
            gap = abs(prev.bbox[1] - line.bbox[3]) if turns[id(line)] > 0 else abs(
                line.bbox[1] - prev.bbox[3])
            text += (" " if gap > 0.5 * (prev.bbox[3] - prev.bbox[1]) else "") + line.text
        box = (min(ln.bbox[0] for ln in run), min(ln.bbox[1] for ln in run),
               max(ln.bbox[2] for ln in run), max(ln.bbox[3] for ln in run))
        joined[id(run[0])] = replace(run[0], text=text, bbox=box, rotated=True, scripts=())
        for line in run[1:]:
            joined[id(line)] = None
    out = []
    for line in lines:
        if id(line) in joined:
            if joined[id(line)] is not None:
                out.append(joined[id(line)])
        else:
            out.append(line)
    return out


def _collect_figure(
    figure: LTFigure, page_number: int, lines: list[_Raw], images: list[ImageRegion],
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
                line = _raw_line(container)
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
            raws: list[_Raw] = []
            images: list[ImageRegion] = []
            drawings: list[ImageRegion] = []
            for element in layout:
                if isinstance(element, LTTextContainer):
                    for container in _text_lines(element):
                        line = _raw_line(container)
                        if line is not None:
                            raws.append(line)
                elif isinstance(element, LTFigure):
                    _collect_figure(element, index, raws, images, drawings)
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
            raws = _join_split_lines(raws)
            lines = [_finish(raw, index) for raw in raws]
            turns = {id(line): raw.turn for line, raw in zip(lines, raws)}
            yield Page(
                number=index,
                width=layout.width,
                height=layout.height,
                lines=tuple(_join_rotated(lines, turns)),
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
