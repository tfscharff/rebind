"""Tag a table as a table: rows, header cells scoped to their column, and a drafted summary."""

from __future__ import annotations

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from .extract import TextLine
from .layout import COLUMN_ALIGN_TOLERANCE_PT


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


def _tagged_table(pdf: pikepdf.Pdf, rows: list[tuple[str, list[tuple[int, TextLine]]]],
                  document_elem: pikepdf.Object, page_obj: pikepdf.Object, leaf,
                  row_tags: dict[str, str]) -> pikepdf.Object:
    """Build a fully tagged `/Table` from its rows of cells, top to bottom, each as `(row id,
    [(line index, line)] left to right)` (`_table_row_groups`): a regular grid of `/TR`s whose
    first row is header cells (`/TH` scoped to their column) and the rest data cells (`/TD`) by
    default -- a person can override any row's header/data status per row through the editor
    (`row_tags`, keyed by row id; see `ROW_TAG_KEYS`).

    Cells are snapped to document-consistent column positions (their clustered left edges), so every
    row emits one cell per column -- an empty cell where a value is missing -- making the grid
    regular, which is what lets assistive technology read a data cell against its column header.
    """
    columns: list[float] = []
    for x in sorted(line.bbox[0] for _row_id, row in rows for _mcid, line in row):
        if not columns or x - columns[-1] > COLUMN_ALIGN_TOLERANCE_PT:
            columns.append(x)

    def column_of(line: TextLine) -> int:
        return min(range(len(columns)), key=lambda c: abs(line.bbox[0] - columns[c]))

    table = pdf.make_indirect(Dictionary(
        Type=Name.StructElem, S=Name.Table, P=document_elem, K=Array([])))
    trs: list[pikepdf.Object] = []
    header_texts: list[str] = []
    row_count = 0
    for row_index, (row_id, row) in enumerate(rows):
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
        # The default guess -- the first detected row is the header -- unless a person corrected
        # this specific row through the editor (Task 3's row sub-elements).
        override = row_tags.get(row_id)
        is_header = override == "TH" if override in ("TH", "TD") else row_index == 0
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

    The grid is laid out from where the cells sit: rows by `_cell_rows`, columns by clustering left
    edges exactly as `_tagged_table` does, and an empty cell wherever a row has nothing in a column,
    so every row is the same width. A header cell is scoped to its column when its whole row is
    headers (the usual header row) and to its row otherwise (a label down the left-hand side).
    """
    boxes = [(min(lines[i].bbox[0] for i in indices), min(lines[i].bbox[1] for i in indices),
              max(lines[i].bbox[2] for i in indices), max(lines[i].bbox[3] for i in indices))
             for _kind, indices in cells]
    columns: list[float] = []
    for x in sorted(box[0] for box in boxes):
        if not columns or x - columns[-1] > COLUMN_ALIGN_TOLERANCE_PT:
            columns.append(x)

    def column_of(k: int) -> int:
        return min(range(len(columns)), key=lambda c: abs(boxes[k][0] - columns[c]))

    table = pdf.make_indirect(Dictionary(
        Type=Name.StructElem, S=Name.Table, P=document_elem, K=Array([])))
    trs: list[pikepdf.Object] = []
    header_texts: list[str] = []
    rows = _cell_rows(boxes)
    for row in rows:
        tr = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.TR, P=table, K=Array([])))
        by_column: dict[int, list[int]] = {}
        for k in row:
            by_column.setdefault(column_of(k), []).append(k)
        header_row = all(cells[k][0] == "TH" for k in row)
        scope = Name.Column if header_row else Name.Row
        row_cells: list[pikepdf.Object] = []
        for c in range(len(columns)):
            members = by_column.get(c)
            if not members:
                row_cells.append(pdf.make_indirect(Dictionary(
                    Type=Name.StructElem, S=Name.TH if header_row else Name.TD, P=tr,
                    K=Array([]))))
                continue
            # Two cells snapped to one column share it, as in `_tagged_table`: dropping one would
            # leave its content on the page owned by nothing.
            indices = [i for k in members for i in cells[k][1]]
            if any(cells[k][0] == "TH" for k in members):
                row_cells.append(leaf(indices, Name.TH, tr,
                                      {"A": Dictionary(O=Name.Table, Scope=scope)}))
                header_texts.append(" ".join(lines[i].text.strip() for i in indices).strip())
            else:
                row_cells.append(leaf(indices, Name.TD, tr))
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
