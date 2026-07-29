# Layout & Reading Order Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the born-digital branch's naive per-page line sort with recursive XY-cut layout analysis, so multi-column pages are segmented into columns and blocks and emitted in correct reading order.

**Architecture:** A new `layout.py` stage runs per page between `profile` and `assemble`. It holds artifact lines out of the cut (identified via the profile), runs recursive XY-cut over the body lines to build an intermediate region tree, and returns the lines in depth-first reading order tagged with a column index. `assemble.py` loses its naive sort and its `_looks_multi_column` heuristic and becomes a consumer of that ordered stream.

**Tech Stack:** Python 3.12 via `uv`; pytest; pure-geometry XY-cut (no new dependency).

## Global Constraints

- **Python 3.12 via uv only** — always `uv run pytest`, never bare `python`/`pytest`.
- **Determinism scoped to the model** — XY-cut must be deterministic; golden tests assert model JSON, never PDF bytes (ADR 0003).
- **Never fabricate** — reading order is derived from geometry only; a marginal cut is flagged, never guessed past.
- **Everything has provenance** — every node keeps page + bbox; multi-column body nodes additionally carry a `column-{n}` flag.
- **No new dependency** — pure geometry over the line boxes `extract.py` already yields.
- **Confidence contract unchanged** — `confidence` stays style-match cleanliness only; reading-order uncertainty is a flag, never folded into the number.
- **Named constants, not magic numbers** — `GUTTER_MIN_FRACTION`, `GUTTER_MIN_HEIGHT_FRACTION`, `BLOCK_GAP_MIN_FRACTION`, `GUTTER_MARGINAL_FRACTION`.
- **Coordinates:** bbox is `(x0, y0, x1, y1)` in PDF points, y-axis up — top-to-bottom order is *descending* y (larger `y1` first), matching `assemble`'s existing `-line.bbox[3]` sort key.

## File Structure

- **Create `src/rebind/layout.py`** — XY-cut, region tree, `order_page`. One responsibility: turn a page's lines into reading order.
- **Create `tests/test_layout.py`** — unit tests over synthetic `TextLine` lists (no PDF).
- **Modify `src/rebind/assemble.py`** — consume `PageLayout`; delete `_horizontal_clusters`, `_vertical_overlap_fraction`, `_looks_multi_column` and their constants.
- **Modify `src/rebind/pipeline.py`** — call `layout.order_page` and pass the result into `assemble`.
- **Modify `tests/` round-trip + golden tests** — the two-column fixture must now assert correct interleaved order.

---

### Task 1: XY-cut single column + reading order

**Files:**
- Create: `src/rebind/layout.py`
- Test: `tests/test_layout.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class PlacedLine: line: TextLine; column: int`
  - `@dataclass class Region: bbox: BBox; kind: str; children: list["Region"]; lines: list[TextLine]` (`kind` in `"page"|"column"|"block"`; a leaf block has `children == []` and `lines` populated; a split node has `lines == []`)
  - `_xy_cut(lines: list[TextLine], bbox: BBox) -> Region`
  - `_reading_order(region: Region) -> list[PlacedLine]`

Use `TextLine` from `.extract` and `BBox` from `.model`. A `TextLine` has `.bbox == (x0, y0, x1, y1)`, `.text`, `.page`. Top-to-bottom is descending `y1`.

- [ ] **Step 1: Write the failing test**

