"""Decide which lines are headings and at what level, on born-digital pages and scans alike."""

from __future__ import annotations

from .extract import Page, TextLine
from .profile import style_of

# --- Line/page classification (relocated from the retired reconstruction pipeline) --------------
# A page with a text layer AND a raster image covering at least this fraction of its area is an
# OCR-over-scan page: the image is the scanned page, the text on top is recognizer output.
OCR_SCAN_COVERAGE = 0.6


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


def _ocr_heading_sizes(lines: list[TextLine]) -> dict[int, float]:
    """For the OCR lines on one page, return {id(line): size} for lines that look like headings.

    `lines` must arrive in reading order (`layout.order_page` put them there); isolation is
    measured against each line's neighbours in that order.

    A heading is larger than the page's body text, set apart by whitespace (isolation), and does
    not fill the text column (shortness). No signal alone is trusted, but their conjunction is not
    something a body line -- which sits inside its paragraph spanning the full width -- produces.

    Size is the line's DECLARED font size, not its box height. Box height was tried first and is
    wrong on the shape this exists for: a tilted scan's words share a line but not a baseline, so
    the box spans the drift across the line's width and measures the tilt rather than the type. On
    a real scanned book (samples/The_power_of_images_in_the_Age_of_Augustus_ocr.pdf) that inflated
    every 10pt body line's box to ~16pt while the 16.1pt chapter title's box was 19.68 -- 1.21x the
    body median, under the bar -- and the whole 25-page book came out with no headings at all.
    The declared size separated them cleanly at 16.1 against 10.0. Nothing is given up by trusting
    it: a third-party OCR layer writes a real size, and for Rebind's own recognizer size IS the box
    height by definition (`ocr.py`), so this is the same measurement it always was.

    Trusting a declared size means guarding against a nonsensical one, which is what the first two
    checks are for. The same book's recogniser found a lone '|' at 26pt and 'at  1' at 17.1pt with
    its five characters strewn across 191pt -- dirt on the platen, isolated and short, and promoted
    to headings the moment size was believed. A heading has to have some text in it, and its
    characters cannot sit further apart than the type is tall.
    """
    if len(lines) < 3:
        return {}
    sizes = sorted(ln.size for ln in lines)
    body_size = sizes[len(sizes) // 2] or 1.0
    max_width = max((ln.bbox[2] - ln.bbox[0]) for ln in lines) or 1.0
    result: dict[int, float] = {}
    for idx, line in enumerate(lines):
        text = line.text.strip()
        width = line.bbox[2] - line.bbox[0]
        height = line.bbox[3] - line.bbox[1]
        if len(text) < MIN_HEADING_CHARS:
            continue
        if width / len(text) > line.size:
            continue        # characters further apart than the type is tall: not a line of text
        if line.size < body_size * OCR_HEADING_HEIGHT_RATIO:
            continue
        if width > max_width * OCR_HEADING_MAX_WIDTH_RATIO:
            continue

        def gap_to(other: TextLine | None, above: bool, line: TextLine = line,
                   height: float = height) -> float:
            """Whitespace between `line` and the neighbour before (above) or after it."""
            if other is None:
                return float("inf")
            gap = (other.bbox[1] - line.bbox[3]) if above else (line.bbox[1] - other.bbox[3])
            # Not vertically adjacent at all: the neighbour is across a column or page break, which
            # is isolation rather than crowding. Measuring against whatever sat nearest vertically
            # anywhere on the page is what hid every title on a two-page spread, where the line
            # alongside on the *other* page sits level with it.
            return float("inf") if gap < -height else gap

        gap_above = gap_to(lines[idx - 1] if idx > 0 else None, True)
        gap_below = gap_to(lines[idx + 1] if idx < len(lines) - 1 else None, False)
        if max(gap_above, gap_below) >= body_size * OCR_HEADING_ISOLATION_RATIO:
            result[id(line)] = line.size
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


def _height_tiers(heights: list[float]) -> list[float]:
    """Cluster heading heights into representative tiers, largest first (a tier per size level)."""
    tiers: list[float] = []
    for height in sorted(set(heights), reverse=True):
        if not tiers or (tiers[-1] - height) > tiers[-1] * OCR_HEADING_TIER_TOLERANCE:
            tiers.append(height)
    return tiers


def _structure_roles(per_page: list[tuple], profile,
                     not_headings: list[set[int]] | None = None) -> list[list[str]]:
    """A structure type ('P' or 'H1'..'H6') for each line, per page.

    Born-digital headings come from the document-global typographic profile. Recognizer output
    (Rebind's OCR, or a hidden OCR layer over a scan) has no reliable font size, so its headings are
    instead recovered geometrically -- size, isolation and shortness together (`_ocr_heading_sizes`)
    -- with heading levels assigned from document-global size tiers. Levels are then normalized so
    the sequence starts at H1 and never skips a level, which PDF/UA requires (7.4.2).

    `not_headings[page]` holds the ids of lines a layout model saw inside a block of running text
    or a caption. However large their type, they are not headings: on a journal's first page the
    "ORIGINAL ARTICLE" kicker and the authors' names are set bigger than the body, and were tagged
    H1 on size alone. They are left out before levels are assigned, so the outline still starts at
    H1 and never skips.
    """
    ocr_headings: list[dict[int, float]] = []   # per page: {id(line): height} for OCR headings
    for src_page, lines, used_ocr in per_page:
        page_is_ocr = used_ocr or _is_ocr_over_scan(src_page)
        ocr_headings.append(_ocr_heading_sizes(lines) if page_is_ocr else {})

    tiers = _height_tiers([h for page in ocr_headings for h in page.values()])

    def tier_level(height: float) -> int:
        for i, tier in enumerate(tiers):
            if abs(height - tier) <= tier * OCR_HEADING_TIER_TOLERANCE:
                return i + 1
        return len(tiers) or 1

    raw: list[list[int]] = []   # per page: 0 for paragraph, or the raw heading level (>=1)
    for page_index, ((src_page, lines, used_ocr), page_headings) in enumerate(
            zip(per_page, ocr_headings)):
        page_is_ocr = used_ocr or _is_ocr_over_scan(src_page)
        prose = not_headings[page_index] if not_headings else set()
        page_levels: list[int] = []
        for line in lines:
            if id(line) in prose:
                page_levels.append(0)
            elif page_is_ocr:
                page_levels.append(tier_level(page_headings[id(line)]) if id(line) in page_headings else 0)
            elif line.ocr_confidence is not None:
                page_levels.append(0)
            elif (profile.role_of(line, page_height=src_page.height) == "heading"
                  and len(line.text.strip()) >= MIN_HEADING_CHARS):
                page_levels.append(profile.heading_level(style_of(line)) or 1)
            else:
                page_levels.append(0)
        if not page_is_ocr:
            # OCR headings already carry their own isolation signal (_ocr_heading_sizes);
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
    #
    # A new style goes one level below the deepest OPEN heading set larger than it, not below
    # whatever happens to be open. A journal's "Abstract" (10pt) comes before its "Introduction"
    # (12pt); nesting each new style under the last one made Introduction a subsection of
    # Abstract, and every section of the paper a level deeper than it should be.
    level_map: dict[int, int] = {}
    branch: list[tuple[int, int]] = []      # the open headings, (raw, level), shallowest first
    for page_levels in raw:
        for lvl in page_levels:
            if not lvl:
                continue
            if lvl not in level_map:
                larger = [level for raw_level, level in branch if raw_level < lvl]
                level_map[lvl] = 1 if lvl <= first_raw else min(
                    (larger[-1] if larger else 0) + 1, 6)
            level = level_map[lvl]
            branch = [(r, v) for r, v in branch if v < level] + [(lvl, level)]

    return [
        [f"H{level_map[lvl]}" if lvl else "P" for lvl in page_levels]
        for page_levels in raw
    ]
