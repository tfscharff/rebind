"""Printed music: finding it on a page, and drafting a description of it.

A music example is a picture to a screen reader -- PDF/UA has no tag for notation -- so what it
needs is to be a `/Figure` with a description. Two things stand in the way on a real page. The
layout model has no word for music, so it boxes an example as a picture only some of the time; and
the recogniser, meeting a stave, reads its note heads and figures as text, which a screen reader
then reads out as a scatter of stray letters.

Music is found here by the one thing every example has and nothing else on a page does: staves --
five long, parallel, evenly spaced lines, closer together than lines of text. That is plain
geometry, deterministic and cheap, and it finds an example whether or not anything captions it.
Staves stacked closely, with no prose between them, are one example.

The description is only ever a *draft*, offered in the editor for the person to check (like a
formula's): what can be read off the staves themselves -- how many staves, their clefs, the key
and time signatures, how many bars. Anything not read with confidence is left out of it rather
than guessed.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# Staves are looked for at this resolution: a stave's lines are a pixel or two thick here, and a
# space between them several pixels, on the smallest music a book prints.
SCAN_DPI = 200
# A stave is at least this long, in its own spaces -- a clef, a key signature and a note.
MIN_STAFF_LENGTH_SPACES = 10
# The space between two stave lines, in points: from the smallest print a book uses for music
# examples to a large-print part.
MIN_SPACE_PT, MAX_SPACE_PT = 1.8, 9.0
# How far the five lines' spacings may differ from their own median and still be one stave.
SPACING_TOLERANCE = 0.22
# Staves are found a strip of the page at a time, this wide (inches), so that a page that is warped
# rather than merely tilted -- the lower half of a real book's page leaned the other way -- still
# has level lines within each strip. A row is a line in a strip when the line fills this much of it.
STRIP_INCHES = 0.6
STRIP_FILL = 0.35
# A stave line is no thicker than this fraction of the space between lines, and the paper between
# two lines is no more filled than this (note heads and beams cross it; text and shading fill it).
MAX_LINE_THICKNESS = 0.4
MAX_FILL_BETWEEN = 0.25
# A scan is straightened by up to this many tenths of a degree before staves are looked for.
SKEW_RANGE_TENTHS = 25
# Staves this close (in spaces) are one example when no prose or caption sits between them; what
# may sit between is the music's own -- the figures under a bass, a lyric, a tempo marking.
LOOSE_EXAMPLE_GAP_SPACES = 14.0
# Prose has at least this many words; this many with no hyphen is prose wherever it starts (a
# lyric is split into syllables).
PROSE_MIN_WORDS = 4
PROSE_PLAIN_WORDS = 6
# An exercise's number, printed on its own.
NUMBER_RE = re.compile(r"^\d{1,3}\.?$")
# An example's box reaches past its outer stave lines to take in what is printed above and below
# them (ledger lines, stems, figures, a tempo), up to this many spaces, stopping at a clear gap.
MAX_REACH_SPACES = 7.0
CLEAR_GAP_SPACES = 1.2
# ...and past its left end by this much, for the brace or bracket joining a system's staves.
BRACE_REACH_SPACES = 2.5


@dataclass(frozen=True)
class Staff:
    """One stave, in pixels of the straightened page image: where its top line runs (sampled along
    it, left to right, as (x, top line height, space between lines)) and its horizontal span."""

    x0: float
    x1: float
    samples: tuple[tuple[float, float, float], ...]

    @cached_property
    def space(self) -> float:
        return float(np.median([space for _x, _top, space in self.samples]))

    @property
    def top(self) -> float:
        return min(top for _x, top, _space in self.samples)

    @property
    def bottom(self) -> float:
        return max(top + 4 * space for _x, top, space in self.samples)

    @cached_property
    def _profile(self) -> tuple[np.ndarray, np.ndarray]:
        return (np.array([x for x, _top, _space in self.samples]),
                np.array([top for _x, top, _space in self.samples]))

    def top_at(self, x: float) -> float:
        xs, tops = self._profile
        return float(np.interp(x, xs, tops))

    def line_at(self, index: int, x: float) -> float:
        """The height of line `index` (0 = top, 4 = bottom) at `x`."""
        return self.top_at(x) + index * self.space


@dataclass
class Example:
    """A music example: staves read together, and the box (in PDF points) the picture occupies."""

    bbox: tuple[float, float, float, float]
    staves: list[Staff] = field(default_factory=list)
    # The band each stave occupies, in PDF points (x0, y0, x1, y1): text inside one is notation
    # the recogniser misread, never prose.
    bands: list[tuple[float, float, float, float]] = field(default_factory=list)
    # A description drafted from the staves, for the person to check; never written on its own.
    draft: str = ""
    analysis: tuple | None = None


def _ink(gray: np.ndarray) -> np.ndarray:
    """Ink, including the faint: a stave line is a pixel or two thick and renders grey, which a
    threshold set for type (Otsu's, between paper and solid black) loses in long stretches."""
    import cv2

    level, _binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    paper = float(np.percentile(gray, 90))
    cut = max(level, paper - 0.25 * (paper - float(np.percentile(gray, 1))))
    return np.where(gray < min(cut, paper - 12), 255, 0).astype(np.uint8)


def _strokes(ink: np.ndarray, length: int) -> np.ndarray:
    """Horizontal strokes at least `length` long: stave lines, ledger lines, beams, rules."""
    import cv2

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, length), 1))
    return cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)