```python
from rebind.extract import TextLine
from rebind.layout import _xy_cut, _reading_order


def _line(x0, y0, x1, y1, text):
    return TextLine(text=text, bbox=(x0, y0, x1, y1), font="Times", size=10.0,
                    bold=False, italic=False, page=1)


def test_single_column_reads_top_to_bottom():
    # Three stacked lines, no gutter. y-up: the highest y is first.
    lines = [_line(72, 700, 500, 710, "top"),
             _line(72, 680, 500, 690, "middle"),
             _line(72, 660, 500, 670, "bottom")]
    region = _xy_cut(lines, (72, 660, 500, 710))
    placed = _reading_order(region)
    assert [p.line.text for p in placed] == ["top", "middle", "bottom"]
    assert {p.column for p in placed} == {0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_layout.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rebind.layout'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Layout analysis: recursive XY-cut turns a page's lines into reading order.

Pure geometry over the extracted line boxes -- no ML, no new dependency, deterministic. The
region tree is an intermediate representation and is never added to the document model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .extract import TextLine
from .model import BBox

# Named thresholds -- fractions of the CURRENT region's box, so they behave the same at any page
# size or recursion depth. Expected to need tuning against the 1905 bulletin; that is planned work.
GUTTER_MIN_FRACTION = 0.05          # a column gutter must be this wide (fraction of region width)
GUTTER_MIN_HEIGHT_FRACTION = 0.5    # ...and span this fraction of region height
BLOCK_GAP_MIN_FRACTION = 0.02       # a block break must be this tall (fraction of region height)
GUTTER_MARGINAL_FRACTION = 0.07     # a gutter narrower than this (but >= MIN) is a marginal cut


@dataclass(frozen=True)
class PlacedLine:
    line: TextLine
    column: int


@dataclass
class Region:
    bbox: BBox
    kind: str
    children: list["Region"] = field(default_factory=list)
    lines: list[TextLine] = field(default_factory=list)


def _xy_cut(lines: list[TextLine], bbox: BBox) -> Region:
    # Task 1 stub: no cutting yet -- one leaf block, lines top-to-bottom.
    ordered = sorted(lines, key=lambda ln: (-ln.bbox[3], ln.bbox[0]))
    return Region(bbox=bbox, kind="block", lines=ordered)


def _reading_order(region: Region) -> list[PlacedLine]:
    if not region.children:
        return [PlacedLine(line=ln, column=0) for ln in region.lines]
    placed: list[PlacedLine] = []
    for child in region.children:
        placed.extend(_reading_order(child))
    return placed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_layout.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/rebind/layout.py tests/test_layout.py
git commit -m "Layout: XY-cut skeleton with single-column reading order"
```

---

### Task 2: Vertical cut — clean two-column

**Files:**
- Modify: `src/rebind/layout.py`
- Test: `tests/test_layout.py`

**Interfaces:**
- Consumes: `_xy_cut`, `_reading_order`, `PlacedLine` from Task 1.
- Produces: `_xy_cut` now performs a single vertical cut when a valid gutter exists; column Regions carry `kind="column"`; `_reading_order` assigns a distinct `column` index per column left-to-right.

- [ ] **Step 1: Write the failing test**

```python
def test_two_columns_interleave_by_column():
    # Left column x in [72,260], right column x in [320,500], wide gutter between.
    left = [_line(72, 700, 260, 710, "L1"), _line(72, 680, 260, 690, "L2")]
    right = [_line(320, 700, 500, 710, "R1"), _line(320, 680, 500, 690, "R2")]
    region = _xy_cut(left + right, (72, 680, 500, 710))
    placed = _reading_order(region)
    assert [p.line.text for p in placed] == ["L1", "L2", "R1", "R2"]
    assert [p.column for p in placed] == [0, 0, 1, 1]


def test_narrow_gap_is_not_a_column():
    # Two blocks 2pt apart horizontally -- below GUTTER_MIN_FRACTION, must stay one column.
    a = [_line(72, 700, 260, 710, "A1"), _line(72, 680, 260, 690, "A2")]
    b = [_line(262, 700, 400, 710, "B1"), _line(262, 680, 400, 690, "B2")]
    region = _xy_cut(a + b, (72, 680, 400, 710))
    placed = _reading_order(region)
    assert {p.column for p in placed} == {0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_layout.py -k "two_columns or narrow_gap" -v`
Expected: FAIL — the stub returns a single block, so `column` is always 0.

- [ ] **Step 3: Write minimal implementation**

Replace `_xy_cut` and add the vertical-gutter helper. Reading order numbers columns by encountering `kind="column"` children left-to-right:

