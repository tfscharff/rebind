"""Pass one: derive a document-global typographic profile.

Heading styles in a long document are consistent document-wide, so a global profile assigns
levels correctly where a per-page rule cannot: a page holding only a heading and one paragraph
has no usable local baseline, and per-page assignment drifts across a long document -- a silent
failure, worst on exactly the 300-page catalog that motivates the project.

This pass retains style statistics only, never text, so memory is bounded by the number of
distinct styles rather than by document length.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .extract import Page, TextLine

# A line is an artifact candidate if it sits within this fraction of the page top or bottom...
EDGE_FRACTION = 0.10
# ...and appears at that position on at least this fraction of pages. Both conditions are
# required: position alone would condemn a first-page title, which also sits at the top.
RECURRENCE_FRACTION = 0.5


@dataclass(frozen=True)
class Style:
    font: str
    size: float
    bold: bool
    italic: bool


def style_of(line: TextLine) -> Style:
    return Style(font=line.font, size=line.size, bold=line.bold, italic=line.italic)


def _edge_band(line: TextLine, page_height: float) -> str | None:
    """Which page edge a line sits in, if any."""
    margin = page_height * EDGE_FRACTION
    if line.bbox[1] >= page_height - margin:
        return "top"
    if line.bbox[3] <= margin:
        return "bottom"
    return None


@dataclass(frozen=True)
class TypographicProfile:
    body: Style | None
    heading_levels: tuple[Style, ...]
    artifact_keys: frozenset[tuple[Style, str]]
    style_volumes: dict[Style, int]
    total_chars: int

    def heading_level(self, style: Style) -> int:
        """1-based heading level, or 0 if this style is not a heading style."""
        for index, candidate in enumerate(self.heading_levels, start=1):
            if candidate == style:
                return index
        return 0

    def role_of(self, line: TextLine, page_height: float) -> str:
        style = style_of(line)
        band = _edge_band(line, page_height)
        if band is not None and (style, band) in self.artifact_keys:
            return "artifact"
        if self.heading_level(style):
            return "heading"
        return "body"

    def confidence_for(self, line: TextLine, page_height: float) -> float:
        """How cleanly this line's style matches the profile.

        This is a style-match score and nothing else. Born-digital text is exact by
        construction, so it is deliberately NOT a text-accuracy score -- conflating the two
        would make the number meaningless once OCR confidence arrives in Phase 2.
        """
        style = style_of(line)
        if style == self.body:
            return 1.0
        if self.heading_level(style):
            return 0.9
        if not self.total_chars:
            return 0.0
        share = self.style_volumes.get(style, 0) / self.total_chars
        # A style covering a large share of the document is a confident classification even when
        # it is neither body nor a recognized heading; a style seen twice is a guess.
        return round(min(0.8, 0.2 + share * 4), 3)


def build_profile(pages: Iterable[Page]) -> TypographicProfile:
    volumes: dict[Style, int] = {}
    # Recurrence must be counted in DISTINCT PAGES, not lines: two lines of an address block
    # sharing a style and edge band on the same page must never look like cross-page recurrence.
    # Pages arrive in order, so we don't need a set of every page seen per key (that would grow
    # with document length, violating this pass's "memory bounded by style count" invariant) --
    # tracking only the last page number recorded for a key lets us detect "this is a new page
    # for this key" in O(1) space per key.
    edge_page_counts: dict[tuple[Style, str], int] = {}
    edge_last_page: dict[tuple[Style, str], int] = {}
    page_count = 0

    for page in pages:
        page_count += 1
        for line in page.lines:
            style = style_of(line)
            volumes[style] = volumes.get(style, 0) + len(line.text)
            band = _edge_band(line, page.height)
            if band is not None:
                key = (style, band)
                if edge_last_page.get(key) != page.number:
                    edge_page_counts[key] = edge_page_counts.get(key, 0) + 1
                    edge_last_page[key] = page.number

    if not volumes:
        return TypographicProfile(None, (), frozenset(), {}, 0)

    # Tie-break on every Style field, never on dict/set iteration order: Style has exactly four
    # fields (font, size, bold, italic), so once all four appear in the sort key the order is
    # fully determined by content. This project has already been bitten by nondeterminism (see
    # ADR 0003), and heading level assignment downstream must not depend on insertion order.
    body = max(
        volumes.items(),
        key=lambda item: (item[1], -item[0].size, item[0].bold, item[0].italic, item[0].font),
    )[0]

    heading_candidates = [
        style for style in volumes
        if style != body and (style.size > body.size or (style.bold and not body.bold))
    ]
    heading_candidates.sort(key=lambda s: (-s.size, not s.bold, not s.italic, s.font))

    # A single page can never demonstrate cross-page recurrence, so the floor of 2 is not a magic
    # number: max(2, int(page_count * RECURRENCE_FRACTION)) is unreachable on a 1-page document
    # (int(1 * 0.5) == 0), which correctly means nothing on a 1-page document is ever an artifact.
    threshold = max(2, int(page_count * RECURRENCE_FRACTION))
    artifact_keys = frozenset(
        key for key, count in edge_page_counts.items() if count >= threshold
    )

    return TypographicProfile(
        body=body,
        heading_levels=tuple(heading_candidates),
        artifact_keys=artifact_keys,
        style_volumes=volumes,
        total_chars=sum(volumes.values()),
    )
