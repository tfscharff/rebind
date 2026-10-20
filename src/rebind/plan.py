"""Plan a page's elements -- lists, tables, paragraphs joined from lines -- and apply the frames a
person drew on top.
"""

from __future__ import annotations

import re
from dataclasses import replace

from . import layout_model
from .extract import TextLine
from .geometry import _box
from .layout import detect_table_lines
from .tables import _table_row_groups, _table_rows

BULLET_PREFIXES = ("•", "‣", "◦", "-", "*")
# An ordered-list marker, with or without trailing content: "1. first", "1.", "2)". The digit run
# is capped at 3 so a year like "1996. It was..." never matches (a longer run leaves a digit before
# the required "[.)]"). A heuristic disambiguator, not a size limit (invariant 5).
ORDERED_RE = re.compile(r"^(\d{1,3})[.)](?:\s+(.*))?$")


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
                    scripts=tuple(s.moved(len(cur.text) + 1) for s in nxt.scripts),
                    bbox=(min(cur.bbox[0], nxt.bbox[0]), min(cur.bbox[1], nxt.bbox[1]),
                          max(cur.bbox[2], nxt.bbox[2]), max(cur.bbox[3], nxt.bbox[3]))))
                i += 2
                continue
        out.append(cur)
        i += 1
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


def _is_list_item(text: str) -> bool:
    item = _list_item_text(text)
    return item is not None and bool(item[0])


