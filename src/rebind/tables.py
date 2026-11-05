"""Tag a table as a table: rows, header cells scoped to their column, and a drafted summary."""

from __future__ import annotations

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from .extract import TextLine

# Two cells are in one column when they overlap across by more than this share of the narrower.
COLUMN_OVERLAP_SHARE = 0.25
# A cell covers a column when it takes up this share of the column's width.
COLUMN_COVER_SHARE = 0.5
# Overlapping a column by no more than this is touching it, not reaching into it.
COLUMN_TOUCH_PT = 2.0
# How near a one-cell column must sit to its neighbour, in its own line heights, to be part of it.
LONE_PIECE_GAP = 0.5
# A second row of labels under a row of group labels fills at least this share of the columns.
SECOND_HEADER_FILL = 0.8


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


def _layout(rows: list[list[TextLine]]):
    """A table's columns, each row's cells placed in them, and the top row spread over the columns
    it heads when it is a row of group labels (`_grouped_heading`; None otherwise)."""
    columns = _column_bands([line.bbox for row in rows for line in row])
    placed = [_row_cells([line.bbox for line in row], columns) for row in rows]
    texts = [[" ".join(row[k].text for k in held) for _first, _last, held in cells]
             for row, cells in zip(rows, placed)]
    return columns, placed, _grouped_heading(placed, texts, len(columns))


def default_header_rows(rows: list[list[TextLine]]) -> set[int]:
    """Which rows of a detected table are header rows unless a person says otherwise."""
    return {0, 1} if _layout(rows)[2] else {0}


def _is_numeric(text: str) -> bool:
    """A value rather than a label: mostly digits among its letters and digits."""
    alnum = [ch for ch in text if ch.isalnum()]
    return bool(alnum) and sum(ch.isdigit() for ch in alnum) * 2 >= len(alnum)


def _grouped_heading(placed: list[list[tuple[int, int, list[int]]]], texts: list[list[str]],
                     column_count: int) -> list[tuple[int, int, list[int]]] | None:
    """The top row's cells spread over the columns they head, when the table opens with a row of
    group labels over a row of column labels ("Sleep latency" over Scores, Frequency, Percent).

    That is: a top row with gaps in it, a second row of labels in nearly every column, and values
    below them. A group label is left-aligned over its group, so it covers only the first of its
    columns; it is taken to run on to the next label. Without this the second row, which is the
    one that names each column, was tagged as data, and its columns had no header at all.
    """
    if len(placed) < 3 or column_count < 3:
        return None
    top, second = placed[0], placed[1]
    covered = sum(last - first + 1 for first, last, _held in top)
    if covered >= column_count or len(top) < 2:
        return None
    if sum(last - first + 1 for first, last, _held in second) < SECOND_HEADER_FILL * column_count:
        return None
    if any(_is_numeric(text) for text in texts[1]) or not texts[1]:
        return None
    values = [text for row in texts[2:] for text in row]
    if sum(_is_numeric(text) for text in values) * 2 < len(values):
        return None
    spread = []
    for k, (first, _last, held) in enumerate(top):
        end = top[k + 1][0] - 1 if k + 1 < len(top) else column_count - 1
        spread.append((first, end, held))
    return spread


def _cell_attributes(span: int, scope) -> Dictionary | None:
    """A cell's Table attributes: how many columns it runs over, and what a header cell heads."""
    attributes = Dictionary(O=Name.Table)
    if scope is not None:
        attributes.Scope = scope
    if span > 1:
        attributes.ColSpan = span
    return attributes if scope is not None or span > 1 else None


def _column_bands(boxes: list[tuple]) -> list[tuple[float, float]]:
    """A table's columns, left to right, as the x-ranges its cells occupy.

    Not by left edge. A column of numbers set right-aligned or centred under its heading has no
    left edge in common with it, and clustering on them made a phantom column for each: on real
    journal tables a value sat in a column whose header cell was empty, so a screen reader announced
    no header for it, or the neighbouring one.

    Cells that overlap across are one column, narrowest first so that a heading spanning several
    columns ("Sleep latency" over Scores, Frequency, Percent) meets them already separate. A cell
    reaching over two or more columns says nothing about where any of them is, and is left out.
    """
    bands: list[list[float]] = []
    for x0, _y0, x1, _y1 in sorted(boxes, key=lambda box: box[2] - box[0]):
        met = [band for band in bands if _shares_width((x0, x1), band)]
        # Reaching into a second column by any real amount is spanning it, however little: a heading
        # over two columns may stop just inside the second, and must not stretch the first over it.
        reached = [band for band in bands if min(x1, band[1]) - max(x0, band[0]) > COLUMN_TOUCH_PT]
        if not met and not reached:
            bands.append([x0, x1])
        elif len(met) == 1 and len(reached) <= 1:
            met[0][0], met[0][1] = min(met[0][0], x0), max(met[0][1], x1)
    bands.sort()
    joined: list[list[float]] = []
    for band in bands:
        # Growing can bring two together; a clean gap between columns is what makes them two.
        if joined and _shares_width(band, joined[-1]):
            joined[-1][1] = max(joined[-1][1], band[1])
        else:
            joined.append(band)
    return _without_lone_pieces(joined, boxes)


