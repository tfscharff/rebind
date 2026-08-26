# Footnote tag and per-row TH/TD hotkeys Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `Note` (footnote) hotkey to the element editor, and let a person flip a specific
row of an already-detected table between header (`TH`) and data (`TD`) without retagging the
whole table.

**Architecture:** `Note` reuses the existing generic content-tag build path unchanged — one new
entry in two tuples. Table rows are exposed as additional sub-elements in the editor's element
list (their own id, own bbox), editable through a *second*, row-scoped hotkey set the frontend
swaps in only when the focused element is a row — never shown on ordinary elements. The row's
chosen header/data status flows back through the existing `Edits.tags` id→tag map (no new wire
format) into `_tagged_table`, which now reads a per-row override instead of hardcoding "row 0 is
always the header."

**Tech Stack:** Python 3.12 (`uv run pytest`), pikepdf, Starlette (`app.py`), vanilla JS embedded
in `ui.py`.

**Spec:** `docs/superpowers/specs/2026-08-26-footnote-and-row-hotkeys-design.md`

## Global Constraints

- Never fabricate: a row with no override keeps today's default (first row = header) exactly as
  now — this change only adds a way to correct it.
- Every offered tag must produce a PDF/UA-2 compliant document (`-f ua2`, 0 failures) — enforced
  by the existing `test_every_offered_tag_produces_a_conformant_document` parametrization over
  `EDITABLE_TAGS`, which `Note` joins automatically.
- `TH`/`TD` must never be offered as whole-element tags — they are only ever valid as a row
  sub-element's kind. Do not add them to `EDITABLE_TAGS` or `CONTENT_TAGS`.
- No pdf-byte-comparison tests (ADR 0003).
- Always `uv run pytest` / `uv run ruff check .`, never bare `python`/`pytest`.

---

## Task 1: `Note` — a footnote hotkey

**Files:**
- Modify: `src/rebind/remediate.py` (`CONTENT_TAGS` at line 1251, `TAG_KEYS` at line 1268)
- Test: `tests/test_remediate.py`