def plan_page(lines: list[TextLine], page_roles: list[str],
              caption_groups: list[int | None] | None = None,
              hints: layout_model.PageHints | None = None) -> list[dict]:
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

    `hints` is what a layout model saw on the page (`layout_model.hints_for`). Where it boxed two
    neighbouring lines, its boxes decide: lines in one block of text are one paragraph, lines in
    different blocks are not, and a table is exactly the lines in its table region. Where it boxed
    neither, the geometric rules below decide as they always have. The model is far better at this
    than measuring margins: a born-digital paragraph whose lines each carry a slightly different
    font came out as one element per line, and a paragraph that runs down past a table's foot was
    cut where the table's rows ended.
    """
    n = len(lines)
    groups = caption_groups if caption_groups is not None else [None] * n
    modelled = hints is not None and hints.tables is not None
    if modelled:
        table_of = [hints.tables.get(id(line)) for line in lines]
    else:
        table_line_ids = detect_table_lines(lines)
        table_of = [0 if id(line) in table_line_ids else None for line in lines]
    is_table = [region is not None for region in table_of]
    block_of = [hints.blocks.get(id(line)) for line in lines] if hints else [None] * n
    # The geometric detector misses sparse rows, so it bridges a few unflagged lines; a table
    # region already contains its sparse rows, and anything outside it is not part of the table.
    table_gap = 0 if modelled else MAX_INTERNAL_TABLE_GAP

    def continues(first: int, index: int, same) -> bool:
        before, here = block_of[index - 1], block_of[index]
        if before is not None and here is not None:
            return before == here
        return same(index, first)

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
            while j < n and (j - last - 1) <= table_gap:
                if table_of[j] == table_of[i]:
                    last = j
                elif is_table[j]:
                    break       # another table
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
                   and continues(i, j + 1, lambda index, first: _same_paragraph(
                       lines, first, index, measures))):
                j += 1
            plan.append({"kind": page_roles[i], "first": i, "last": j})
            i = j + 1
            continue

        # An equation is the lines in its own region, the number beside it included -- and
        # nothing else, whatever the margins say.
        if page_roles[i] == "Formula":
            j = i
            while (j + 1 < n and not is_table[j + 1] and page_roles[j + 1] == "Formula"
                   and continues(i, j + 1, lambda _index, _first: False)):
                j += 1
            plan.append({"kind": "Formula", "first": i, "last": j})
            i = j + 1
            continue

        # A heading runs on the same way a paragraph does, on its own simpler evidence: a title
        # too long for one line is one title, and leaving it as two elements puts a phantom entry
        # in a screen reader's heading list and a pause in the middle of the title.
        if page_roles[i].startswith("H"):
            j = i
            while (j + 1 < n and not is_table[j + 1] and page_roles[j + 1] == page_roles[i]
                   and continues(i, j + 1, lambda index, _first: _same_heading(lines, index))):
                j += 1
            plan.append({"kind": page_roles[i], "first": i, "last": j})
            i = j + 1
            continue

        plan.append({"kind": page_roles[i], "first": i, "last": i})
        i += 1
    return plan


def _apply_frames(plan: list[dict], lines: list[TextLine],
                  frames: dict[str, tuple[float, float, float, float]],
                  orphan_id, added: list[dict] | None = None,
                  removed: set[str] | frozenset[str] = frozenset(),
                  after: dict[str, str] | None = None) -> tuple[list[int], list[dict]]:
    """Regroup a page's plan around the frames a person drew: `(new line order, new plan)`.

    A framed element becomes exactly the lines whose centres its frame (PDF points, x0 y0 x1 y1)
    encloses, taken from whatever element held them -- so dragging a frame down over the next line
    merges it in, and pulling it back up gives the line away. An element emptied that way is gone;
    lines a frame gave up that no other frame took become an element of their own where they were
    (`orphan_id(line index)` names it, stably, from its first line). Where two frames overlap, the
    earlier element in the plan keeps the line.

    `added` are elements a person inserted (Insert in the editor): entries with an id and a kind
    but no lines of their own, which hold whatever their frame encloses, after the plan's own
    frames have had theirs. One whose frame encloses nothing yet is left out. An inserted element
    or row named in `after` is read straight after the element or row it was inserted after,
    wherever its own lines sit on the page -- inserting is putting something into the sequence at
    a place a person chose.

    Plan entries are ranges over the line list, and a frame can gather lines that were not next to
    each other, so the lines are put into a new order in which every element's lines are adjacent:
    the returned order lists old line indices, and the new plan's ranges index into it. Each
    element sits where its first line was, which leaves every unframed element where it stood.
    Ids are the plan's own, decided before any frame, so a frame is found again on the next run.

    A detected table's rows (`{table id}r{n}`, see `_table_row_groups`) can be framed too. A row's
    frame takes the lines it encloses into that row -- and into the table, wherever they were --
    after element frames have had theirs; a line it gives up leaves the table, as above. A framed
    row id the table never had is a row a person inserted. A row whose id is in `removed` leaves
    the table: its lines become an entry under the row's own id, marked `"removed_row"`, for the
    caller to drop with the other removed elements. The table's entry then carries its rows
    explicitly (`"rows"`), under the ids they had before any frame, so a row keeps its id, its tag
    and its frame however the others are reshaped.
    """
    added = [{**entry, "first": 0, "last": -1} for entry in (added or [])]
    row_frames: dict[str, list[tuple[str, list[int]]]] = {}
    new_rows: dict[str, list[str]] = {}
    for entry in [*plan, *added]:
        if entry["kind"] != "Table":
            continue
        base = _table_row_groups(entry, lines) if entry["last"] >= entry["first"] else []
        known = {row_id for row_id, _members in base}
        extra = sorted((key for key in frames
                        if key not in known and re.fullmatch(re.escape(entry["id"]) + r"r\d+", key)),
                       key=lambda key: int(key[len(entry["id"]) + 1:]))
        if extra or any(row_id in frames or row_id in removed for row_id in known):
            row_frames[entry["id"]] = base
            new_rows[entry["id"]] = extra
    framed = [entry for entry in [*plan, *added] if entry["id"] in frames]
    if not framed and not row_frames:
        return list(range(len(lines))), plan

    def inside(frame, line: TextLine) -> bool:
        x0, y0, x1, y1 = frame
        cx = (line.bbox[0] + line.bbox[2]) / 2
        cy = (line.bbox[1] + line.bbox[3]) / 2
        return x0 <= cx <= x1 and y0 <= cy <= y1

    claimed: dict[int, str] = {}
    for entry in framed:
        for i, line in enumerate(lines):
            if i not in claimed and inside(frames[entry["id"]], line):
                claimed[i] = entry["id"]
    row_of: dict[int, str] = {}         # line -> the framed row that took it
    given_up: set[int] = set()          # a framed row's own lines, until a frame takes them again
    dropped: dict[str, list[int]] = {}  # a removed row -> its lines
    for table_id, base in row_frames.items():
        for row_id, members in base:
            if row_id in removed:
                dropped[row_id] = members
                given_up.update(members)
        framing = [(row_id, members) for row_id, members in base
                   if row_id in frames and row_id not in removed]
        framing += [(row_id, []) for row_id in new_rows[table_id] if row_id not in removed]
        for row_id, members in framing:
            given_up.update(members)
            for i, line in enumerate(lines):
                if (i not in row_of and claimed.get(i, table_id) == table_id
                        and inside(frames[row_id], line)):
                    claimed[i] = table_id
                    row_of[i] = row_id
    given_up -= row_of.keys()
    dropped_line = {i: row_id for row_id, members in dropped.items() for i in members
                    if i not in claimed}

    groups: list[tuple[dict, list[int]]] = []
    for row_id, members in dropped.items():
        mine = [i for i in members if dropped_line.get(i) == row_id]
        if mine:
            groups.append(({"kind": "P", "id": row_id, "alt": "", "removed_row": True}, mine))
    for entry in [*plan, *added]:
        own = range(entry["first"], entry["last"] + 1)
        framed_whole = entry["id"] in frames
        members = sorted({*(i for i, owner in claimed.items() if owner == entry["id"]),
                          *(() if framed_whole else
                            (i for i in own if i not in claimed and i not in given_up))})
        if framed_whole:
            # Read as the lines sit inside the frame. Their places in the line order are not to be
            # trusted for this: a line taken from an element that held it wrongly -- a table read
            # across both columns -- has the place that element gave it.
            members = _top_to_bottom(members, lines)
        if members:
            groups.append((entry, members))
        run: list[int] = []
        for i in [*own, None]:
            if (i is not None and i not in claimed and i not in dropped_line
                    and (framed_whole or i in given_up)):
                run.append(i)
                continue
            if run:
                # Named from its first line -- unless an element already has that name (one made
                # of released lines whose frame has since moved off them), then from the next.
                taken = {e["id"] for e in [*plan, *added]}
                name = next((orphan_id(i) for i in run if orphan_id(i) not in taken),
                            orphan_id(run[0]) + "x")
                groups.append(({"kind": "P", "id": name, "alt": "",
                                "_given_up_by": entry["id"]}, run))
                run = []
    # Each element stays where its own first line was; one whose own lines all went elsewhere goes
    # where the first of its new ones was.
    def stands_at(group: tuple[dict, list[int]]) -> int:
        entry, members = group
        mine = [i for i in members if entry.get("first", 0) <= i <= entry.get("last", -1)]
        return min(mine) if mine else min(members)

    groups.sort(key=stands_at)
    groups = _join_given_up(groups, lines)
    placed = _given_up_anchors(groups, lines)
    groups = _follow_anchors(groups, lambda group: group[0]["id"], {**placed, **(after or {})})

    order: list[int] = []
    out: list[dict] = []
    for entry, members in groups:
        start = len(order)
        at = stands_at((entry, members))
        order.extend(members)
        new_entry = {k: v for k, v in entry.items() if k != "_given_up_by"}
        new_entry.update(first=start, last=len(order) - 1, _at=at)
        if entry["id"] in placed:
            new_entry["_after"] = placed[entry["id"]]
        if entry["id"] in row_frames:
            position = {old: start + k for k, old in enumerate(members)}
            original = {i: row_id for row_id, rows in row_frames[entry["id"]] for i in rows}
            by_row: dict[str, list[int]] = {}
            stray: list[int] = []
            for i in members:
                row_id = row_of.get(i) or original.get(i)
                if row_id is None or row_id in removed:
                    stray.append(i)
                else:
                    by_row.setdefault(row_id, []).append(position[i])
            for n, row in enumerate(_table_rows([(i, lines[i]) for i in stray])):
                by_row[f"{entry['id']}s{n}"] = [position[i] for i, _line in row]
            new_entry["rows"] = _follow_anchors(
                sorted(by_row.items(), key=lambda item: -max(
                    lines[order[p]].bbox[3] for p in item[1])),
                lambda item: item[0], after or {})
        out.append(new_entry)
    return order, out


def _top_to_bottom(members: list[int], lines: list[TextLine]) -> list[int]:
    """Line indices in the order they sit on the page: by line, top to bottom, each left to right."""
    return [i for row in _table_rows([(i, lines[i]) for i in members]) for i, _line in row]


def _join_given_up(groups: list[tuple[dict, list[int]]], lines: list[TextLine]
                   ) -> list[tuple[dict, list[int]]]:
    """Join the lines one element gave up into one paragraph wherever they run on down a column.

    Shrinking a table's rows back off the prose beside it gives that prose up a line at a time --
    each row lets go of one line -- and five one-line paragraphs is not what was on the page.
    Runs join when they overlap across and the gap between them is under a line's height.
    """
    out: list[tuple[dict, list[int]]] = []
    for entry, members in groups:
        source = entry.get("_given_up_by")
        if source is not None:
            x0, y0, x1, _y1 = _box(members, lines)
            for k in range(len(out) - 1, -1, -1):
                other, theirs = out[k]
                if other.get("_given_up_by") != source:
                    continue
                ox0, oy0, ox1, oy1 = _box(theirs, lines)
                height = min(lines[i].bbox[3] - lines[i].bbox[1] for i in [*members, *theirs])
                across = min(x1, ox1) - max(x0, ox0)
                gap = max(oy0 - _box(members, lines)[3], _box(members, lines)[1] - oy1)
                if across > 0.5 * min(x1 - x0, ox1 - ox0) and gap < height:
                    out[k] = (other, _top_to_bottom([*theirs, *members], lines))
                    break
            else:
                out.append((entry, members))
            continue
        out.append((entry, members))
    return out


def _given_up_anchors(groups: list[tuple[dict, list[int]]], lines: list[TextLine]
                      ) -> dict[str, str]:
    """Where each run of given-up lines is read: after the nearest element above it in its column.

    A given-up line's own place in the line order is where the page's reading order put it, which
    is wherever the element that held it was -- and an element that held it wrongly (a table read
    across both columns) put it in the wrong column. So it is placed by where it sits instead:
    after the closest element above it that starts in the same column (a heading run across both
    columns does not, so text at the top of the right column is not read before the left one).
    One with nothing above it stays where it was.
    """
    tolerance = 20.0
    anchors: dict[str, str] = {}
    boxes = [(entry["id"], _box(members, lines)) for entry, members in groups]
    for entry, members in groups:
        if "_given_up_by" not in entry:
            continue
        x0, _y0, x1, y1 = _box(members, lines)
        best: tuple[float, str] | None = None
        for other_id, (ox0, oy0, ox1, _oy1) in boxes:
            if other_id == entry["id"] or min(x1, ox1) <= max(x0, ox0):
                continue
            if ox0 < x0 - tolerance or oy0 < y1 - 2.0:
                continue
            if best is None or oy0 < best[0]:
                best = (oy0, other_id)
        if best is not None:
            anchors[entry["id"]] = best[1]
    return anchors


def _follow_anchors(items: list, key, after: dict[str, str]) -> list:
    """`items` with each one named in `after` moved to straight after the item it names.

    One whose anchor is not among `items` stays where it was. Placed one at a time, so an item
    inserted after an item that was itself inserted follows it, and the latest of several
    inserted after the same item comes first -- straight after the item, as it was when made.
    """
    out = [item for item in items if key(item) not in after]
    pending = [item for item in items if key(item) in after]
    while pending:
        placed = False
        for item in list(pending):
            ids = [key(x) for x in out]
            if after[key(item)] in ids:
                out.insert(ids.index(after[key(item)]) + 1, item)
                pending.remove(item)
                placed = True
        if not placed:
            break
    for item in pending:        # anchor gone: back where it would have been
        out.insert(min(len(out), items.index(item)), item)
    return out


def _frame_in_points(frame: tuple[float, float, float, float], width: float,
                     height: float) -> tuple[float, float, float, float]:
    """The editor's (left, top, width, height) page percentages as a PDF-space (x0, y0, x1, y1)."""
    left, top, w, h = frame
    x0 = left * width / 100
    y1 = height - top * height / 100
    return x0, y1 - h * height / 100, x0 + w * width / 100, y1