```python
def _widest_vertical_gutter(lines: list[TextLine], bbox: BBox) -> tuple[float, float] | None:
    """Widest whitespace band on the x-axis that clears the guards. Returns (gap_x, gap_width)
    where gap_x is the left edge of the gutter, or None if no valid gutter exists."""
    x0, y0, x1, y1 = bbox
    region_w = x1 - x0
    region_h = y1 - y0
    if region_w <= 0 or len(lines) < 2:
        return None
    # Sort by left edge; sweep, tracking the running rightmost extent. A gap opens where the next
    # line starts beyond the running max. Only gutters spanning enough of the height count.
    spans = sorted(((ln.bbox[0], ln.bbox[2]) for ln in lines), key=lambda s: s[0])
    best: tuple[float, float] | None = None
    running_max = spans[0][1]
    for left, right in spans[1:]:
        gap = left - running_max
        if gap > 0:
            # Height guard: lines must exist both left and right that overlap vertically enough.
            if _gutter_spans_height(lines, running_max, left, y0, y1, region_h):
                if best is None or gap > best[1]:
                    best = (running_max, gap)
        running_max = max(running_max, right)
    if best is None:
        return None
    if best[1] < region_w * GUTTER_MIN_FRACTION:
        return None
    return best


def _gutter_spans_height(lines, gap_left, gap_right, y0, y1, region_h) -> bool:
    """True when lines on BOTH sides of the gutter together cover at least
    GUTTER_MIN_HEIGHT_FRACTION of the region height -- guards against a lone page number or a
    centered title manufacturing a false column."""
    left_lines = [ln for ln in lines if ln.bbox[2] <= gap_left]
    right_lines = [ln for ln in lines if ln.bbox[0] >= gap_right]
    if not left_lines or not right_lines:
        return False

    def covered(side) -> float:
        top = max(ln.bbox[3] for ln in side)
        bottom = min(ln.bbox[1] for ln in side)
        return top - bottom

    return min(covered(left_lines), covered(right_lines)) >= region_h * GUTTER_MIN_HEIGHT_FRACTION


def _xy_cut(lines: list[TextLine], bbox: BBox) -> Region:
    if not lines:
        return Region(bbox=bbox, kind="block", lines=[])
    gutter = _widest_vertical_gutter(lines, bbox)
    if gutter is not None:
        gap_left, gap_width = gutter
        split_x = gap_left + gap_width / 2
        x0, y0, x1, y1 = bbox
        left = [ln for ln in lines if ln.bbox[0] < split_x]
        right = [ln for ln in lines if ln.bbox[0] >= split_x]
        return Region(bbox=bbox, kind="page", children=[
            _labelled_column(_xy_cut(left, (x0, y0, split_x, y1))),
            _labelled_column(_xy_cut(right, (split_x, y0, x1, y1))),
        ])
    ordered = sorted(lines, key=lambda ln: (-ln.bbox[3], ln.bbox[0]))
    return Region(bbox=bbox, kind="block", lines=ordered)


def _labelled_column(region: Region) -> Region:
    region.kind = "column"
    return region


def _reading_order(region: Region) -> list[PlacedLine]:
    counter = {"col": -1}

    def walk(r: Region, column: int) -> list[PlacedLine]:
        if r.kind == "column":
            counter["col"] += 1
            column = counter["col"]
        if not r.children:
            return [PlacedLine(line=ln, column=max(column, 0)) for ln in r.lines]
        out: list[PlacedLine] = []
        for child in r.children:
            out.extend(walk(child, column))
        return out

    return walk(region, -1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_layout.py -v`
Expected: PASS (all three tests)

- [ ] **Step 5: Commit**

```bash
git add src/rebind/layout.py tests/test_layout.py
git commit -m "Layout: vertical XY-cut with gutter guards"
```

---

### Task 3: Horizontal cut + recursion — header over two columns

**Files:**
- Modify: `src/rebind/layout.py`
- Test: `tests/test_layout.py`

**Interfaces:**
- Consumes: Task 2 `_xy_cut`.
- Produces: `_xy_cut` chooses between the widest valid vertical gutter and the widest valid horizontal gap, cuts the more significant (vertical wins ties), and recurses so a full-width header above two columns is isolated *before* the column split.

- [ ] **Step 1: Write the failing test**

```python
def test_full_width_header_isolated_before_columns():
    header = [_line(72, 750, 500, 762, "HEADER")]     # spans full width, above both columns
    left = [_line(72, 700, 260, 710, "L1"), _line(72, 680, 260, 690, "L2")]
    right = [_line(320, 700, 500, 710, "R1"), _line(320, 680, 500, 690, "R2")]
    region = _xy_cut(header + left + right, (72, 680, 500, 762))
    placed = _reading_order(region)
    assert [p.line.text for p in placed] == ["HEADER", "L1", "L2", "R1", "R2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_layout.py -k header -v`
Expected: FAIL — with only vertical cuts, the full-width header line spans both columns and lands in one, scrambling order.

