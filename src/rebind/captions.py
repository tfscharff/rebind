"""Find a picture's caption: the document's own words for it, which become its description."""

from __future__ import annotations

import re

from .extract import TextLine
from .geometry import _horizontally_overlaps

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
#
# The marker set is wider than "Fig." because publishers label figures a dozen ways and a caption
# Rebind does not recognise costs the reader the whole description. The number is optional: an
# unnumbered "Photograph of the reading room." below a picture is still that picture's caption.
# What is NOT accepted is ordinary prose that merely happens to sit under an image -- alt text has
# to be the document's own description of the figure, and a paragraph that follows one is not that.
CAPTION_MARKER_RE = re.compile(
    r"^(?:fig(?:ure)?s?|plate|chart|graph|diagram|scheme|illus(?:tration)?|image|photo(?:graph)?"
    r"|exhibit|map|panel)\b\.?\s*(\d+)?",
    re.IGNORECASE)
# "Figure 3 shows..." is prose that names a figure; a caption never goes on with a verb.
CAPTION_PROSE_VERBS = frozenset(
    "is are was were shows show showed shown illustrates illustrate depicts depict presents present "
    "displays display gives give summarizes summarises compares demonstrates reveals indicates "
    "represents contains has have can could may will".split())
CAPTION_MAX_GAP_PT = 20.0
# How far into the margin beside a figure its caption may sit. Much wider than the gap allowed
# above or below, for two reasons: a caption set in the outer margin is separated by the margin
# itself, and a picture found from a scan's pixels is measured from its ink, so its box stops short
# of where the photograph visually ends. Measured on the real sample the two together come to about
# 52pt. What keeps this safe is not the distance but the caller's rule -- a side caption is used
# only when exactly one unclaimed one is beside the figure.
CAPTION_SIDE_MAX_GAP_PT = 60.0
# How far a stray mark may sit from a caption's own text and still be read as part of it rather
# than as something in its own right. Small: this is for the specks a recogniser drops between a
# caption's lines, not for reaching out and claiming a neighbour.
CAPTION_STRAY_MAX_GAP_PT = 30.0
CAPTION_CONTINUATION_GAP_PT = 10.0
CAPTION_MAX_LINES = 24
# A caption that's just its own label ("Fig. 8", or "Fig. 8 (Continued)" -- a page-break artifact)
# isn't usable as alt text on its own (WCAG 1.1.1: a figure's bare label never conveys its actual
# content) and signals _document_captions should look for a fuller caption elsewhere instead.
MIN_CAPTION_WORDS = 3


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


def _is_caption_marker(text: str) -> bool:
    """Whether a line opens with a caption label of any kind, numbered or not.

    Not a sentence *about* a figure: "Figure 3A. is FTIR spectra of..." opened a paragraph of a
    real journal page, was taken for a caption, and made the table above it a figure.
    """
    match = CAPTION_MARKER_RE.match(text.strip())
    if match is None:
        return False
    rest = text.strip()[match.end():].split()
    if rest and re.fullmatch(r"\(?[A-Za-z]?\)?[.:]?", rest[0]):
        rest = rest[1:]         # a panel letter: "Figure 3A." / "Fig. 2 (b)"
    return not (rest and rest[0].lower() in CAPTION_PROSE_VERBS)


def _caption_number(text: str) -> str | None:
    """The figure number a line's caption marker names ("Fig. 8" -> "8").

    None when the line is not a caption *or* when it is an unnumbered one ("Photograph of the
    reading room."). The number's only job is matching a caption to its figure across a page
    break, which an unnumbered caption cannot do anyway -- so the two cases are the same answer
    to this question, and `_is_caption_marker` is what asks whether a line is a caption at all.
    """
    match = CAPTION_MARKER_RE.match(text.strip())
    return match.group(1) if match and _is_caption_marker(text) else None


def _caption_block(ordered: list[TextLine], used: list | None = None) -> str | None:
    """`ordered[0]` must be a caption-marker line; concatenate it with any tightly-following
    continuation lines (small vertical gap -- the same paragraph, not unrelated content) into the
    full caption text, capped at CAPTION_MAX_LINES as a safety backstop.

    `used`, when given, is filled with the lines the block was built from. The caller needs those
    to tag them `/Caption`, and they cannot be recovered afterwards by matching the text back
    against the page: the search that found this block filtered the lines first (by position
    relative to the figure), so re-deriving it from all of them picks up whatever OCR noise fell
    between the caption's lines and produces a different string.
    """
    if not ordered or not _is_caption_marker(ordered[0].text):
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
    if used is not None:
        used.extend(block)
    return _join_caption_lines([ln.text.strip() for ln in block])