def _line_rows(profile: np.ndarray) -> list[tuple[float, int]]:
    """The lines crossing a strip, as (height, thickness in rows): runs of rows the strokes mostly
    fill."""
    found: list[tuple[float, int]] = []
    rows = np.flatnonzero(profile >= STRIP_FILL)
    if not len(rows):
        return found
    start = prev = int(rows[0])
    for row in [int(r) for r in rows[1:]] + [None]:
        if row is not None and row == prev + 1:
            prev = row
            continue
        weights = profile[start:prev + 1]
        found.append((float(np.average(np.arange(start, prev + 1), weights=weights)),
                      prev - start + 1))
        if row is not None:
            start = prev = row
    return found


def _strip_staves(rows: list[tuple[float, int]], profile: np.ndarray, min_space: float,
                  max_space: float) -> list[tuple[float, float]]:
    """Five evenly spaced lines among a strip's lines: each as (top line height, space).

    Stave lines are hairlines with clear paper between them. Lines of text, a table's shaded rows
    and the texture of a photographed stone can also come out of a strip as evenly spaced bands --
    real pages produced all three -- but each band is thick for its spacing, or the paper between
    them is not clear."""
    centres = [centre for centre, _thickness in rows]
    found = []
    used: set[int] = set()
    for i in range(len(centres)):
        if i in used:
            continue
        for j in range(i + 1, len(centres)):
            gap = centres[j] - centres[i]
            if gap > max_space:
                break
            if gap < min_space:
                continue
            chosen = [i, j]
            for step in range(2, 5):
                want = centres[i] + step * gap
                near = [k for k in range(j + 1, len(centres))
                        if abs(centres[k] - want) <= max(1.5, SPACING_TOLERANCE * gap)]
                if not near:
                    break
                chosen.append(min(near, key=lambda k: abs(centres[k] - want)))
            if len(chosen) < 5:
                continue
            ys = [centres[k] for k in chosen]
            spacings = np.diff(ys)
            space = float(np.median(spacings))
            if np.any(np.abs(spacings - space) > max(1.5, SPACING_TOLERANCE * space)):
                continue
            if any(rows[k][1] > max(3, MAX_LINE_THICKNESS * space) for k in chosen):
                continue
            between = [profile[int(a + 0.3 * space):int(b - 0.3 * space) + 1]
                       for a, b in zip(ys, ys[1:])]
            if any(len(part) and part.mean() > MAX_FILL_BETWEEN for part in between):
                continue
            # A sixth line at the same spacing is ruled paper or a table, not a stave.
            if any(abs(c - (ys[4] + space)) <= max(1.0, 0.15 * space)
                   or abs(c - (ys[0] - space)) <= max(1.0, 0.15 * space) for c in centres):
                continue
            found.append((ys[0], (ys[4] - ys[0]) / 4))
            used.update(chosen)
            break
    return found


def _extent(strokes: np.ndarray, top_at, space: float, start: float, step: int) -> float:
    """How far a stave's lines carry on from `start` in direction `step` (+1 right, -1 left):
    while at least four of its five lines are inked, give or take a short break."""
    h, w = strokes.shape
    x, last, misses = int(start), start, 0
    while 0 <= x < w and misses <= max(2, space):
        top = top_at(x)
        hits = 0
        for k in range(5):
            y = int(round(top + k * space))
            if strokes[max(0, y - 1):min(h, y + 2), x].any():
                hits += 1
        if hits >= 4:
            last, misses = x, 0
        else:
            misses += 1
        x += step
    return float(last)