- [ ] **Step 3: Write minimal implementation**

Add horizontal-gap detection and a chooser. A horizontal cut splits into stacked blocks (top block read first). The full-width header blocks any vertical gutter from spanning the height, so the horizontal cut is taken first, then each band is recursed:

```python
def _widest_horizontal_gap(lines: list[TextLine], bbox: BBox) -> float | None:
    """Y coordinate to split at (top band above, bottom band below), or None. y-up: a gap is
    open vertical space between the bottom of the upper line and the top of the lower one."""
    x0, y0, x1, y1 = bbox
    region_h = y1 - y0
    if region_h <= 0 or len(lines) < 2:
        return None
    # Walk lines top-to-bottom (descending y1); a gap opens below the running lowest bottom.
    ordered = sorted(lines, key=lambda ln: -ln.bbox[3])
    best_gap = 0.0
    best_y: float | None = None
    running_min_bottom = ordered[0].bbox[1]
    for ln in ordered[1:]:
        gap = running_min_bottom - ln.bbox[3]
        if gap > best_gap:
            best_gap = gap
            best_y = ln.bbox[3] + gap / 2
        running_min_bottom = min(running_min_bottom, ln.bbox[1])
    if best_y is None or best_gap < region_h * BLOCK_GAP_MIN_FRACTION:
        return None
    return best_y


def _xy_cut(lines: list[TextLine], bbox: BBox) -> Region:
    if not lines:
        return Region(bbox=bbox, kind="block", lines=[])
    x0, y0, x1, y1 = bbox
    gutter = _widest_vertical_gutter(lines, bbox)
    split_y = _widest_horizontal_gap(lines, bbox)

    # Prefer the vertical (column) cut; it wins ties. Fall to horizontal when no valid gutter.
    if gutter is not None:
        gap_left, gap_width = gutter
        split_x = gap_left + gap_width / 2
        left = [ln for ln in lines if ln.bbox[0] < split_x]
        right = [ln for ln in lines if ln.bbox[0] >= split_x]
        return Region(bbox=bbox, kind="page", children=[
            _labelled_column(_xy_cut(left, (x0, y0, split_x, y1))),
            _labelled_column(_xy_cut(right, (split_x, y0, x1, y1))),
        ])
    if split_y is not None:
        top = [ln for ln in lines if ln.bbox[1] >= split_y]
        bottom = [ln for ln in lines if ln.bbox[1] < split_y]
        return Region(bbox=bbox, kind="block-stack", children=[
            _xy_cut(top, (x0, split_y, x1, y1)),
            _xy_cut(bottom, (x0, y0, x1, split_y)),
        ])
    ordered = sorted(lines, key=lambda ln: (-ln.bbox[3], ln.bbox[0]))
    return Region(bbox=bbox, kind="block", lines=ordered)
```

Note: `_reading_order`'s `walk` already recurses through non-column children (`block-stack`) without incrementing the column counter, so a stacked block inherits its parent column — correct, because a block split *inside* a column is still that column.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_layout.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/rebind/layout.py tests/test_layout.py
git commit -m "Layout: horizontal cut + recursion isolates full-width headers"
```

---

### Task 4: Three columns, determinism

**Files:**
- Test: `tests/test_layout.py`

**Interfaces:**
- Consumes: Task 3 `_xy_cut`, `_reading_order`.
- Produces: no code change expected — these tests confirm recursion already handles N columns and that output is deterministic. If a test fails, fix `_xy_cut` minimally.

- [ ] **Step 1: Write the failing test**

```python
import random


def test_three_columns_read_left_to_right():
    c0 = [_line(72, 700, 180, 710, "A"), _line(72, 680, 180, 690, "B")]
    c1 = [_line(220, 700, 330, 710, "C"), _line(220, 680, 330, 690, "D")]
    c2 = [_line(370, 700, 500, 710, "E"), _line(370, 680, 500, 690, "F")]
    region = _xy_cut(c0 + c1 + c2, (72, 680, 500, 710))
    placed = _reading_order(region)
    assert [p.line.text for p in placed] == ["A", "B", "C", "D", "E", "F"]
    assert [p.column for p in placed] == [0, 0, 1, 1, 2, 2]


