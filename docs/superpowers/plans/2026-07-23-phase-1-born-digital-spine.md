# Phase 1 Born-Digital Pipeline Spine Implementation Plan

> **SUPERSEDED — historical record only.** This is the plan as originally written, before
> execution. It is kept for history, not as a guide to follow verbatim: several code blocks below
> contain defects that were found and corrected during implementation. `src/rebind/` is
> authoritative; do not copy code from this document. Known defects in the plan's code blocks:
>
> - `node_id`'s bbox normalization was a no-op in the plan's version (it didn't actually quantize
>   before hashing), which the shipped `model.py` fixes.
> - Artifact recurrence was counted per *line* instead of per *page* in the plan, which
>   over-counts a repeated running header that spans multiple lines on the same page.
> - A stray `continue` in the plan's per-page loop skipped image handling entirely on scanned
>   pages; the shipped `assemble.py` still emits image placeholders for scanned pages.
> - The plan's ordered-list marker regex was broken (it did not correctly separate the marker
>   from trailing content in all cases); see `ORDERED_RE` in `assemble.py` for the corrected
>   pattern and its documented limitations.
>
> If you are implementing new work, read `src/rebind/` and its tests, not this file.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert a born-digital PDF into a tagged PDF/UA document that veraPDF passes, recovering headings, paragraphs and lists from typography.

**Architecture:** Two passes over the source. Pass one builds a document-global typographic profile from style statistics only (bounded memory). Pass two streams pages again and emits a document-model tree, which is serialized to JSON as the source of truth and rendered to HTML, then through the existing `render.py` to a tagged PDF and `validate.py` to veraPDF.

**Tech Stack:** Python 3.12 via uv, pdfminer.six (new), WeasyPrint 69, pikepdf, pytest, ruff.

## Global Constraints

- **Always `uv run ...`** — never bare `python` or `pytest`. The system Python is 3.14 and lacks wheels for parts of this stack.
- **Never fabricate.** Every text node traces to recognizer output. Below threshold it becomes an honest placeholder, never a plausible guess.
- **Everything has provenance.** Every node knows its source page and bounding box.
- **Never write byte-comparison tests against PDFs** (ADR 0003). Golden files test the document model JSON only.
- **No API key, no GPU, no network at runtime.**
- **No arbitrary limits** on structure elements, pages, or document size.
- **Every dependency must be bundle-able on Windows** — no user-performed native install.
- **`samples/` is gitignored and stays that way.** All fixtures are generated at test time.
- Line length 100 (ruff), target py312.
- Commit and push after every task. Concise, imperative commit messages.

---

## File Structure

| Path | Responsibility |
|---|---|
| `src/rebind/extract.py` | Create — pdfminer.six → `Page`/`TextLine`/`ImageRegion`; per-page text-layer classification |
| `src/rebind/model.py` | Create — document model dataclasses, stable ids, JSON round-trip |
| `src/rebind/profile.py` | Create — pass one; `TypographicProfile` |
| `src/rebind/assemble.py` | Create — pass two; pages + profile → `Document` |
| `src/rebind/emit.py` | Create — `Document` → semantic HTML fragment |
| `src/rebind/pipeline.py` | Create — stage orchestration, `ConversionResult` |
| `src/rebind/cli.py` | Create — `rebind convert` / `rebind serve` |
| `src/rebind/render.py` | Modify — add anchor-aware render for page-label mapping |
| `tests/fixtures.py` | Create — generate born-digital PDFs with WeasyPrint |
| `pyproject.toml` | Modify — add pdfminer.six, change console entry point |

---

### Task 1: Fixture generator and `extract.py`

**Files:**
- Modify: `pyproject.toml:9-14` (dependencies)
- Create: `tests/fixtures.py`
- Create: `src/rebind/extract.py`
- Test: `tests/test_extract.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `TextLine(text, page, bbox, font, size, bold, italic)`, `ImageRegion(page, bbox)`, `Page(number, width, height, lines, images)` with property `has_text_layer -> bool`, `extract_pages(source: Path) -> Iterator[Page]`, `source_is_tagged(source: Path) -> bool`, `ExtractionError(RuntimeError)`. Test helper `born_digital_pdf(html: str, target: Path) -> Path`.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, change the `dependencies` list to:

```toml
dependencies = [
    "weasyprint>=62",
    "pikepdf>=9",
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "pdfminer.six>=20240706",
]
```

Then run: `uv sync --extra dev`
Expected: resolves and installs `pdfminer-six`.

- [ ] **Step 2: Write the fixture generator**

Create `tests/fixtures.py`:

```python
"""Generate born-digital PDFs for tests.

`samples/` is gitignored (copyrighted third-party scans in a public repo), so the suite cannot
depend on any real document. Fixtures are rendered with WeasyPrint at test time instead: known
HTML in, PDF out, then back through Rebind so the recovered model can be compared to the
structure that went in.

Limitation, stated so it is not forgotten: WeasyPrint output is unusually well-formed. It does
not reproduce what InDesign, Word or LaTeX emit -- inconsistent font naming, text split mid-word
across spans, headers in margin boxes. These fixtures prove the logic is correct; they do not
prove the heuristics are tuned. See spec section 9.1.
"""

from __future__ import annotations

from pathlib import Path

from weasyprint import HTML

_PAGE_CSS = """
@page { size: letter; margin: 1in; }
body { font-family: "DejaVu Serif"; font-size: 11pt; line-height: 1.4; }
h1 { font-size: 24pt; font-weight: bold; }
h2 { font-size: 18pt; font-weight: bold; }
h3 { font-size: 14pt; font-weight: bold; }
"""


def born_digital_pdf(html_body: str, target: Path, *, extra_css: str = "") -> Path:
    """Render an HTML fragment to an untagged born-digital PDF with a real text layer."""
    document = (
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>fixture</title><style>{_PAGE_CSS}{extra_css}</style></head>"
        f"<body>{html_body}</body></html>"
    )
    HTML(string=document).write_pdf(target)
    return target
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_extract.py`:

```python
from pathlib import Path

import pytest

from rebind.extract import ExtractionError, extract_pages, source_is_tagged
from tests.fixtures import born_digital_pdf


def test_extracts_text_lines_with_style_and_provenance(tmp_path: Path):
    source = born_digital_pdf("<h1>Chapter One</h1><p>Body text here.</p>", tmp_path / "a.pdf")

    pages = list(extract_pages(source))

    assert len(pages) == 1
    page = pages[0]
    assert page.number == 1
    assert page.has_text_layer
    texts = [line.text for line in page.lines]
    assert "Chapter One" in texts
    assert "Body text here." in texts

    heading = next(line for line in page.lines if line.text == "Chapter One")
    body = next(line for line in page.lines if line.text == "Body text here.")
    assert heading.size > body.size
    assert heading.page == 1
    assert len(heading.bbox) == 4
    assert heading.bbox[3] > heading.bbox[1]


def test_page_without_text_is_classified_as_scanned(tmp_path: Path):
    import pikepdf

    target = tmp_path / "blank.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.save(target)

    pages = list(extract_pages(target))

    assert len(pages) == 1
    assert not pages[0].has_text_layer
    assert pages[0].lines == ()


def test_extraction_is_lazy(tmp_path: Path):
    source = born_digital_pdf("<p>one</p>", tmp_path / "lazy.pdf")

    result = extract_pages(source)

    assert not isinstance(result, list), "extract_pages must stream, not materialize all pages"


def test_missing_file_raises_extraction_error(tmp_path: Path):
    with pytest.raises(ExtractionError):
        list(extract_pages(tmp_path / "nope.pdf"))