def _without_lone_pieces(bands: list[list[float]], boxes: list[tuple]) -> list[tuple[float, float]]:
    """Join a one-cell column to its neighbour when it is set closer to it than columns are set
    (`LONE_PIECE_GAP` of its line height, about a word space): a heading broken into two runs of text, not a column ("ΔfH" then "298 (kJ/mol)", on a real table,
    which left the first as a column with nothing under it)."""
    def held(band: list[float]) -> list[tuple]:
        return [box for box in boxes if band[0] <= (box[0] + box[2]) / 2 <= band[1]]

    out = [list(band) for band in bands]
    k = 0
    while k < len(out) - 1:
        left, right = out[k], out[k + 1]
        lone = held(left) if len(held(left)) == 1 else held(right) if len(held(right)) == 1 else []
        other = right if len(held(left)) == 1 else left
        if (lone and len(held(other)) >= 2
                and right[0] - left[1] <= LONE_PIECE_GAP * (lone[0][3] - lone[0][1])):
            out[k:k + 2] = [[left[0], max(left[1], right[1])]]
        else:
            k += 1
    return [(band[0], band[1]) for band in out]


def _shares_width(a, b) -> bool:
    """Whether two x-ranges overlap by more than a sliver of the narrower: neighbouring columns
    routinely touch by a point or two."""
    shared = min(a[1], b[1]) - max(a[0], b[0])
    return shared > COLUMN_OVERLAP_SHARE * max(min(a[1] - a[0], b[1] - b[0]), 1.0)


def _columns_of(x0: float, x1: float, bands: list[tuple[float, float]]) -> tuple[int, int]:
    """The first and last column a cell covers: every one it takes up most of, or failing that the
    one it overlaps most."""
    covered = [c for c, (left, right) in enumerate(bands)
               if min(x1, right) - max(x0, left) >= COLUMN_COVER_SHARE * (right - left)]
    if covered:
        return covered[0], covered[-1]
    best = max(range(len(bands)), key=lambda c: min(x1, bands[c][1]) - max(x0, bands[c][0]))
    return best, best


def _row_cells(boxes: list[tuple], bands: list[tuple[float, float]]) -> list[tuple[int, int, list[int]]]:
    """One row's cells placed in the columns: `(first column, last column, indices of what is in
    it)`, left to right. Whatever lands on the same columns shares a cell, rather than the later
    ones being dropped -- dropping one would leave its content on the page owned by nothing."""
    placed = sorted((*_columns_of(boxes[k][0], boxes[k][2], bands), k) for k in range(len(boxes)))
    out: list[tuple[int, int, list[int]]] = []
    for first, last, k in placed:
        if out and first <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], last), out[-1][2] + [k])
        else:
            out.append((first, last, [k]))
    return out


def _tagged_table(pdf: pikepdf.Pdf, rows: list[tuple[str, list[tuple[int, TextLine]]]],
                  document_elem: pikepdf.Object, page_obj: pikepdf.Object, leaf,
                  row_tags: dict[str, str]) -> pikepdf.Object:
    """Build a fully tagged `/Table` from its rows of cells, top to bottom, each as `(row id,
    [(line index, line)] left to right)` (`_table_row_groups`): a regular grid of `/TR`s whose
    first row is header cells (`/TH` scoped to their column) and the rest data cells (`/TD`) by
    default -- a person can override any row's header/data status per row through the editor
    (`row_tags`, keyed by row id; see `ROW_TAG_KEYS`).

    Cells are placed in the table's columns (`_column_bands`), so every row covers each column once
    -- an empty cell where a value is missing, a /ColSpan where one cell runs over several -- making
    the grid regular, which is what lets assistive technology read a data cell against its column
    header.
    """
    columns, placed, groups = _layout([[line for _mcid, line in row] for _row_id, row in rows])
    defaults = {0, 1} if groups else {0}

    table = pdf.make_indirect(Dictionary(
        Type=Name.StructElem, S=Name.Table, P=document_elem, K=Array([])))
    trs: list[pikepdf.Object] = []
    header_texts: list[str] = []
    row_count = 0
    for row_index, (row_id, row) in enumerate(rows):
        row_count += 1
        tr = pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=Name.TR, P=table, K=Array([])))
        # The default guess -- the first detected row is the header, and the second too under a row
        # of group labels -- unless a person corrected this row through the editor.
        override = row_tags.get(row_id)
        is_header = override == "TH" if override in ("TH", "TD") else row_index in defaults
        cell_type = Name.TH if is_header else Name.TD
        row_cells: list[pikepdf.Object] = []

        def empty_cells(count: int) -> None:
            # An empty cell keeps the grid regular; it holds no content, so no marked content.
            row_cells.extend(pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=cell_type, P=tr, K=Array([]))) for _ in range(count))

        cells = placed[row_index]
        if row_index == 0 and groups and is_header:
            cells = groups
        next_column = 0
        for first, last, held in cells:
            empty_cells(first - next_column)
            members = [row[k] for k in held]
            attributes = _cell_attributes(last - first + 1, Name.Column if is_header else None)
            row_cells.append(leaf([mcid for mcid, _line in members], cell_type, tr,
                                  {"A": attributes} if attributes else None))
            if is_header:
                header_texts.append(
                    " ".join(line.text.strip() for _mcid, line in members).strip())
            next_column = last + 1
        empty_cells(len(columns) - next_column)
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


