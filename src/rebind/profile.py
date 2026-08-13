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
# The shortest run of letters that can identify a running head. Below this a match means almost
# nothing -- "the", "part i" -- and would condemn an ordinary short line sitting at a page edge.
RUNNING_HEAD_MIN_CHARS = 8
# How many of a document's pages must carry the SAME WORDS at the same edge for it to be furniture.
# Much lower than RECURRENCE_FRACTION, and it has to be: a book's running head alternates, the
# chapter title on the verso and the section title on the recto, so neither can ever appear on more
# than about half the pages. On the real sample each side appeared on 12 of 29 pages -- under a 0.5
# threshold by two pages, which is why every one of them was still being read as a heading. Identity
# of text is far stronger evidence than identity of style, so it can afford the lower bar.
RUNNING_HEAD_FRACTION = 0.25
# ...but never fewer than this many pages, so a phrase appearing twice in a short document cannot
# delete itself on the strength of one coincidence.
RUNNING_HEAD_MIN_PAGES = 3


@dataclass(frozen=True)
class Style:
    font: str
    size: float
    bold: bool
    italic: bool


def style_of(line: TextLine) -> Style:
    return Style(font=line.font, size=line.size, bold=line.bold, italic=line.italic)


def _is_folio(text: str) -> bool:
    """Whether a line is nothing but a page number.

    The recurrence rule below cannot catch every folio: a chapter's opening page often carries its
    number at the *bottom* while every later page carries it at the top, so that one instance never
    recurs at its own edge and stays tagged as content -- a screen reader then announces a bare
    "27" in the middle of the prose. A line in the page's edge band whose entire content is a
    number is furniture with no ambiguity about it, whatever the rest of the document does. The
    test is deliberately narrow: a number and nothing else, so a real one-line paragraph or a
    footnote can never match it.
    """
    stripped = text.strip().strip(".-—–[]() \t")
    if not stripped or len(stripped) > 12:
        return False
    return stripped.isdigit() or (
        stripped.upper().strip("IVXLCDM") == "" and stripped.isalpha())


def _edge_band(line: TextLine, page_height: float) -> str | None:
    """Which page edge a line sits in, if any."""
    margin = page_height * EDGE_FRACTION
    if line.bbox[1] >= page_height - margin:
        return "top"
    if line.bbox[3] <= margin:
        return "bottom"
    return None


def _running_head_key(text: str) -> str:
    """A running head reduced to what stays the same from page to page.

    The folio changes ("6 The Power of Images...", "22 The Power of Images..."), so digits go, as
    does punctuation and case; what is left is the words. Short leftovers are refused outright --
    a bare folio is already caught by `_is_folio`, and matching on one or two words would let an
    ordinary short line at a page edge condemn every other page's.
    """
    words = "".join(c if c.isalpha() or c.isspace() else " " for c in text).split()
    joined = " ".join(words).lower()
    return joined if len(joined) >= RUNNING_HEAD_MIN_CHARS else ""


@dataclass(frozen=True)
class TypographicProfile:
    body: Style | None
    heading_levels: tuple[Style, ...]
    artifact_keys: frozenset[tuple[Style, str]]
    style_volumes: dict[Style, int]
    total_chars: int
    artifact_texts: frozenset[tuple[str, str]] = frozenset()

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
        if band is not None and _is_folio(line.text):
            return "artifact"
        if band is not None and (_running_head_key(line.text), band) in self.artifact_texts:
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
        if not self.total_chars:
            return 0.0
        share = self.style_volumes.get(style, 0) / self.total_chars
        if self.heading_level(style):
            # A heading style covering a meaningful share of the document -- because it recurs
            # across many pages, or introduces long sections -- is a confident classification;
            # one seen only a handful of characters anywhere (a style guessed to be a heading
            # purely from being larger/bolder than body, with almost nothing else to go on) is a
            # guess and must score low, not the flat 0.9 every heading style used to get
            # regardless of evidence. The floor and ceiling are higher than the generic fallback
            # below because heading candidates arrive here already narrowed by the size/bold
            # heuristic in `build_profile`, so the same share of evidence carries more weight.
            return round(min(1.0, 0.4 + share * 6), 3)
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
    # The same, keyed by the line's *words* rather than its style. This is the one place the pass
    # looks at text, and it is what a scan needs: OCR reports no typeface, so every line of a
    # scanned book shares one style and the style rule above can never separate a running head from
    # the prose under it. Memory stays bounded -- the keys are the distinct phrases that appear in
    # a page's edge band, which is a handful per document, not one per line.
    text_page_counts: dict[tuple[str, str], int] = {}
    text_last_page: dict[tuple[str, str], int] = {}
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
                words = _running_head_key(line.text)
                if words:
                    text_key = (words, band)
                    if text_last_page.get(text_key) != page.number:
                        text_page_counts[text_key] = text_page_counts.get(text_key, 0) + 1
                        text_last_page[text_key] = page.number

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
    # The body style itself is never eligible to become an artifact key, no matter how often it
    # recurs at a page edge. Style + position alone cannot tell a running header from an ordinary
    # paragraph that happens to be the first or last line on its page (a page with generous
    # margins, or a document with no running header at all, routinely has body-styled lines
    # reaching into EDGE_FRACTION on well over RECURRENCE_FRACTION of pages) -- a running header
    # is additionally distinguished by being the *same text* recurring, which this pass never
    # inspects (it retains style statistics only, never text, see the module docstring). Absent
    # that check, classifying the body style as an artifact silently deletes real paragraphs with
    # confidence=1.0 and no flag -- exactly the "never fabricate/never silently drop" invariant
    # this project exists to uphold. The cost is that a genuine running header sharing the body's
    # exact style is not caught here; that is a real but much rarer shape than the false positive
    # this excludes, and catching it would require the text-recurrence signal this pass
    # deliberately does not retain.
    artifact_keys = frozenset(
        key for key, count in edge_page_counts.items() if count >= threshold and key[0] != body
    )

    # Unlike the style rule, this one has no body-style exclusion to make: it is not condemning a
    # style, it is condemning one exact phrase that sits at the same page edge on half the
    # document's pages. Ordinary prose does not do that, whatever style it is set in.
    text_threshold = max(RUNNING_HEAD_MIN_PAGES, int(page_count * RUNNING_HEAD_FRACTION))
    artifact_texts = frozenset(
        key for key, count in text_page_counts.items() if count >= text_threshold
    )

    return TypographicProfile(
        body=body,
        heading_levels=tuple(heading_candidates),
        artifact_keys=artifact_keys,
        style_volumes=volumes,
        total_chars=sum(volumes.values()),
        artifact_texts=artifact_texts,
    )