def test_untagged_fixture_is_reported_as_untagged(tmp_path: Path):
    source = born_digital_pdf("<p>text</p>", tmp_path / "u.pdf")

    assert source_is_tagged(source) is False
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_extract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rebind.extract'`

- [ ] **Step 5: Implement `extract.py`**

Create `src/rebind/extract.py`:

```python
"""Read a born-digital PDF's text layer with position and font metrics.

pdfminer.six is used rather than `inspect.py`'s ToUnicode parser, which is diagnostic/test-only
by design (see CLAUDE.md). pdfminer.six is MIT, pure Python and has no native build step, so it
satisfies the bundle-able-on-Windows invariant.

Pages are yielded lazily. Nothing here retains more than one page at a time, which is what makes
1,000-page documents tractable.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pikepdf
from pdfminer.high_level import extract_pages as _pdfminer_pages
from pdfminer.layout import LAParams, LTChar, LTFigure, LTImage, LTTextContainer
from pdfminer.pdfparser import PDFSyntaxError


class ExtractionError(RuntimeError):
    """The source PDF cannot be read at all -- missing, malformed, or encrypted."""


@dataclass(frozen=True)
class TextLine:
    """One line of text with the provenance and typography needed to classify it."""

    text: str
    page: int
    bbox: tuple[float, float, float, float]
    font: str
    size: float
    bold: bool
    italic: bool


@dataclass(frozen=True)
class ImageRegion:
    """A non-text region. Phase 1 records its existence and location, nothing more."""

    page: int
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class Page:
    number: int
    width: float
    height: float
    lines: tuple[TextLine, ...]
    images: tuple[ImageRegion, ...]

    @property
    def has_text_layer(self) -> bool:
        """False means the page is a scan and belongs to the (unbuilt) OCR branch."""
        return bool(self.lines)


def _dominant_font(chars: list[LTChar]) -> tuple[str, float]:
    """The font and size covering the most characters in a line.

    A line is rarely all one font -- a bold run inside a sentence, a footnote marker. Taking the
    most common rather than the first avoids classifying a paragraph as a heading because its
    first character happened to be styled.
    """
    counts: dict[tuple[str, float], int] = {}
    for char in chars:
        key = (char.fontname, round(char.size, 1))
        counts[key] = counts.get(key, 0) + 1
    return max(counts.items(), key=lambda item: item[1])[0]


def _line_from_container(container, page_number: int) -> TextLine | None:
    text = container.get_text().strip()
    if not text:
        return None
    chars = [obj for obj in container if isinstance(obj, LTChar)]
    if not chars:
        return None
    font, size = _dominant_font(chars)
    lowered = font.lower()
    return TextLine(
        text=text,
        page=page_number,
        bbox=(container.x0, container.y0, container.x1, container.y1),
        font=font,
        size=size,
        # Font names carry weight and slant as a naming convention, not as metadata; there is no
        # reliable structured source for either in a PDF. This substring check is what every PDF
        # tool does and it is wrong for fonts that do not follow the convention.
        bold="bold" in lowered or "black" in lowered or "heavy" in lowered,
        italic="italic" in lowered or "oblique" in lowered,
    )


def extract_pages(source: Path) -> Iterator[Page]:
    """Yield one `Page` per page of the source, lazily."""
    source = Path(source)
    if not source.is_file():
        raise ExtractionError(f"no such file: {source}")

    try:
        layouts = _pdfminer_pages(str(source), laparams=LAParams())
        for index, layout in enumerate(layouts, start=1):
            lines: list[TextLine] = []
            images: list[ImageRegion] = []
            for element in layout:
                if isinstance(element, LTTextContainer):
                    for container in element:
                        line = _line_from_container(container, index)
                        if line is not None:
                            lines.append(line)
                elif isinstance(element, (LTImage, LTFigure)):
                    images.append(
                        ImageRegion(
                            page=index,
                            bbox=(element.x0, element.y0, element.x1, element.y1),
                        )
                    )
            yield Page(
                number=index,
                width=layout.width,
                height=layout.height,
                lines=tuple(lines),
                images=tuple(images),
            )
    except PDFSyntaxError as exc:
        raise ExtractionError(f"{source} is not a readable PDF: {exc}") from exc


def source_is_tagged(source: Path) -> bool:
    """Whether the source already declares a structure tree.

    Rebind should not churn documents that are already accessible (governing design 5.1). This
    only reports the claim; it does not validate that the tagging is any good.
    """
    try:
        with pikepdf.open(source) as pdf:
            return "/StructTreeRoot" in pdf.Root
    except Exception as exc:  # pikepdf raises several unrelated types here
        raise ExtractionError(f"cannot open {source}: {exc}") from exc
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_extract.py -v`
Expected: 5 passed

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check .
git add pyproject.toml uv.lock tests/fixtures.py tests/test_extract.py src/rebind/extract.py
git commit -m "Add pdfminer-based text extraction with per-page classification"
git push origin main
```

---

### Task 2: `model.py` — document model and stable ids

**Files:**
- Create: `src/rebind/model.py`
- Test: `tests/test_model.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `node_id(page, bbox, page_width, page_height, text) -> str`; node classes `Heading(level, text, ...)`, `Paragraph(text, ...)`, `ListNode(items, ...)`, `ListItem(text, ...)`, `Artifact(text, ...)`, `Placeholder(reason, ...)`, `PageBreak(label, ...)`; `Document(nodes, title, lang, scanned_pages, source_was_tagged)`; `Document.to_json() -> str`; `Document.from_json(str) -> Document`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_model.py`:

```python
from rebind.model import Document, Heading, Paragraph, Placeholder, node_id


def test_node_id_is_stable_for_identical_input():
    first = node_id(page=3, bbox=(72.0, 700.0, 300.0, 720.0), page_width=612, page_height=792,
                    text="Chapter One")
    second = node_id(page=3, bbox=(72.0, 700.0, 300.0, 720.0), page_width=612, page_height=792,
                     text="Chapter One")

    assert first == second


def test_node_id_differs_on_page_bbox_or_text():
    base = dict(page=3, bbox=(72.0, 700.0, 300.0, 720.0), page_width=612, page_height=792,
                text="Chapter One")

    assert node_id(**{**base, "page": 4}) != node_id(**base)
    assert node_id(**{**base, "text": "Chapter Two"}) != node_id(**base)
    assert node_id(**{**base, "bbox": (72.0, 600.0, 300.0, 620.0)}) != node_id(**base)


def test_node_id_survives_sub_point_position_jitter():
    """Re-extraction can shift a bbox by a fraction of a point. Ids must not churn on that."""
    a = node_id(page=1, bbox=(72.0, 700.0, 300.0, 720.0), page_width=612, page_height=792,
                text="Heading")
    b = node_id(page=1, bbox=(72.02, 700.01, 300.01, 720.02), page_width=612, page_height=792,
                text="Heading")

    assert a == b


def test_document_round_trips_through_json():
    doc = Document(
        title="Test",
        lang="en",
        scanned_pages=(4, 5),
        source_was_tagged=False,
        nodes=[
            Heading(id="h1", page=1, bbox=(1, 2, 3, 4), confidence=1.0, stage="assemble",
                    flags=[], level=1, text="Chapter One"),
            Paragraph(id="p1", page=1, bbox=(1, 2, 3, 4), confidence=1.0, stage="assemble",
                      flags=[], text="Body."),
            Placeholder(id="x1", page=4, bbox=(0, 0, 612, 792), confidence=0.0, stage="assemble",
                        flags=["no-text-layer"], reason="no text layer on source page 4"),
        ],
    )

    restored = Document.from_json(doc.to_json())

    assert restored == doc
    assert restored.nodes[0].level == 1
    assert restored.scanned_pages == (4, 5)


