# Rebind

Accessible PDF remediation for damaged library scans.

Rebind takes a PDF — scanned or born-digital, tagged or not — and produces a PDF/UA-2 document that
validates with veraPDF at zero failures. It preserves each page as it is and adds a selectable text
layer and a structure tree. Everything runs on the local machine: no API key, no GPU, no network.

## Install

**Windows.** Download `rebind-setup.exe` from the
[latest release](https://github.com/tfscharff/rebind/releases/latest) and run it. No admin rights
needed. The installer is unsigned, so SmartScreen warns on first run — choose *More info* →
*Run anyway*.

Open Rebind from the Start menu. It opens a page in your browser; that is the app. Closing the tab
quits it.

**From source** (Python 3.12):

```bash
git clone https://github.com/tfscharff/rebind
cd rebind
uv sync                # https://docs.astral.sh/uv/
uv run rebind serve
```

On Windows, `rebind.cmd` in the clone does the same.

```bash
uv run rebind convert in.pdf out.pdf     # no app
```

`REBIND_DEBUG=1` prints a full traceback on failure.

## Per page

- **Born-digital** — kept verbatim, vector text unchanged, tagged with reading order and structure.
- **Scanned** — image kept, with an invisible selectable text layer from on-device OCR (RapidOCR on
  the CPU, models bundled). Deskewed and denoised before recognition.
- **No recoverable text** (blank, or an image with no words) — kept as is, and reported.

A scan that already carries another tool's invisible OCR layer has it removed first, so the output
holds one copy of each word. Visible marks are untouched and the page is not re-rendered.

Text is never fabricated. Every text node traces to recognizer output with a confidence score;
below threshold it becomes `[text not recoverable from source scan, p. 214]`.

## Reading order

Recovered by recursive XY-cut over the text-line boxes, including the layout where a full-width
heading sits above the columns and hides the gutter. A figure's callout labels are held out of the
cut and spliced back in at the height they sit at.

Within a block, items on one line read left to right. Two lines count as one line when their boxes
share most of the shorter one's height. The rule never crosses a gutter: in two-column text the
whole left column is read before the right.

Born-digital pages name their own structure through markup. Scanned pages do not, so structure is
inferred from geometry: line height against the body median, whitespace above and below, and
alignment into recurring column positions.

## Structure tree

- **Headings** `/H1`–`/H6`, level-normalized so the sequence starts at H1 and skips no level.
  Born-digital from font size; scanned from geometry — distinctly taller than body text, set apart
  by whitespace, shorter than the column. A title wrapping onto a second line is one heading:
  consecutive lines set the same way, stacked with tight leading and overlapping horizontally, are
  joined. Two lines is the limit, since a wrapped byline is geometrically identical to a wrapped
  title; longer runs are demoted to paragraphs.
- **Paragraphs** `/P` — whole paragraphs, not one per line. Lines join unless the typesetting says
  otherwise: the previous line stopping short of the measure, a first-line indent, a gap wider than
  the run's leading, or a change of size, weight, slope or face. Where signals disagree the split
  is kept.
- **Page furniture** `/Artifact` — running heads, footers, folios. Born-digital: the same style
  recurring at a page edge. Scanned: the same *words* recurring at a page edge, digits stripped so
  a changing folio still matches. The threshold is a quarter of the document's pages, because a
  running head alternates between verso and recto.
- **Captions** `/Caption` — one element per caption. Grouped by which caption a line belongs to
  rather than by adjacency, so stray marks between a caption's lines do not split it. Each caption
  is moved inside the figure nearest it, measured between boxes. PDF/UA-2 permits at most one
  caption per figure and requires it to be the first or last child; a caption with no figure left
  to attach to stays a paragraph.
- **Lists** `/L` → `/LI` → `/LBody`.
- **Tables** `/Table` → `/TR` → `/TD` as a regular grid. Top row is `/TH` scoped to its column,
  empty cells fill gaps, and the table carries an `/Alt` summary of column/row count and header
  text.
- **Figures** `/Figure` with `/Alt` — see below.
- **Links** — external URIs tagged with an object reference to the annotation. Removed rather than
  carried through: internal links using legacy page/coordinate destinations (PDF/UA-2 requires
  structure destinations), and links whose target is unfollowable, such as `http:0.5–0.75` from an
  auto-linker firing on a numeric range.
- **Bookmarks** — an outline from the recovered headings, nested by level, each a PDF 2.0 structure
  destination.

### Figures

Found three ways:

1. **Placed images**, read from the file.
2. **Drawn figures** — line art leaves no image object, so it is found by anchoring on its caption:
   everything drawn in the band above a `Fig. N …` caption.
3. **Regions of a scan.** A scanned sheet is one raster and an illustration on it is a patch of
   that raster, not an object. OCR says where the words are, those are masked out, and the
   remaining ink is closed into blobs. A blob qualifies if it covers ≥1.5% of the page, is solid
   enough to be a picture, is not mostly text, and does not reach three page edges (what does is
   the scanner's dark strip at the spine or platen edge).

A scan region is kept only if the document captions it somewhere. Uncaptioned, it is treated as
part of the page and can be marked by hand with `f`. Declared images are always kept.

Alt text comes from the document's own caption, including when the caption is split across a page
break. Captions are recognised by a wide label set (`Fig.`, `Figure`, `Plate`, `Chart`, `Diagram`,
`Scheme`, `Photograph`, `Exhibit`, `Map`, …, numbered or not). Searched below, above, and — where a
book stacks captions in the outer margin — beside, the last only when exactly one unclaimed caption
sits beside the figure. Line-break hyphens are healed, so the alt text reads "iconographical", not
"iconograph- ical".

Never used as alt text: prose that merely sits under a picture, and a bare label ("Fig. 8"). Both
satisfy a checker while telling a reader nothing. A bare label is offered in the editor as a
starting point.

A figure's callout labels ("A", "B", "3 mm") belong to the figure, described or not, and are drawn
inside it rather than left loose in the reading order.

## The accessibility report

Adobe's Accessibility Checker rule list, judged against the produced PDF. Four verdicts: *passes*,
*needs you*, *needs your eye* (reading order only), and *not applicable*. Each is read off the
finished document — the structure tree, the fonts, the annotations — never inferred from what
remediation intended.

Every open item carries either a fix Rebind can perform or the place in the document to correct it
by hand. A test asserts this for every check.

### Colour contrast

Measured against WCAG 2.1 SC 1.4.3 (4.5:1, or 3:1 for large text) and corrected during remediation,
then re-measured. It is never asked of the user.

Ink comes from the page's own declaration; paper is sampled from the rendered page, since what sits
behind text is not stated anywhere. Each failing colour moves away from the paper behind it — text
on white darkens, text on a dark panel lightens — keeping its hue. Changes are scoped to a text
object and undone at its end, so a colour shared by a heading and a rule corrects only the heading.

Two kinds of text are not measured, because no colour is being chosen: text with no declared colour
(a scan's words are the picture), and text drawn invisibly in rendering mode 3.

### Reading order

No measurement settles reading order, so it stays with the user. Every element is a tab stop and
Tab runs off the end of one page onto the next. The report counts pages walked and ticks the check
when all have been seen.

## The result view

Three columns: the report on the left, the document at twice the width in the middle, the walk on
the right.

Nothing has to be saved. An edit goes to the server on its own and the document is rebuilt behind
you; the header says where that has got to. Saving is triggered by a change, not by a keystroke.

**Tab order:** each failing check in the report, then element 1 of the document, with nothing in
between. Other controls are out of the natural order and reached from what they belong to — a fix
field from its `!` row, the pager from `[` and `]`. **Download PDF** and **Do another document**
are the last two stops.

**Left.** Adobe's rule list in Adobe's groups. A rule with nothing to test is *not applicable*, not
passed. Anything that did not pass is a button: activate it and the middle column turns to the page
concerned, or focus lands on the field that fixes it.

**Middle.** The document, sized so a whole page is visible without scrolling. Each tagged element
is drawn over the page and is a tab stop, in reading order.

**Right.** The element you are on, named in large type with the key that sets it, and the key
legend below. Pressing a key sets the type and moves to the next element. `Enter` opens a list of
every type.

On a figure, the right column becomes its description with the caption pre-filled. Landing on it
does not take focus: `Tab` accepts the guess and moves on, `Enter` or any character goes into the
box, and from the box `Enter` or `Tab` accepts while `Esc` returns to the page. `x` still takes the
figure out of the reading order; other type keys type instead.

| key | | key | | key | |
|---|---|---|---|---|---|
| `p` | Paragraph | `q` | Block quote | `s` | Section |
| `1`–`6` | Heading 1–6 | `c` | Caption | `d` | Division |
| `f` | Figure | `t` | Table | `a` | Article |
| `l` | List | `m` | Formula | `i` | Index |
| `e` | Code | `o` | Form field | `n` | No structure |
| `v` | Footnote | `x` | Not read | `[` `]` | Previous / next page |

`+` adds the region you are on to the reading order; `−` takes it out.

`x` is an action, not a type: it marks content as an artifact rather than untagging it, since
untagged content is a conformance failure. Page furniture and text inside figures are drawn hatched
as "not read"; giving one a type puts it back into the reading order.

On a table row, only `h` (Header cell) and `b` (Data cell) are offered, letting you correct which rows are headers without retagging the whole table.

Every change rebuilds the document from the corrected plan rather than patching the structure tree,
so grouping decisions change too. Every offered type has a test that applies it and validates the
result: `/Caption` and `/Quote` are illegal directly under the document, `/Aside` is not a PDF 2.0
name, a grouping element may not hold content directly, a `/Figure` needs an `/Alt`, and a
`/Caption` must be nested inside the figure or table it captions. `/Part` is not offered.

The result view also shows a structure badge: a fast, dependency-free check of what remediation is
expected to have built. It is not conformance validation — that is veraPDF, dev/CI only (ADR 0006).

Closing the tab quits Rebind. A reload does not: the beacon starts a short countdown that the
reloaded page's first heartbeat cancels.

## Status

**1.0.2.** Born-digital and scanned inputs both work end to end, through convert, walk, describe
and download, to a document that validates PDF/UA-2 at zero failures.

Not implemented:

- Page dewarp for spine-curved scans.
- Detection of an uncaptioned figure on a scan.
- Mathematics, chemistry and music recognition.
- A source document's own internal navigation, which is dropped rather than rebuilt (ADR 0006).
- Single-character OCR specks in body text still become elements of their own.
- Byte-reproducible output (ADR 0003).

## Runtime

Local only — no API key, no GPU, no network. The Windows installer (~86 MB) is unsigned, so
SmartScreen warns on first run.

## License

MIT — see [LICENSE](LICENSE). Bundled components carry their own licenses; the installer ships the
full notices (`packaging/licenses/`).

## Documentation

- [Design specification](docs/superpowers/specs/2026-07-22-rebind-design.md)
- [Contributing](CONTRIBUTING.md)