def _column_measures(lines: list[TextLine], roles: list[str]) -> list[tuple[float, float]]:
    """For each line, the (left, right) margins of the column of body text it sits in.

    The right margin is what tells a last line from a middle one, and it has to come from the text
    itself: nothing in a PDF states where a column ends. It is taken as the furthest right reached
    by more than one nearby line -- a paragraph's own full lines cluster tightly around their true
    margin, so requiring at least two of them to agree is what keeps a single outlier from setting
    it by itself.

    That outlier is not hypothetical: a caption or an "Exhibit N.N" title is commonly set at the
    same left margin as the body column beneath it (ordinary typesetting) while running much wider
    -- to the left-edge grouping in `near`, sharing a margin is all it takes to look like the same
    column. On a real sample (`1087881.pdf` p.8) one such title at x0=86.5 running to x1=506.5 sat
    within PARAGRAPH_COLUMN_TOLERANCE_PT of a paragraph whose own eleven lines all ended near
    x1=306 -- so every one of them read as falling short of a 506.5pt margin that was never really
    its own, and a single paragraph became eleven one-line elements. Requiring company before a
    right edge counts fixes that without weakening the rule for an ordinary paragraph, where
    several lines legitimately reach the true margin together.
    """
    body = [ln for ln, role in zip(lines, roles) if role == "P"]
    out: list[tuple[float, float]] = []
    for line in lines:
        near = [ln for ln in body
                if abs(ln.bbox[0] - line.bbox[0]) <= PARAGRAPH_COLUMN_TOLERANCE_PT]
        if not near:
            out.append((line.bbox[0], line.bbox[2]))
            continue
        rights = sorted((ln.bbox[2] for ln in near), reverse=True)
        measure = next(
            (r for r in rights
             if sum(1 for x in rights if abs(x - r) <= PARAGRAPH_ALIGN_TOLERANCE_PT) >= 2),
            rights[0])   # nothing recurs (too few lines to tell) -- fall back to the widest line
        out.append((min(ln.bbox[0] for ln in near), measure))
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
