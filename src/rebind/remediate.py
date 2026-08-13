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
# How much of the shorter box a figure and an element must share vertically to be on one row, and
# so be ordered by which is further left rather than by which starts a shade higher.
FIGURE_SAME_ROW_FRACTION = 0.5
# The same question for two of the editor's elements, which are measured as percentages of the page
# rather than in points. Lower than the line-level rule: these are whole blocks, and a folio sitting
# inside a tall paragraph's vertical span shares its row for this purpose even though it covers
# only a sliver of it.
RECORD_SAME_ROW_FRACTION = 0.5
# The longest a callout label runs to ("Ventral", "2-10 ml glass syringe", "3 mm"). Longer than
# this, inside a guessed figure box, is a sentence -- prose the picture sits around, not part of it.
FIGURE_LABEL_MAX_WORDS = 6

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

# How much of the shorter line's width two lines of one wrapped heading must share. A title's
# second line sits under the first whether the block is centred, left-aligned or justified, so the
# overlap is large; two headings that merely follow one another in a column also overlap, which is
# why the vertical test below carries the weight and this one only excludes side-by-side fragments.
HEADING_MIN_OVERLAP = 0.5
# How far a heading's next line may reach back up into the previous one before it is read as part
# of the same physical line rather than the line under it. Accents and ascenders routinely push a
# box a little above the one it follows; a byline's fragments overlap almost entirely.
HEADING_OVERLAP_SLACK = 0.25

# A table spans from its first detected row to its last; lines in between that were not themselves
# detected as table rows are sparse rows (a subtotal, or a row with an empty cell -- too few
# side-by-side cells to detect alone) and are kept so no row is dropped. The gap is bounded so prose
# between two separate tables cannot merge them: at most this many consecutive undetected lines.
MAX_INTERNAL_TABLE_GAP = 2

# --- Paragraphs -------------------------------------------------------------------------------
# A paragraph is several lines, and tagging each line as its own /P is wrong in a way that matters:
# a screen reader pauses at every element boundary, so a page of prose read as forty paragraphs is
# read as forty fragments. Lines are joined into one /P unless something says they are not the same
# paragraph. The signals below are the ones typesetting actually leaves behind, in rough order of
# how much they can be trusted:
#
#   * The previous line stops short of the measure. In justified or ragged-right prose every line
#     but the last runs to the right margin, so a short line is the end of its paragraph. This is
#     the strongest signal there is, and it is what makes the rest mostly unnecessary.
#   * The next line is indented past the paragraph's own left edge -- a first-line indent, the
#     other half of the same convention.
#   * The vertical gap is bigger than the run's own leading (space between paragraphs).
#   * The typography changes: a different size, weight, slope or face is a different thing.
#
# Where the signals disagree, the split is kept: two paragraphs wrongly joined lose a boundary a
# reader needs, and a boundary is not recoverable from the joined text.
PARAGRAPH_ALIGN_TOLERANCE_PT = 3.0    # left edges this close count as the same margin
PARAGRAPH_INDENT_MIN_PT = 4.0         # a first line indented at least this much starts a paragraph
PARAGRAPH_RAGGED_FRACTION = 0.10      # a line ending this far short of the measure ends its own
PARAGRAPH_GAP_SLACK = 0.55            # extra leading (in line heights) that still reads as one
PARAGRAPH_COLUMN_TOLERANCE_PT = 36.0  # left edges within this belong to the same column
PARAGRAPH_SIZE_TOLERANCE = 0.18       # fraction by which two lines' sizes may differ and still match

# Reading-order review thumbnails: big enough to recognize the page's shape and read the block
# numbers laid over it, small enough that a dozen of them stay a page the browser renders at once.
REVIEW_THUMB_DPI = 80
REVIEW_THUMB_PX = 420
# The page editor shows a bigger picture -- elements are selected on it and their text read from
# it -- so it renders larger than the review thumbnail, once per page.
EDITOR_PAGE_DPI = 110
EDITOR_PAGE_PX = 900


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
    # Every tagged element, in reading order, for the app's page editor: {id, page, kind, text,
    # left/top/width/height as percentages of the page}. A figure additionally carries "alt".
    elements: tuple[dict, ...] = ()
    # A rendered thumbnail per page (data URI), so the editor can lay elements over the page.
    page_images: dict = field(default_factory=dict)


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


def _is_caption_marker(text: str) -> bool:
    """Whether a line opens with a caption label of any kind, numbered or not."""
    return CAPTION_MARKER_RE.match(text.strip()) is not None


def _caption_number(text: str) -> str | None:
    """The figure number a line's caption marker names ("Fig. 8" -> "8").

    None when the line is not a caption *or* when it is an unnumbered one ("Photograph of the
    reading room."). The number's only job is matching a caption to its figure across a page
    break, which an unnumbered caption cannot do anyway -- so the two cases are the same answer
    to this question, and `_is_caption_marker` is what asks whether a line is a caption at all.
    """
    match = CAPTION_MARKER_RE.match(text.strip())
    return match.group(1) if match else None


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
                    caption_of: dict[int, int], blocks: list[list[TextLine]]) -> list[int | None]:
    """Which caption each content line belongs to, with the strays in between folded in.

    A caption's own lines are known exactly. What is not is what the recogniser dropped between
    them: on a real scan a stray "~" and "|" sat between the lines of a caption in the margin, and
    since a plan entry is a contiguous run, those two marks split one caption into three elements.

    A line is folded into a caption when it sits inside that caption's vertical span and within
    arm's length of it horizontally. It has to be near in both, or an unrelated line level with the
    caption on the other side of the page would join it -- which is the same mistake as reading
    across a gutter.
    """
    groups: list[int | None] = [caption_of.get(id(line)) for line in content_lines]
    if not blocks:
        return groups
    extents = [(min(ln.bbox[0] for ln in b), min(ln.bbox[1] for ln in b),
                max(ln.bbox[2] for ln in b), max(ln.bbox[3] for ln in b)) for b in blocks]
    for index, (line, role) in enumerate(zip(content_lines, content_roles)):
        if groups[index] is not None or role != "P":
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


def _records_in_reading_order(records: list[dict]) -> list[dict]:
    """The editor's elements for one page: down the page, and across each row left to right.

    Sorting on `top` alone (with `left` only as a tie-break on the exact same value) is what put a
    page's folio ahead of the footer line beside it: the two differ by a fraction of a percent, so
    the tie-break never fires and the reading order turns on that fraction. Grouping first is also
    what keeps the two halves of a two-column page in the right order, since a left-column block
    and the right-column block beside it overlap for most of their height and the left one wins.
    """
    ordered = sorted(records, key=lambda r: (r["top"], r["left"]))
    out: list[dict] = []
    row: list[dict] = []
    for record in ordered:
        if row:
            top = min(r["top"] for r in row)
            bottom = max(r["top"] + r["height"] for r in row)
            overlap = min(bottom, record["top"] + record["height"]) - max(top, record["top"])
            shorter = min(record["height"], min(r["height"] for r in row))
            if overlap <= RECORD_SAME_ROW_FRACTION * max(shorter, 0.01):
                out.extend(sorted(row, key=lambda r: r["left"]))
                row = []
        row.append(record)
    out.extend(sorted(row, key=lambda r: r["left"]))
    return out


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


