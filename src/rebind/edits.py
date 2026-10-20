"""The person's corrections, the tags they can give, and the element records the editor shows."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .extract import TextLine
from .tables import _table_row_groups


def _records_in_reading_order(records: list[dict]) -> list[dict]:
    """The editor's elements for one page, in the order the page is actually read.

    That order is not re-derived here. It was decided by `layout.order_page`'s XY-cut before any
    of this ran, `_lines_for` returned the page's lines in it, and every record carries the index
    of the line it starts at as `_order`. Sorting on that reproduces the cut exactly -- including
    its columns -- and the key never reaches the editor.

    This function used to guess instead, grouping records into visual rows and sorting each row
    left to right. Both halves of that were wrong on a real scanned book (a tilted two-page
    spread, `The_power_of_images...`):

    * The row's lower edge was `max(top + height)` over its members, which only ever grows, so a
      row of overlapping multi-line paragraphs never closed. All twenty elements on a page landed
      in one row.
    * That row was then sorted by left edge -- and a tilted scan drifts every line's left edge
      about 0.7pt further across as the page goes down, so the sort walked the page BOTTOM TO TOP.

    On a two-column page it also read straight across the gutter, because a left-column element
    and the right-column element beside it share a row. The tagged output was correct throughout
    (it is built from the plan, which kept the cut's order); it was the person correcting that
    output who was handed word salad to tab through.
    """
    # Stable, so a table's row sub-elements -- which share their table's key -- stay with it.
    return [{k: v for k, v in record.items() if k != "_order"}
            for record in sorted(records, key=lambda r: r["_order"])]


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
    # element id -> the frame a person drew around it, as (left, top, width, height) in percent of
    # the page -- the editor's own coordinates. The element becomes exactly the lines whose centres
    # fall inside it (`_apply_frames`).
    frames: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)
    # inserted element id -> the element it was inserted after, which it follows in the reading
    # order wherever its lines are on the page (`_apply_frames`).
    after: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict | None) -> Edits:
        payload = payload or {}

        # TH/TD are accepted on any id: on a detected table's row they set that row, and on any
        # other element they make it a cell of a table built around it (`_page_structure`), so a
        # bare /TH with no /TR or /Table around it can no longer arise from either.
        def allowed(v: str) -> bool:
            return v in EDITABLE_TAGS

        def frame(v) -> tuple[float, float, float, float] | None:
            try:
                left, top, width, height = (float(x) for x in v)
            except (TypeError, ValueError):
                return None
            values = (left, top, width, height)
            if not all(math.isfinite(x) for x in values) or width <= 0 or height <= 0:
                return None
            return values

        frames = {str(k): frame(v) for k, v in (payload.get("frames") or {}).items()}
        return cls(
            tags={str(k): str(v) for k, v in (payload.get("tags") or {}).items()
                  if allowed(str(v))},
            removed={str(v) for v in (payload.get("removed") or [])},
            alts={str(k): str(v).strip() for k, v in (payload.get("alts") or {}).items()
                  if str(v).strip()},
            frames={k: v for k, v in frames.items() if v is not None},
            after={str(k): str(v) for k, v in (payload.get("after") or {}).items()},
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
CONTENT_TAGS = ("P", "H1", "H2", "H3", "H4", "H5", "H6", "BlockQuote", "Code", "Formula", "Form",
                "Note")
# /Part is not offered: it is a container with no behaviour of its own that /Sect and /Div do not
# already have, so it was one more thing to choose between for no gain to a reader.
GROUPING_TAGS = ("Sect", "Div", "Art", "Index", "NonStruct")
SPECIAL_TAGS = ("Figure", "Table", "L", "Caption")
# A cell is never tagged where it stands -- a /TH or /TD is only legal inside a /TR inside a /Table.
# A run of elements marked as cells is gathered into one table around them (`_cell_table`), which
# is how a table Rebind did not detect is built by hand.
CELL_TAGS = ("TH", "TD")
EDITABLE_TAGS = CONTENT_TAGS + GROUPING_TAGS + SPECIAL_TAGS + CELL_TAGS

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
    ("h", "TH", "Table header cell", "A cell that labels a row or column of a table. Cells marked "
                                     "one after another are built into one table."),
    ("b", "TD", "Table data cell", "An ordinary cell of a table, read against its header. Cells "
                                   "marked one after another are built into one table."),
    ("l", "L", "List", "A bulleted or numbered list."),
    ("s", "Sect", "Section", "A container grouping related content together."),
    ("d", "Div", "Division", "A generic container, when nothing more specific fits."),
    ("a", "Art", "Article", "A self-contained piece of writing within the document."),
    ("i", "Index", "Index", "An index of terms and where they appear."),
    ("n", "NonStruct", "No structure", "Content that carries no structural meaning of its own."),
    ("m", "Formula", "Formula (maths)", "A mathematical or chemical expression."),
    ("e", "Code", "Code", "Program code or other text where the exact characters matter."),
    ("o", "Form", "Form field", "An interactive field a reader fills in."),
    ("v", "Note", "Footnote", "A footnote or endnote — read separately from the body text it "
                              "annotates, not in the middle of it."),
)

# Not a type, so not in TAG_KEYS -- an action, on its own key: take this out of the reading order
# and let it be drawn as page furniture instead. Page furniture Rebind already identified is an
# artifact without anyone pressing anything; this is for the one it got wrong.
ARTIFACT_KEY = "x"
ARTIFACT_LABEL = "Not read"
ARTIFACT_WHAT = ("Page furniture — on the page, but skipped by a screen reader. Give it a type to "
                 "have it read after all.")

# A detected table's rows answer to a keymap of their own, sent to the frontend separately (like
# ARTIFACT_KEY) and swapped in only when the focused element is a row: a row is only ever a header
# row or a data row. Its keys are the same as the whole-element cell keys above, so marking a cell
# is the same keystroke whether or not Rebind found the table.
ROW_TAG_KEYS = (
    ("h", "TH", "Header cell", "This row labels the columns beneath it — a screen reader reads "
                               "it before each data cell in its column."),
    ("b", "TD", "Data cell", "An ordinary cell of the table, read against its column's header."),
)
ROW_TAGS = tuple(tag for _key, tag, _label, _what in ROW_TAG_KEYS)


def _element_records(src_page, plan: list[dict], lines: list[TextLine],
                     mcid_of: list[int | None], edits: Edits,
                     source_index: list[int]) -> list[dict]:
    """One record per element for the app's editor: what it is, where it is, what it says.

    A `Table` entry also yields one sub-record per detected row -- its own id and bbox -- so a
    row's header/data status can be corrected without retagging the whole table (`ROW_TAG_KEYS`).
    A row's id is derived from the table's, never from a source line, so it can never collide with
    a top-level element's id.

    `source_index` maps an index into `lines` back to the line's position on the page as the XY-cut
    ordered it. Each record carries that as `_order`, which is what puts the editor's walk in
    reading order (`_records_in_reading_order`); a table's rows share their table's key so they
    stay with it. It is stripped before the records reach the editor.
    """
    out = []
    order_of: dict[str, int] = {}
    for entry in plan:
        first, last = entry["first"], entry["last"]
        if mcid_of[first] is None:
            continue
        # An element inserted after another is listed with it (the sort is stable, and the plan
        # already has it straight after), not where its lines happen to sit on the page.
        anchor = edits.after.get(entry["id"]) or entry.get("_after", "")
        order_of[entry["id"]] = order_of.get(anchor, entry.get("_order", source_index[first]))
        members = lines[first:last + 1]
        box = (min(ln.bbox[0] for ln in members), min(ln.bbox[1] for ln in members),
               max(ln.bbox[2] for ln in members), max(ln.bbox[3] for ln in members))
        out.append({
            "id": entry["id"],
            "_order": order_of[entry["id"]],
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
        if entry["kind"] == "Table":
            for row_index, (row_id, row) in enumerate(_table_row_groups(entry, lines)):
                row_lines = [lines[i] for i in row]
                rbox = (min(ln.bbox[0] for ln in row_lines), min(ln.bbox[1] for ln in row_lines),
                        max(ln.bbox[2] for ln in row_lines), max(ln.bbox[3] for ln in row_lines))
                default_kind = "TH" if row_index == 0 else "TD"
                out.append({
                    "id": row_id,
                    "_order": order_of[entry["id"]],
                    "page": src_page.number,
                    "kind": edits.tags.get(row_id, default_kind),
                    "text": " ".join(ln.text.strip() for ln in row_lines).strip()[:300],
                    "left": round(100 * rbox[0] / src_page.width, 2),
                    "top": round(100 * (src_page.height - rbox[3]) / src_page.height, 2),
                    "width": round(100 * (rbox[2] - rbox[0]) / src_page.width, 2),
                    "height": round(100 * (rbox[3] - rbox[1]) / src_page.height, 2),
                    "editable": True,
                    "row": True,
                })
    return out


def _record_box(record: dict, src_page) -> tuple[float, float, float, float]:
    """An editor record's box back in PDF points (its left/top/width/height are page percents)."""
    x0 = record["left"] * src_page.width / 100
    x1 = (record["left"] + record["width"]) * src_page.width / 100
    y1 = src_page.height - record["top"] * src_page.height / 100
    y0 = y1 - record["height"] * src_page.height / 100
    return (x0, y0, x1, y1)


def _untagged_record(src_page, element_id: str, line: TextLine, order: int) -> dict:
    """A line Rebind left out of the structure tree -- page furniture, or text inside a figure --
    offered to the editor so it can be given a tag and read after all."""
    x0, y0, x1, y1 = line.bbox
    return {
        "id": element_id, "_order": order, "page": src_page.number, "kind": "Artifact",
        "text": line.text.strip()[:300],
        "left": round(100 * x0 / src_page.width, 2),
        "top": round(100 * (src_page.height - y1) / src_page.height, 2),
        "width": round(100 * (x1 - x0) / src_page.width, 2),
        "height": round(100 * (y1 - y0) / src_page.height, 2),
        "editable": True,
    }


# How much of the shorter box a figure and an element must share vertically to be on one row, and
# so be ordered by which is further left rather than by which starts a shade higher.
FIGURE_SAME_ROW_FRACTION = 0.5


def _place_figures(elements: list[dict], src_page, figures: list[tuple[str, str, tuple]],
                   music: set[str]) -> None:
    """Put the page's pictures, each `(id, alt, bbox)`, into the editor's elements at the
    position they are read, so the editor's order is the document's order.

    Undescribed ones are here too, with an empty alt. In the DOCUMENT they are decorative artifacts
    and must be -- tagging a figure with no description is a conformance failure -- but in the
    EDITOR they have to be visible, because a picture nobody can see is a picture nobody will
    describe. Tabbing onto one is what opens the description prompt, and a description promotes it
    to a real /Figure on the next rebuild.
    """
    # Placed left to right within a row, so two pictures side by side are met the way they are
    # read. Inserting each one purely by its top edge cannot do this: the two are never level to
    # the point, and whichever sits a hair higher wins regardless of which side of the page it
    # is on -- which is how a page's photograph came after the coin printed beside it.
    for figure_id, alt, bbox in sorted(figures, key=lambda spec: (-spec[2][3], spec[2][0])):
        fig_top = 100 * (src_page.height - bbox[3]) / src_page.height
        fig_bottom = 100 * (src_page.height - bbox[1]) / src_page.height
        fig_left = 100 * bbox[0] / src_page.width
        fig_right = 100 * bbox[2] / src_page.width

        def after(elem, top=fig_top, bottom=fig_bottom, left=fig_left) -> bool:
            """Whether `elem` is read after this figure: on a later row, or beside it and
            further right."""
            if elem["page"] != src_page.number:
                return False
            shared = min(bottom, elem["top"] + elem["height"]) - max(top, elem["top"])
            same_row = shared > FIGURE_SAME_ROW_FRACTION * min(
                bottom - top, max(elem["height"], 0.01))
            return elem["left"] > left if same_row else elem["top"] > top

        # In its own column, as in the document (`_figure_anchor`): straight after the last
        # element above it that shares any of its width, else before the first one below.
        middle = (fig_top + fig_bottom) / 2
        column = [i for i, elem in enumerate(elements)
                  if elem["page"] == src_page.number
                  and elem["kind"] not in ("Figure", "Artifact")
                  and elem["left"] < fig_right and elem["left"] + elem["width"] > fig_left]
        above = [i for i in column if elements[i]["top"] + elements[i]["height"] / 2 < middle]
        if above:
            position = max(above) + 1
        elif column:
            position = min(column)
        else:
            position = next((i for i, elem in enumerate(elements) if after(elem)), len(elements))
        # Pictures placed at the same spot already, above or before this one, stay first.
        while (position < len(elements)
               and elements[position]["page"] == src_page.number
               and elements[position]["kind"] == "Figure"
               and not after(elements[position])):
            position += 1
        elements.insert(position, {
            "id": figure_id,
            "page": src_page.number, "kind": "Figure", "text": "", "alt": alt,
            **({"music": True} if figure_id in music else {}),
            "left": round(100 * bbox[0] / src_page.width, 2),
            "top": round(100 * (src_page.height - bbox[3]) / src_page.height, 2),
            "width": round(100 * (bbox[2] - bbox[0]) / src_page.width, 2),
            "height": round(100 * (bbox[3] - bbox[1]) / src_page.height, 2),
            "editable": True,
        })