def test_reading_order_is_input_order_independent():
    lines = [_line(72, 700, 260, 710, "L1"), _line(72, 680, 260, 690, "L2"),
             _line(320, 700, 500, 710, "R1"), _line(320, 680, 500, 690, "R2")]
    bbox = (72, 680, 500, 710)
    baseline = [p.line.text for p in _reading_order(_xy_cut(lines, bbox))]
    for seed in range(5):
        shuffled = lines[:]
        random.Random(seed).shuffle(shuffled)
        got = [p.line.text for p in _reading_order(_xy_cut(shuffled, bbox))]
        assert got == baseline
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `uv run pytest tests/test_layout.py -k "three_columns or input_order" -v`
Expected: PASS if recursion is correct. If FAIL, fix `_xy_cut` so the recursion is right and re-run.

- [ ] **Step 3: (only if needed) fix `_xy_cut`**

If the three-column test fails, the recursion on the right sub-region is not finding the second gutter — verify `_widest_vertical_gutter` is called on each sub-region's own lines and bbox. No change expected.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_layout.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_layout.py
git commit -m "Layout: cover three columns and input-order independence"
```

---

### Task 5: `order_page` — artifact exclusion + marginal flag

**Files:**
- Modify: `src/rebind/layout.py`
- Test: `tests/test_layout.py`

**Interfaces:**
- Consumes: `_xy_cut`, `_reading_order`; `Page` from `.extract`; `TypographicProfile` from `.profile`.
- Produces:
  - `@dataclass class PageLayout: lines: list[PlacedLine]; flags: list[str]`
  - `order_page(page: Page, profile: TypographicProfile) -> PageLayout`
  - Artifact lines (`profile.role_of(line, page_height=page.height) == "artifact"`) are held out of the cut and appended after the body in top-to-bottom order with `column == -1`.
  - `flags` contains `"multi-column-suspected"` when any accepted gutter was marginal (width `< GUTTER_MARGINAL_FRACTION * region_width`).

- [ ] **Step 1: Write the failing test**

```python
from rebind.extract import Page
from rebind.profile import build_profile
from rebind.layout import order_page


def test_order_page_excludes_artifacts_and_orders_body():
    body = [_line(72, 700, 500, 710, "para one"), _line(72, 680, 500, 690, "para two")]
    footer = _line(72, 40, 500, 50, "page 3")   # bottom edge -> artifact after profiling
    # Two pages so the footer recurs and becomes an artifact key.
    p1 = Page(number=1, width=560, height=740, lines=body + [footer], images=[], has_text_layer=True)
    p2 = Page(number=2, width=560, height=740,
              lines=[_line(72, 700, 500, 710, "more"), _line(72, 40, 500, 50, "page 3")],
              images=[], has_text_layer=True)
    profile = build_profile([p1, p2])
    layout = order_page(p1, profile)
    body_lines = [p.line.text for p in layout.lines if p.column >= 0]
    artifact_lines = [p.line.text for p in layout.lines if p.column == -1]
    assert body_lines == ["para one", "para two"]
    assert artifact_lines == ["page 3"]
    assert "multi-column-suspected" not in layout.flags
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_layout.py -k order_page -v`
Expected: FAIL — `order_page` / `PageLayout` not defined.

- [ ] **Step 3: Write minimal implementation**

Thread a marginal-flag accumulator through the cut. Add to `layout.py`:

```python
from .extract import Page, TextLine          # extend the existing import
from .profile import TypographicProfile


@dataclass
class PageLayout:
    lines: list[PlacedLine]
    flags: list[str]


def order_page(page: Page, profile: TypographicProfile) -> PageLayout:
    body: list[TextLine] = []
    artifacts: list[TextLine] = []
    for line in page.lines:
        if profile.role_of(line, page_height=page.height) == "artifact":
            artifacts.append(line)
        else:
            body.append(line)

    marginal: list[bool] = []
    region = _xy_cut(body, (0.0, 0.0, page.width, page.height), marginal)
    placed = _reading_order(region)

    artifacts_ordered = sorted(artifacts, key=lambda ln: (-ln.bbox[3], ln.bbox[0]))
    placed.extend(PlacedLine(line=ln, column=-1) for ln in artifacts_ordered)

    flags = ["multi-column-suspected"] if any(marginal) else []
    return PageLayout(lines=placed, flags=flags)