def _continuous(lines: np.ndarray, x0: float, top0: float, x1: float, top1: float,
                space: float) -> bool:
    """Whether a stave's lines really run from one place to another -- not two staves that merely
    sit at the same height either side of a column gap."""
    h = lines.shape[0]
    xs = range(int(x0), int(x1) + 1)
    if not len(xs):
        return True
    inked, blank, longest = 0, 0, 0
    for x in xs:
        top = top0 + (top1 - top0) * (x - x0) / max(1.0, x1 - x0)
        hits = sum(bool(lines[max(0, int(round(top + k * space)) - 1):
                              min(h, int(round(top + k * space)) + 2), x].any())
                   for k in range(5))
        if hits >= 4:
            inked, blank = inked + 1, 0
        else:
            blank += 1
            longest = max(longest, blank)
    # A stave's lines do not stop: a clear gap wider than two spaces is the gap between two examples
    # set side by side on one line, however well inked the rest of the way is.
    return inked >= 0.8 * len(xs) and longest <= max(3.0, 2 * space)


def find_staves(gray: np.ndarray, dpi: float) -> list[Staff]:
    """Every stave on a (straightened) page image: five long lines, evenly spaced, one above
    another -- found strip by strip down the page, and chained across it."""
    min_space = MIN_SPACE_PT * dpi / 72
    max_space = MAX_SPACE_PT * dpi / 72
    ink = _ink(gray)
    strokes = _strokes(ink, int(4 * min_space))
    # For following a line along, short strokes count: an old scan's lines break every few points.
    lines = _strokes(ink, int(1.5 * min_space))
    h, w = strokes.shape
    strip = max(24, int(STRIP_INCHES * dpi))
    step = strip // 2
    chains: list[list[tuple[float, float, float]]] = []     # (x, top, space)
    for x0 in range(0, w - step, step):
        profile = strokes[:, x0:x0 + strip].mean(axis=1) / 255
        x = x0 + strip / 2
        for top, space in _strip_staves(_line_rows(profile), profile, min_space, max_space):
            for chain in chains:
                last_x, last_top, last_space = chain[-1]
                if (0 < x - last_x <= 4 * step and abs(top - last_top) <= 0.6 * last_space
                        and abs(space - last_space) <= 0.2 * last_space
                        and _continuous(lines, last_x, last_top, x, top, space)):
                    chain.append((x, top, space))
                    break
            else:
                chains.append([(x, top, space)])
    staves = []
    for chain in chains:
        staff = Staff(0.0, 0.0, tuple(chain))
        space = staff.space
        x0 = _extent(lines, staff.top_at, space, chain[0][0], -1)
        x1 = _extent(lines, staff.top_at, space, chain[-1][0], +1)
        staves.append(Staff(x0, x1, tuple(chain)))
    # Pieces of one stave the strips failed to chain (a stretch crowded with notes, or faded) join
    # up when one starts where the other stops, at the same height and spacing: under two spaces
    # apart, where two examples set side by side on one line stand further apart than that.
    staves.sort(key=lambda s: (s.x0, s.top))
    merged: list[Staff] = []
    for staff in staves:
        for k, other in enumerate(merged):
            if (abs(staff.top_at(staff.x0) - other.top_at(other.x1)) <= 0.6 * other.space
                    and abs(staff.space - other.space) <= 0.2 * other.space
                    and staff.x0 - other.x1 <= 2 * other.space):
                merged[k] = Staff(other.x0, max(other.x1, staff.x1),
                                  tuple(sorted(set(other.samples + staff.samples))))
                break
        else:
            merged.append(staff)
    staves = [s for s in merged if s.x1 - s.x0 >= MIN_STAFF_LENGTH_SPACES * s.space]
    staves.sort(key=lambda s: (s.top, s.x0))
    return staves


def _skew(ink: np.ndarray, min_length: int) -> float:
    """The page's tilt in degrees, from its long horizontal strokes: the angle that stacks them
    into the fewest, sharpest rows. A stave's lines run the whole width of the music, so half a
    degree of tilt -- ordinary on a scan -- moves a line by more than the gap to the next one."""
    import cv2

    strokes = _strokes(ink, min_length)
    scale = min(1.0, 1000 / max(strokes.shape))
    small = cv2.resize(strokes, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    if not small.any():
        return 0.0
    h, w = small.shape
    best, best_score = 0.0, -1.0
    for tenth in range(-SKEW_RANGE_TENTHS, SKEW_RANGE_TENTHS + 1):
        angle = tenth / 10
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        rows = cv2.warpAffine(small, matrix, (w, h)).sum(axis=1, dtype=np.float64)
        score = float((rows ** 2).sum())
        if score > best_score:
            best, best_score = angle, score
    return best


def _rotate(gray: np.ndarray, angle: float) -> np.ndarray:
    import cv2

    h, w = gray.shape
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, matrix, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=255)