def _cell_rows(boxes: list[tuple[float, float, float, float]]) -> list[list[int]]:
    """Group cell boxes into rows, top to bottom, each row's indices left to right.

    By vertical overlap rather than by centre: a cell that wraps over three lines sits beside
    one-line cells set level with its top, and their centres are a line or more apart. A cell joins
    the row above it when it shares at least half of the shorter of the two heights with it.
    """
    rows: list[list] = []       # [member indices, bottom, top]
    for i in sorted(range(len(boxes)), key=lambda k: -boxes[k][3]):
        _x0, y0, _x1, y1 = boxes[i]
        if rows:
            members, low, high = rows[-1]
            overlap = min(high, y1) - max(low, y0)
            if overlap >= 0.5 * min(max(y1 - y0, 1.0), max(high - low, 1.0)):
                members.append(i)
                rows[-1][1], rows[-1][2] = min(low, y0), max(high, y1)
                continue
        rows.append([[i], y0, y1])
    return [sorted(members, key=lambda k: boxes[k][0]) for members, _low, _high in rows]


def _cell_table(pdf: pikepdf.Pdf, cells: list[tuple[str, list[int]]], lines: list[TextLine],
                document_elem: pikepdf.Object, leaf) -> pikepdf.Object:
    """A `/Table` built around a run of elements a person marked as cells, `(TH|TD, line indices)`.

    The grid is laid out from where the cells sit: rows by `_cell_rows`, columns by `_column_bands`
    exactly as `_tagged_table` does, and an empty cell wherever a row has nothing in a column, so
    every row is the same width. A header cell is scoped to its column when its whole row is
    headers (the usual header row) and to its row otherwise (a label down the left-hand side).
    """
    boxes = [(min(lines[i].bbox[0] for i in indices), min(lines[i].bbox[1] for i in indices),
              max(lines[i].bbox[2] for i in indices), max(lines[i].bbox[3] for i in indices))
             for _kind, indices in cells]
    columns = _column_bands(boxes)

    table = pdf.make_indirect(Dictionary(
        Type=Name.StructElem, S=Name.Table, P=document_elem, K=Array([])))
    trs: list[pikepdf.Object] = []
    header_texts: list[str] = []
    rows = _cell_rows(boxes)
    for row in rows:
        tr = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.TR, P=table, K=Array([])))
        header_row = all(cells[k][0] == "TH" for k in row)
        scope = Name.Column if header_row else Name.Row
        row_cells: list[pikepdf.Object] = []

        def empty_cells(count: int) -> None:
            row_cells.extend(pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name.TH if header_row else Name.TD, P=tr,
                K=Array([]))) for _ in range(count))

        next_column = 0
        for first, last, held in _row_cells([boxes[k] for k in row], columns):
            empty_cells(first - next_column)
            members = [row[position] for position in held]
            indices = [i for k in members for i in cells[k][1]]
            is_header = any(cells[k][0] == "TH" for k in members)
            attributes = _cell_attributes(last - first + 1, scope if is_header else None)
            row_cells.append(leaf(indices, Name.TH if is_header else Name.TD, tr,
                                  {"A": attributes} if attributes else None))
            if is_header:
                header_texts.append(" ".join(lines[i].text.strip() for i in indices).strip())
            next_column = last + 1
        empty_cells(len(columns) - next_column)
        tr.K = Array(row_cells)
        trs.append(tr)
    table.K = Array(trs)
    summary = _table_summary(len(columns), len(rows), header_texts)
    table.Alt = String(summary)
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


def _table_row_groups(entry: dict, lines: list[TextLine]) -> list[tuple[str, list[int]]]:
    """A table entry's rows, top to bottom, as `(row id, line indices left to right)`.

    The rows a frame settled (`_apply_frames`) when there are any, otherwise the detected ones
    (`_table_rows`), numbered `{table id}r{n}` from the top.
    """
    if "rows" in entry:
        return [(row_id, sorted(members, key=lambda i: lines[i].bbox[0]))
                for row_id, members in entry["rows"]]
    cells = [(i, lines[i]) for i in range(entry["first"], entry["last"] + 1)]
    return [(f"{entry['id']}r{n}", [i for i, _line in row])
            for n, row in enumerate(_table_rows(cells))]
