"""Find the pictures on a page -- declared images, line art, a scan's pixels -- and the text that
belongs to each.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import layout_model, music
from .captions import (
    _caption_block,
    _caption_block_bottom,
    _caption_is_substantial,
    _caption_number,
    _figure_caption,
    _is_caption_marker,
    _nearby_caption_number,
    _side_captions,
)
from .extract import TextLine
from .geometry import _area, _boxes_overlap, _center_in_box, _horizontally_overlaps, _overlaps
from .layout import detect_table_lines
from .ocr import render_page_to_image

# An image covering between these fractions of the page is a figure worth describing. Smaller is a
# rule/icon/bullet; a page-covering image is the scan itself, not a figure.
FIGURE_MIN_COVERAGE = 0.01
FIGURE_MAX_COVERAGE = 0.6

# Vector (line-art) figure recovery -- see `_vector_figures`. A drawing sits a little further from
# its caption than a raster image does (the caption marker's own top edge vs the drawing's lowest
# ink): 11-23pt observed on the real sample, hence a wider tolerance than CAPTION_MAX_GAP_PT. The
# path-count floor is what keeps a lone rule above a caption from being promoted to a figure --
# observed exactly that, a 1-path horizontal rule directly above a "Fig. 8" continuation caption.
VECTOR_CAPTION_MAX_GAP_PT = 30.0
MIN_VECTOR_PATHS = 6
RULE_MAX_THICKNESS_PT = 3.0
RULE_MIN_LENGTH_RATIO = 0.5
# How far outside a figure's ink a callout label may sit and still belong to it. Observed on the
# real sample: "Bonding" and "2-10 ml glass syringe" print 3-11pt above the top of the apparatus
# they label, at the end of their leader lines. Comfortably smaller than the gap to a caption.
FIGURE_TEXT_TOLERANCE_PT = 12.0
# Picture-hunting on a scan works from a render of its own; 150 dpi is ample for finding where the
# ink is and costs a fraction of the 300 dpi the page itself is rebuilt at.
REGION_SCAN_DPI = 150
# The longest a callout label runs to ("Ventral", "2-10 ml glass syringe", "3 mm"). Longer than
# this, inside a guessed figure box, is a sentence -- prose the picture sits around, not part of it.
FIGURE_LABEL_MAX_WORDS = 6
# A figure found from drawings or pixels is a table instead when at least this many lines sit in
# it and this share of them are a table's.
MIN_TABLE_LINES_IN_FIGURE = 4
TABLE_SHARE_OF_FIGURE = 0.6
# Sideways text is in the margin when no more than this share of the page's upright lines share
# any of its width.
MARGIN_SHARE = 0.1
# A short line this close (in line heights) above or below running text is part of it.
PARAGRAPH_LEADING_GAP = 1.0
# A picture found some other way over the same music gives way to the music's own box, unless it is
# this many times bigger -- then it is a picture with music in it, and keeps its box.
MUSIC_REPLACES_AREA = 2.0
# A speck on a scan (`_is_speck`): a line of up to this many marks with no letter or digit in it...
SPECK_MAX_MARKS = 3
# ...or a line this short that the layout model found outside any region of words.
SPECK_MAX_CHARS = 2


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
    markers = sorted((ln for ln in lines if _is_caption_marker(ln.text)),
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


def _is_full_page_scan(src_page) -> bool:
    """Whether the page is one big picture -- a scanned sheet -- rather than a laid-out page.

    That is the same image `_page_figures` rejects for covering too much of the page to be a figure
    on it. Here the fact is used the other way round: it is what says the page's illustrations have
    to be found in the pixels, because the file itself holds only the one raster.
    """
    area = src_page.width * src_page.height
    if area <= 0:
        return False
    for image in src_page.images:
        x0, y0, x1, y1 = image.bbox
        if (x1 - x0) * (y1 - y0) / area > FIGURE_MAX_COVERAGE:
            return True
    return False


def _scanned_figures(source: Path, src_page, lines: list[TextLine], dpi: int,
                     already: list[tuple[str, tuple]]) -> list[tuple[str, tuple]]:
    """Pictures printed on a scanned page, found from its pixels. Each is (stable id, bbox).

    A region that overlaps something already found (a placed image, a line-art figure) is dropped:
    the same picture must not be announced twice, and the earlier detections know more about what
    they are.
    """
    from .regions import find_picture_regions

    try:
        page_image = render_page_to_image(source, src_page.number, dpi=REGION_SCAN_DPI)
    except Exception:  # noqa: BLE001 -- a page that will not render simply yields no figures
        return []
    found = find_picture_regions(
        page_image, [line.bbox for line in lines],
        page_width=src_page.width, page_height=src_page.height)
    out: list[tuple[str, tuple]] = []
    for index, region in enumerate(found):
        if any(_boxes_overlap(region.bbox, bbox) for _fid, bbox in already + out):
            continue
        out.append((f"p{src_page.number}r{index}", region.bbox))
    return out


def _is_speck(line: TextLine, hints: layout_model.PageHints) -> bool:
    """Whether a line on a scanned page is a speck: a mark the recogniser read as text.

    Two shapes, both seen on real scans: a line of nothing but marks ("|", "~", "." -- dirt on the
    platen, a rule's end, a gutter shadow), and a one- or two-character line the layout model found
    in no region where words are expected, set alone in a margin or a gap. A real word is never one
    of these: born-digital text is not passed here, and anything inside a block of text, a title, a
    table or an equation is kept whatever its length.
    """
    text = line.text.strip()
    if not text:
        return False
    if len(text) <= SPECK_MAX_MARKS and not any(ch.isalnum() for ch in text):
        return True
    return (hints.tables is not None and len(text) <= SPECK_MAX_CHARS
            and id(line) not in hints.in_text)


def _holds_table(lines: list[TextLine], hints: layout_model.PageHints, bbox: tuple) -> bool:
    """Whether most of the text inside a region is a table's -- as the layout model boxed it, or,
    without a model, as the geometric detector finds it."""
    inside = [ln for ln in lines if _center_in_box(ln.bbox, bbox)]
    if len(inside) < MIN_TABLE_LINES_IN_FIGURE:
        return False
    table = ({id(ln) for ln in inside if id(ln) in hints.tables} if hints.tables is not None
             else detect_table_lines(inside))
    return len(table) >= TABLE_SHARE_OF_FIGURE * len(inside)


def _margin_furniture(lines: list[TextLine], width: float, height: float) -> list[TextLine]:
    """Sideways text standing clear of the page's other text, lines made only of icon glyphs
    (private-use characters: an envelope, a globe, a tick) that have nothing to read out, and text
    set off the page entirely, which nobody sees."""
    upright = [ln for ln in lines if not ln.rotated]
    out = []
    for line in lines:
        text = line.text.strip()
        x0, y0, x1, y1 = line.bbox
        # In the margin: hardly any of the page's text shares its width. (Not "beyond the
        # rightmost line": a heading's trailing spaces reached past a stamp on a real page.)
        sharing = sum(1 for ln in upright if _horizontally_overlaps(ln.bbox, line.bbox))
        if line.rotated and sharing <= MARGIN_SHARE * len(upright):
            out.append(line)
        elif x1 <= 0 or y1 <= 0 or x0 >= width or y0 >= height:
            out.append(line)
        elif text and all("" <= ch <= "" or ch.isspace() for ch in text):
            out.append(line)
    return out


def _figure_text_strict(lines: list[TextLine], bbox: tuple) -> list[TextLine]:
    """A picture's own callout labels, when the picture's box is a guess rather than a declaration.

    Used for a figure found in a scan's pixels. Two things are given up compared with
    `_figure_text`: ownership never grows outward from the box, and only *labels* are claimed --
    a few words, the length a callout runs to. Anything longer is a sentence, and a sentence inside
    the box is prose the picture happens to sit around, not part of it.

    Both restrictions are there for the same reason. Growing from a guessed box cascaded across the
    page on a real scanned book and pulled 117 paragraphs out of the reading order; claiming every
    line inside the box still held 238 of them back. Text hidden from a screen reader is the worst
    outcome available here, so a guessed box gets no benefit of the doubt.
    """
    # Text turned sideways inside a picture is lettering on it (a plan's room labels), not prose.
    return [line for line in lines
            if _center_in_box(line.bbox, bbox)
            and (line.rotated or len(line.text.split()) <= FIGURE_LABEL_MAX_WORDS)]


def _music_text(lines: list[TextLine], example) -> list[TextLine]:
    """What a music example owns of the page's text: anything on its staves, which is notation the
    recogniser misread whatever it came out as, and the short labels around them -- figures under a
    bass, a tempo marking. Never its caption ("Ex. 24."), which is read in its own right."""
    owned = []
    for line in lines:
        if any(_center_in_box(line.bbox, band) for band in example.bands):
            owned.append(line)      # even one that opens like a caption: it is on the stave
        elif (_center_in_box(line.bbox, example.bbox) and not music.is_label(line.text)
              and not _is_caption_marker(line.text)
              and len(line.text.split()) <= FIGURE_LABEL_MAX_WORDS):
            owned.append(line)
    return owned


def _figure_text(lines: list[TextLine], bbox: tuple) -> list[TextLine]:
    """The lines belonging to a figure: its own callout labels, not the prose around it.

    A label sits *on* the artwork, but the figure's box is the extent of its ink, and a label
    routinely sits just past that -- "Bonding" printed a few points above the top of the drawing it
    names, at the end of a leader line. So the box is tested with a small tolerance, and ownership
    grows: a line near the figure joins it and widens the box, which may in turn reach a label near
    only that. Prose does not sit on artwork, so it is never reached.

    Growth is floored at the caption. The caption is real text that belongs in the reading order in
    its own right -- it is also what the figure's alt text was taken from -- so neither it nor the
    body text beneath it may be swallowed, however close the artwork comes to it.

    Only labels are ever claimed, never a line of a paragraph (`_paragraph_lines`). Growing
    through prose cascaded on a real journal: a cover's sidebar image swallowed the whole title
    block beside it, and a page's chart swallowed the five paragraphs stacked above it, leading
    for leading -- all of it hidden from a screen reader as decoration.
    """
    floor = max(
        (ln.bbox[3] for ln in lines
         if _is_caption_marker(ln.text) and ln.bbox[3] <= bbox[1] + FIGURE_TEXT_TOLERANCE_PT),
        default=None,
    )
    prose = _paragraph_lines(lines)
    candidates = [ln for ln in lines if (floor is None or ln.bbox[3] > floor)
                  and id(ln) not in prose]
    owned: list[TextLine] = []
    box = bbox
    changed = True
    while changed:
        changed = False
        grown = (box[0] - FIGURE_TEXT_TOLERANCE_PT, box[1] - FIGURE_TEXT_TOLERANCE_PT,
                 box[2] + FIGURE_TEXT_TOLERANCE_PT, box[3] + FIGURE_TEXT_TOLERANCE_PT)
        for line in candidates:
            if any(line is seen for seen in owned):
                continue
            if _center_in_box(line.bbox, grown) or _overlaps(line.bbox, grown):
                owned.append(line)
                box = (min(box[0], line.bbox[0]), min(box[1], line.bbox[1]),
                       max(box[2], line.bbox[2]), max(box[3], line.bbox[3]))
                changed = True
    return owned


def _paragraph_lines(lines: list[TextLine]) -> set[int]:
    """The lines (by `id`) that are running text: a line longer than a label set in a run with
    other lines, and every line set at a paragraph's leading next to one -- the last line of a
    paragraph ("rivers."), a title's short last line, a byline's DOI under its journal reference.

    A long line standing alone is not a paragraph: on a drawing it is a callout ("D  Bond this part
    with super glue"), and it stays the drawing's."""
    def near(line: TextLine, other: TextLine) -> bool:
        if other is line or not _horizontally_overlaps(line.bbox, other.bbox):
            return False
        gap = max(other.bbox[1] - line.bbox[3], line.bbox[1] - other.bbox[3])
        return gap <= PARAGRAPH_LEADING_GAP * max(line.bbox[3] - line.bbox[1],
                                                  other.bbox[3] - other.bbox[1])

    prose = {id(ln) for ln in lines if len(ln.text.split()) > FIGURE_LABEL_MAX_WORDS
             and any(near(ln, other) for other in lines)}
    grew = True
    while grew:
        grew = False
        for line in lines:
            if id(line) not in prose and any(id(o) in prose and near(line, o) for o in lines):
                prose.add(id(line))
                grew = True
    return prose


def _figure_anchor(lines: list[TextLine], mcids: list[int | None], figure_index: int,
                   owner_figure: dict[int, int], bbox: tuple) -> int | None:
    """The MCID of the content line a figure should be read *before*, or None to read it last.

    Reading order is a sequence, so a figure needs a place in it. Its own labels were already
    spliced to the right height by the layout pass, so the line following them is where the figure
    belongs. Failing that (a figure with no labels of its own) it is read straight after the text
    above it *in its own column* -- the last line over it that it shares any width with -- or, with
    nothing above, before the first such line below it. Taking simply the first line anywhere below
    the figure put a left-hand column's music examples into the middle of the right-hand column on
    a real two-column page, since the right column's lower half is "below" them too.
    """
    seen_own_text = False
    content: list[tuple[TextLine, int]] = []
    for line, mcid in zip(lines, mcids):
        if owner_figure.get(id(line)) == figure_index:
            seen_own_text = True
            continue
        if mcid is None:
            continue
        if seen_own_text:
            return mcid
        content.append((line, mcid))
    middle = (bbox[1] + bbox[3]) / 2
    column = [k for k, (line, _mcid) in enumerate(content)
              if _horizontally_overlaps(line.bbox, bbox)]
    above = [k for k in column if (content[k][0].bbox[1] + content[k][0].bbox[3]) / 2 > middle]
    if above:
        after = max(above) + 1
        return content[after][1] if after < len(content) else None
    below = [k for k in column if k not in above]
    if below:
        return content[min(below)][1]
    return next((mcid for line, mcid in content if line.bbox[3] <= bbox[1]), None)


@dataclass
class PageFigures:
    """A page's pictures -- each an id and a box in points -- and what is known about each."""

    boxes: list[tuple[str, tuple]]
    # Captions found together with line art, by figure id.
    anchored: dict[str, str] = field(default_factory=dict)
    # Printed music, by figure id (see `music`).
    music_of: dict[str, music.Example] = field(default_factory=dict)
    # Pictures the layout model boxed on a scan, and pictures guessed from a scan's pixels.
    modelled: set[str] = field(default_factory=set)
    scan_regions: set[str] = field(default_factory=set)
    # Each picture's description (None for none yet), set by `describe_figures`.
    alt: dict[str, str | None] = field(default_factory=dict)
    # Which of the page's lines each caption used as a description was built from, so they can be
    # tagged /Caption rather than left as ordinary prose -- one list per caption, because they are
    # grouped into elements by which caption they belong to, not by being adjacent.
    caption_blocks: list[list[TextLine]] = field(default_factory=list)

    @property
    def described(self) -> list[tuple[str, tuple]]:
        return [(fid, bbox) for fid, bbox in self.boxes if self.alt.get(fid)]

    @property
    def undescribed(self) -> list[tuple[str, tuple]]:
        return [(fid, bbox) for fid, bbox in self.boxes if not self.alt.get(fid)]

    def text_of(self, lines: list[TextLine], fid: str, bbox: tuple) -> list[TextLine]:
        """The lines that belong to a picture: its labels and callouts, read as part of it."""
        if fid in self.music_of:
            return _music_text(lines, self.music_of[fid])
        return (_figure_text_strict(lines, bbox) if fid in self.scan_regions or fid in self.modelled
                else _figure_text(lines, bbox))

    def draft(self, lines: list[TextLine], fid: str, bbox: tuple) -> str:
        """A starting point for the person to finish, for a picture with no description yet.

        A caption too thin to be used as alt text on its own ("Fig. 8", or a "(Continued)"
        page-break artifact) is still the best opening line anyone has: it is offered to the
        editor, never written into the document unedited. Nothing is invented -- an empty box stays
        empty. Music comes with a draft read off its staves, after its own label where it has one.
        """
        if fid in self.music_of:
            return " ".join(part for part in (music.label_for(self.music_of[fid], lines),
                                              self.music_of[fid].draft) if part)
        return (self.anchored.get(fid) or _figure_caption(lines, bbox) or "").strip()


def find_figures(source: Path, render_source: Path, src_page, lines: list[TextLine],
                 hints: layout_model.PageHints, *, scanned: bool, dpi: int,
                 full_render) -> PageFigures:
    """Every picture on the page: images the file declares, line art, printed music, and -- on a
    scan -- the pictures the layout model boxed and those found from the pixels.

    `full_render` returns the page rendered at `dpi`, rendering it only the first time it is asked.
    """
    boxes = _page_figures(src_page)
    vector = _vector_figures(src_page, list(lines), boxes)
    boxes += [(fid, bbox) for fid, bbox, _caption in vector]
    figs = PageFigures(boxes, anchored={fid: caption for fid, _bbox, caption in vector})
    # A scanned page places no images at all -- the whole sheet is one raster, and a diagram
    # printed on it is a patch of that raster, invisible to `_page_figures`. Every illustration
    # in a scanned book was therefore missed. They are found from the pixels instead: ink that
    # is not text (see `regions`).
    #
    # The layout model's picture boxes come first. It looks at the whole page and boxes a
    # picture as a picture, where the pixel search can only guess from ink that is not text --
    # which is why a pixel guess nobody captions is dropped (`describe_figures`), and a picture the
    # model found is not: an illustration the book never captions still needs describing, and used
    # to vanish. Its box also claims the scraps the recogniser made of the picture's own labels.
    #
    # Printed music, found by its staves (see `music`), goes first of all, because it is the surest
    # thing on the page about what a picture is. It replaces a picture found some other way over
    # the same music, unless that picture is much bigger than the music (a photograph of a
    # manuscript page, say), which keeps its own box.
    if scanned or src_page.drawings or src_page.images:
        for index, example in enumerate(music.examples_for_page(
                source, src_page.number, src_page.width, src_page.height, list(lines),
                keep_out=lambda line: music.is_label(line.text) or _is_caption_marker(line.text),
                render=(lambda: (full_render(), dpi)) if scanned else None)):
            covering = [(fid, bbox) for fid, bbox in figs.boxes
                        if _boxes_overlap(bbox, example.bbox)]
            if any(_area(bbox) > MUSIC_REPLACES_AREA * _area(example.bbox)
                   for _fid, bbox in covering):
                continue
            figs.boxes = [(fid, bbox) for fid, bbox in figs.boxes if (fid, bbox) not in covering]
            fid = f"p{src_page.number}s{index}"
            figs.boxes.append((fid, example.bbox))
            figs.music_of[fid] = example
    if scanned:
        pictures = [(f"p{src_page.number}m{index}", bbox)
                    for index, bbox in enumerate(hints.pictures)
                    if not any(_boxes_overlap(bbox, known) for _fid, known in figs.boxes)]
        figs.modelled = {fid for fid, _bbox in pictures}
        figs.boxes += pictures
        found = _scanned_figures(render_source, src_page, list(lines), dpi, figs.boxes)
        figs.scan_regions = {fid for fid, _bbox in found}
        figs.boxes += found
    # A table is not a picture. A drawn table's rules look like line art and a scanned one's
    # ink like an illustration, so a figure found from drawings or pixels that holds a table's
    # lines gives way to the table: tagged as one, a screen reader can move through it cell by
    # cell with each header announced, which no description of it can do (WCAG 1.3.1).
    figs.boxes = [(fid, bbox) for fid, bbox in figs.boxes
                  if fid in figs.music_of or re.fullmatch(r"p\d+f\d+", fid)
                  or not _holds_table(lines, hints, bbox)]
    return figs


def describe_figures(figs: PageFigures, lines: list[TextLine], alt_texts: dict[str, str],
                     document_captions: dict[str, str], removed) -> None:
    """Give each picture its description, and drop the pictures nothing vouches for.

    A figure sitting directly under (or, less commonly, above) a "Fig. N ..." caption needs no
    manual description at all: the author's own words are more accurate than anything Rebind could
    invent, and this skips the app's describe step entirely. A thin local match (just the marker,
    e.g. a "(Continued)" page-break artifact -- not substantial per WCAG 1.1.1) falls back to
    `document_captions`, which may have found the real caption elsewhere. Explicit user-supplied
    text (`alt_texts`) always wins over either -- the user may have deliberately typed something
    better, or the caption match may be imperfect.

    If NOTHING substantial turns up anywhere, the description stays None on purpose, so the figure
    lands in the app's describe list and the user is asked. A bare label ("Fig. 8", or the "Fig. 8
    (Continued)" page-break artifact) must never be accepted as alt text just because it exists: it
    satisfies a checker's "has /Alt" box while telling a screen-reader user nothing about the
    image, which is the exact failure WCAG 1.1.1 is about. Silence that prompts a human beats a
    placeholder that suppresses the prompt.
    """
    def caption_for(fid: str, bbox: tuple) -> str | None:
        # Music is never described on its own: its label opens the draft the person checks.
        if fid in figs.music_of:
            return None
        used: list[TextLine] = []
        local = figs.anchored.get(fid) or _figure_caption(lines, bbox, used)
        if local and _caption_is_substantial(local):
            if used:
                figs.caption_blocks.append(used)
            return local
        number = (_caption_number(local) if local else None) or _nearby_caption_number(lines, bbox)
        elsewhere = document_captions.get(number) if number else None
        if elsewhere and _caption_is_substantial(elsewhere):
            return elsewhere
        return None

    figs.alt = {fid: alt_texts.get(fid) or caption_for(fid, bbox) for fid, bbox in figs.boxes}
    # A caption does not always sit under its picture. In a book laid out with a wide outer
    # margin the captions are stacked *beside* the pictures, which is how a real page's
    # photograph came out with "Rebind found no caption to guess from" while its caption --
    # "Fig. 2. Head of the statue in figure 1..." -- sat two inches to its right.
    #
    # Reaching sideways is much more dangerous than reaching down, because several figures
    # share one vertical span and their captions are stacked in the margin beside all of them.
    # So it is taken only when the answer is unambiguous: exactly one caption block beside this
    # figure that no other figure has already claimed. Anything else leaves it undescribed and
    # asks the person, which is the honest outcome -- the wrong caption is a fabrication, and
    # worse than an empty box.
    claimed = {text for text in figs.alt.values() if text}
    for fid, bbox in figs.boxes:
        # Music is labelled beside its staves with a bare "Ex. 238.", never a sentence; a
        # "caption" found beside it is the next column's text.
        if figs.alt[fid] or fid in figs.music_of:
            continue
        beside = [(text, used) for text, used in _side_captions(lines, bbox)
                  if text not in claimed]
        if len(beside) == 1 and _caption_is_substantial(beside[0][0]):
            figs.alt[fid] = beside[0][0]
            claimed.add(beside[0][0])
            if beside[0][1]:
                figs.caption_blocks.append(beside[0][1])
    # A picture guessed from a scan's pixels, with no caption anywhere in the document to say
    # what it is, is treated as no picture at all. The guess comes from ink that is not text,
    # and on a page of maps and plans that is mostly the drawing's own hatching and rules --
    # pages 20 and 25 of a real scanned book produced a scatter of them. A picture the document
    # never captions is one Rebind has nothing to say about and no way to check, so it goes
    # back to being part of the page: no element, no held-out text, nothing to answer. What is
    # lost is a genuinely uncaptioned illustration, which the person can still mark by hand.
    #
    # This applies only to guesses. An image the FILE declares really is there, whatever the
    # document says about it, and stays -- undescribed and asking.
    #
    # And a figure the person has taken out stays out. Figures live outside the plan, so the
    # edit that removes an element never reached them: pressing "x" on a picture did nothing at
    # all, which on a page with two wrongly-found ones is a wall the walk cannot get past.
    figs.boxes = [(fid, bbox) for fid, bbox in figs.boxes
                  if (figs.alt[fid] or fid not in figs.scan_regions) and fid not in removed]