def _unrotate_box(box: tuple[float, float, float, float], angle: float,
                  shape: tuple[int, int]) -> tuple[float, float, float, float]:
    """A box drawn on the straightened image, as the box it covers on the image as scanned."""
    import cv2

    if not angle:
        return box
    h, w = shape
    inverse = cv2.invertAffineTransform(cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0))
    x0, y0, x1, y1 = box
    corners = np.array([[x0, y0, 1], [x1, y0, 1], [x0, y1, 1], [x1, y1, 1]], dtype=np.float64)
    mapped = corners @ inverse.T
    return (float(mapped[:, 0].min()), float(mapped[:, 1].min()),
            float(mapped[:, 0].max()), float(mapped[:, 1].max()))


def straighten(gray: np.ndarray, dpi: float) -> tuple[np.ndarray, float]:
    """The page image with its tilt taken out, and the angle that took it out."""
    min_space = MIN_SPACE_PT * dpi / 72
    angle = _skew(_ink(gray), int(MIN_STAFF_LENGTH_SPACES * min_space))
    return (_rotate(gray, angle), angle) if angle else (gray, 0.0)


def _forward(points: np.ndarray, angle: float, shape: tuple[int, int]) -> np.ndarray:
    """Points on the image as scanned, where they sit on the straightened image."""
    import cv2

    if not angle:
        return points
    h, w = shape
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    ones = np.ones((len(points), 1))
    return np.hstack([points, ones]) @ matrix.T


@dataclass
class PageMusic:
    """The music found on one page, with the straightened image it was read from."""

    examples: list[Example]
    gray: np.ndarray
    angle: float
    dpi: float

    @cached_property
    def ink(self) -> np.ndarray:
        return _ink(self.gray)