def _join_caption_lines(parts: list[str]) -> str:
    """Join a caption's lines, healing the hyphen the typesetter put in to break a word.

    A line ending in "-" was broken mid-word, so joining with a space produces "iconograph- ical
    elements" -- which is what a screen reader then says, one syllable at a time. The hyphen and
    the space both go. Anything else joins with a space as before.

    Only a *trailing* hyphen is touched, and only when the next line starts lowercase: a real
    compound ("Greco-Roman") never carries its hyphen at the end of the line, and a line ending in
    a dash before a capital is far more likely to be an em-dash aside than a broken word.
    """
    out = ""
    for part in parts:
        if not out:
            out = part
        elif out.endswith("-") and part[:1].islower():
            out = out[:-1] + part
        else:
            out += " " + part
    return out


def _caption_is_substantial(text: str) -> bool:
    """Whether a caption says more than just its own label. WCAG 1.1.1 is explicit that a
    figure's bare label ("Figure 8") never serves as its text alternative on its own -- it must
    convey the image's actual content. A local match that's just the marker (or the marker plus a
    page-break artifact like "(Continued)") isn't usable as-is; _document_captions is what finds
    the real caption elsewhere in that case.
    """
    remainder = CAPTION_MARKER_RE.sub("", text.strip(), count=1)
    return len(remainder.split()) >= MIN_CAPTION_WORDS


def _figure_caption(lines: list[TextLine], bbox: tuple[float, float, float, float],
                    used: list | None = None) -> str | None:
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
        found = _caption_block(below, used)
        if found is not None:
            return found

    above = sorted(
        (ln for ln in lines if ln.bbox[1] >= fy1 and _horizontally_overlaps(ln.bbox, bbox)),
        key=lambda ln: ln.bbox[1],   # closest to the figure (lowest y0) first
    )
    if above and (above[0].bbox[1] - fy1) <= CAPTION_MAX_GAP_PT:
        return _caption_block(above, used)
    return None


def _caption_groups(content_lines: list[TextLine], content_roles: list[str],
                    caption_of: dict[int, int], blocks: list[list[TextLine]],
                    boxed: set[int] | frozenset[int] = frozenset()) -> list[int | None]:
    """Which caption each content line belongs to, with the strays in between folded in.

    A caption's own lines are known exactly. What is not is what the recogniser dropped between
    them: on a real scan a stray "~" and "|" sat between the lines of a caption in the margin, and
    since a plan entry is a contiguous run, those two marks split one caption into three elements.

    A line is folded into a caption when it sits inside that caption's vertical span and within
    arm's length of it horizontally. It has to be near in both, or an unrelated line level with the
    caption on the other side of the page would join it -- which is the same mistake as reading
    across a gutter.

    A line a layout model already placed in a block of its own (`boxed`) is never folded in: the
    model has seen the gutter. A table title in the left column of a journal page took the two
    lines of body text level with it in the right column, which sat closer than arm's length.
    """
    groups: list[int | None] = [caption_of.get(id(line)) for line in content_lines]
    if not blocks:
        return groups
    extents = [(min(ln.bbox[0] for ln in b), min(ln.bbox[1] for ln in b),
                max(ln.bbox[2] for ln in b), max(ln.bbox[3] for ln in b)) for b in blocks]
    for index, (line, role) in enumerate(zip(content_lines, content_roles)):
        if groups[index] is not None or role != "P" or id(line) in boxed:
            continue
        centre = (line.bbox[1] + line.bbox[3]) / 2
        for group, (bx0, by0, bx1, by1) in enumerate(extents):
            if not by0 <= centre <= by1:
                continue
            gap = max(0.0, max(bx0 - line.bbox[2], line.bbox[0] - bx1))
            if gap <= CAPTION_STRAY_MAX_GAP_PT:
                groups[index] = group
                content_roles[index] = "Caption"
                break
    return groups


def _side_captions(lines: list[TextLine], bbox: tuple[float, float, float, float]
                   ) -> list[tuple[str, list[TextLine]]]:
    """Every caption block sitting *beside* a figure, in the margin to its left or right.

    A book with a wide outer margin stacks its captions there rather than under the pictures, and
    a search that only looks up and down finds nothing at all on such a page. Each caption-marker
    line clear of the figure horizontally, whose own vertical span overlaps the figure's, starts a
    block; the caller decides what to do when more than one comes back, because several figures
    can share one vertical span and their captions are stacked beside all of them.
    """
    fx0, fy0, fx1, fy1 = bbox
    out: list[tuple[str, list[TextLine]]] = []
    for line in sorted(lines, key=lambda ln: -ln.bbox[3]):
        lx0, ly0, lx1, ly1 = line.bbox
        if not _is_caption_marker(line.text):
            continue
        gap = (lx0 - fx1) if lx0 >= fx1 else (fx0 - lx1)
        if not 0 <= gap <= CAPTION_SIDE_MAX_GAP_PT:
            continue
        if min(ly1, fy1) - max(ly0, fy0) <= 0:
            continue        # not beside it at all -- above or below, which the caller handles
        block_lines: list[TextLine] = []
        block = _caption_block(
            sorted((ln for ln in lines if ln.bbox[3] <= ly1 and _horizontally_overlaps(
                ln.bbox, (lx0, ly0, lx1, ly1))), key=lambda ln: -ln.bbox[3]), block_lines)
        if block:
            out.append((block, block_lines))
    return out


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