def _draw_line(out: io.BytesIO, line: TextLine, font_name: str) -> None:
    """The invisible (render mode 3) text for one line, with no marked-content wrapper of its own."""
    x0, y0, x1, y1 = line.bbox
    size = max(y1 - y0, 1.0)
    out.write(b"q BT 3 Tr /" + font_name.encode("ascii") + b" 1 Tf\n")
    out.write(f"{size:.2f} 0 0 {size:.2f} {x0:.2f} {y0:.2f} Tm (".encode("ascii"))
    out.write(_encode_winansi(_escape(line.text)))
    out.write(b") Tj\nET Q\n")


def _tagged_text_stream(lines: list[TextLine], font_name: str,
                        mcids: list[int | None]) -> bytes:
    """Invisible text (render mode 3), one marked-content sequence per line.

    A line with an MCID is wrapped in `/P <</MCID n>> BDC ... EMC` so the structure tree can point
    at it. A line with `None` is wrapped in `/Artifact BMC ... EMC` instead: page furniture -- a
    running header, a footer, a folio -- which PDF/UA requires be marked as an artifact rather than
    tagged as content, so a screen reader does not announce "Makoto Kamei et al., 34" in the middle
    of the prose on every single page. The text is still drawn, so it stays selectable and
    searchable; it simply is not part of the document's content.
    """
    out = io.BytesIO()
    for line, mcid in zip(lines, mcids):
        out.write(b"/Artifact BMC\n" if mcid is None
                  else f"/P <</MCID {mcid}>> BDC\n".encode("ascii"))
        _draw_line(out, line, font_name)
        out.write(b"EMC\n")
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

    This counts LINES, deliberately, even though `_same_heading` can tell that two of them are one
    wrapped heading. Counting joined headings instead was tried and reverted: a byline that wraps
    is geometrically indistinguishable from a title that wraps -- same face, same tight leading,
    same horizontal overlap -- so exempting joins let the real sample's 3-fragment byline back into
    the outline as headings. The limit of 2 already admits a title set across two lines, which is
    the shape that actually occurs; a title needing three is demoted, which is the safe direction.
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


def _same_heading(lines: list[TextLine], index: int) -> bool:
    """Whether `lines[index]` is the next line of the same wrapped heading as the line before it.

    A heading is joined on much simpler evidence than a paragraph: there is no measure to run out
    to (a title is usually centred, and one that fills its line does so by accident), and no
    first-line indent to read. What is left is that the two lines are set the same way, sit
    directly under one another, and overlap horizontally -- which a wrapped title does and the two
    shapes it must not swallow do not. Byline fragments broken around superscript markers share a
    baseline rather than stacking, and a diagram's callout labels are spread across the picture.
    """
    previous, current = lines[index - 1], lines[index]
    if previous.bold != current.bold or previous.italic != current.italic:
        return False
    if abs(previous.size - current.size) > max(PARAGRAPH_SIZE_TOLERANCE * max(
            previous.size, current.size, 1.0), 0.5):
        return False
    if previous.ocr_confidence is None and current.ocr_confidence is None \
            and previous.font != current.font:
        return False

    height = max(previous.bbox[3] - previous.bbox[1], 1.0)
    gap = previous.bbox[1] - current.bbox[3]
    # Strictly below, by less than its own leading. The lower bound is what excludes fragments of
    # one physical line, which overlap vertically rather than following one another.
    if not -height * HEADING_OVERLAP_SLACK <= gap <= height * PARAGRAPH_GAP_SLACK:
        return False

    left = max(previous.bbox[0], current.bbox[0])
    right = min(previous.bbox[2], current.bbox[2])
    narrower = min(previous.bbox[2] - previous.bbox[0], current.bbox[2] - current.bbox[0])
    return (right - left) >= HEADING_MIN_OVERLAP * max(narrower, 1.0)


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
        # Several of a row's cells can snap to one column -- their left edges sit within the
        # tolerance of each other, which a noisy OCR'd grid produces constantly. They share the
        # cell rather than the later ones being dropped: dropping one would leave its marked
        # content on the page owned by nothing, which is untagged content, not a tidier table.
        by_column: dict[int, list[tuple[int, TextLine]]] = {}
        for mcid, line in row:
            by_column.setdefault(column_of(line), []).append((mcid, line))
        is_header = row_index == 0
        cell_type = Name.TH if is_header else Name.TD
        row_cells: list[pikepdf.Object] = []
        for c in range(len(columns)):
            if c in by_column:
                members = by_column[c]
                extra = {"A": Dictionary(O=Name.Table, Scope=Name.Column)} if is_header else None
                row_cells.append(leaf([mcid for mcid, _line in members], cell_type, tr, extra))
                if is_header:
                    header_texts.append(
                        " ".join(line.text.strip() for _mcid, line in members).strip())
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


def _center_in_box(bbox: tuple, box: tuple) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


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
    from .ocr import render_page_to_image
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