def find_examples(gray: np.ndarray, dpi: float, page_width: float, page_height: float,
                  lines, keep_out=lambda line: False) -> PageMusic:
    """The music examples on a page image (as scanned), each with its box in PDF points.

    `lines` are the page's text lines (PDF points); `keep_out(line)` says which of them may not be
    taken into an example's box -- a caption, or prose -- so the box stops short of them.
    """
    straight, angle = straighten(gray, dpi)
    staves = find_staves(straight, dpi)
    if not staves:
        return PageMusic([], straight, angle, dpi)
    to_px = dpi / 72
    # Each text line, in straightened pixels: (x0, y0, x1, y1, line).
    placed = []
    for line in lines:
        x0, y0, x1, y1 = line.bbox
        corners = np.array([[x0 * to_px, (page_height - y1) * to_px],
                            [x1 * to_px, (page_height - y0) * to_px]])
        (a, b), (c, d) = _forward(corners, angle, straight.shape)
        placed.append((min(a, c), min(b, d), max(a, c), max(b, d), line))

    staves = [s for s in staves if not _ruled_text(s, placed) and not _under_text(s, placed)]
    if not staves:
        return PageMusic([], straight, angle, dpi)

    def overlap(a0, a1, b0, b1) -> float:
        return (min(a1, b1) - max(a0, b0)) / max(1.0, min(a1 - a0, b1 - b0))

    def outside(line, lx0: float, members: list[Staff]) -> bool:
        """Whether a line is the page's own text rather than the music's: a caption, or prose --
        words that start out in the margin the staves are indented from, or a run of plain words
        with none of the hyphens that split a lyric into syllables."""
        if keep_out(line):
            return True
        words = [w for w in line.text.split() if sum(ch.isalpha() for ch in w) >= 2]
        if len(words) < PROSE_MIN_WORDS:
            return False
        space = members[0].space
        return (lx0 < min(s.x0 for s in members) - space
                or (len(words) >= PROSE_PLAIN_WORDS and "-" not in line.text))

    def prose_between(upper: Staff, lower: Staff) -> bool:
        # Only text reaching over the staves counts: a sentence between two examples often starts
        # out in the margin and stops short, but it still runs under them, where a label set in
        # the margin beside a system ("Ex. 238.") and the next column's text do not.
        start = max(upper.x0, lower.x0)
        right = min(upper.x1, lower.x1)
        for x0, y0, x1, y1, line in placed:
            cy = (y0 + y1) / 2
            if not upper.bottom < cy < lower.top:
                continue
            # An exercise's bare number ("7") set over the left end of the lower stave starts a
            # new example; a figure over a note stands further in.
            if (NUMBER_RE.match(line.text.strip()) and x1 > lower.x0 - 3 * lower.space
                    and x1 <= lower.x0 + 2.5 * lower.space and cy < lower.top - 0.5 * lower.space):
                return True
            if x1 > start + upper.space and x0 < right and outside(line, x0, [upper, lower]):
                return True
        return False

    # Staves into examples: each joins the nearest stave above it in the same column, when close.
    group = list(range(len(staves)))

    def root(k: int) -> int:
        while group[k] != k:
            k = group[k]
        return k

    for j, lower in enumerate(staves):
        above = [i for i in range(j) if overlap(staves[i].x0, staves[i].x1, lower.x0, lower.x1)
                 >= 0.5 and staves[i].top < lower.top]
        if not above:
            continue
        i = max(above, key=lambda k: staves[k].bottom)
        upper = staves[i]
        gap = (lower.top - upper.bottom) / upper.space
        if gap <= LOOSE_EXAMPLE_GAP_SPACES and not prose_between(upper, lower):
            group[root(j)] = root(i)

    ink = _ink(straight)
    h, w = ink.shape
    examples = []
    for key in sorted({root(k) for k in range(len(staves))}):
        members = [s for k, s in enumerate(staves) if root(k) == key]
        space = float(np.median([s.space for s in members]))
        x0 = max(0.0, min(s.x0 for s in members) - BRACE_REACH_SPACES * space)
        x1 = min(w - 1.0, max(s.x1 for s in members) + 0.5 * space)
        top, bottom = min(s.top for s in members), max(s.bottom for s in members)
        # Reach out to what is printed over and under the staves, but stop at a clear gap, and
        # before any line that must stay outside.
        fences_up = [y1 for lx0, _y0, lx1, y1, line in placed
                     if y1 <= top and overlap(lx0, lx1, x0, x1) > 0
                     and outside(line, lx0, members)]
        fences_down = [y0 for lx0, y0, lx1, _y1, line in placed
                       if y0 >= bottom and overlap(lx0, lx1, x0, x1) > 0
                       and outside(line, lx0, members)]
        limit_up = max([top - MAX_REACH_SPACES * space] + [f + 1 for f in fences_up])
        limit_down = min([bottom + MAX_REACH_SPACES * space] + [f - 1 for f in fences_down])
        columns = ink[:, int(x0):int(x1) + 1]

        def reach(start: float, limit: float, step: int) -> float:
            y, last, blank = int(start), start, 0
            while (y >= limit if step < 0 else y <= limit) and 0 <= y < h:
                if np.count_nonzero(columns[y]) >= 2:
                    last, blank = y, 0
                else:
                    blank += 1
                    if blank > CLEAR_GAP_SPACES * space:
                        break
                y += step
            return float(last)

        box = (x0, reach(top, limit_up, -1), x1, reach(bottom, limit_down, +1))

        def to_points(px_box):
            bx0, by0, bx1, by1 = _unrotate_box(px_box, angle, straight.shape)
            return (max(0.0, bx0 / to_px), max(0.0, page_height - by1 / to_px),
                    min(page_width, bx1 / to_px), min(page_height, page_height - by0 / to_px))

        examples.append(Example(
            bbox=to_points(box), staves=members,
            bands=[to_points((s.x0, s.top - s.space, s.x1, s.bottom + s.space)) for s in members]))
    # A stave the strips found only in pieces can come out as a second, smaller example lying over
    # the first; two boxes over the same music are one example.
    merged: list[Example] = []
    for example in sorted(examples, key=lambda e: -e.bbox[3]):
        for other in merged:
            a, b = other.bbox, example.bbox
            if not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]):
                other.bbox = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                other.staves += example.staves
                other.bands += example.bands
                break
        else:
            merged.append(example)
    for example in merged:
        example.staves.sort(key=lambda s: (s.top, s.x0))
    page = PageMusic(merged, straight, angle, dpi)
    # Music opens each stave with a clef. Five evenly spaced hairlines with no clef anywhere were,
    # on a real book, the brickwork of an architectural drawing; an example whose clefs all failed
    # to read is lost to this search, but is still there to be found as a picture.
    page.examples = [e for e in merged if any(analyse(e, page)[1])]
    return page


