# Footnote tag and per-row TH/TD hotkeys

**Status:** designed 2026-08-26. Not yet implemented.

Builds on the editor introduced for figure alt-text and element retagging (`remediate.py`
`EDITABLE_TAGS`/`TAG_KEYS`, `app.py` `job_elements`, `ui.py`'s hotkey palette).

## 1. Problem

The browser editor lets a person retag any element with a single keystroke (`TAG_KEYS` in
`remediate.py`), but two things a librarian needs to fix by hand aren't offered:

1. **Footnotes.** Rebind has no footnote structure type at all. A footnote is currently just a
   paragraph, so a screen reader reads it in place as if it were body text.
2. **Wrong header row.** `_tagged_table` hardcodes `is_header = row_index == 0` — the first
   detected row of a table is always `/TH`, the rest always `/TD`. When that guess is wrong (a
   table with no header row, or one whose header spans two rows), there's no way to correct it:
   the editor only ever sees a table as *one* element covering the whole grid.

## 2. Goal and non-goal

**Goal:** a footnote hotkey usable on any element, and a way to flip a specific row of an
already-detected table between header and data.

**Non-goal: building a table's grid by hand.** For a table `detect_table_lines` missed entirely,
there is no detected grid to correct — drawing one (assigning arbitrary blocks to rows/columns)
is a marquee/grid-editing UI, not a hotkey, and is out of scope here. The existing whole-element
`t` (Table) hotkey already lets a person mark a missed run of lines as a table and get an
auto-built grid; this spec only adds the ability to fix that grid's header row afterward.

## 3. Footnote

`/Note` is PDF 2.0's structure type for footnotes and endnotes (ISO 32000-2 Table 5). It needs no
special construction — like `P`, `H1`–`H6`, `BlockQuote`, `Formula`, `Code`, and `Form`, it takes
its element's marked content directly (`_page_structure`'s generic `else: spanning(kind, ...)`
branch already handles any kind not special-cased above it).

Change:
- `CONTENT_TAGS` in `remediate.py` gains `"Note"`.
- `TAG_KEYS` gains one row: key, label, and explanation ("A footnote or endnote — read separately
  from the body text it annotates, not in the middle of it.").
- Key choice: every mnemonic letter for "footnote" is already taken (`f`→Figure, `n`→NonStruct,
  `t`→Table). Use `v` (unclaimed, arbitrary) — flagged here for a final look during review since
  it's the one non-obvious pick in this spec.

No frontend change beyond what the generic tag-list rendering already does: `Note` shows up in the
palette and the one-keystroke direct-set path exactly like any other content tag.

## 4. Per-row TH/TD toggle

### Data flow today

A `Table` plan entry (`plan_page`) spans a run of lines as one element with one id
(`f"p{page}n{firstLineIndex}"`). The editor (`_element_records`) emits it as a single bbox. Only
inside `_tagged_table`, at PDF-build time, does it split into rows (`_table_rows`) and cells — far
past the point where the editor's per-element retag mechanism (`edits.tags: {id: newKind}`) can
reach.

### Change

**Expose rows as sub-elements.** In `_element_records`, when a plan entry's kind is `"Table"`,
also emit one record per detected row (reusing `_table_rows` on that entry's line range) with:
- `id`: `f"{tableEntry['id']}r{rowIndex}"` — distinct from the table's own id and every line-based
  id, so it can never collide with a whole-element retag.
- `kind`: `"TH"` if `rowIndex == 0` else `"TD"` (today's default), overridden by
  `edits.tags.get(id, kind)` as usual — no new mechanism, the existing `tags` dict already maps
  arbitrary ids to a tag string.
- bbox: the union of that row's lines, same computation `_element_records` already does for
  whole elements.
- `editable: True`, plus a `"row": true` marker the frontend uses to route it to the scoped
  hotkey set instead of the general one (see below).

Row records are additional entries in the `elements` list the editor already receives — they ride
alongside the table's own whole-element record (which keeps its existing hotkeys: retag the whole
table as a paragraph, delete it, etc.) rather than replacing it.

**Read the override back when building.** `_tagged_table` currently decides `is_header` from
`row_index == 0`. It gains a `row_kind: Callable[[int], str]` (or a plain `dict[int, str]`) built
from `edits.tags` keyed the same way (`f"{tableId}r{rowIndex}"`), consulted instead of the
hardcoded check. Default unchanged when no override is present.

**Scope TH/TD out of the general palette.** TH/TD are meaningless outside a table row. Rather than
adding a `scope` field to every `TAG_KEYS` entry, mirror the existing pattern for the `Artifact`
action (`ARTIFACT_KEY`/`ARTIFACT_LABEL`/`ARTIFACT_WHAT`, sent alongside `keys` rather than inside
it): add a second small tuple, `ROW_TAG_KEYS = (("h", "TH", "Header cell", "..."), ("d", "TD",
"Data cell", "..."))`, sent to the frontend as its own `"rowKeys"` field from `job_elements`. In
`ui.py`, the keydown handler and palette builder pick `ed.rowKeys` instead of `ed.allKeys` when
the focused element's `row` flag is set, exactly the way `Figure` already gets its own special
branch in the same keydown handler (`ui.py:1004`). An ordinary paragraph or heading never sees
`h`/`d` as live keys, and a table row never sees the whole-document tag set.

Key choice: `h` (header) / `d` (data) — both free in the *row* scope (the whole-element palette's
`h`... there is no whole-element `h`, and the row scope is a completely separate keymap, so no
collision is possible in either direction).

## 5. Testing

- `remediate.py`: a synthetic table fixture where the header is the *second* row (or where the
  first row should stay data) — apply a row-id tag override, rebuild, assert the resulting `/TR`
  order has `/TH` on the overridden row and `/TD` elsewhere, and that PDF/UA-2 validation still
  passes (`-f ua2`, 0 failures) — the existing table validation test's pattern.
- `remediate.py`: a `Note`-tagged element rebuilds as `/Note` and validates.
- `app.py`/`job_elements`: response includes one row sub-record per detected table row, each with
  a distinct id, and `rowKeys` alongside `keys`.
- No pdf-byte-comparison tests (invariant 3).

## 6. Out of scope (noted, not built)

Manual grid-building for an undetected table, and linking a `/Note` back to its in-body reference
marker (PDF 2.0 supports `/Note`→`/Reference` pairing via `/ID`; Rebind doesn't attempt to find the
reference mark automatically, matching the "never fabricate" invariant — same reasoning that kept
`/TOC` unoffered). Both are candidate follow-ups, not part of this change.