```

Give `_xy_cut` an optional accumulator (default `None` keeps Tasks 1-4 callers working) and record marginality where a vertical gutter is accepted:

```python
def _xy_cut(lines: list[TextLine], bbox: BBox, marginal: list[bool] | None = None) -> Region:
    if not lines:
        return Region(bbox=bbox, kind="block", lines=[])
    x0, y0, x1, y1 = bbox
    gutter = _widest_vertical_gutter(lines, bbox)
    split_y = _widest_horizontal_gap(lines, bbox)
    if gutter is not None:
        gap_left, gap_width = gutter
        if marginal is not None and gap_width < (x1 - x0) * GUTTER_MARGINAL_FRACTION:
            marginal.append(True)
        split_x = gap_left + gap_width / 2
        left = [ln for ln in lines if ln.bbox[0] < split_x]
        right = [ln for ln in lines if ln.bbox[0] >= split_x]
        return Region(bbox=bbox, kind="page", children=[
            _labelled_column(_xy_cut(left, (x0, y0, split_x, y1), marginal)),
            _labelled_column(_xy_cut(right, (split_x, y0, x1, y1), marginal)),
        ])
    if split_y is not None:
        top = [ln for ln in lines if ln.bbox[1] >= split_y]
        bottom = [ln for ln in lines if ln.bbox[1] < split_y]
        return Region(bbox=bbox, kind="block-stack", children=[
            _xy_cut(top, (x0, split_y, x1, y1), marginal),
            _xy_cut(bottom, (x0, y0, x1, split_y), marginal),
        ])
    ordered = sorted(lines, key=lambda ln: (-ln.bbox[3], ln.bbox[0]))
    return Region(bbox=bbox, kind="block", lines=ordered)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_layout.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/rebind/layout.py tests/test_layout.py
git commit -m "Layout: order_page holds artifacts out of the cut, flags marginal gutters"
```

---

### Task 6: Integrate into pipeline and assemble

**Files:**
- Modify: `src/rebind/pipeline.py`
- Modify: `src/rebind/assemble.py`
- Test: existing `tests/test_assemble.py` / `tests/test_pipeline.py` (whichever holds the two-column round-trip) plus a new assertion.

**Interfaces:**
- Consumes: `layout.order_page` (Task 5).
- Produces: `assemble` consumes reading-ordered lines; `_looks_multi_column`, `_horizontal_clusters`, `_vertical_overlap_fraction` and their constants are deleted; body nodes on a multi-column page carry a `column-{n}` flag.

- [ ] **Step 1: Write the failing test**

Find the current two-column round-trip test (search `multi-column-suspected` in `tests/`). Replace its "flag is present" assertion with a reading-order assertion. Add:

```python
def test_two_column_pdf_recovers_interleaved_reading_order(tmp_path):
    from tests.fixtures import born_digital_pdf
    html = ("<div style='column-count:2; column-gap:2em;'>"
            "<p>Alpha one.</p><p>Alpha two.</p><p>Beta one.</p><p>Beta two.</p></div>")
    src = tmp_path / "cols.pdf"
    born_digital_pdf(html, src)
    doc = convert(src, tmp_path / "out.pdf").document
    paras = [n.text for n in doc.nodes if n.kind == "Paragraph"]
    # Left column fully before right column -- not page-interleaved.
    assert paras.index("Alpha one.") < paras.index("Beta one.")
    assert paras.index("Alpha two.") < paras.index("Beta one.")
    assert any("column-1" in n.flags for n in doc.nodes if n.kind == "Paragraph")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ -k interleaved -v`
Expected: FAIL — `assemble` still uses the naive sort, so columns interleave line-by-line.

- [ ] **Step 3: Write minimal implementation**

In `pipeline.py`, where `assemble(pages, profile, ...)` is called, build layouts first and pass them in. Change `assemble`'s signature to accept the per-page layout:

- Add `from .layout import order_page, PageLayout` to `pipeline.py`; compute `layouts = {page.number: order_page(page, profile) for page in pages}` and pass `layouts=layouts` to `assemble`. (Pages are consumed once — materialize the page list before both `build_profile` and this loop if it isn't already a list.)
- In `assemble.py`:
  - Delete `_horizontal_clusters`, `_vertical_overlap_fraction`, `_looks_multi_column`, and the constants `_MIN_LINES_PER_CLUSTER`, `_MIN_VERTICAL_OVERLAP_FRACTION`, `_MIN_COLUMN_CLUSTERS`.
  - Add `layouts: dict[int, PageLayout]` parameter (import `PageLayout` from `.layout`).
  - Replace the body block. Instead of `ordered_lines = sorted(...)` and `page_multi_column = _looks_multi_column(...)`, use:

```python
            layout = layouts[page.number]
            page_flags = layout.flags
            column_count = len({p.column for p in layout.lines if p.column >= 0})
            placed_lines = layout.lines