def _boxes_overlap(a: tuple, b: tuple) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


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
    return [line for line in lines
            if _center_in_box(line.bbox, bbox)
            and len(line.text.split()) <= FIGURE_LABEL_MAX_WORDS]


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
    """
    floor = max(
        (ln.bbox[3] for ln in lines
         if _is_caption_marker(ln.text) and ln.bbox[3] <= bbox[1] + FIGURE_TEXT_TOLERANCE_PT),
        default=None,
    )
    candidates = [ln for ln in lines if floor is None or ln.bbox[3] > floor]
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


def _figure_anchor(lines: list[TextLine], mcids: list[int | None], figure_index: int,
                   owner_figure: dict[int, int], bbox: tuple) -> int | None:
    """The MCID of the content line a figure should be read *before*, or None to read it last.

    Reading order is a sequence, so a figure needs a place in it. Its own labels were already
    spliced to the right height by the layout pass, so the line following them is where the figure
    belongs; failing that (a figure with no labels of its own) the first content line starting
    below the figure serves the same purpose.
    """
    seen_own_text = False
    for line, mcid in zip(lines, mcids):
        if owner_figure.get(id(line)) == figure_index:
            seen_own_text = True
            continue
        if mcid is None:
            continue
        if seen_own_text or line.bbox[3] <= bbox[1]:
            return mcid
    return None


def _top_index_for(tops: list, owners: list, anchor: int | None) -> int:
    """Where in `tops` an element anchored before MCID `anchor` belongs."""
    if anchor is None or anchor >= len(owners):
        return len(tops)
    owner = owners[anchor]
    for position, top in enumerate(tops):
        if top is owner or _contains(top, owner):
            return position
    return len(tops)


def _contains(parent, target) -> bool:
    """Whether `target` is `parent` or sits somewhere beneath it in the structure tree."""
    kids = parent.get("/K")
    if not isinstance(kids, Array):
        return False
    for kid in kids:
        if isinstance(kid, Dictionary):
            if kid == target or _contains(kid, target):
                return True
    return False


@dataclass
class Edits:
    """A person's corrections to what Rebind decided, applied on the next run.

    Everything is keyed by element id, and an element id is derived from the source line it starts
    at -- never from its position in the list -- so removing or retagging one element cannot
    silently shift what every later correction refers to.
    """

    tags: dict[str, str] = field(default_factory=dict)      # element id -> structure type
    removed: set[str] = field(default_factory=set)          # element ids to drop from the tree
    alts: dict[str, str] = field(default_factory=dict)      # figure id -> alt text

    @classmethod
    def from_payload(cls, payload: dict | None) -> Edits:
        payload = payload or {}
        allowed = set(EDITABLE_TAGS)
        return cls(
            tags={str(k): str(v) for k, v in (payload.get("tags") or {}).items() if v in allowed},
            removed={str(v) for v in (payload.get("removed") or [])},
            alts={str(k): str(v).strip() for k, v in (payload.get("alts") or {}).items()
                  if str(v).strip()},
        )


# What a person may retag an element as, and how each has to be built. ISO 32005 Table 5 governs
# what may appear directly under the document element, and veraPDF enforces it strictly -- every
# entry below was checked by producing a document that uses it and validating the result, because
# guessing got it wrong twice (/Caption and /Quote both look reasonable and are both illegal there).
#
# Three shapes, because "legal under Document" is not the same as "may hold content directly":
#   * CONTENT -- takes the page's marked content as its own children. The ordinary case.
#   * GROUPING -- a container that may only hold block-level children, so the content is wrapped
#     in a /P inside it. Tagging one of these directly over content fails; wrapping is the fix.
#   * built specially -- /Figure needs an /Alt, /Table needs rows and cells, /L needs list items,
#     /Caption must sit inside the figure or table it captions rather than beside it.
#
# Two names that look obviously right and are not. /Aside is HTML5's, not PDF 2.0's, and fails
# "all structure elements shall belong to one of the following namespaces". /TOC is legal, and can
# be built (its children must be /TOCI, never /P), but every /TOCI must carry a /Ref identifying
# what the entry points at -- and Rebind cannot know which heading a line of a contents list refers
# to without guessing. So it is not offered: a table of contents that names the wrong targets is
# worse than one tagged as ordinary paragraphs.
CONTENT_TAGS = ("P", "H1", "H2", "H3", "H4", "H5", "H6", "BlockQuote", "Code", "Formula", "Form")
# /Part is not offered: it is a container with no behaviour of its own that /Sect and /Div do not
# already have, so it was one more thing to choose between for no gain to a reader.
GROUPING_TAGS = ("Sect", "Div", "Art", "Index", "NonStruct")
SPECIAL_TAGS = ("Figure", "Table", "L", "Caption")
EDITABLE_TAGS = CONTENT_TAGS + GROUPING_TAGS + SPECIAL_TAGS

# One keystroke per type, so retagging is Tab-and-press rather than Tab-and-open-a-menu. Chosen for
# the first letter of the thing wherever it is free, and the digits for heading levels. Defined
# here rather than in the page's script so the keys, the labels and the tags cannot drift apart.
# The third column is what the editor shows in big letters when an element has focus, with its key
# beside it; the fourth says what the type *means* -- a librarian retagging a page should not have
# to already know what a PDF structure type does, and "BlockQuote" is not self-explanatory to
# anyone who doesn't.
#
# /Artifact is not here either. It is not a structure type -- it is the absence of one -- and
# offering it alongside real types put a non-answer in the list of answers.
TAG_KEYS = (
    ("p", "P", "Paragraph", "Ordinary body text. The default, and right for most things."),
    ("1", "H1", "Heading 1", "The document's top-level heading — usually its title."),
    ("2", "H2", "Heading 2", "A major section heading beneath a Heading 1."),
    ("3", "H3", "Heading 3", "A subsection heading beneath a Heading 2."),
    ("4", "H4", "Heading 4", "A fourth-level heading."),
    ("5", "H5", "Heading 5", "A fifth-level heading."),
    ("6", "H6", "Heading 6", "A sixth-level heading — the deepest there is."),
    ("q", "BlockQuote", "Block quote", "An extended quotation set apart from the body text."),
    ("c", "Caption", "Caption", "The text that describes a figure or table. Rebind puts it inside "
                                "the figure or table it belongs to."),
    ("f", "Figure", "Figure", "A picture, chart or diagram. Needs a description, so a screen "
                              "reader has something to say about it."),
    ("t", "Table", "Table", "A grid of data. Rebind builds the rows, cells and column headers."),
    ("l", "L", "List", "A bulleted or numbered list."),
    ("s", "Sect", "Section", "A container grouping related content together."),
    ("d", "Div", "Division", "A generic container, when nothing more specific fits."),
    ("a", "Art", "Article", "A self-contained piece of writing within the document."),
    ("i", "Index", "Index", "An index of terms and where they appear."),
    ("n", "NonStruct", "No structure", "Content that carries no structural meaning of its own."),
    ("m", "Formula", "Formula (maths)", "A mathematical or chemical expression."),
    ("e", "Code", "Code", "Program code or other text where the exact characters matter."),
    ("o", "Form", "Form field", "An interactive field a reader fills in."),
)

# Not a type, so not in TAG_KEYS -- an action, on its own key: take this out of the reading order
# and let it be drawn as page furniture instead. Page furniture Rebind already identified is an
# artifact without anyone pressing anything; this is for the one it got wrong.
ARTIFACT_KEY = "x"
ARTIFACT_LABEL = "Not read"
ARTIFACT_WHAT = ("Page furniture — on the page, but skipped by a screen reader. Give it a type to "
                 "have it read after all.")


def _element_records(src_page, plan: list[dict], lines: list[TextLine],
                     mcid_of: list[int | None]) -> list[dict]:
    """One record per element for the app's editor: what it is, where it is, what it says."""
    out = []
    for entry in plan:
        first, last = entry["first"], entry["last"]
        if mcid_of[first] is None:
            continue
        members = lines[first:last + 1]
        box = (min(ln.bbox[0] for ln in members), min(ln.bbox[1] for ln in members),
               max(ln.bbox[2] for ln in members), max(ln.bbox[3] for ln in members))
        out.append({
            "id": entry["id"],
            "page": src_page.number,
            "kind": entry["kind"],
            "alt": entry.get("alt", ""),
            "text": " ".join(ln.text.strip() for ln in members).strip()[:300],
            "left": round(100 * box[0] / src_page.width, 2),
            "top": round(100 * (src_page.height - box[3]) / src_page.height, 2),
            "width": round(100 * (box[2] - box[0]) / src_page.width, 2),
            "height": round(100 * (box[3] - box[1]) / src_page.height, 2),
            "editable": True,
        })
    return out


def _untagged_record(src_page, element_id: str, line: TextLine) -> dict:
    """A line Rebind left out of the structure tree -- page furniture, or text inside a figure --
    offered to the editor so it can be given a tag and read after all."""
    x0, y0, x1, y1 = line.bbox
    return {
        "id": element_id, "page": src_page.number, "kind": "Artifact",
        "text": line.text.strip()[:300],
        "left": round(100 * x0 / src_page.width, 2),
        "top": round(100 * (src_page.height - y1) / src_page.height, 2),
        "width": round(100 * (x1 - x0) / src_page.width, 2),
        "height": round(100 * (y1 - y0) / src_page.height, 2),
        "editable": True,
    }