**Interfaces:**
- Produces: `"Note"` as a member of `CONTENT_TAGS`/`EDITABLE_TAGS`, and a `TAG_KEYS` entry
  `("v", "Note", "Footnote", ...)` — consumed as-is by `app.py`'s `job_elements` (already sends
  the whole `TAG_KEYS` tuple) and by the existing parametrized conformance test.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_remediate.py` (near `test_figure_is_decorative_until_described`, same style):

```python
def test_footnote_tag_builds_as_note(tmp_path: Path, verapdf_exe: Path):
    # /Note is PDF 2.0's structure type for a footnote or endnote -- content read separately from
    # the body text it annotates, not inline with it. It needs no special construction: like P,
    # H1-H6 and BlockQuote, it takes its element's marked content directly.
    from rebind.remediate import Edits
    from rebind.validate import validate_pdf_ua
    from tests.fixtures import born_digital_pdf

    source = born_digital_pdf(
        "<h1>Title</h1><p>Body text.</p><p>1. A footnote at the bottom of the page.</p>",
        tmp_path / "in.pdf")
    plain = remediate(source, tmp_path / "plain.pdf", title="T")
    target = next(e["id"] for e in plain.elements
                  if e["kind"] == "P" and "footnote" in e["text"])

    out = tmp_path / "out.pdf"
    remediate(source, out, title="T", edits=Edits(tags={target: "Note"}))

    with pikepdf.open(out) as pdf:
        notes = [e for e in pdf.Root.StructTreeRoot.K[0].K if str(e.get("/S")) == "/Note"]
        assert len(notes) == 1

    result = validate_pdf_ua(out, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_remediate.py::test_footnote_tag_builds_as_note -v`
Expected: FAIL at `assert len(notes) == 1` — `remediate()` applies `Edits.tags` directly (it
doesn't route through `from_payload`'s allow-list), so the plan entry's `kind` does get set to the
string `"Note"`, but `_page_structure`'s generic branch builds whatever `Name("/" + kind)` says
regardless — the real gap is that `"Note"` isn't a legal choice yet from the app's/editor's point
of view. Confirm the gap directly first:

```python
    from rebind.remediate import CONTENT_TAGS
    assert "Note" in CONTENT_TAGS
```
Add this assertion right after the imports and re-run — it fails now (`CONTENT_TAGS` doesn't
contain `"Note"`), which is the change Step 3 makes.

- [ ] **Step 3: Add `Note` to `CONTENT_TAGS` and `TAG_KEYS`**

In `src/rebind/remediate.py`, change:

```python
CONTENT_TAGS = ("P", "H1", "H2", "H3", "H4", "H5", "H6", "BlockQuote", "Code", "Formula", "Form")
```
to:
```python
CONTENT_TAGS = ("P", "H1", "H2", "H3", "H4", "H5", "H6", "BlockQuote", "Code", "Formula", "Form",
                "Note")
```

And add a row to `TAG_KEYS` (after the `"o", "Form"` row):

```python
    ("o", "Form", "Form field", "An interactive field a reader fills in."),
    ("v", "Note", "Footnote", "A footnote or endnote — read separately from the body text it "
                              "annotates, not in the middle of it."),
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_remediate.py::test_footnote_tag_builds_as_note tests/test_remediate.py::test_every_hotkey_names_a_tag_that_exists "tests/test_remediate.py::test_every_offered_tag_produces_a_conformant_document[Note]" -v`
Expected: all PASS. `test_every_offered_tag_produces_a_conformant_document[Note]` is a new
parametrized case that appears automatically because it iterates `EDITABLE_TAGS`.

- [ ] **Step 5: Commit**

```bash
git add src/rebind/remediate.py tests/test_remediate.py
git commit -m "Add a Note (footnote) hotkey to the element editor"
```

---

## Task 2: Row tag vocabulary and `Edits` acceptance

**Files:**
- Modify: `src/rebind/remediate.py` (`Edits.from_payload` at line 1221, near `ARTIFACT_KEY` at
  line 1296)
- Test: `tests/test_remediate.py`

**Interfaces:**
- Produces: `ROW_TAG_KEYS: tuple[tuple[str, str, str, str], ...]` (same 4-tuple shape as
  `TAG_KEYS`: key, tag, label, what) with two entries, tags `"TH"` and `"TD"`. `ROW_TAGS: tuple`
  of just the tag names, for the `Edits.from_payload` allow-list.
- Consumes: nothing new.
- Consumed later by: Task 3 (`_element_records` default row kind), Task 4 (`_tagged_table`
  override lookup), Task 5 (`app.py` sends `ROW_TAG_KEYS` to the frontend).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_remediate.py`, near `test_every_hotkey_names_a_tag_that_exists`:

```python
def test_row_hotkeys_are_well_formed_and_never_offered_as_whole_element_tags():
    # TH/TD only ever make sense as a row inside an already-tagged Table -- offering them as a
    # whole-element retag would let a bare paragraph become a /TH with no /TR or /Table around it,
    # which fails PDF/UA-2 structurally (Table 5 restricts what may hold a /TH directly).
    from rebind.remediate import EDITABLE_TAGS, ROW_TAG_KEYS

    keys = [key for key, _tag, _label, _what in ROW_TAG_KEYS]
    assert len(keys) == len(set(keys)), "row hotkeys must be unique"
    tags = {tag for _key, tag, _label, _what in ROW_TAG_KEYS}
    assert tags == {"TH", "TD"}
    assert tags.isdisjoint(EDITABLE_TAGS), "TH/TD must never be offered as whole-element tags"
    for key, tag, label, what in ROW_TAG_KEYS:
        assert len(key) == 1, f"{tag}: a hotkey should be one keystroke, got {key!r}"
        assert label and what and what != label, f"{tag}: needs a label and an explanation"


def test_edits_accepts_row_tags_alongside_element_tags():
    from rebind.remediate import Edits

    edits = Edits.from_payload({"tags": {"p1n0": "H2", "p1n0r0": "TH", "p1n0r1": "TD",
                                          "p1n0r2": "NotARealTag"}})
    assert edits.tags == {"p1n0": "H2", "p1n0r0": "TH", "p1n0r1": "TD"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_remediate.py -k "row_hotkeys or accepts_row_tags" -v`
Expected: FAIL — `ROW_TAG_KEYS` does not exist yet (`ImportError`), and the second test drops
`"p1n0r0"`/`"p1n0r1"` because `Edits.from_payload`'s `allowed` set is only `EDITABLE_TAGS`.

- [ ] **Step 3: Add `ROW_TAG_KEYS`/`ROW_TAGS` and widen `Edits.from_payload`**

In `src/rebind/remediate.py`, right after the `ARTIFACT_KEY`/`ARTIFACT_LABEL`/`ARTIFACT_WHAT`
block (after line 1299), add:

```python
# TH/TD are never offered as a whole-element tag (see EDITABLE_TAGS) -- they only mean something
# as a row inside a Table the editor already built. They get their own small keymap, sent to the
# frontend separately (like ARTIFACT_KEY) and swapped in only when the focused element is a row.
ROW_TAG_KEYS = (
    ("h", "TH", "Header cell", "This row labels the columns beneath it — a screen reader reads "
                               "it before each data cell in its column."),
    ("c", "TD", "Data cell", "An ordinary cell of the table, read against its column's header."),
)
ROW_TAGS = tuple(tag for _key, tag, _label, _what in ROW_TAG_KEYS)
```

Then change `Edits.from_payload` (line ~1221-1230):

```python
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
```
to:
```python
    @classmethod
    def from_payload(cls, payload: dict | None) -> Edits:
        payload = payload or {}
        allowed = set(EDITABLE_TAGS) | set(ROW_TAGS)
        return cls(
            tags={str(k): str(v) for k, v in (payload.get("tags") or {}).items() if v in allowed},
            removed={str(v) for v in (payload.get("removed") or [])},
            alts={str(k): str(v).strip() for k, v in (payload.get("alts") or {}).items()
                  if str(v).strip()},
        )
```

`ROW_TAG_KEYS`/`ROW_TAGS` are defined after `Edits` in the file today, but `from_payload` is a
method body evaluated at call time, not at class-definition time, so the forward reference
resolves fine — no need to move either definition. (If ruff or a linter flags it, move the
`ROW_TAG_KEYS`/`ROW_TAGS` block to just above the `Edits` class instead; behavior is identical.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_remediate.py -k "row_hotkeys or accepts_row_tags" -v`
Expected: PASS.

- [ ] **Step 5: Run the full fast suite to check nothing else moved**

Run: `uv run pytest -q`
Expected: PASS (same pass count as before plus the new tests).

- [ ] **Step 6: Commit**

```bash
git add src/rebind/remediate.py tests/test_remediate.py
git commit -m "Add a TH/TD row tag vocabulary, accepted by Edits"
```

---

## Task 3: Expose table rows as editor sub-elements

**Files:**
- Modify: `src/rebind/remediate.py` (`_element_records` at line 1302, its call site at line 2084)
- Test: `tests/test_remediate.py`

**Interfaces:**
- Consumes: `ROW_TAG_KEYS`/`ROW_TAGS` (Task 2), `_table_rows` (existing, line 959), `Edits`
  (existing).
- Produces: `_element_records(src_page, plan, lines, mcid_of, edits)` — note the new required
  `edits` parameter (the function's only call site is updated in the same task, so this is not a
  breaking change to any other caller). For a plan entry with `kind == "Table"`, the returned list
  now also contains one record per detected row, each shaped like every other record plus
  `"row": True`, with `"id"` = `f"{tableEntryId}r{rowIndex}"` and default `"kind"` `"TH"` for row
  0 and `"TD"` otherwise, overridden by `edits.tags.get(row_id, default_kind)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_remediate.py`, near `test_tagged_table_is_pdf_ua_compliant`:

```python
def test_table_rows_are_offered_as_editable_sub_elements(tmp_path: Path):
    # A table's header row is a guess (today: always the first detected row). Exposing each row as
    # its own element -- with its own id and bbox -- is what lets a person correct that guess for
    # one row without retagging the whole table.
    from tests.fixtures import born_digital_pdf_with_table

    source = born_digital_pdf_with_table(tmp_path / "in.pdf")
    result = remediate(source, tmp_path / "out.pdf", title="T")

    table = next(e for e in result.elements if e["kind"] == "Table")
    rows = [e for e in result.elements if e.get("row") and e["id"].startswith(table["id"] + "r")]
    assert len(rows) == 4, rows          # header + 3 data rows, per born_digital_pdf_with_table
    assert rows[0]["id"] == f"{table['id']}r0"
    assert rows[0]["kind"] == "TH"
    assert [r["kind"] for r in rows[1:]] == ["TD", "TD", "TD"]
    for row in rows:
        assert row["editable"] is True
        # A row's box sits inside the table's box -- it did not escape onto some other part of the
        # page.
        assert table["top"] <= row["top"] <= row["top"] + row["height"] <= table["top"] + table["height"] + 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_remediate.py::test_table_rows_are_offered_as_editable_sub_elements -v`
Expected: FAIL — `rows` is empty (`_element_records` doesn't emit row sub-records yet), and
separately `_element_records`'s call site doesn't pass `edits` yet so this step will also surface
a `TypeError` once Step 3 changes the signature but before the call site is updated — do Step 3
and the call-site fix together, then re-run.

- [ ] **Step 3: Emit row sub-records from `_element_records`, and update its call site**

In `src/rebind/remediate.py`, change the function signature and body (line 1302-1325):

```python
def _element_records(src_page, plan: list[dict], lines: list[TextLine],
                     mcid_of: list[int | None], edits: Edits) -> list[dict]:
    """One record per element for the app's editor: what it is, where it is, what it says.

    A `Table` entry also yields one sub-record per detected row -- its own id and bbox -- so a
    row's header/data status can be corrected without retagging the whole table (`ROW_TAG_KEYS`).
    A row's id is derived from the table's, never from a source line, so it can never collide with
    a top-level element's id.
    """
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
        if entry["kind"] == "Table":
            cells = [(i, lines[i]) for i in range(first, last + 1)]
            for row_index, row in enumerate(_table_rows(cells)):
                row_lines = [line for _i, line in row]
                rbox = (min(ln.bbox[0] for ln in row_lines), min(ln.bbox[1] for ln in row_lines),
                        max(ln.bbox[2] for ln in row_lines), max(ln.bbox[3] for ln in row_lines))
                row_id = f"{entry['id']}r{row_index}"
                default_kind = "TH" if row_index == 0 else "TD"
                out.append({
                    "id": row_id,
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
```

And its call site (line 2084):

```python
        records = _element_records(src_page, plan, content_lines, mcid_of)
```
becomes:
```python
        records = _element_records(src_page, plan, content_lines, mcid_of, edits)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_remediate.py::test_table_rows_are_offered_as_editable_sub_elements -v`
Expected: PASS.

- [ ] **Step 5: Run the full fast suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/rebind/remediate.py tests/test_remediate.py
git commit -m "Expose a table's rows as editable sub-elements"
```

---

## Task 4: Apply a row override when building the table

**Files:**
- Modify: `src/rebind/remediate.py` (`_tagged_table` at line 983, `_page_structure` at line 1487
  and its `Table` branch at line 1528, `remediate()`'s call to `_page_structure` at line 2153)
- Test: `tests/test_remediate.py`

**Interfaces:**
- Consumes: `ROW_TAGS`/row id scheme from Tasks 2–3 (`f"{tableEntryId}r{rowIndex}"`).
- Produces: `_tagged_table(pdf, cells, document_elem, page_obj, leaf, table_id, row_tags)` — two
  new required parameters. `_page_structure(pdf, lines, plan, mcid_of, document_elem, page_obj,
  caption_hosts=None, edits=None)` — one new optional parameter (optional because
  `test_table_cells_sharing_a_column_are_all_owned` calls it directly without one).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_remediate.py`, near `test_tagged_table_is_pdf_ua_compliant`:

```python
def test_a_row_can_be_promoted_to_header_by_id(tmp_path: Path, verapdf_exe: Path):
    # Rebind's default guess -- the first detected row is the header -- is sometimes wrong (a
    # table with no header row at all, or one whose header is really its second row). The row
    # sub-elements from Task 3 exist so a person can correct exactly one row without retagging the
    # whole table.
    from rebind.remediate import Edits
    from rebind.validate import validate_pdf_ua
    from tests.fixtures import born_digital_pdf_with_table

    source = born_digital_pdf_with_table(tmp_path / "in.pdf")
    plain = remediate(source, tmp_path / "plain.pdf", title="T")
    table = next(e for e in plain.elements if e["kind"] == "Table")

    out = tmp_path / "out.pdf"
    # Swap the header from row 0 to row 1 ("North" becomes the header row instead of "Region").
    result = remediate(source, out, title="T", edits=Edits(
        tags={f"{table['id']}r0": "TD", f"{table['id']}r1": "TH"}))

    rows = [e for e in result.elements if e.get("row") and e["id"].startswith(table["id"] + "r")]
    assert [r["kind"] for r in rows] == ["TD", "TH", "TD", "TD"]

    with pikepdf.open(out) as pdf:
        tbl = next(e for e in pdf.Root.StructTreeRoot.K[0].K if str(e.get("/S")) == "/Table")
        trs = list(tbl.K)
        first_row_cell_types = {str(c.get("/S")) for c in trs[0].K}
        second_row_cell_types = {str(c.get("/S")) for c in trs[1].K}
        assert first_row_cell_types == {"/TD"}
        assert second_row_cell_types == {"/TH"}

    result_report = validate_pdf_ua(out, verapdf_exe=verapdf_exe)
    assert result_report.compliant, result_report.summary()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_remediate.py::test_a_row_can_be_promoted_to_header_by_id -v`
Expected: FAIL — `trs[0]` is still all `/TH` and `trs[1]` all `/TD` (the hardcoded
`row_index == 0` default), so `first_row_cell_types == {"/TD"}` fails.

- [ ] **Step 3: Read a row override in `_tagged_table`**

In `src/rebind/remediate.py`, change the signature (line 983-984):

```python
def _tagged_table(pdf: pikepdf.Pdf, cells: list[tuple[int, TextLine]],
                  document_elem: pikepdf.Object, page_obj: pikepdf.Object, leaf) -> pikepdf.Object:
```
to:
```python
def _tagged_table(pdf: pikepdf.Pdf, cells: list[tuple[int, TextLine]],
                  document_elem: pikepdf.Object, page_obj: pikepdf.Object, leaf,
                  table_id: str, row_tags: dict[str, str]) -> pikepdf.Object:
```

And inside the row loop (line 1005-1017), change:

```python
    for row_index, row in enumerate(_table_rows(cells)):
        row_count += 1
        tr = pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=Name.TR, P=table, K=Array([])))
        by_column: dict[int, list[tuple[int, TextLine]]] = {}
        for mcid, line in row:
            by_column.setdefault(column_of(line), []).append((mcid, line))
        is_header = row_index == 0
        cell_type = Name.TH if is_header else Name.TD
```
to:
```python
    for row_index, row in enumerate(_table_rows(cells)):
        row_count += 1
        tr = pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=Name.TR, P=table, K=Array([])))
        by_column: dict[int, list[tuple[int, TextLine]]] = {}
        for mcid, line in row:
            by_column.setdefault(column_of(line), []).append((mcid, line))
        # The default guess -- the first detected row is the header -- unless a person corrected
        # this specific row through the editor (Task 3's row sub-elements).
        override = row_tags.get(f"{table_id}r{row_index}")
        is_header = override == "TH" if override in ("TH", "TD") else row_index == 0
        cell_type = Name.TH if is_header else Name.TD
```

- [ ] **Step 4: Thread the override through `_page_structure` and its caller**

Change `_page_structure`'s signature (line 1487-1490):

```python
def _page_structure(pdf: pikepdf.Pdf, lines: list[TextLine], plan: list[dict],
                    mcid_of: list[int | None],
                    document_elem: pikepdf.Object, page_obj: pikepdf.Object,
                    caption_hosts: list | None = None):
```
to:
```python
def _page_structure(pdf: pikepdf.Pdf, lines: list[TextLine], plan: list[dict],
                    mcid_of: list[int | None],
                    document_elem: pikepdf.Object, page_obj: pikepdf.Object,
                    caption_hosts: list | None = None, edits: Edits | None = None):
```

And its `Table` branch (line 1528-1530):

```python
        if kind == "Table":
            tops.append(_tagged_table(pdf, [(i, lines[i]) for i in indices],
                                      document_elem, page_obj, leaf))
```
to:
```python
        if kind == "Table":
            row_tags = edits.tags if edits else {}
            tops.append(_tagged_table(pdf, [(i, lines[i]) for i in indices],
                                      document_elem, page_obj, leaf, entry["id"], row_tags))
```

Finally, `remediate()`'s call to `_page_structure` (line 2153-2155):

```python
        tops, owner_of_mcid = _page_structure(pdf, content_lines, plan, mcid_of,
                                              document_elem, page.obj,
                                              caption_hosts=figure_elems)
```
to:
```python
        tops, owner_of_mcid = _page_structure(pdf, content_lines, plan, mcid_of,
                                              document_elem, page.obj,
                                              caption_hosts=figure_elems, edits=edits)
```

- [ ] **Step 5: Fix the other direct caller of `_tagged_table`/`_page_structure`**

`test_table_cells_sharing_a_column_are_all_owned` (line ~1081-1105) calls `_page_structure`
directly without `edits` — it will still work unchanged since `edits` now defaults to `None` and
`_page_structure` falls back to `row_tags = {}`. No change needed there. Run it to confirm:

Run: `uv run pytest tests/test_remediate.py::test_table_cells_sharing_a_column_are_all_owned -v`
Expected: PASS, unchanged.

- [ ] **Step 6: Run the new test to verify it passes**

Run: `uv run pytest tests/test_remediate.py::test_a_row_can_be_promoted_to_header_by_id -v`
Expected: PASS.

- [ ] **Step 7: Run the full fast suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/rebind/remediate.py tests/test_remediate.py
git commit -m "Let a table row's header/data status be corrected by id"
```

---

## Task 5: Serve `rowKeys` from the app

**Files:**
- Modify: `src/rebind/app.py` (`job_elements`, around line 225-242)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `ROW_TAG_KEYS` (Task 2).
- Produces: the `/jobs/{id}/elements` JSON response gains a `"rowKeys"` array, same shape as
  `"keys"` (`{"key", "tag", "label", "what"}`).

- [ ] **Step 1: Write the failing test**

Extend `tests/test_app.py::test_the_page_editor_lists_elements_and_applies_corrections` — replace
its source fixture with a table so row sub-elements and `rowKeys` are exercised in the same
end-to-end test. Change the test body (lines 39-83) to add, right after the existing `keys`
assertions (after line 67), before the `for element in body["elements"]:` loop:

```python
    row_keys = {entry["key"] for entry in body["rowKeys"]}
    assert row_keys == {"h", "c"}
    assert {entry["tag"] for entry in body["rowKeys"]} == {"TH", "TD"}
    assert row_keys.isdisjoint(keys), "row hotkeys must not collide with the whole-element ones"
```

Also add a second, focused test in the same file for the table-specific shape:

```python
def test_table_rows_reach_the_editor_with_their_own_ids(tmp_path: Path):
    from tests.fixtures import born_digital_pdf_with_table

    source = born_digital_pdf_with_table(tmp_path / "in.pdf")
    client = TestClient(create_app())
    job_id = client.post("/convert?filename=in.pdf", content=source.read_bytes()).json()["job_id"]
    assert _run(client, job_id)["status"] == "done"

    body = client.get(f"/jobs/{job_id}/elements").json()
    table = next(e for e in body["elements"] if e["kind"] == "Table")
    rows = [e for e in body["elements"] if e.get("row")]
    assert len(rows) == 4
    assert all(r["id"].startswith(table["id"] + "r") for r in rows)

    client.post(f"/jobs/{job_id}/edits",
                json={"tags": {f"{table['id']}r0": "TD", f"{table['id']}r1": "TH"}})
    status = _run(client, job_id)
    assert status["status"] == "done", status.get("error")

    after_rows = {e["id"]: e["kind"]
                  for e in client.get(f"/jobs/{job_id}/elements").json()["elements"]
                  if e.get("row")}
    assert after_rows[f"{table['id']}r0"] == "TD"
    assert after_rows[f"{table['id']}r1"] == "TH"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app.py -k "editor_lists_elements or rows_reach_the_editor" -v`
Expected: FAIL — `body["rowKeys"]` raises `KeyError`.

- [ ] **Step 3: Send `rowKeys` from `job_elements`**

In `src/rebind/app.py`, change the import (around line 225-231):

```python
        from rebind.remediate import (
            ARTIFACT_KEY,
            ARTIFACT_LABEL,
            ARTIFACT_WHAT,
            EDITABLE_TAGS,
            TAG_KEYS,
        )
```
to:
```python
        from rebind.remediate import (
            ARTIFACT_KEY,
            ARTIFACT_LABEL,
            ARTIFACT_WHAT,
            EDITABLE_TAGS,
            ROW_TAG_KEYS,
            TAG_KEYS,
        )
```

And the returned `JSONResponse` (line 233-242):

```python
        return JSONResponse({
            "elements": job.elements, "pages": job.page_images,
            "tags": list(EDITABLE_TAGS), "edits": job.edits,
            "keys": [{"key": key, "tag": tag, "label": label, "what": what}
                     for key, tag, label, what in TAG_KEYS],
            # Sent alongside rather than among them: taking an element out of the reading order is
            # an action, not one more type to choose between.
            "artifact": {"key": ARTIFACT_KEY, "tag": "Artifact", "label": ARTIFACT_LABEL,
                         "what": ARTIFACT_WHAT},
        })
```
to:
```python
        return JSONResponse({
            "elements": job.elements, "pages": job.page_images,
            "tags": list(EDITABLE_TAGS), "edits": job.edits,
            "keys": [{"key": key, "tag": tag, "label": label, "what": what}
                     for key, tag, label, what in TAG_KEYS],
            # Sent alongside rather than among them: taking an element out of the reading order is
            # an action, not one more type to choose between.
            "artifact": {"key": ARTIFACT_KEY, "tag": "Artifact", "label": ARTIFACT_LABEL,
                         "what": ARTIFACT_WHAT},
            # A second, small keymap -- TH/TD only mean something on a table's row sub-elements
            # (remediate._element_records), so they are never mixed into the general "keys" list a
            # librarian sees on an ordinary paragraph.
            "rowKeys": [{"key": key, "tag": tag, "label": label, "what": what}
                        for key, tag, label, what in ROW_TAG_KEYS],
        })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_app.py -k "editor_lists_elements or rows_reach_the_editor" -v`
Expected: PASS.

- [ ] **Step 5: Run the full fast suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/rebind/app.py tests/test_app.py
git commit -m "Serve TH/TD row hotkeys alongside the element editor"
```

---

## Task 6: Wire the row hotkeys into the browser editor

**Files:**
- Modify: `src/rebind/ui.py` (state init ~line 415-417, `loadEditor` ~765-771, `keyFor` ~788-792,
  `showType` ~852-874, `wireStage`'s keydown handler ~962-1019, `openPalette` ~1028-1063)

**Interfaces:**
- Consumes: `d.rowKeys` from the `/jobs/{id}/elements` response (Task 5), `e.row` on an element
  record (Task 3).
- Produces: `ed.rowKeys` (state), `keysFor(e)` (new helper: `ed.rowKeys` for a row element,
  `ed.allKeys` otherwise) — used everywhere a hotkey list is walked.

There is no JS test harness in this codebase (`ui.py` is Python-templated HTML/JS, exercised only
through the Starlette app tests already covering the data side in Task 5). This task is verified
by the manual walkthrough in Step 8, matching how every other purely-frontend change to `ui.py`
in this project has been checked (`test_ui.py` covers server-rendered content and flow endpoints,
not in-browser keystrokes).

- [ ] **Step 1: Add `rowKeys` to editor state**

In `src/rebind/ui.py`, change (line 414-417):

```javascript
  var ed={id:null,name:null,elements:[],pages:{},tags:[],keys:[],page:1,pageList:[],
          tags_edit:{},removed:{},alts:{},focused:null,figures:[],checks:[],status:null,
          palette:false,walked:{},artifact:null,allKeys:[]};
```
to:
```javascript
  var ed={id:null,name:null,elements:[],pages:{},tags:[],keys:[],page:1,pageList:[],
          tags_edit:{},removed:{},alts:{},focused:null,figures:[],checks:[],status:null,
          palette:false,walked:{},artifact:null,allKeys:[],rowKeys:[]};
```

- [ ] **Step 2: Populate it in `loadEditor`**

Change (line 765-771):

```javascript
      ed.elements=d.elements||[]; ed.pages=d.pages||{}; ed.tags=d.tags||[]; ed.keys=d.keys||[];
      // "Not read" is an action, not a type, so it arrives separately -- but it answers to a key
      // exactly like the types do, so the editor holds them in one list for lookup.
      ed.artifact=d.artifact||null;
      ed.allKeys=ed.artifact? ed.keys.concat([ed.artifact]) : ed.keys;
```
to:
```javascript
      ed.elements=d.elements||[]; ed.pages=d.pages||{}; ed.tags=d.tags||[]; ed.keys=d.keys||[];
      ed.rowKeys=d.rowKeys||[];
      // "Not read" is an action, not a type, so it arrives separately -- but it answers to a key
      // exactly like the types do, so the editor holds them in one list for lookup.
      ed.artifact=d.artifact||null;
      ed.allKeys=ed.artifact? ed.keys.concat([ed.artifact]) : ed.keys;
```

- [ ] **Step 3: Make `keyFor` find a row tag's label too, and add `keysFor(e)`**

Change (line 788-792):

```javascript
  function keyFor(tag){
    var found=null;
    ed.allKeys.forEach(function(k){ if(k.tag===tag) found=k; });
    return found;
  }
```
to:
```javascript
  function keyFor(tag){
    var found=null;
    // TH/TD live in a separate keymap from everything else, but a row's label still has to be
    // found by tag name wherever any element's label is looked up (showType, boxHtml) -- the two
    // vocabularies never share a tag name, so searching both together is safe.
    ed.allKeys.concat(ed.rowKeys).forEach(function(k){ if(k.tag===tag) found=k; });
    return found;
  }

  // Which keymap answers to the keyboard on this element: the row-only one for a table row's
  // sub-element, the general one for everything else. A table row never sees "make this a
  // Division"; an ordinary paragraph never sees "make this a header cell".
  function keysFor(e){
    return e.row? ed.rowKeys : ed.allKeys;
  }
```

- [ ] **Step 4: Don't offer Add/Remove on a row**

A row isn't independently removable from the reading order (only the whole table is), so the
add/remove pair — meaningless for a row — is left out entirely rather than shown disabled.
Change `showType` (line 864-874):

```javascript
    var out=(k==='Artifact');
    h+='<div class="addrem">'+
      '<button type="button" class="btn ghost small" id="addel"'+(out?'':' disabled')+
      ' title="Add this to the reading order"><b>+</b> Add</button>'+
      '<button type="button" class="btn ghost small" id="delel"'+(out?' disabled':'')+
      ' title="Take this out of the reading order"><b>−</b> Remove</button>'+
      '<span class="hint">'+(out? 'Not read. + puts it into the reading order.'
                                : 'In the reading order. − takes it out.')+'</span></div>';
```
to:
```javascript
    var out=(k==='Artifact');
    if(!e.row){
      h+='<div class="addrem">'+
        '<button type="button" class="btn ghost small" id="addel"'+(out?'':' disabled')+
        ' title="Add this to the reading order"><b>+</b> Add</button>'+
        '<button type="button" class="btn ghost small" id="delel"'+(out?' disabled':'')+
        ' title="Take this out of the reading order"><b>−</b> Remove</button>'+
        '<span class="hint">'+(out? 'Not read. + puts it into the reading order.'
                                  : 'In the reading order. − takes it out.')+'</span></div>';
    }
```

(The existing `document.getElementById('addel')`/`'delel'` wiring just below is already
null-safe — `if(add) add.addEventListener(...)` — so no further change is needed there.)

- [ ] **Step 5: Scope the +/− hotkeys and the direct-set hotkey lookup to skip rows**

Change the keydown handler (line 988-990 and 1014-1018):

```javascript
        // The two edits that are not "what is this?", on the obvious pair of keys.
        if(key==='+'||key==='='){ ev.preventDefault(); addElement(e.id); return; }
        if(key==='-'||key==='_'){ ev.preventDefault(); setKind(e.id, 'Artifact'); return; }
```
to:
```javascript
        // The two edits that are not "what is this?", on the obvious pair of keys -- meaningless
        // on a table row, which is never independently added or removed (see showType).
        if(!e.row && (key==='+'||key==='=')){ ev.preventDefault(); addElement(e.id); return; }
        if(!e.row && (key==='-'||key==='_')){ ev.preventDefault(); setKind(e.id, 'Artifact'); return; }
```

And (line 1014-1018):

```javascript
        // The key sets the type straight away. Enter is only for when you cannot remember which
        // key you want; knowing it should never cost you a menu.
        var hit=null;
        ed.allKeys.forEach(function(k){ if(k.key===key.toLowerCase()) hit=k.tag; });
        if(hit){ ev.preventDefault(); setKind(e.id, hit); }
```
to:
```javascript
        // The key sets the type straight away. Enter is only for when you cannot remember which
        // key you want; knowing it should never cost you a menu. A row answers to its own, smaller
        // keymap (keysFor) rather than the whole document's.
        var hit=null;
        keysFor(e).forEach(function(k){ if(k.key===key.toLowerCase()) hit=k.tag; });
        if(hit){ ev.preventDefault(); setKind(e.id, hit); }
```

- [ ] **Step 6: Scope the Enter-key palette to the same keymap**

Change `openPalette` (line 1027-1063):

```javascript
  function openPalette(elementId){
    closePalette();
    var e=null;
    ed.elements.forEach(function(x){ if(x.id===elementId) e=x; });
    if(!e) return;
    var current=kindOf(e);
    var host=document.createElement('div');
    host.className='palette';
    host.id='palette';
    host.setAttribute('role','dialog');
    host.setAttribute('aria-modal','true');
    host.setAttribute('aria-label','Change what this element is');
    host.innerHTML='<div class="card"><h2>What is this?</h2>'+
      '<p class="sub">Press a key. Esc leaves it as it is.</p><ul class="keys">'+
      ed.allKeys.map(function(k){
        return '<li'+(k.tag===current?' class="current"':'')+
          (k.tag==='Artifact'?' class="action"':'')+'><kbd>'+esc(k.key)+'</kbd>'+
          '<span><span class="lab">'+esc(k.label)+'</span><br>'+
          '<span class="what">'+esc(k.what||'')+'</span></span></li>';
      }).join('')+'</ul></div>';
    document.body.appendChild(host);
    ed.palette=true;
    host.tabIndex=-1;
    host.focus();
    host.addEventListener('keydown',function(ev){
      if(ev.ctrlKey||ev.metaKey||ev.altKey) return;
      if(ev.key==='Escape'){ ev.preventDefault(); closePalette(); focusBox(elementId); return; }
      var pressed=(ev.key||'').toLowerCase();
      var hit=null;
      ed.allKeys.forEach(function(k){ if(k.key===pressed) hit=k.tag; });
      if(hit){ ev.preventDefault(); closePalette(); setKind(elementId, hit); }
    });
    host.addEventListener('click',function(ev){
      if(ev.target===host){ closePalette(); focusBox(elementId); }
    });
  }
```
to:
```javascript
  function openPalette(elementId){
    closePalette();
    var e=null;
    ed.elements.forEach(function(x){ if(x.id===elementId) e=x; });
    if(!e) return;
    var current=kindOf(e);
    var keys=keysFor(e);
    var host=document.createElement('div');
    host.className='palette';
    host.id='palette';
    host.setAttribute('role','dialog');
    host.setAttribute('aria-modal','true');
    host.setAttribute('aria-label','Change what this element is');
    host.innerHTML='<div class="card"><h2>What is this?</h2>'+
      '<p class="sub">Press a key. Esc leaves it as it is.</p><ul class="keys">'+
      keys.map(function(k){
        return '<li'+(k.tag===current?' class="current"':'')+
          (k.tag==='Artifact'?' class="action"':'')+'><kbd>'+esc(k.key)+'</kbd>'+
          '<span><span class="lab">'+esc(k.label)+'</span><br>'+
          '<span class="what">'+esc(k.what||'')+'</span></span></li>';
      }).join('')+'</ul></div>';
    document.body.appendChild(host);
    ed.palette=true;
    host.tabIndex=-1;
    host.focus();
    host.addEventListener('keydown',function(ev){
      if(ev.ctrlKey||ev.metaKey||ev.altKey) return;
      if(ev.key==='Escape'){ ev.preventDefault(); closePalette(); focusBox(elementId); return; }
      var pressed=(ev.key||'').toLowerCase();
      var hit=null;
      keys.forEach(function(k){ if(k.key===pressed) hit=k.tag; });
      if(hit){ ev.preventDefault(); closePalette(); setKind(elementId, hit); }
    });
    host.addEventListener('click',function(ev){
      if(ev.target===host){ closePalette(); focusBox(elementId); }
    });
  }
```

- [ ] **Step 7: Run the full fast suite**

Run: `uv run pytest -q`
Expected: PASS — `ui.py` has no direct unit tests for this logic, but `test_ui.py`'s
`test_convert_flow_end_to_end` and `test_index_page_serves_accessible_html` still exercise that
the page is served without a Python-side error, and Task 5's app tests cover the data this JS
consumes.

- [ ] **Step 8: Manual verification**

Use the `run` skill (or `uv run rebind serve`) to launch the app, convert a PDF containing a table
(e.g. build one with `born_digital_pdf_with_table` via a throwaway script, or use a real sample
from `samples/` if one is on hand), and in the browser:
1. Tab to a `P` element — confirm `v` retags it `Note` (footnote), shown in the palette (Enter)
   and directly.
2. Tab into the table — confirm you land on the whole table first, then on each row in turn, each
   row's box a thin band within the table's own box.
3. On a row, press `h` — confirm it becomes "Header cell" (`TH`); press `c` — confirm it becomes
   "Data cell" (`TD`). Confirm `+`/`-`/`x` do nothing on a row (no add/remove control shown).
4. Confirm a row never shows `p`, `1`, `t`, etc. in its palette (Enter) — only Header cell/Data
   cell.
5. Let the job finish rebuilding and confirm the checklist still reports the document as PDF/UA
   compliant.

- [ ] **Step 9: Commit**

```bash
git add src/rebind/ui.py
git commit -m "Wire footnote and per-row TH/TD hotkeys into the browser editor"
```

---

## Task 7: Update the README

**Files:**
- Modify: `README.md` (wherever the editor's hotkeys/tag types are currently described — check
  before writing new text, since the exact section heading isn't pinned here)

- [ ] **Step 1: Find the relevant section**

Run: `grep -n "hotkey\|Table\|BlockQuote\|Figure" README.md`

- [ ] **Step 2: Add a line each for Footnote and for the row-level TH/TD correction**, matching
  the tone and format of whatever is already there for the other tag types.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document the footnote hotkey and per-row header correction"
```