```

  - Iterate `for placed in placed_lines:` with `line = placed.line`. Skip artifact-role reclassification for `placed.column == -1` lines? No — keep calling `profile.role_of`; artifacts held out by layout still classify as `"artifact"` and are emitted as `Artifact` nodes exactly as before, now at the end of the page. Body lines classify as heading/body as before.
  - Where the paragraph node is built, replace the multi-column flag logic:

```python
                flags = [] if confidence >= 0.5 else ["degraded-region"]
                if "multi-column-suspected" in page_flags:
                    flags.append("multi-column-suspected")
                if column_count > 1 and placed.column >= 0:
                    flags.append(f"column-{placed.column}")
```

  - Apply the same `column-{n}` provenance flag to `Heading` and `ListItem` body nodes (compute a small local `provenance = [f"column-{placed.column}"] if column_count > 1 and placed.column >= 0 else []` and extend each node's `flags`).

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS. Fix any test that asserted the old naive order or the old `_looks_multi_column` behavior — those assertions are now wrong by design; update them to the reading-order expectation. Run `uv run ruff check .` and clear any unused-import warnings from the deletions.

- [ ] **Step 5: Commit**

```bash
git add src/rebind/pipeline.py src/rebind/assemble.py tests/
git commit -m "Wire XY-cut layout stage into the pipeline; assemble consumes reading order"
```

---

### Task 7: Golden model for a two-column document

**Files:**
- Create: `tests/golden/two_column_document.model.json`
- Test: the golden-file test module (mirror the existing `simple_document` golden test).

**Interfaces:**
- Consumes: the full pipeline.
- Produces: a committed golden model proving the two-column reading order and `column-{n}` provenance are stable.

- [ ] **Step 1: Write the failing test**

Mirror the existing golden test (find it via `grep -rl "simple_document.model.json" tests/`). Add a case that converts a fixed two-column HTML fixture and compares `doc.to_json()` against `tests/golden/two_column_document.model.json`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ -k golden -v`
Expected: FAIL — golden file does not exist.

- [ ] **Step 3: Generate and eyeball the golden**

Write the produced JSON to `tests/golden/two_column_document.model.json`, then **read it** and confirm: paragraphs are in per-column order (both left-column paragraphs precede the right-column ones), body nodes carry `column-0`/`column-1`, provenance (page, bbox) is present. Only commit once it reads correctly — a blessed-but-wrong golden locks in a bug.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ -k golden -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/golden/two_column_document.model.json tests/
git commit -m "Golden: two-column reading order and column provenance"
```

---

## Self-Review

**Spec coverage:**
- §3 pipeline placement → Task 6. §3.1 branch-agnostic interface (`PlacedLine`/`order_page` take generic lines) → Tasks 1, 5. §4 XY-cut → Tasks 1-4. §4.1 guards → Tasks 2, 3, 5. §4.2 artifact exclusion → Task 5. §4.3 marginal fallback → Task 5. §5 region tree intermediate + column provenance + confidence untouched → Tasks 1, 6. §6 tests: unit (1-5), round-trip (6), golden (7), determinism (4), veraPDF gate (existing, runs in 6's full-suite step). Real-sample smoke is manual/out-of-CI — noted, no task. All covered.

**Placeholder scan:** No TBD/TODO; every code step shows code; the one conditional step (Task 4 Step 3) is explicitly "only if needed" with the check to run. OK.

**Type consistency:** `PlacedLine(line, column)`, `Region(bbox, kind, children, lines)`, `PageLayout(lines, flags)`, `order_page(page, profile) -> PageLayout`, `_xy_cut(lines, bbox, marginal=None)` used consistently across tasks. `kind` values `"page"|"column"|"block"|"block-stack"` — `_reading_order` only special-cases `"column"` for numbering and recurses all others, so `"block-stack"` is handled. OK.