def plan_page(lines: list[TextLine], page_roles: list[str],
              caption_groups: list[int | None] | None = None) -> list[dict]:
    """Group a page's content lines into the elements the structure tree will hold.

    This is the whole of Rebind's per-page structural judgement, expressed as plain data before any
    PDF object exists: `[{"kind": "P"|"H1".."H6"|"L"|"Table", "first": i, "last": j}]`, over
    inclusive indices into `lines`. Separating it out is what lets the app show a page's elements,
    let a person correct them, and rebuild from the corrected plan -- the alternative, editing a
    structure tree after the fact, cannot change which lines belong together.

    `caption_groups[i]` names the caption a line belongs to, or None. Captions are grouped by that
    rather than by the paragraph rule, because the paragraph rule cannot hold them together: a
    caption set in a narrow margin column has no measure to run out to, and on a scan the
    recogniser drops stray marks between its lines, each of which breaks a run of "consecutive
    lines with the same role". The caption search already decided which lines are one caption --
    this just carries that decision through instead of trying to re-derive it from geometry.
    """
    n = len(lines)
    groups = caption_groups if caption_groups is not None else [None] * n
    table_line_ids = detect_table_lines(lines)
    is_table = [id(line) in table_line_ids for line in lines]
    measures = _column_measures(lines, page_roles)
    plan: list[dict] = []
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
            plan.append({"kind": "Table", "first": i, "last": last})
            i = last + 1
            continue

        if _is_list_item(lines[i].text):
            j = i
            while j < n and not is_table[j] and _is_list_item(lines[j].text):
                j += 1
            if j - i >= MIN_LIST_ITEMS:
                plan.append({"kind": "L", "first": i, "last": j - 1})
                i = j
                continue

        # Body text runs on until something says the paragraph has ended. Everything else -- a
        # heading, a caption, page furniture -- is one element per line by nature.
        # A caption is one element, and which lines it is made of was settled when it was found.
        if page_roles[i] == "Caption" and groups[i] is not None:
            j = i
            while j + 1 < n and groups[j + 1] == groups[i]:
                j += 1
            plan.append({"kind": "Caption", "first": i, "last": j})
            i = j + 1
            continue

        if page_roles[i] in ("P", "Caption"):
            j = i
            while (j + 1 < n and not is_table[j + 1] and page_roles[j + 1] == page_roles[i]
                   and not _is_list_item(lines[j + 1].text)
                   and _same_paragraph(lines, i, j + 1, measures)):
                j += 1
            plan.append({"kind": page_roles[i], "first": i, "last": j})
            i = j + 1
            continue

        # A heading runs on the same way a paragraph does, on its own simpler evidence: a title
        # too long for one line is one title, and leaving it as two elements puts a phantom entry
        # in a screen reader's heading list and a pause in the middle of the title.
        if page_roles[i].startswith("H"):
            j = i
            while (j + 1 < n and not is_table[j + 1] and page_roles[j + 1] == page_roles[i]
                   and _same_heading(lines, j + 1)):
                j += 1
            plan.append({"kind": page_roles[i], "first": i, "last": j})
            i = j + 1
            continue

        plan.append({"kind": page_roles[i], "first": i, "last": i})
        i += 1
    return plan


def _column_measures(lines: list[TextLine], roles: list[str]) -> list[tuple[float, float]]:
    """For each line, the (left, right) margins of the column of body text it sits in.

    The right margin is what tells a last line from a middle one, and it has to come from the text
    itself: nothing in a PDF states where a column ends. It is taken as the furthest right any body
    line in the same column reaches, which is the measure by definition -- at least one line in a
    paragraph of prose runs the full width of it.
    """
    body = [ln for ln, role in zip(lines, roles) if role == "P"]
    out: list[tuple[float, float]] = []
    for line in lines:
        near = [ln for ln in body
                if abs(ln.bbox[0] - line.bbox[0]) <= PARAGRAPH_COLUMN_TOLERANCE_PT]
        if not near:
            out.append((line.bbox[0], line.bbox[2]))
            continue
        out.append((min(ln.bbox[0] for ln in near), max(ln.bbox[2] for ln in near)))
    return out


def _same_paragraph(lines: list[TextLine], first: int, index: int,
                    measures: list[tuple[float, float]]) -> bool:
    """Whether `lines[index]` continues the paragraph that started at `lines[first]`."""
    previous, current = lines[index - 1], lines[index]
    if previous.bold != current.bold or previous.italic != current.italic:
        return False
    # Size is compared with tolerance, and the face only when both lines are born-digital. A
    # recognized line has neither exactly: its "size" is derived from the height of a box drawn
    # around inked pixels, which varies line to line with whatever ascenders and descenders happen
    # to be in it, and its font name is whatever the recognizer reports. Demanding equality there
    # refused nearly every join on a scan -- 470 paragraphs of 15 words each on a real one, which
    # is a page of prose read as a list.
    if abs(previous.size - current.size) > max(PARAGRAPH_SIZE_TOLERANCE * max(
            previous.size, current.size, 1.0), 0.5):
        return False
    born_digital = previous.ocr_confidence is None and current.ocr_confidence is None
    if born_digital and previous.font != current.font:
        return False

    height = max(previous.bbox[3] - previous.bbox[1], 1.0)
    gap = previous.bbox[1] - current.bbox[3]
    if gap < -height:
        return False        # not below the previous line at all: a new column, or a new page
    if gap > height * PARAGRAPH_GAP_SLACK:
        return False        # set apart by more than its own leading

    left, right = measures[index - 1]
    if previous.bbox[2] < right - PARAGRAPH_RAGGED_FRACTION * max(right - left, 1.0):
        return False        # the previous line stopped short of the measure, so it ended there

    # The paragraph's own left margin is set by its *second* line: the first may be indented, and
    # comparing against an indented first line would split every indented paragraph in two.
    margin = lines[first + 1].bbox[0] if index > first + 1 else current.bbox[0]
    if current.bbox[0] > margin + PARAGRAPH_INDENT_MIN_PT:
        return False        # indented: the first line of the next paragraph
    return abs(current.bbox[0] - margin) <= PARAGRAPH_ALIGN_TOLERANCE_PT


