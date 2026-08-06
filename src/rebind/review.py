"""Evidence for the two accessibility checks a machine is never allowed to sign off on.

Adobe's checker reports "Logical Reading Order" and "Colour contrast" as *needs manual check* on
every document, always, because both are ultimately about what a human perceives. Rebind cannot
make them pass -- nothing can -- but it can hand the person doing the signing off the evidence they
would otherwise have to gather by eye, page by page, on a 300-page catalogue.

Reading order is a decision Rebind *made*, so the honest thing is to show its work. Most pages have
no decision in them: text runs straight down a single column and the only possible order is the
obvious one. Those are reported in bulk. A page where the order is a real choice -- two columns, a
figure interrupting the flow, blocks that do not simply stack -- gets shown with its blocks
numbered in the order a screen reader will read them, so a glance either confirms it or doesn't.

`contrast` is the sibling module; it measures rather than reasons.
"""

from __future__ import annotations

from dataclasses import dataclass

from .extract import Page, TextLine
from .layout import PageLayout

# Consecutive lines in the same column belong to the same visual block until a vertical gap opens
# up. Measured against the line height rather than in absolute points, so it holds for a 6pt
# footnote and a 24pt heading alike.
BLOCK_GAP_RATIO = 1.8
# A page with more blocks than this is dense enough that a numbered overlay stops being readable;
# the blocks are still numbered, just capped so the review stays a glance rather than a puzzle.
MAX_BLOCKS_SHOWN = 40


@dataclass(frozen=True)
class Block:
    """One visual block of text, numbered in the order it will be read."""

    number: int
    bbox: tuple[float, float, float, float]
    text: str
    column: int


@dataclass(frozen=True)
class PageOrder:
    page: int
    blocks: tuple[Block, ...]
    columns: int
    reason: str          # why this page needs an eye; "" when it does not
    width: float
    height: float

    @property
    def needs_review(self) -> bool:
        return bool(self.reason)


def _group_blocks(placed) -> list[tuple[int, list[TextLine]]]:
    """Consecutive placed lines grouped into visual blocks, preserving reading order."""
    blocks: list[tuple[int, list[TextLine]]] = []
    for item in placed:
        line, column = item.line, item.column
        if blocks:
            prev_column, prev_lines = blocks[-1]
            last = prev_lines[-1]
            height = max(last.bbox[3] - last.bbox[1], 1.0)
            gap = last.bbox[1] - line.bbox[3]
            if prev_column == column and -height <= gap <= height * BLOCK_GAP_RATIO:
                prev_lines.append(line)
                continue
        blocks.append((column, [line]))
    return blocks


def page_order(page: Page, layout: PageLayout, figure_boxes: tuple = ()) -> PageOrder:
    """Rebind's chosen reading order for one page, and whether it was a real choice.

    A page is flagged only when the order could plausibly have come out otherwise. Three things do
    that: more than one column (the classic way reading order goes wrong -- a screen reader that
    reads across the gutter produces word salad), a figure sitting inside the text flow (its
    position in the order is a judgement call), and a layout the cut itself was unsure about.
    Everything else reads top to bottom, where there is nothing to second-guess.
    """
    grouped = _group_blocks(layout.lines)
    blocks = tuple(
        Block(
            number=index,
            bbox=(min(ln.bbox[0] for ln in lines), min(ln.bbox[1] for ln in lines),
                  max(ln.bbox[2] for ln in lines), max(ln.bbox[3] for ln in lines)),
            text=" ".join(ln.text.strip() for ln in lines).strip()[:120],
            column=column,
        )
        for index, (column, lines) in enumerate(grouped[:MAX_BLOCKS_SHOWN], start=1)
    )
    columns = len({column for column, _lines in grouped if column >= 0})

    if columns > 1:
        reason = f"{columns} columns — a screen reader must not read across the gutter"
    elif "multi-column-suspected" in layout.flags:
        reason = "the column layout here was ambiguous"
    elif figure_boxes:
        reason = "a figure sits in the text flow"
    else:
        reason = ""
    return PageOrder(page=page.number, blocks=blocks, columns=columns, reason=reason,
                     width=page.width, height=page.height)


def summarize(orders: list[PageOrder], thumbs: dict[int, str] | None = None) -> dict:
    """The reading-order section of the review: a bulk verdict plus the pages needing an eye.

    Block boxes are emitted as percentages of the page, so the UI can lay the numbered overlay
    over a thumbnail of any size without knowing the page's point dimensions. PDF's y axis runs
    bottom-up and CSS's runs top-down, so `top` is flipped here rather than in the browser.
    """
    thumbs = thumbs or {}
    flagged = [order for order in orders if order.needs_review]

    def box(order: PageOrder, b: Block) -> dict:
        x0, y0, x1, y1 = b.bbox
        return {
            "n": b.number, "text": b.text,
            "left": round(100 * x0 / order.width, 2),
            "top": round(100 * (order.height - y1) / order.height, 2),
            "width": round(100 * (x1 - x0) / order.width, 2),
            "height": round(100 * (y1 - y0) / order.height, 2),
        }

    return {
        "checked": len(orders),
        "clear": len(orders) - len(flagged),
        "pages": [
            {
                "page": order.page,
                "reason": order.reason,
                "thumb": thumbs.get(order.page, ""),
                "blocks": [box(order, b) for b in order.blocks],
            }
            for order in flagged
        ],
    }