def _under_text(staff: Staff, placed) -> bool:
    """Whether lines of words run across a "stave" -- it is then text, or a picture behind text.
    What a recogniser makes of real notation is a scrap or a run of junk, not a sentence."""
    width = staff.x1 - staff.x0
    sentences = 0
    for x0, y0, x1, y1, line in placed:
        if not staff.top <= (y0 + y1) / 2 <= staff.bottom:
            continue
        if min(x1, staff.x1) - max(x0, staff.x0) < 0.3 * width:
            continue
        words = [w for w in line.text.split() if len(w) >= 3 and w.strip(".,;:!?'\"()").isalpha()]
        sentences += len(words) >= 3
    return sentences >= 1


def _ruled_text(staff: Staff, placed) -> bool:
    """Whether a "stave" holds a line of words in its spaces -- a ruled table's rows, five rules
    evenly spaced, rather than five stave lines. Music has nothing in a single space that reads as
    a word; what a recogniser makes of notation spans the stave, or is a scrap."""
    filled = set()
    space = staff.space
    for x0, y0, x1, y1, line in placed:
        if x1 <= staff.x0 or x0 >= staff.x1:
            continue
        if not any(sum(ch.isalpha() for ch in w) >= 3 for w in line.text.split()):
            continue
        top = staff.top_at((x0 + x1) / 2)
        for k in range(4):
            if y0 >= top + k * space - 0.25 * space and y1 <= top + (k + 1) * space + 0.25 * space:
                filled.add(k)
    return len(filled) >= 2


# A page is looked at closely only when a quick, coarse look finds at least this many long
# horizontal strokes on it (each at least PREFILTER_INCHES long): a page of prose has a rule or two.
PREFILTER_DPI = 100
PREFILTER_INCHES = 0.6
PREFILTER_STROKES = 5
_CACHE = 5000
_examples: OrderedDict[tuple, list[Example]] = OrderedDict()


def _worth_a_look(gray: np.ndarray, dpi: float) -> bool:
    import cv2

    strokes = _strokes(_ink(gray), int(PREFILTER_INCHES * dpi))
    count, _labels = cv2.connectedComponents(strokes)
    return count - 1 >= PREFILTER_STROKES


def examples_for_page(source: Path, page_number: int, page_width: float, page_height: float,
                      lines, keep_out=lambda line: False, render=None) -> list[Example]:
    """The music examples on one page of `source`, each with its box in PDF points and a drafted
    description. Remembered for as long as the file is unchanged: the editor rebuilds the document
    on every saved correction.

    `render`, when given, returns the page as an RGB image and the resolution it is at -- a render
    the caller needs anyway, so a scanned page's picture (slow to decode) is not decoded twice."""
    from .ocr import render_page_to_image

    source = Path(source)
    try:
        stat = source.stat()
        key = (str(source.resolve()), stat.st_size, stat.st_mtime_ns, page_number,
               tuple((line.text, tuple(round(v, 1) for v in line.bbox)) for line in lines))
    except OSError:
        key = None
    if key is not None and key in _examples:
        _examples.move_to_end(key)
        return _examples[key]
    import cv2

    try:
        if render is not None:
            image, dpi = render()
        else:
            image, dpi = render_page_to_image(source, page_number, dpi=SCAN_DPI), SCAN_DPI
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        coarse = cv2.resize(gray, None, fx=PREFILTER_DPI / dpi, fy=PREFILTER_DPI / dpi,
                            interpolation=cv2.INTER_AREA)
        found: list[Example] = []
        if _worth_a_look(coarse, PREFILTER_DPI):
            if dpi != SCAN_DPI:
                gray = cv2.resize(gray, None, fx=SCAN_DPI / dpi, fy=SCAN_DPI / dpi,
                                  interpolation=cv2.INTER_AREA)
            page = find_examples(gray, SCAN_DPI, page_width, page_height, lines, keep_out)
            found = page.examples
            for example in found:
                example.draft = describe(example, page)
    except Exception:   # finding music must never cost the person their document
        log.exception("music search failed on page %d", page_number)
        found = []
    if key is not None:
        _examples[key] = found
        while len(_examples) > _CACHE:
            _examples.popitem(last=False)
    return found


# How a book labels a music example: "Ex. 24.", "Example 3", "Mus. ex. 2", "Musical Example 4.1".
LABEL_RE = re.compile(r"^(?:mus(?:ical)?\.?\s+)?ex(?:ample)?s?\.?\s*\d+[a-z]?(?:[.:]\d+)?\b",
                      re.IGNORECASE)
# How far from its music a label may sit (points): above it, or in the margin beside it.
LABEL_REACH_PT = 36.0


def is_label(text: str) -> bool:
    return LABEL_RE.match(text.strip()) is not None