def _page_structure(pdf: pikepdf.Pdf, lines: list[TextLine], plan: list[dict],
                    mcid_of: list[int | None],
                    document_elem: pikepdf.Object, page_obj: pikepdf.Object,
                    caption_hosts: list | None = None):
    """Build one page's structure elements from an (already decided, possibly edited) plan.

    Returns (top-level elements in reading order, owners) where `owners[mcid]` is the leaf element
    that directly holds that MCID -- what the page's ParentTree entry indexes.
    """
    owners: dict[int, pikepdf.Object] = {}
    tops: list[pikepdf.Object] = []

    def leaf(index: int | list[int], structure_type, parent,
             extra: dict | None = None) -> pikepdf.Object:
        """One element holding the marked content of one line -- or of several, when more than one
        line lands in the same cell. Every id passed in is owned; dropping one would leave content
        drawn on the page that no element claims (clause 8.2.2)."""
        mcids = [mcid_of[i] for i in ([index] if isinstance(index, int) else index)]
        elem = pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=structure_type, P=parent, Pg=page_obj,
            K=Array(mcids) if len(mcids) > 1 else mcids[0], **(extra or {})))
        for mcid in mcids:
            owners[mcid] = elem
        return elem

    def spanning(kind: str, parent, indices: list[int]) -> pikepdf.Object:
        """One element holding every line's marked content directly."""
        elem = pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=Name("/" + kind), P=parent, Pg=page_obj,
            K=Array([mcid_of[i] for i in indices]) if len(indices) > 1 else mcid_of[indices[0]]))
        for i in indices:
            owners[mcid_of[i]] = elem
        return elem

    captions: list[tuple[int, pikepdf.Object]] = []      # (first line, the caption element)
    for entry in plan:
        first, last, kind = entry["first"], entry["last"], entry["kind"]
        if any(mcid_of[i] is None for i in range(first, last + 1)):
            continue        # every line of this element was removed by an edit
        indices = list(range(first, last + 1))

        if kind == "Table":
            tops.append(_tagged_table(pdf, [(i, lines[i]) for i in indices],
                                      document_elem, page_obj, leaf))
        elif kind == "L":
            lst = pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name.L, P=document_elem, K=Array([])))
            items = []
            for i in indices:
                li = pdf.make_indirect(Dictionary(
                    Type=Name.StructElem, S=Name.LI, P=lst, K=Array([])))
                li.K = Array([leaf(i, Name.LBody, li)])
                items.append(li)
            lst.K = Array(items)
            tops.append(lst)
        elif kind in GROUPING_TAGS:
            # A grouping element may only hold block-level children, never content of its own --
            # tagging one straight over the text fails validation, so the text goes in a /P inside.
            group = pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name("/" + kind), P=document_elem, K=Array([])))
            group.K = Array([spanning("P", group, indices)])
            tops.append(group)
        elif kind == "Figure":
            figure = spanning("Figure", document_elem, indices)
            # Never fabricated: a figure made out of text is described by that text unless the
            # person typed something better, which is what the editor's description box is for.
            figure.Alt = String(entry.get("alt")
                                or " ".join(lines[i].text.strip() for i in indices).strip()
                                or "Figure")
            tops.append(figure)
        elif kind == "Caption":
            box = (min(lines[i].bbox[0] for i in indices),
                   min(lines[i].bbox[1] for i in indices),
                   max(lines[i].bbox[2] for i in indices),
                   max(lines[i].bbox[3] for i in indices))
            captions.append((first, spanning("Caption", document_elem, indices), box))
        else:
            tops.append(spanning(kind, document_elem, indices))

    # A /Caption is not legal beside the thing it captions -- it belongs inside it. Each is moved
    # into the figure or table it is nearest to; one with nothing left to caption stays where it
    # is, as a paragraph, rather than being dropped or made non-conformant.
    #
    # Two constraints from PDF/UA-2 shape this, and both were learned by failing them. A figure may
    # hold AT MOST ONE caption (Table 5, Figure-Caption), so a host that already has one is not
    # offered again -- on a page with two pictures, taking "the last figure built" put every
    # caption into the same one. And a caption must be the FIRST OR LAST child of its parent
    # (clause 8.2.5.27), which one of several siblings cannot be.
    taken: set = set()
    for first, caption, box in captions:
        host = _caption_host(list(caption_hosts or []) + tops, box, taken)
        if host is None:
            caption.S = Name.P
            tops.insert(_position_for(tops, first, plan), caption)
            continue
        taken.add(host.objgen)
        caption.P = host
        kids = host.get("/K")
        host.K = Array(list(kids) + [caption]) if isinstance(kids, Array) else Array(
            [kids, caption] if kids is not None else [caption])
    return tops, owners


def _caption_host(tops: list, box: tuple, taken: set) -> pikepdf.Object | None:
    """The figure or table a caption belongs inside: the nearest one that has not got one already.

    Nearness is measured between boxes, so a caption in the margin goes to the picture beside it
    rather than to whichever figure happened to be built last. A figure records its own box (in
    `/A /BBox`); a table does not, so tables stay a fallback taken in reverse build order.
    """
    best, best_gap = None, None
    for elem in tops:
        if str(elem.get("/S")) != "/Figure" or elem.objgen in taken:
            continue
        layout = elem.get("/A")
        bbox = layout.get("/BBox") if layout is not None else None
        if bbox is None:
            continue
        fx0, fy0, fx1, fy1 = (float(v) for v in bbox)
        gap = max(0.0, max(fx0 - box[2], box[0] - fx1)) + max(0.0, max(fy0 - box[3], box[1] - fy1))
        if best_gap is None or gap < best_gap:
            best, best_gap = elem, gap
    if best is not None:
        return best
    for elem in reversed(tops):
        if str(elem.get("/S")) == "/Table" and elem.objgen not in taken:
            return elem
    return None


def _position_for(tops: list, first: int, plan: list[dict]) -> int:
    """Where an element starting at line `first` belongs among the page's elements."""
    seen = 0
    for entry in plan:
        if entry["first"] >= first:
            return min(seen, len(tops))
        seen += 1
    return len(tops)


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


def _text_visibility(page: pikepdf.Page) -> tuple[bool, bool]:
    """(has any text, has any *visible* text) for one page.

    Rendering mode 3 draws nothing: it is how an OCR tool lays a searchable text layer over a scan.
    Text drawn that way is not perceivable, so it is not text a reader can be said to see.
    """
    try:
        instructions = list(pikepdf.parse_content_stream(page))
    except Exception:  # noqa: BLE001 -- an unparseable stream tells us nothing either way
        return (False, False)
    has_text = has_visible = False
    mode = 0
    saved: list[int] = []
    for ins in instructions:
        operator = str(getattr(ins, "operator", ""))
        if operator == "Tr":
            mode = int(ins.operands[0])
        elif operator == "q":
            saved.append(mode)
        elif operator == "Q" and saved:
            mode = saved.pop()
        elif operator in ("Tj", "TJ", "'", '"'):
            has_text = True
            if mode != 3:
                has_visible = True
    return (has_text, has_visible)


def _invisible_text_pages(source: Path) -> set[int]:
    """Page numbers whose text is *entirely* invisible -- a scan with an OCR layer over it.

    Such a page's words are not something a reader perceives: what they see is the picture. Their
    colours are therefore not a colour decision the document made, and measuring the contrast of
    text drawn in rendering mode 3 produces failures for text that is not on the page at all
    (939 lines and 49 "failures" on a real Tesseract-processed scan, every one of them invisible).
    Rebind strips that layer anyway, so the measurement would describe a document that no longer
    exists by the time it is written.
    """
    with pikepdf.open(source) as pdf:
        return {number for number, page in enumerate(pdf.pages, start=1)
                if _text_visibility(page) == (True, False)}