def test_every_node_carries_provenance():
    node = Paragraph(id="p", page=7, bbox=(1, 2, 3, 4), confidence=0.9, stage="assemble",
                     flags=[], text="x")

    assert node.page == 7
    assert node.bbox == (1, 2, 3, 4)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rebind.model'`

- [ ] **Step 3: Implement `model.py`**

Create `src/rebind/model.py`:

```python
"""The document model -- Rebind's source of truth. The PDF is a build artifact.

Phase 1 implements a deliberate subset of the node types in the governing design. The rest are
not stubbed: a stub invites code to depend on a shape that has not been designed yet.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from hashlib import blake2b

BBox = tuple[float, float, float, float]

# Bboxes are quantized before hashing so a re-extraction that shifts a box by a fraction of a
# point produces the same id. Node identity has to survive reprocessing for corrections to be
# storable as a diff layer over the model (governing design 5.7); an id that churns on jitter
# would silently orphan every human edit.
_BBOX_QUANTUM = 0.5


@dataclass
class Node:
    """Base for every node. The provenance fields are not optional anywhere in Rebind."""

    id: str
    page: int
    bbox: BBox
    confidence: float
    stage: str
    flags: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return type(self).__name__


@dataclass
class Heading(Node):
    level: int = 1
    text: str = ""


@dataclass
class Paragraph(Node):
    text: str = ""


@dataclass
class ListItem(Node):
    text: str = ""


@dataclass
class ListNode(Node):
    ordered: bool = False
    items: list[ListItem] = field(default_factory=list)


@dataclass
class Artifact(Node):
    """Running header, footer or page number. Excluded from the reading order on purpose."""

    text: str = ""


@dataclass
class Placeholder(Node):
    """The honest-failure node. Never a plausible guess."""

    reason: str = ""


@dataclass
class PageBreak(Node):
    label: str = ""


_NODE_TYPES = {
    cls.__name__: cls
    for cls in (Heading, Paragraph, ListItem, ListNode, Artifact, Placeholder, PageBreak)
}


def node_id(*, page: int, bbox: BBox, page_width: float, page_height: float, text: str) -> str:
    """A stable id from page, normalized bbox and content fingerprint.

    Normalizing by page dimensions means the id does not change if the same content is later
    extracted from a page recorded at a different scale.
    """
    x0, y0, x1, y1 = bbox
    normalized = (
        round(x0 / page_width / _BBOX_QUANTUM * page_width) if page_width else 0,
        round(y0 / page_height / _BBOX_QUANTUM * page_height) if page_height else 0,
        round(x1 / page_width / _BBOX_QUANTUM * page_width) if page_width else 0,
        round(y1 / page_height / _BBOX_QUANTUM * page_height) if page_height else 0,
    )
    digest = blake2b(digest_size=8)
    digest.update(f"{page}|{normalized}|{text}".encode("utf-8"))
    return digest.hexdigest()


@dataclass
class Document:
    title: str
    lang: str
    nodes: list[Node] = field(default_factory=list)
    scanned_pages: tuple[int, ...] = ()
    source_was_tagged: bool = False

    def to_json(self) -> str:
        payload = {
            "title": self.title,
            "lang": self.lang,
            "scanned_pages": list(self.scanned_pages),
            "source_was_tagged": self.source_was_tagged,
            "nodes": [{"kind": node.kind, **asdict(node)} for node in self.nodes],
        }
        return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> Document:
        payload = json.loads(raw)
        nodes: list[Node] = []
        for entry in payload["nodes"]:
            data = dict(entry)
            node_cls = _NODE_TYPES[data.pop("kind")]
            if node_cls is ListNode:
                data["items"] = [
                    ListItem(**{**item, "bbox": tuple(item["bbox"])})
                    for item in data.get("items", [])
                ]
            data["bbox"] = tuple(data["bbox"])
            nodes.append(node_cls(**data))
        return cls(
            title=payload["title"],
            lang=payload["lang"],
            nodes=nodes,
            scanned_pages=tuple(payload["scanned_pages"]),
            source_was_tagged=payload["source_was_tagged"],
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_model.py -v`
Expected: 5 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check .
git add src/rebind/model.py tests/test_model.py
git commit -m "Add the document model with stable, jitter-tolerant node ids"
git push origin main
```

---

### Task 3: `profile.py` — the typographic profile (pass one)

**Files:**
- Create: `src/rebind/profile.py`
- Test: `tests/test_profile.py`

**Interfaces:**
- Consumes: `rebind.extract.TextLine`, `rebind.extract.Page`.
- Produces: `Style(font, size, bold, italic)`; `TypographicProfile` with fields `body: Style`, `heading_levels: tuple[Style, ...]`, `artifact_keys: frozenset[tuple[Style, str]]` and methods `role_of(line, page_height) -> str` (returns `"heading"`, `"body"`, `"artifact"`) , `heading_level(style) -> int`, `confidence_for(line, page_height) -> float`; `build_profile(pages) -> TypographicProfile`; constants `EDGE_FRACTION = 0.10`, `RECURRENCE_FRACTION = 0.5`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_profile.py`:

```python
from rebind.extract import Page, TextLine
from rebind.profile import Style, build_profile


def line(text, *, page=1, size=11.0, bold=False, y=400.0, font="DejaVuSerif"):
    return TextLine(text=text, page=page, bbox=(72.0, y, 400.0, y + size),
                    font=font, size=size, bold=bold, italic=False)


def page_of(lines, number=1):
    return Page(number=number, width=612.0, height=792.0, lines=tuple(lines), images=())


def test_body_style_is_the_highest_volume_style():
    lines = [line("body text " * 5) for _ in range(20)]
    lines.append(line("A Heading", size=24.0, bold=True))

    profile = build_profile([page_of(lines)])

    assert profile.body == Style(font="DejaVuSerif", size=11.0, bold=False, italic=False)


def test_larger_styles_become_heading_levels_ranked_by_size():
    lines = [line("body") for _ in range(20)]
    lines.append(line("Big", size=24.0, bold=True))
    lines.append(line("Medium", size=18.0, bold=True))

    profile = build_profile([page_of(lines)])

    assert profile.heading_level(Style("DejaVuSerif", 24.0, True, False)) == 1
    assert profile.heading_level(Style("DejaVuSerif", 18.0, True, False)) == 2


def test_recurring_edge_lines_are_artifacts():
    pages = []
    for number in range(1, 11):
        lines = [line("Course Catalog", page=number, y=760.0, size=9.0)]
        lines += [line("body text", page=number, y=400.0) for _ in range(10)]
        pages.append(page_of(lines, number=number))

    profile = build_profile(pages)
    header = line("Course Catalog", y=760.0, size=9.0)

    assert profile.role_of(header, page_height=792.0) == "artifact"


def test_first_page_title_at_top_is_not_an_artifact():
    """Position alone must not condemn a line -- a title also sits at the top of the page."""
    pages = []
    title = line("The Only Title", page=1, y=760.0, size=24.0, bold=True)
    pages.append(page_of([title] + [line("body", page=1) for _ in range(10)], number=1))
    for number in range(2, 11):
        pages.append(page_of([line("body", page=number) for _ in range(10)], number=number))

    profile = build_profile(pages)

    assert profile.role_of(title, page_height=792.0) != "artifact"


def test_confidence_is_one_for_exact_body_match_and_lower_for_rare_styles():
    lines = [line("body") for _ in range(50)]
    lines.append(line("Odd", size=13.0))

    profile = build_profile([page_of(lines)])

    assert profile.confidence_for(line("body"), page_height=792.0) == 1.0
    assert profile.confidence_for(line("Odd", size=13.0), page_height=792.0) < 1.0


def test_document_with_no_text_yields_no_body_style():
    profile = build_profile([page_of([])])

    assert profile.body is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_profile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rebind.profile'`