def label_for(example: Example, lines) -> str:
    """The example's own label, as printed ("Ex. 24."): the nearest one above it with nothing in
    between, or in the margin at its left. A longer line ("Example 3. Beethoven, Op. 27 No. 2") is kept whole,
    except where the recogniser ran it on into junk read off the stave beside it."""
    x0, y0, x1, y1 = example.bbox
    best, best_gap = "", LABEL_REACH_PT
    for line in lines:
        text = line.text.strip()
        match = LABEL_RE.match(text)
        if not match:
            continue
        lx0, ly0, lx1, ly1 = line.bbox
        if ly1 > y0 and ly0 < y1 and x0 - LABEL_REACH_PT <= lx1 and lx0 <= x0 + LABEL_REACH_PT:
            gap = 0.0                       # beside it, at its left
        elif ly0 >= y1 - 2 and lx1 > x0 and lx0 < x1 and not any(
                other is not line and y1 < (other.bbox[1] + other.bbox[3]) / 2 < ly0
                and other.bbox[2] > x0 and other.bbox[0] < x1 for other in lines):
            gap = max(0.0, ly0 - y1)        # above it, with nothing between
        else:
            continue
        if gap >= best_gap and best:
            continue
        if gap > LABEL_REACH_PT:
            continue
        # What follows the label is kept only when it reads as words (a title, a source); junk the
        # recogniser ran on into the label from the stave beside it is cut off.
        rest = text[match.end():].split()
        words = [w for w in rest if sum(ch.isalpha() for ch in w) >= 3]
        junk = any(not word.isascii() or "^" in word for word in rest)
        label = " ".join((text if words and not junk else match.group(0)).split()).rstrip(",;: ")
        best, best_gap = label if label.endswith(".") else label + ".", gap
    return best


# ---- Reading an example's staves, for the draft -------------------------------------------------
#
# The draft says only what is read reliably: how the staves are laid out, and their clefs. A key
# signature, a time signature and a count of bars were all tried on two real scanned books and all
# came out wrong too often to offer -- a sharp's crossbars break where they cross a stave line, the
# recogniser read old engravings' time signatures as Chinese characters, and a barline touching a
# note is lost -- and a draft that states a wrong key is worse than one that leaves it to the
# person. Measurements here are in spaces (the gap between two stave lines), so they hold for any
# size of print.


# A clef stands within this many spaces of its stave's left end.
CLEF_REACH_SPACES = 4.0


@dataclass
class _Glyph:
    """A connected mark on the stave once its lines are taken out, in spaces relative to the
    stave: x from the stave's left end, `top`/`bottom` down from its top line."""

    x0: float
    x1: float
    top: float
    bottom: float
    middle: float = 0.0     # the height of its centre of mass

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.bottom - self.top


def _without_lines(ink: np.ndarray, staves: list[Staff], reach: float) -> np.ndarray:
    """The ink with the stave lines taken out, along the first `reach` spaces of each stave: at
    every stave line, a vertical run no taller than the line itself is erased, so whatever crosses
    the line (a clef, a stem) stays whole."""
    out = ink.copy()
    h = out.shape[0]
    for staff in staves:
        space = staff.space
        thin = max(3, int(round(0.4 * space)))
        for x in range(int(staff.x0 - 0.5 * space), int(min(staff.x1, staff.x0 + reach * space)) + 1):
            if x >= out.shape[1]:
                break
            column = out[:, x]
            for k in range(5):
                y = int(round(staff.line_at(k, x)))
                if not (0 <= y < h):
                    continue
                # Find the run of ink through the line (allowing the line to sit a pixel off).
                start = next((yy for yy in (y, y - 1, y + 1) if 0 <= yy < h and column[yy]), None)
                if start is None:
                    continue
                top = start
                while top > 0 and column[top - 1]:
                    top -= 1
                bottom = start
                while bottom < h - 1 and column[bottom + 1]:
                    bottom += 1
                if bottom - top + 1 <= thin:
                    column[top:bottom + 1] = 0
    return out


def _glyphs(clean: np.ndarray, staff: Staff, x_to: float) -> list[_Glyph]:
    """The marks at the start of one stave, up to `x_to` (pixels), left to right."""
    import cv2

    space = staff.space
    top = int(max(0, staff.top - 4 * space))
    bottom = int(min(clean.shape[0], staff.bottom + 4 * space))
    left, right = int(max(0, staff.x0 - 0.5 * space)), int(min(clean.shape[1], x_to))
    if right <= left or bottom <= top:
        return []
    window = clean[top:bottom, left:right]
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(window, connectivity=8)
    glyphs = []
    for label in range(1, count):
        x, y, w, h, area = stats[label]
        if area < max(4, 0.05 * space * space):
            continue
        line0 = staff.top_at(left + x + w / 2)
        glyphs.append(_Glyph(x0=(left + x - staff.x0) / space, x1=(left + x + w - staff.x0) / space,
                             top=(top + y - line0) / space, bottom=(top + y + h - line0) / space,
                             middle=(top + centroids[label][1] - line0) / space))
    glyphs.sort(key=lambda g: g.x0)
    return glyphs