def _strip_invisible_text(pdf: pikepdf.Pdf, page: pikepdf.Page) -> bool:
    """Remove a scan's own invisible OCR text layer, leaving every visible mark untouched.

    A scan that has already been through an OCR tool carries the picture plus a layer of text drawn
    in rendering mode 3 -- invisible, there only to be selectable. Rebind lays its *own* tagged
    invisible layer over the same page, so the original's is a second, untagged copy of the same
    words; and Tesseract's stand-in font ("GlyphLessFont") declares a ToUnicode CMap veraPDF
    rejects, failing clause 8.4.5.8 for a font the document no longer needs. Removing that layer
    (rather than re-rendering the whole page, which would resample the scan) keeps the page's own
    pixels exactly as they were -- nothing that marks the page is touched, so it still looks
    identical -- and takes the unusable font out with it.

    Returns whether anything was removed.
    """
    try:
        instructions = list(pikepdf.parse_content_stream(page))
    except Exception:  # noqa: BLE001 -- unparseable content is left alone, never half-rewritten
        return False

    out: list = []
    block: list = []            # the current BT..ET run, held back until we know if it shows ink
    in_text = False
    mode = 0                    # text rendering mode, part of the graphics state (persists past ET)
    block_mode: int | None = None
    visible = False
    removed = False
    saved: list[int] = []       # `q`/`Q` save and restore the mode along with the rest of the state
    for ins in instructions:
        operator = str(getattr(ins, "operator", ""))
        if operator == "BT":
            in_text, block, visible, block_mode = True, [ins], False, None
            continue
        if not in_text:
            # Tracking q/Q matters: a mode restored by Q and not re-set is 0, and mistaking that
            # for a still-invisible state would delete text the page actually shows.
            if operator == "q":
                saved.append(mode)
            elif operator == "Q" and saved:
                mode = saved.pop()
            elif operator == "Tr":
                mode = int(ins.operands[0])
            out.append(ins)
            continue
        block.append(ins)
        if operator == "Tr":
            mode = int(ins.operands[0])
            block_mode = mode
        elif operator in ("Tj", "TJ", "'", '"') and mode != 3:
            visible = True
        elif operator == "ET":
            in_text = False
            if visible:
                out.extend(block)
            else:
                removed = True
                # A rendering mode set inside the dropped run persists past ET, so it has to be
                # re-stated: dropping it silently would make the *next* run visible or invisible.
                if block_mode is not None:
                    out.append(pikepdf.ContentStreamInstruction([block_mode], pikepdf.Operator("Tr")))
            block = []
    out.extend(block)           # an unterminated BT run: keep it rather than lose content
    if not removed:
        return False

    page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(out))
    # Fonts nothing draws with any more go too -- leaving one behind would keep failing 8.4.5.8.
    used = {str(ins.operands[0]) for ins in out
            if str(getattr(ins, "operator", "")) == "Tf" and ins.operands}
    fonts = (page.obj.get("/Resources") or Dictionary()).get("/Font")
    if fonts is not None:
        for key in [str(k) for k in fonts.keys() if str(k) not in used]:
            del fonts[key]
    return True


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
              darken_contrast: bool = True, strip_scripts: bool = False,
              edits: Edits | None = None) -> RemediationResult:
    """Write `target`: the source made accessible, looking exactly like the original.

    The original pages are kept verbatim (vector text stays crisp, a scan stays a scan) and marked
    as an artifact; an invisible, tagged text layer is added over them and referenced from a PDF/UA
    structure tree. Embedded figures are decorative by default (compliant); pass `alt_texts`
    (keyed by the figure ids in a prior result's `.figures`) to promote a figure to a tagged
    `/Figure` with that description.

    `darken_contrast` is the one thing that changes how the document *looks*: text failing WCAG AA
    is darkened just enough to pass, keeping its hue (see `recolor`). On by default -- an
    accessible document is the job, and a contrast failure Rebind can fix is not worth handing back
    as homework. It is conservative by construction: only colours used exclusively by text are
    touched, never one the artwork also uses, and a page with nothing failing keeps its original
    bytes. Pass False to measure and report without correcting.
    """
    source, target = Path(source), Path(target)
    edits = edits or Edits()
    alt_texts = {**edits.alts, **(alt_texts or {})}
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
    if strip_scripts:
        _strip_scripts(pdf)

    # Contrast is measured first and corrected from that measurement -- each failing colour against
    # the paper actually sampled behind it, rather than against an assumed white page. A human eye
    # cannot compute a luminance ratio, so this is settled here rather than asked about later.
    #
    # Recolouring happens before the page's own content is wrapped as an artifact. Anything that
    # rasterizes a page (a rebuilt page, a figure crop, the contrast re-measurement) reads from a
    # *file*, not from this in-memory Pdf, so the corrected document is written out once here and
    # used as the render source from then on -- otherwise those renders would quietly show the
    # original, uncorrected colours.
    render_source = source
    recoloured = 0
    invisible_pages = _invisible_text_pages(source)
    if darken_contrast:
        corrections = recolor.corrections_for(
            contrast.measure(source, source_pages, skip_pages=invisible_pages))
        for page in pdf.pages:
            recoloured += recolor.apply_corrections(pdf, page, corrections)
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
    page_elements: list[dict] = []
    page_images: dict[int, str] = {}
    figure_boxes: dict[int, tuple] = {}
    heading_entries: list[tuple[int, pikepdf.Object, str]] = []   # (level, struct_elem, title)

    for struct_parent, (page, (src_page, lines, used_ocr), page_roles) in enumerate(
        zip(pdf.pages, per_page, roles)
    ):
        figures = _page_figures(src_page)
        vector = _vector_figures(src_page, list(lines), figures)
        figures += [(fid, bbox) for fid, bbox, _caption in vector]
        anchored = {fid: caption for fid, _bbox, caption in vector}
        # A scanned page places no images at all -- the whole sheet is one raster, and a diagram
        # printed on it is a patch of that raster, invisible to `_page_figures`. Every illustration
        # in a scanned book was therefore missed. They are found from the pixels instead: ink that
        # is not text (see `regions`).
        #
        # "Scanned" cannot mean "Rebind ran OCR on it": a scan that arrived already OCR'd has a
        # text layer, so nothing is recognized here and that test is false on exactly the documents
        # this is for. A page is a scan when its words are an invisible layer over a picture, or
        # when one image covers the whole sheet -- both of which are known by now.
        scan_regions: set[str] = set()
        if used_ocr or src_page.number in invisible_pages or _is_full_page_scan(src_page):
            found = _scanned_figures(render_source, src_page, list(lines), dpi, figures)
            scan_regions = {fid for fid, _bbox in found}
            figures += found
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
        # Which of this page's lines each accepted caption was built from, so they can be tagged
        # /Caption rather than left as ordinary prose -- one list per caption, because they are
        # grouped into elements by which caption they belong to, not by being adjacent.
        caption_blocks: list[list[TextLine]] = []

        def caption_for(fid: str, bbox: tuple) -> str | None:
            used: list[TextLine] = []
            local = anchored.get(fid) or _figure_caption(lines, bbox, used)
            if local and _caption_is_substantial(local):
                if used:
                    caption_blocks.append(used)
                return local
            number = (_caption_number(local) if local else None) or _nearby_caption_number(lines, bbox)
            elsewhere = document_captions.get(number) if number else None
            if elsewhere and _caption_is_substantial(elsewhere):
                return elsewhere
            return None

        effective_alt = {fid: alt_texts.get(fid) or caption_for(fid, bbox)
                         for fid, bbox in figures}
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
        claimed = {text for text in effective_alt.values() if text}
        for fid, bbox in figures:
            if effective_alt[fid]:
                continue
            beside = [(text, used) for text, used in _side_captions(lines, bbox)
                      if text not in claimed]
            if len(beside) == 1 and _caption_is_substantial(beside[0][0]):
                effective_alt[fid] = beside[0][0]
                claimed.add(beside[0][0])
                if beside[0][1]:
                    caption_blocks.append(beside[0][1])
        described = [(fid, bbox) for fid, bbox in figures if effective_alt[fid]]
        undescribed = [(fid, bbox) for fid, bbox in figures if not effective_alt[fid]]
        rebuild = _has_marked_content(page)
        page_image = (render_page_to_image(render_source, src_page.number, dpi=dpi)
                      if rebuild or figures else None)

        # Classify every line before anything is drawn. Three kinds, and only one of them is the
        # document's content:
        #   * page furniture (running head, footer, folio) -> an /Artifact, never tagged content;
        #   * text belonging to a described figure -> drawn INSIDE that figure's marked content, so
        #     the figure is one thing in the reading order rather than a picture plus a scatter of
        #     loose labels ("A", "B", "3 mm") a screen reader would read out as if it were prose;
        #   * everything else -> ordinary content, tagged with its own MCID.
        def figure_text(fid: str, fbox: tuple) -> list[TextLine]:
            return (_figure_text_strict(lines, fbox) if fid in scan_regions
                    else _figure_text(lines, fbox))

        owner_figure: dict[int, int] = {}   # id(line) -> index into `described`
        for k, (fid, fbox) in enumerate(described):
            for line in figure_text(fid, fbox):
                owner_figure[id(line)] = k
        # A figure with no description yet stays a decorative artifact (tagging it without an /Alt
        # is a conformance failure), but its own callout labels still belong to it. Left loose they
        # became elements in their own right -- "A", "B", "3 mm" tagged as paragraphs and read out
        # as if they were prose, which is how a picture ends up in the reading order as a scatter
        # of fragments. They are held out here and drawn as artifacts with the picture.
        inside_undescribed: set[int] = set()
        for fid, fbox in undescribed:
            for line in figure_text(fid, fbox):
                if id(line) not in owner_figure:
                    inside_undescribed.add(id(line))
        # An id for every line on the page, whether or not it ends up tagged. Giving a tag to a line
        # Rebind set aside is how an element is *added*: the exact inverse of removing one, and the
        # answer to "this running head really is a heading" or "that label inside the figure needs
        # reading". Anything with a tag override is content, whatever it would otherwise have been.
        line_ids = [f"p{src_page.number}n{index}" for index in range(len(lines))]
        promoted = {index for index, key in enumerate(line_ids) if key in edits.tags}
        is_artifact = [
            index not in promoted
            and id(ln) not in owner_figure
            and (id(ln) in inside_undescribed
                 or profile.role_of(ln, page_height=src_page.height) == "artifact")
            for index, ln in enumerate(lines)
        ]

        # A caption Rebind was willing to use as a picture's description is, self-evidently, a
        # caption -- so it is tagged as one rather than left as an ordinary paragraph. The text
        # already had to be recognised to become the /Alt; not carrying that through to the tag
        # threw the knowledge away at the last step, and a screen reader met the document's own
        # captions as unremarkable prose sitting between the paragraphs.
        caption_of = {id(line): group
                      for group, block in enumerate(caption_blocks) for line in block}

        content_lines: list[TextLine] = []
        content_roles: list[str] = []
        content_source: list[int] = []      # content index -> index into `lines`
        for index, (line, role, artifact) in enumerate(zip(lines, page_roles, is_artifact)):
            if artifact or (id(line) in owner_figure and index not in promoted):
                continue
            content_lines.append(line)
            content_roles.append(
                "Caption" if id(line) in caption_of and role == "P" else role)
            content_source.append(index)

        caption_groups = _caption_groups(content_lines, content_roles, caption_of, caption_blocks)

        # The page's structural judgement as data, then the user's corrections on top of it. Ids
        # are keyed to the *source* line index, not to a position in the element list, so they
        # survive a re-run in which earlier elements were retagged, merged or removed.
        plan = plan_page(content_lines, content_roles, caption_groups)
        for entry in plan:
            entry["id"] = f"p{src_page.number}n{content_source[entry['first']]}"
            entry["kind"] = edits.tags.get(entry["id"], entry["kind"])
            entry["alt"] = edits.alts.get(entry["id"], "")
        plan = [entry for entry in plan if entry["id"] not in edits.removed]

        kept = {i for entry in plan for i in range(entry["first"], entry["last"] + 1)}
        mcid_of: list[int | None] = []
        mcids: list[int | None] = [None] * len(lines)
        next_mcid = 0
        for index, line in enumerate(content_lines):
            if index not in kept:
                mcid_of.append(None)        # removed by an edit: drawn as an artifact instead
                continue
            mcid_of.append(next_mcid)
            mcids[content_source[index]] = next_mcid
            next_mcid += 1
        records = _element_records(src_page, plan, content_lines, mcid_of)
        # Lines Rebind set aside are listed too, marked as untagged, so the editor can offer them.
        # They sit at the position they occupy on the page, so the list stays the page's order.
        for index, line in enumerate(lines):
            if line_ids[index] in {entry["id"] for entry in plan} or not line.text.strip():
                continue
            if index in {content_source[e] for e in range(len(content_source))
                         if mcid_of[e] is not None}:
                continue
            records.append(_untagged_record(src_page, line_ids[index], line))
        page_elements.extend(_records_in_reading_order(records))

        # Draw each described figure (a crop of the rendered region) inside a tagged /Figure,
        # together with any text that belongs to it, all under the figure's single MCID.
        figure_stream = b""
        extra_xobjects: dict = {}
        figure_specs: list[tuple] = []   # (mcid, alt, bbox, anchor line index)
        mcid = next_mcid
        for k, (fid, bbox) in enumerate(described):
            extra_xobjects[f"Fig{k}"] = _figure_xobject(
                pdf, page_image, bbox, src_page.width, src_page.height)
            x0, y0, x1, y1 = bbox
            block = io.BytesIO()
            block.write(f"/Figure <</MCID {mcid}>> BDC q {x1 - x0:.2f} 0 0 {y1 - y0:.2f} "
                        f"{x0:.2f} {y0:.2f} cm /Fig{k} Do Q\n".encode())
            for line in lines:
                if owner_figure.get(id(line)) == k:
                    _draw_line(block, line, "RebindF")
            block.write(b"EMC\n")
            figure_stream += block.getvalue()
            figure_specs.append((mcid, effective_alt[fid], bbox, _figure_anchor(lines, mcids, k,
                                                                               owner_figure, bbox)))
            mcid += 1

        overlay = _tagged_text_stream(lines, "RebindF", mcids) + figure_stream

        if rebuild:
            _rebuild_page(pdf, page, page_image, overlay, extra_xobjects, font,
                          src_page.width, src_page.height)
        else:
            # Before the page is wrapped as an artifact: a scan that arrived already OCR'd carries
            # its own invisible text layer, which Rebind is about to duplicate with a tagged one.
            _strip_invisible_text(pdf, page)
            page.contents_add(pdf.make_stream(b"/Artifact BMC\n"), prepend=True)
            page.contents_add(pdf.make_stream(b"EMC\n" + overlay), prepend=False)
            _add_font(pdf, page, font, "RebindF")
            for name, obj in extra_xobjects.items():
                _add_xobject(pdf, page, obj, name)
        page.obj.StructParents = struct_parent
        page.obj.Tabs = Name.S

        # Figures are built before the rest of the page's structure, because a /Caption the user
        # marked has to be put *inside* the figure it captions and so needs it to exist already.
        figure_elems = [
            pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name.Figure, P=document_elem, Pg=page.obj, K=fmcid,
                Alt=String(alt),
                A=Dictionary(O=Name.Layout, BBox=Array([round(v, 2) for v in bbox]))))
            for fmcid, alt, bbox, _anchor in figure_specs
        ]
        tops, owner_of_mcid = _page_structure(pdf, content_lines, plan, mcid_of,
                                              document_elem, page.obj,
                                              caption_hosts=figure_elems)
        # The parent tree is indexed BY marked-content id, so every slot must hold the element that
        # owns that id. Appending here instead of assigning (as this briefly did) shifts the figure
        # entries past their own ids and leaves nulls behind -- content that names a structure
        # element the tree cannot resolve, which reads as untagged content and fails clause 8.2.2.
        owners: list = [None] * mcid
        for key, elem in owner_of_mcid.items():
            owners[key] = elem
        assert all(o is not None for o in owners[:next_mcid]), "unowned marked content"
        for entry in plan:
            kind, first = entry["kind"], entry["first"]
            if kind.startswith("H") and kind[1:].isdigit() and content_lines[first].text.strip():
                owner = owner_of_mcid.get(mcid_of[first])
                if owner is not None:
                    heading_entries.append(
                        (int(kind[1:]), owner, content_lines[first].text.strip()))
        # A figure goes into the reading order where it actually sits, not after everything else.
        # `tops` holds the page's top-level elements in reading order; each figure is spliced in at
        # the element that owns the first content line below it. Appending them all at the end (as
        # this did originally) put every figure after the whole page's prose -- so a screen reader
        # met a page's figures only once it had finished reading the page.
        placements: list[tuple[int, pikepdf.Object]] = []
        for (fmcid, _alt, _bbox, anchor), figure_elem in zip(figure_specs, figure_elems):
            owners[fmcid] = figure_elem
            placements.append((_top_index_for(tops, owners, anchor), figure_elem))
        for position, figure_elem in sorted(placements, key=lambda p: -p[0]):
            tops.insert(position, figure_elem)
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
        # Figures join the element list at the position they are read, so the editor's order is
        # the document's order. Their alt text is editable in place, which is the one correction
        # only a person can make.
        #
        # Undescribed ones are here too, with an empty /Alt. In the DOCUMENT they are decorative
        # artifacts and must be -- tagging a figure with no description is a conformance failure --
        # but in the EDITOR they have to be visible, because a picture nobody can see is a picture
        # nobody will describe. They used to reach the person only through a list of thumbnails in
        # the report; with that list gone they became invisible, which read exactly like a missed
        # figure. Tabbing onto one is what opens the description prompt, and a description promotes
        # it to a real /Figure on the next rebuild.
        editor_figures = list(figure_specs) + [
            (None, "", bbox, None) for _fid, bbox in undescribed]
        # Placed left to right within a row, so two pictures side by side are met the way they are
        # read. Inserting each one purely by its top edge cannot do this: the two are never level to
        # the point, and whichever sits a hair higher wins regardless of which side of the page it
        # is on -- which is how a page's photograph came after the coin printed beside it.
        editor_figures.sort(key=lambda spec: (-spec[2][3], spec[2][0]))
        for fmcid, alt, bbox, _anchor in editor_figures:
            fig_top = 100 * (src_page.height - bbox[3]) / src_page.height
            fig_bottom = 100 * (src_page.height - bbox[1]) / src_page.height
            fig_left = 100 * bbox[0] / src_page.width

            def after(elem, top=fig_top, bottom=fig_bottom, left=fig_left) -> bool:
                """Whether `elem` is read after this figure: on a later row, or beside it and
                further right."""
                if elem["page"] != src_page.number:
                    return False
                shared = min(bottom, elem["top"] + elem["height"]) - max(top, elem["top"])
                same_row = shared > FIGURE_SAME_ROW_FRACTION * min(
                    bottom - top, max(elem["height"], 0.01))
                return elem["left"] > left if same_row else elem["top"] > top

            position = next((i for i, elem in enumerate(page_elements) if after(elem)),
                            len(page_elements))
            page_elements.insert(position, {
                "id": next(fid for fid, fbox in figures if fbox == bbox),
                "page": src_page.number, "kind": "Figure", "text": "", "alt": alt,
                "left": round(100 * bbox[0] / src_page.width, 2),
                "top": round(100 * (src_page.height - bbox[3]) / src_page.height, 2),
                "width": round(100 * (bbox[2] - bbox[0]) / src_page.width, 2),
                "height": round(100 * (bbox[3] - bbox[1]) / src_page.height, 2),
                "editable": True,
            })
        # The editor lays elements over a picture of the page, so every page needs one -- reusing
        # the render already made for a figure crop or a rebuild where there is one.
        editor_image = page_image if page_image is not None else render_page_to_image(
            render_source, src_page.number, dpi=EDITOR_PAGE_DPI)
        page_images[src_page.number] = _crop_data_uri(
            editor_image, (0.0, 0.0, src_page.width, src_page.height),
            src_page.width, src_page.height, max_side=EDITOR_PAGE_PX)
        for fid, bbox in undescribed:
            # A caption too thin to be used as alt text on its own ("Fig. 8", or a "(Continued)"
            # page-break artifact) is still the best opening line anyone has: it is offered to the
            # editor as a starting point for the person to finish, never written into the document
            # unedited. Nothing is invented -- an empty box stays empty.
            undescribed_figures.append({
                "id": fid, "page": src_page.number,
                "alt_guess": (anchored.get(fid) or _figure_caption(lines, bbox) or "").strip(),
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
    # The review reuses the editor's page pictures rather than rendering the same pages again.
    thumbs = {order.page: page_images.get(order.page, "")
              for order in orders if order.needs_review}
    # After recolouring, the pages extracted at the top of this function still carry the *old*
    # declared ink colours, and the ink is what `contrast` trusts the declaration for. Re-read them
    # from the corrected document so the report describes what was actually produced.
    measured_pages = list(extract_pages(render_source)) if render_source != source else source_pages
    measured = contrast.measure(render_source, measured_pages, figures=figure_boxes,
                                skip_pages=invisible_pages)
    if render_source != source:
        render_source.unlink(missing_ok=True)

    return RemediationResult(
        pdf_path=target, page_count=len(source_pages),
        ocr_pages=tuple(ocr_pages), empty_pages=tuple(empty_pages), added_text_layer=added_layer,
        figures=tuple(undescribed_figures),
        structure_ok=self_check.ok, structure_issues=self_check.issues,
        reading_order=review.summarize(orders, thumbs),
        contrast=contrast.summarize(measured, darkened=recoloured),
        elements=tuple(page_elements), page_images=page_images,
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


def _strip_scripts(pdf: pikepdf.Pdf) -> int:
    """Remove every script the document carries. Returns how many were removed.

    Not done automatically: a script is behaviour the author put there, and removing behaviour
    without being asked is precisely what Rebind does not do. The app offers this as the fix
    alongside the check that reports it, so it happens because someone chose it.
    """
    removed = 0
    names = pdf.Root.get("/Names")
    if names is not None and "/JavaScript" in names:
        del names.JavaScript
        removed += 1
    if "/OpenAction" in pdf.Root:
        del pdf.Root.OpenAction
        removed += 1
    if "/AA" in pdf.Root:
        del pdf.Root.AA
        removed += 1
    for page in pdf.pages:
        if "/AA" in page.obj:
            del page.obj.AA
            removed += 1
        for annot in page.obj.get("/Annots") or []:
            if "/AA" in annot:
                del annot.AA
                removed += 1
            action = annot.get("/A")
            if action is not None and action.get("/S") == Name.JavaScript:
                del annot["/A"]
                removed += 1
    return removed


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