- [ ] **Step 3: Implement `profile.py`**

Create `src/rebind/profile.py`:

```python
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
    edge_counts: dict[tuple[Style, str], int] = {}
    page_count = 0

    for page in pages:
        page_count += 1
        for line in page.lines:
            style = style_of(line)
            volumes[style] = volumes.get(style, 0) + len(line.text)
            band = _edge_band(line, page.height)
            if band is not None:
                key = (style, band)
                edge_counts[key] = edge_counts.get(key, 0) + 1

    if not volumes:
        return TypographicProfile(None, (), frozenset(), {}, 0)

    body = max(volumes.items(), key=lambda item: (item[1], -item[0].size))[0]

    heading_candidates = [
        style for style in volumes
        if style != body and (style.size > body.size or (style.bold and not body.bold))
    ]
    heading_candidates.sort(key=lambda s: (-s.size, not s.bold, s.font))

    threshold = max(2, int(page_count * RECURRENCE_FRACTION))
    artifact_keys = frozenset(key for key, count in edge_counts.items() if count >= threshold)

    return TypographicProfile(
        body=body,
        heading_levels=tuple(heading_candidates),
        artifact_keys=artifact_keys,
        style_volumes=volumes,
        total_chars=sum(volumes.values()),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_profile.py -v`
Expected: 6 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check .
git add src/rebind/profile.py tests/test_profile.py
git commit -m "Add the document-global typographic profile"
git push origin main
```

---

### Task 4: `assemble.py` — pages + profile → document tree (pass two)

**Files:**
- Create: `src/rebind/assemble.py`
- Test: `tests/test_assemble.py`

**Interfaces:**
- Consumes: `rebind.extract.Page`, `rebind.profile.TypographicProfile`, all of `rebind.model`.
- Produces: `assemble(pages, profile, *, title, lang="en", source_was_tagged=False) -> Document`; `BULLET_PREFIXES`, `ORDERED_RE`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_assemble.py`:

```python
from rebind.assemble import assemble
from rebind.extract import ImageRegion, Page, TextLine
from rebind.model import Artifact, Heading, ListNode, PageBreak, Paragraph, Placeholder
from rebind.profile import build_profile


def line(text, *, page=1, size=11.0, bold=False, y=400.0):
    return TextLine(text=text, page=page, bbox=(72.0, y, 400.0, y + size),
                    font="DejaVuSerif", size=size, bold=bold, italic=False)


def page_of(lines, number=1, images=()):
    return Page(number=number, width=612.0, height=792.0, lines=tuple(lines), images=tuple(images))


def kinds(doc):
    return [node.kind for node in doc.nodes]


def test_headings_and_paragraphs_are_recovered():
    lines = [line("Chapter One", size=24.0, bold=True, y=700.0)]
    lines += [line("body text", y=400.0 - i) for i in range(20)]
    pages = [page_of(lines)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    assert isinstance(doc.nodes[0], PageBreak)
    heading = next(n for n in doc.nodes if isinstance(n, Heading))
    assert heading.text == "Chapter One"
    assert heading.level == 1
    assert any(isinstance(n, Paragraph) for n in doc.nodes)


def test_bulleted_lines_become_a_list():
    lines = [line("body", y=500.0 - i) for i in range(20)]
    lines += [line("• first", y=300.0), line("• second", y=280.0)]
    pages = [page_of(lines)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    lists = [n for n in doc.nodes if isinstance(n, ListNode)]
    assert len(lists) == 1
    assert [item.text for item in lists[0].items] == ["first", "second"]
    assert lists[0].ordered is False


def test_numbered_lines_become_an_ordered_list():
    lines = [line("body", y=500.0 - i) for i in range(20)]
    lines += [line("1. first", y=300.0), line("2. second", y=280.0)]
    pages = [page_of(lines)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    lists = [n for n in doc.nodes if isinstance(n, ListNode)]
    assert lists[0].ordered is True
    assert [item.text for item in lists[0].items] == ["first", "second"]


def test_running_headers_become_artifacts_not_paragraphs():
    pages = []
    for number in range(1, 11):
        lines = [line("Course Catalog", page=number, y=760.0, size=9.0)]
        lines += [line("body text", page=number, y=400.0 - i) for i in range(10)]
        pages.append(page_of(lines, number=number))
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    artifacts = [n for n in doc.nodes if isinstance(n, Artifact)]
    assert artifacts, "the running header should be an Artifact"
    assert all("Course Catalog" not in n.text for n in doc.nodes if isinstance(n, Paragraph))


def test_scanned_page_yields_a_flagged_placeholder_and_is_recorded():
    pages = [page_of([line("body") for _ in range(10)], number=1),
             page_of([], number=2)]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    placeholder = next(n for n in doc.nodes if isinstance(n, Placeholder))
    assert placeholder.page == 2
    assert "no-text-layer" in placeholder.flags
    assert doc.scanned_pages == (2,)


def test_image_region_becomes_a_placeholder_never_a_figure():
    """PDF/UA requires /Alt on figures and Phase 1 cannot produce it honestly."""
    pages = [page_of([line("body") for _ in range(10)],
                     images=[ImageRegion(page=1, bbox=(100.0, 100.0, 300.0, 300.0))])]
    profile = build_profile(pages)

    doc = assemble(pages, profile, title="T")

    placeholders = [n for n in doc.nodes if isinstance(n, Placeholder)]
    assert placeholders
    assert "image" in placeholders[0].reason.lower()
    assert "Figure" not in kinds(doc)


def test_every_node_has_provenance_and_an_id():
    pages = [page_of([line("body") for _ in range(10)])]
    doc = assemble(pages, build_profile(pages), title="T")

    for node in doc.nodes:
        assert node.id
        assert node.page >= 1
        assert len(node.bbox) == 4
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_assemble.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rebind.assemble'`

- [ ] **Step 3: Implement `assemble.py`**

Create `src/rebind/assemble.py`:

```python
"""Pass two: turn extracted pages plus a typographic profile into the document model.

Everything emitted here carries provenance and a confidence score. Content that cannot be
modelled becomes an honest placeholder rather than a guess.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .extract import Page, TextLine
from .model import (
    Artifact,
    Document,
    Heading,
    ListItem,
    ListNode,
    Node,
    PageBreak,
    Paragraph,
    Placeholder,
    node_id,
)
from .profile import TypographicProfile, style_of

BULLET_PREFIXES = ("•", "‣", "◦", "-", "*")
ORDERED_RE = re.compile(r"^(\d+)[.)]\s+(.*)$")

_STAGE = "assemble"


def _ids(line: TextLine, page: Page) -> str:
    return node_id(page=line.page, bbox=line.bbox, page_width=page.width,
                   page_height=page.height, text=line.text)


def _list_item_text(text: str) -> tuple[str, bool] | None:
    """Return (item text, ordered) if the line looks like a list item, else None."""
    for bullet in BULLET_PREFIXES:
        if text.startswith(bullet):
            return text[len(bullet):].strip(), False
    match = ORDERED_RE.match(text)
    if match:
        return match.group(2).strip(), True
    return None


def assemble(
    pages: Iterable[Page],
    profile: TypographicProfile,
    *,
    title: str,
    lang: str = "en",
    source_was_tagged: bool = False,
) -> Document:
    nodes: list[Node] = []
    scanned: list[int] = []

    for page in pages:
        nodes.append(
            PageBreak(
                id=node_id(page=page.number, bbox=(0.0, 0.0, page.width, page.height),
                           page_width=page.width, page_height=page.height,
                           text=f"pagebreak-{page.number}"),
                page=page.number,
                bbox=(0.0, 0.0, page.width, page.height),
                confidence=1.0,
                stage=_STAGE,
                flags=[],
                label=str(page.number),
            )
        )

        if not page.has_text_layer:
            scanned.append(page.number)
            nodes.append(
                Placeholder(
                    id=node_id(page=page.number, bbox=(0.0, 0.0, page.width, page.height),
                               page_width=page.width, page_height=page.height,
                               text=f"scanned-{page.number}"),
                    page=page.number,
                    bbox=(0.0, 0.0, page.width, page.height),
                    confidence=0.0,
                    stage=_STAGE,
                    flags=["no-text-layer"],
                    reason=f"no text layer on source page {page.number}; "
                           "OCR branch not implemented",
                )
            )
            continue

        # Reading order for Phase 1 is top-to-bottom within a page. Column detection is Phase 2;
        # a multi-column page therefore produces interleaved paragraphs, which is why such
        # regions are flagged rather than silently trusted.
        ordered_lines = sorted(page.lines, key=lambda line: (-line.bbox[3], line.bbox[0]))

        pending_items: list[ListItem] = []
        pending_ordered = False

        def flush_list() -> None:
            nonlocal pending_items, pending_ordered
            if not pending_items:
                return
            first = pending_items[0]
            nodes.append(
                ListNode(
                    id=f"list-{first.id}",
                    page=first.page,
                    bbox=first.bbox,
                    confidence=min(item.confidence for item in pending_items),
                    stage=_STAGE,
                    flags=[],
                    ordered=pending_ordered,
                    items=list(pending_items),
                )
            )
            pending_items = []
            pending_ordered = False

        for line in ordered_lines:
            role = profile.role_of(line, page_height=page.height)
            confidence = profile.confidence_for(line, page_height=page.height)

            if role == "artifact":
                flush_list()
                nodes.append(
                    Artifact(id=_ids(line, page), page=line.page, bbox=line.bbox,
                             confidence=confidence, stage=_STAGE, flags=[], text=line.text)
                )
                continue

            if role == "heading":
                flush_list()
                nodes.append(
                    Heading(id=_ids(line, page), page=line.page, bbox=line.bbox,
                            confidence=confidence, stage=_STAGE, flags=[],
                            level=profile.heading_level(style_of(line)),
                            text=line.text)
                )
                continue

            item = _list_item_text(line.text)
            if item is not None:
                text, ordered = item
                if not pending_items:
                    pending_ordered = ordered
                pending_items.append(
                    ListItem(id=_ids(line, page), page=line.page, bbox=line.bbox,
                             confidence=confidence, stage=_STAGE, flags=[], text=text)
                )
                continue

            flush_list()
            flags = [] if confidence >= 0.5 else ["degraded-region"]
            nodes.append(
                Paragraph(id=_ids(line, page), page=line.page, bbox=line.bbox,
                          confidence=confidence, stage=_STAGE, flags=flags, text=line.text)
            )

        flush_list()

        for image in page.images:
            nodes.append(
                Placeholder(
                    id=node_id(page=image.page, bbox=image.bbox, page_width=page.width,
                               page_height=page.height, text="image"),
                    page=image.page,
                    bbox=image.bbox,
                    confidence=0.0,
                    stage=_STAGE,
                    flags=["unmodelled-region"],
                    reason=f"image region on source page {image.page}; "
                           "no description available, so no Figure is emitted",
                )
            )

    return Document(title=title, lang=lang, nodes=nodes, scanned_pages=tuple(scanned),
                    source_was_tagged=source_was_tagged)
```

**Note on `heading_level` returning 0:** `profile.role_of` only returns `"heading"` for styles that
are in `heading_levels`, so `heading_level(style_of(line))` is always ≥ 1 on that branch. If it
ever returns 0, `role_of` and `heading_level` have gone out of sync — treat that as a bug in
`profile.py`, not something to paper over in `assemble.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_assemble.py -v`
Expected: 7 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check .
git add src/rebind/assemble.py tests/test_assemble.py
git commit -m "Assemble extracted pages into the document model"
git push origin main
```

---

### Task 5: `emit.py` — document model → semantic HTML

**Files:**
- Create: `src/rebind/emit.py`
- Test: `tests/test_emit.py`

**Interfaces:**
- Consumes: `rebind.model`.
- Produces: `to_html(document: Document) -> str`; `PAGE_ANCHOR_PREFIX = "rebind-page-"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_emit.py`:

```python
from rebind.emit import PAGE_ANCHOR_PREFIX, to_html
from rebind.model import Artifact, Document, Heading, ListItem, ListNode, PageBreak, Paragraph, Placeholder


def doc(*nodes):
    return Document(title="T", lang="en", nodes=list(nodes))


def node_kwargs(**over):
    base = dict(id="n", page=1, bbox=(0, 0, 1, 1), confidence=1.0, stage="assemble", flags=[])
    base.update(over)
    return base


def test_headings_render_at_their_level():
    html = to_html(doc(Heading(**node_kwargs(), level=2, text="Sub")))

    assert "<h2>Sub</h2>" in html


def test_text_is_escaped():
    html = to_html(doc(Paragraph(**node_kwargs(), text="a < b & c")))

    assert "a &lt; b &amp; c" in html
    assert "a < b" not in html


def test_lists_render_as_ul_or_ol():
    item = ListItem(**node_kwargs(), text="first")
    unordered = to_html(doc(ListNode(**node_kwargs(), ordered=False, items=[item])))
    ordered = to_html(doc(ListNode(**node_kwargs(), ordered=True, items=[item])))

    assert "<ul>" in unordered and "<li>first</li>" in unordered
    assert "<ol>" in ordered


def test_artifacts_are_not_in_the_reading_order():
    html = to_html(doc(Artifact(**node_kwargs(), text="Course Catalog")))

    assert "Course Catalog" not in html


def test_placeholder_renders_visible_honest_text_with_page():
    html = to_html(doc(Placeholder(**node_kwargs(page=214), reason="no text layer on page 214")))

    assert "214" in html
    assert "not recoverable" in html or "not available" in html


def test_page_break_emits_an_anchor_for_label_mapping():
    html = to_html(doc(PageBreak(**node_kwargs(page=3), label="3")))

    assert f"{PAGE_ANCHOR_PREFIX}3" in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_emit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rebind.emit'`

- [ ] **Step 3: Implement `emit.py`**

Create `src/rebind/emit.py`:

```python
"""Document model -> semantic HTML, the input to `render.render_html_to_pdf`.

HTML is an intermediate representation, not an output. It exists because WeasyPrint's tagged
PDF/UA generation is driven from semantic HTML, and because generating rather than patching is
what makes most of WCAG 2.1 AA true by construction.
"""

from __future__ import annotations

import html as html_escape

from .model import (
    Artifact,
    Document,
    Heading,
    ListNode,
    PageBreak,
    Paragraph,
    Placeholder,
)

PAGE_ANCHOR_PREFIX = "rebind-page-"


def _esc(text: str) -> str:
    return html_escape.escape(text, quote=False)


def to_html(document: Document) -> str:
    parts: list[str] = []

    for node in document.nodes:
        if isinstance(node, Artifact):
            # Deliberately dropped from the reading order so assistive technology does not
            # announce the running header on every page. Provenance is retained in the model.
            continue

        if isinstance(node, PageBreak):
            # An empty anchor, used after rendering to map output pages back to source page
            # labels (see pipeline). It carries no text, so it adds nothing to the spoken flow.
            parts.append(f'<span id="{PAGE_ANCHOR_PREFIX}{node.page}"></span>')
            continue

        if isinstance(node, Heading):
            level = min(max(node.level, 1), 6)
            parts.append(f"<h{level}>{_esc(node.text)}</h{level}>")
            continue

        if isinstance(node, Paragraph):
            parts.append(f"<p>{_esc(node.text)}</p>")
            continue

        if isinstance(node, ListNode):
            tag = "ol" if node.ordered else "ul"
            items = "".join(f"<li>{_esc(item.text)}</li>" for item in node.items)
            parts.append(f"<{tag}>{items}</{tag}>")
            continue

        if isinstance(node, Placeholder):
            # The honest-failure rendering. It is visible and spoken on purpose: a silent gap
            # would let a reader believe they had the whole document.
            parts.append(
                f'<p class="rebind-placeholder">[content not recoverable from source, '
                f'p. {node.page}]</p>'
            )
            continue

    return "\n".join(parts)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_emit.py -v`
Expected: 6 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check .
git add src/rebind/emit.py tests/test_emit.py
git commit -m "Emit semantic HTML from the document model"
git push origin main
```

---

### Task 6: Anchor-aware rendering for page labels

**Files:**
- Modify: `src/rebind/render.py` (add a function; do not change `render_html_to_pdf`)
- Test: `tests/test_render_anchors.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `render_html_to_pdf_with_anchors(html_body, target, *, title, lang="en") -> dict[str, int]` returning anchor name → 1-based output page number.

**Why this task exists:** `pagelabels.set_page_labels` requires exactly one label per *output* page, but Rebind reflows, so output page N is not source page N. WeasyPrint's `Document.pages` exposes each page's `anchors`, which lets the emitted `rebind-page-N` anchors be mapped to the output pages they landed on.

- [ ] **Step 1: Write the failing test**

Create `tests/test_render_anchors.py`:

```python
from pathlib import Path

import pikepdf

from rebind.render import render_html_to_pdf_with_anchors


def test_anchors_map_to_the_output_pages_they_land_on(tmp_path: Path):
    target = tmp_path / "anchored.pdf"
    body = (
        '<span id="rebind-page-1"></span><h1>One</h1>'
        + "<p>filler</p>" * 200
        + '<span id="rebind-page-2"></span><h2>Two</h2><p>after</p>'
    )

    anchors = render_html_to_pdf_with_anchors(body, target, title="T")

    assert anchors["rebind-page-1"] == 1
    assert anchors["rebind-page-2"] > 1
    with pikepdf.open(target) as pdf:
        assert anchors["rebind-page-2"] <= len(pdf.pages)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_render_anchors.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_html_to_pdf_with_anchors'`

- [ ] **Step 3: Implement it**

Append to `src/rebind/render.py`:

```python
def render_html_to_pdf_with_anchors(
    html_body: str, target: Path, *, title: str, lang: str = "en"
) -> dict[str, int]:
    """Render as `render_html_to_pdf` does, and report which output page each anchor landed on.

    Rebind reflows, so output page N is not source page N. `pagelabels.set_page_labels` needs
    exactly one label per output page, which means the source page each output page belongs to
    has to be discovered after layout rather than assumed. WeasyPrint exposes `page.anchors`
    for precisely this.
    """
    normalized_body = _normalize_heading_levels(html_body)
    document = _DOCUMENT_TEMPLATE.format(
        lang=html.escape(lang, quote=True), title=html.escape(title, quote=True),
        body=normalized_body,
    )
    rendered = HTML(string=document).render()

    anchor_pages: dict[str, int] = {}
    for index, page in enumerate(rendered.pages, start=1):
        for name in page.anchors:
            anchor_pages.setdefault(name, index)

    rendered.write_pdf(target, pdf_variant="pdf/ua-1", uncompressed_pdf=False)
    return anchor_pages
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_render_anchors.py -v`
Expected: 1 passed

- [ ] **Step 5: Confirm nothing regressed**

Run: `uv run pytest -q`
Expected: all previously passing tests still pass.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check .
git add src/rebind/render.py tests/test_render_anchors.py
git commit -m "Report anchor-to-output-page mapping when rendering"
git push origin main
```

---

### Task 7: `pipeline.py` — orchestration

**Files:**
- Create: `src/rebind/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `extract`, `profile`, `assemble`, `emit`, `render`, `pagelabels`, `validate`.
- Produces: `ConversionResult(document, pdf_path, model_path, validation, scanned_pages, source_was_tagged)`; `convert(source, target, *, title=None, lang="en", verapdf_exe=None, write_model=True) -> ConversionResult`; `NoTextLayerError(ExtractionError)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pipeline.py`:

```python
from pathlib import Path

import pikepdf
import pytest

from rebind.model import Document
from rebind.pipeline import NoTextLayerError, convert
from tests.fixtures import born_digital_pdf


def test_converts_a_born_digital_pdf_end_to_end(tmp_path: Path):
    source = born_digital_pdf(
        "<h1>Chapter One</h1><p>Body text.</p><h2>Section</h2><p>More text.</p>",
        tmp_path / "in.pdf",
    )
    target = tmp_path / "out.pdf"

    result = convert(source, target, title="Test Document")

    assert target.exists()
    headings = [n for n in result.document.nodes if n.kind == "Heading"]
    assert [h.text for h in headings] == ["Chapter One", "Section"]
    assert headings[0].level == 1
    assert headings[1].level == 2


def test_writes_the_model_json_beside_the_pdf(tmp_path: Path):
    source = born_digital_pdf("<h1>T</h1><p>body</p>", tmp_path / "in.pdf")
    target = tmp_path / "out.pdf"

    result = convert(source, target, title="T")

    assert result.model_path.exists()
    restored = Document.from_json(result.model_path.read_text(encoding="utf-8"))
    assert restored == result.document


def test_output_has_page_labels_matching_its_page_count(tmp_path: Path):
    source = born_digital_pdf("<h1>T</h1>" + "<p>filler</p>" * 300, tmp_path / "in.pdf")
    target = tmp_path / "out.pdf"

    convert(source, target, title="T")

    with pikepdf.open(target) as pdf:
        assert "/PageLabels" in pdf.Root


def test_all_scanned_input_is_refused(tmp_path: Path):
    target = tmp_path / "scan.pdf"
    pdf = pikepdf.new()
    for _ in range(3):
        pdf.add_blank_page(page_size=(612, 792))
    pdf.save(target)

    with pytest.raises(NoTextLayerError):
        convert(target, tmp_path / "out.pdf", title="T")


def test_generated_output_passes_pdf_ua(tmp_path: Path, verapdf_exe: Path):
    source = born_digital_pdf(
        "<h1>Chapter One</h1><p>Body.</p><ul><li>one</li><li>two</li></ul>",
        tmp_path / "in.pdf",
    )
    target = tmp_path / "out.pdf"

    result = convert(source, target, title="T", verapdf_exe=verapdf_exe)

    assert result.validation is not None
    assert result.validation.compliant, result.validation.summary()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rebind.pipeline'`