def _clef(glyphs: list[_Glyph]) -> str | None:
    """The clef at the start of a stave: the first sizeable mark that is not the opening barline or
    a brace, told by where it sits on the stave. A treble clef reaches well above and below it; a
    bass clef hangs from its top line and stops short of the bottom; a C clef spans the stave
    exactly, its weight on the middle line. Anything else is not named."""
    for glyph in glyphs:
        if glyph.x0 > 3.0:
            return None
        if glyph.width < 0.6 or glyph.height < 2.0 or glyph.top < -3.0 or glyph.bottom > 7.0:
            continue                    # the opening barline, a brace or bracket, a dot
        if glyph.height >= 5.0 and glyph.top <= -0.6 and glyph.bottom >= 4.0:
            return "treble"
        if 2.2 <= glyph.height <= 3.6 and abs(glyph.top) <= 0.7 and glyph.bottom <= 3.6:
            return "bass"
        if (3.5 <= glyph.height <= 4.8 and abs(glyph.top) <= 0.6 and abs(glyph.bottom - 4) <= 0.6
                and abs(glyph.middle - 2) <= 0.3):
            return "C"
        return None
    return None


def _systems(example: Example, clefs: list[str]) -> list[list[Staff]]:
    """The example's staves grouped into systems -- the staves played together, read across before
    the next line down -- from its clefs, which repeat down the example one system at a time: a
    piano's treble and bass, a choir's four voices. Staves whose clefs do not repeat are one
    system each, unless the first system is the whole example. (Braces and brackets were tried
    too, and a column rule beside the music fakes them.)"""
    staves = example.staves
    for period in range(1, len(staves)):
        if len(staves) % period == 0 and all(
                clefs[i] == clefs[i % period] for i in range(len(staves))):
            return [staves[i:i + period] for i in range(0, len(staves), period)]
    return [staves]


def analyse(example: Example, page: PageMusic) -> tuple[list[int], list[str | None]]:
    """How an example's staves are laid out -- the number of staves in each system -- and the clef
    of each stave (None where it could not be told; then the layout is not told either, and the
    staves count as one system)."""
    if example.analysis is None:
        example.analysis = _analyse(example, page)
    return example.analysis


def _analyse(example: Example, page: PageMusic) -> tuple[list[int], list[str | None]]:
    clean = _without_lines(page.ink, example.staves, CLEF_REACH_SPACES)
    clefs = [_clef(_glyphs(clean, staff, staff.x0 + CLEF_REACH_SPACES * staff.space))
             for staff in example.staves]
    if not all(clefs):
        return [len(example.staves)], clefs
    return [len(system) for system in _systems(example, clefs)], clefs


_COUNT = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
          8: "eight", 9: "nine", 10: "ten"}


def _count(n: int, noun: str) -> str:
    return f"{_COUNT.get(n, str(n))} {noun}{'' if n == 1 else 's'}"


def describe(example: Example, page: PageMusic) -> str:
    """A draft description of an example: how it is laid out on its staves, and their clefs --
    "Music in two lines, each on two staves: treble and bass clefs." The person adds what the music
    is."""
    systems, clefs = analyse(example, page)
    if not systems:
        return ""
    size = systems[0]           # every system is the same size (`_systems`)
    if len(systems) == 1:
        layout = "Music on one stave" if size == 1 else f"Music on {_count(size, 'stave')}"
    elif size == 1:
        layout = f"Music on one stave, in {_count(len(systems), 'line')}"
    else:
        layout = f"Music in {_count(len(systems), 'line')}, each on {_count(size, 'stave')}"
    if not all(clefs):
        return layout + "."
    # The clefs as they stand in a system, top to bottom -- or, past a handful of staves, each clef
    # once, in the order it first appears.
    names = clefs[:size]
    if len(names) > 4:
        names = list(dict.fromkeys(names))
    if len(set(names)) == 1:
        return f"{layout}, {names[0]} clef." if size == 1 else             f"{layout}, all in the {names[0]} clef."
    return f"{layout}: {', '.join(names[:-1])} and {names[-1]} clefs."