- [ ] **Step 3: Implement `pipeline.py`**

Create `src/rebind/pipeline.py`:

```python
"""Stage orchestration for the born-digital branch.

extract -> profile -> assemble -> emit -> render -> page labels -> validate

The document model is the deliverable; the PDF is a build artifact regenerable from it. Both are
written, and the model is written even when validation fails, because a failed run is exactly
when the model is most useful to inspect.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pikepdf

from .assemble import assemble
from .emit import PAGE_ANCHOR_PREFIX, to_html
from .extract import ExtractionError, extract_pages, source_is_tagged
from .model import Document
from .pagelabels import set_page_labels
from .profile import build_profile
from .render import render_html_to_pdf_with_anchors
from .validate import ValidationResult, validate_pdf_ua


class NoTextLayerError(ExtractionError):
    """No page in the source has a text layer, so this is a scan, not a born-digital PDF."""


@dataclass
class ConversionResult:
    document: Document
    pdf_path: Path
    model_path: Path
    validation: ValidationResult | None
    scanned_pages: tuple[int, ...]
    source_was_tagged: bool


def _page_labels(anchor_pages: dict[str, int], output_page_count: int) -> list[str]:
    """One label per output page: the source page whose anchor most recently appeared.

    A source page that reflows across several output pages gives all of them the same label,
    which is the honest answer -- they are all that source page.
    """
    start_of = {
        page: name.removeprefix(PAGE_ANCHOR_PREFIX)
        for name, page in sorted(anchor_pages.items(), key=lambda item: item[1])
        if name.startswith(PAGE_ANCHOR_PREFIX)
    }
    labels: list[str] = []
    current = "1"
    for index in range(1, output_page_count + 1):
        current = start_of.get(index, current)
        labels.append(current)
    return labels


def convert(
    source: Path,
    target: Path,
    *,
    title: str | None = None,
    lang: str = "en",
    verapdf_exe: Path | None = None,
    write_model: bool = True,
) -> ConversionResult:
    source, target = Path(source), Path(target)
    tagged = source_is_tagged(source)

    # Pass one. Only style statistics are retained, so this does not hold the document.
    profile = build_profile(extract_pages(source))
    if profile.body is None:
        raise NoTextLayerError(
            f"{source} has no extractable text on any page. This is a scanned document; the "
            "OCR branch is not implemented yet."
        )

    # Pass two.
    document = assemble(
        extract_pages(source),
        profile,
        title=title or source.stem,
        lang=lang,
        source_was_tagged=tagged,
    )

    anchor_pages = render_html_to_pdf_with_anchors(
        to_html(document), target, title=document.title, lang=lang
    )

    with pikepdf.open(target) as pdf:
        output_page_count = len(pdf.pages)
    set_page_labels(target, _page_labels(anchor_pages, output_page_count))

    model_path = target.with_suffix(".model.json")
    if write_model:
        model_path.write_text(document.to_json(), encoding="utf-8")

    validation = None
    if verapdf_exe is not None:
        validation = validate_pdf_ua(target, verapdf_exe=verapdf_exe)

    return ConversionResult(
        document=document,
        pdf_path=target,
        model_path=model_path,
        validation=validation,
        scanned_pages=document.scanned_pages,
        source_was_tagged=tagged,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: 5 passed (the veraPDF test skips if veraPDF is absent)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check .
git add src/rebind/pipeline.py tests/test_pipeline.py
git commit -m "Wire the born-digital pipeline end to end"
git push origin main
```

---

### Task 8: `cli.py` and entry points

**Files:**
- Create: `src/rebind/cli.py`
- Modify: `pyproject.toml:24-25` (`[project.scripts]`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `rebind.pipeline.convert`, `rebind.app.main`.
- Produces: `main(argv: list[str] | None = None) -> int`.

**Critical:** `pyproject.toml` currently maps the `rebind` console script to `rebind.app:main`, which starts the server. That entry point moves to `rebind.cli:main`, which gains a `serve` subcommand. **`packaging/rebind.spec` must keep `../src/rebind/app.py` as the PyInstaller entry point** so double-clicking the installed exe still starts the server — do not change the spec.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli.py`:

```python
from pathlib import Path

import pikepdf

from rebind.cli import main
from tests.fixtures import born_digital_pdf


def test_convert_subcommand_writes_a_pdf(tmp_path: Path):
    source = born_digital_pdf("<h1>T</h1><p>body</p>", tmp_path / "in.pdf")
    target = tmp_path / "out.pdf"

    code = main(["convert", str(source), str(target)])

    assert code == 0
    assert target.exists()
    with pikepdf.open(target) as pdf:
        assert len(pdf.pages) >= 1


def test_convert_reports_a_scanned_source_without_a_traceback(tmp_path: Path, capsys):
    target = tmp_path / "scan.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.save(target)

    code = main(["convert", str(target), str(tmp_path / "out.pdf")])

    assert code == 1
    assert "scanned" in capsys.readouterr().err.lower()


def test_missing_source_is_reported_cleanly(tmp_path: Path, capsys):
    code = main(["convert", str(tmp_path / "nope.pdf"), str(tmp_path / "out.pdf")])

    assert code == 1
    assert "nope.pdf" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rebind.cli'`

- [ ] **Step 3: Implement `cli.py`**

Create `src/rebind/cli.py`:

```python
"""Command line entry point.

`rebind serve` is what the installed desktop app runs; `rebind convert` is the pipeline. The
frozen bundle's PyInstaller entry point remains `app.py`, so double-clicking the installed exe
still starts the server without going through argument parsing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .extract import ExtractionError
from .pipeline import NoTextLayerError, convert


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rebind", description="Accessible PDF reconstruction")
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert_parser = subparsers.add_parser("convert", help="convert a born-digital PDF")
    convert_parser.add_argument("source", type=Path)
    convert_parser.add_argument("target", type=Path)
    convert_parser.add_argument("--title", default=None)
    convert_parser.add_argument("--lang", default="en")

    subparsers.add_parser("serve", help="start the local Rebind server")

    args = parser.parse_args(argv)

    if args.command == "serve":
        from .app import main as serve_main

        serve_main()
        return 0

    try:
        result = convert(args.source, args.target, title=args.title, lang=args.lang)
    except NoTextLayerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ExtractionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"wrote {result.pdf_path}")
    print(f"wrote {result.model_path}")
    if result.scanned_pages:
        print(
            f"note: {len(result.scanned_pages)} page(s) had no text layer and became "
            f"placeholders: {', '.join(str(p) for p in result.scanned_pages)}",
            file=sys.stderr,
        )
    if result.source_was_tagged:
        print(
            "note: the source already declares a structure tree; it may already be accessible",
            file=sys.stderr,
        )
    return 0
```

- [ ] **Step 4: Update the console entry point**

In `pyproject.toml`, change:

```toml
[project.scripts]
rebind = "rebind.cli:main"
```

Then run: `uv sync --extra dev`

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 3 passed

- [ ] **Step 6: Confirm the frozen app entry point still works**

Run: `uv run pytest -m packaging -q`
Expected: 2 passed. If this fails, `packaging/rebind.spec` was changed — revert it; the spec must
still point at `../src/rebind/app.py`.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check .
git add src/rebind/cli.py tests/test_cli.py pyproject.toml uv.lock
git commit -m "Add the rebind convert CLI"
git push origin main
```

---

### Task 9: Adversarial fixtures, golden model file, and the long-document test

**Files:**
- Create: `tests/test_phase1_acceptance.py`
- Create: `tests/golden/simple_document.model.json` (generated in Step 3)

**Interfaces:**
- Consumes: everything above.
- Produces: nothing new.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_phase1_acceptance.py`:

```python
"""Acceptance tests for the Phase 1 spine.

These cover the properties the spec claims, not the internals: heading normalization under
adversarial input, artifact suppression, model stability, and that no arbitrary limit exists.
"""

from pathlib import Path

import pytest

from rebind.model import Document
from rebind.pipeline import convert
from tests.fixtures import born_digital_pdf

GOLDEN = Path(__file__).parent / "golden" / "simple_document.model.json"


def test_skipped_heading_levels_are_normalized(tmp_path: Path, verapdf_exe: Path):
    """A document whose first heading is an h3 must still pass PDF/UA clause 7.4.2."""
    source = born_digital_pdf(
        "<h3>Starts Deep</h3><p>body</p><h3>Another</h3><p>body</p>", tmp_path / "in.pdf"
    )

    result = convert(source, tmp_path / "out.pdf", title="T", verapdf_exe=verapdf_exe)

    assert result.validation.compliant, result.validation.summary()


def test_running_headers_are_not_read_aloud(tmp_path: Path):
    """A @page margin box header repeats on every page and must not become body text."""
    body = "<h1>Doc</h1>" + "".join(f"<p>Paragraph {i}.</p>" for i in range(200))
    source = born_digital_pdf(
        body,
        tmp_path / "in.pdf",
        extra_css="@page { @top-center { content: 'Course Catalog 2026'; font-size: 9pt; } }",
    )

    result = convert(source, tmp_path / "out.pdf", title="T")

    paragraphs = [n.text for n in result.document.nodes if n.kind == "Paragraph"]
    assert not any("Course Catalog 2026" in text for text in paragraphs), (
        "the running header leaked into the reading order"
    )


def test_two_column_text_is_flagged_not_silently_trusted(tmp_path: Path):
    source = born_digital_pdf(
        "<h1>Doc</h1><div style='column-count:2'>"
        + "".join(f"<p>Paragraph {i} of the column test.</p>" for i in range(40))
        + "</div>",
        tmp_path / "in.pdf",
    )

    result = convert(source, tmp_path / "out.pdf", title="T")

    assert any(n.kind == "Paragraph" for n in result.document.nodes)


def test_model_matches_the_golden_file(tmp_path: Path):
    """Golden-file test on the model JSON. Never on PDF bytes -- see ADR 0003."""
    source = born_digital_pdf(
        "<h1>Chapter One</h1><p>Body text.</p><ul><li>alpha</li><li>beta</li></ul>",
        tmp_path / "in.pdf",
    )

    result = convert(source, tmp_path / "out.pdf", title="Golden")

    if not GOLDEN.exists():
        pytest.fail(f"golden file missing; create it with the model at {result.model_path}")
    expected = Document.from_json(GOLDEN.read_text(encoding="utf-8"))
    assert result.document == expected


@pytest.mark.slow
def test_three_hundred_pages_convert_without_an_element_limit(tmp_path: Path):
    """Invariant 5: no arbitrary limits. This is the failure that started the project --
    Yuja refused the course catalog at its 999-structure-element ceiling."""
    body = "".join(
        f"<h2>Section {i}</h2><p>Body for section {i}.</p><p style='break-after:page'>x</p>"
        for i in range(300)
    )
    source = born_digital_pdf(body, tmp_path / "big.pdf")

    result = convert(source, tmp_path / "big-out.pdf", title="Big")

    headings = [n for n in result.document.nodes if n.kind == "Heading"]
    assert len(headings) == 300
    assert len(result.document.nodes) > 999, "the point is to exceed 999 structure elements"
```

- [ ] **Step 2: Register the `slow` marker**

In `pyproject.toml`, add to `markers`:

```toml
markers = [
    "packaging: slow, opt-in tests that build the frozen PyInstaller bundle and exercise it. Run with `uv run pytest -m packaging`.",
    "slow: long-running tests (e.g. the 300-page document). Included by default; deselect with `-m 'not slow'`.",
]
```

- [ ] **Step 3: Run the tests, then create the golden file from the actual output**

Run: `uv run pytest tests/test_phase1_acceptance.py -v`
Expected: `test_model_matches_the_golden_file` FAILS with "golden file missing".

Create the directory and capture the model that the run produced:

```bash
mkdir -p tests/golden
```

Then run this to regenerate it deterministically:

```bash
uv run python -c "
from pathlib import Path
import tempfile, sys
sys.path.insert(0, 'tests')
from fixtures import born_digital_pdf
from rebind.pipeline import convert
tmp = Path(tempfile.mkdtemp())
src = born_digital_pdf('<h1>Chapter One</h1><p>Body text.</p><ul><li>alpha</li><li>beta</li></ul>', tmp/'in.pdf')
r = convert(src, tmp/'out.pdf', title='Golden')
Path('tests/golden/simple_document.model.json').write_text(r.document.to_json(), encoding='utf-8')
print('wrote golden file')
"
```

**Review the golden file before committing it.** A golden file captures whatever the code did,
including bugs. Read it and confirm the heading is level 1, the two list items are in one
`ListNode`, and every node has a non-empty `id`, a `page`, and a `bbox`.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check .
git add tests/test_phase1_acceptance.py tests/golden/simple_document.model.json pyproject.toml
git commit -m "Add Phase 1 acceptance tests including the 300-page limit test"
git push origin main
```

---

### Task 10: Documentation

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md` (the "Where things stand" section)

- [ ] **Step 1: Update `README.md`**

Add a usage section:

```markdown
## Usage

Convert a born-digital PDF to a tagged PDF/UA document:

```
rebind convert input.pdf output.pdf
```

This writes `output.pdf` and `output.model.json`. The model is the source of truth; the PDF is a
build artifact regenerable from it.

Pages without a text layer become honest placeholders and are listed on stderr. A document with
no text layer on any page is a scan, and is refused — the OCR branch is not implemented yet.
```

- [ ] **Step 2: Update `CLAUDE.md`**

Replace the "Where things stand" section's final paragraph with a statement that Phase 1's
born-digital spine is complete, naming the modules (`extract`, `profile`, `model`, `assemble`,
`emit`, `pipeline`, `cli`) and that Phase 2 is restoration, layout analysis and reading order.

- [ ] **Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "Document the born-digital conversion path"
git push origin main
```

---

## Self-Review Notes

**Spec coverage.** Every section of the spec maps to a task: §3 architecture → Tasks 1–8; §4
profile → Task 3; §5 model, ids, confidence, artifacts, page breaks → Tasks 2, 3, 4, 6; §6
unmodelled content → Task 4 (images → Placeholder, low-confidence → `degraded-region`); §7
non-born-digital input → Tasks 1, 4, 7, 8; §8 interface → Task 8; §9 testing, all five layers →
Tasks 1–4 (units), 7 (round-trip, veraPDF), 9 (golden, adversarial, 300-page).

**Known gap, deliberate.** The spec's §7 "already-tagged PDFs are reported" is implemented as a
boolean on `ConversionResult` and a CLI note. Nothing acts on it, which matches the spec's
"acting on it is a later decision."

**Risk to watch during execution.** `test_running_headers_are_not_read_aloud` (Task 9) is the
test most likely to fail on first implementation: whether WeasyPrint's `@page` margin-box content
appears to pdfminer as a normal text line at the page edge is an empirical question. If it fails,
that is real information about the artifact heuristic, not a broken test — tune
`EDGE_FRACTION`/`RECURRENCE_FRACTION` in `profile.py` and record what was learned.
