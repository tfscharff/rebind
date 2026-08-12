# Rebind

Accessible PDF reconstruction for damaged library scans.

Rebind takes a PDF — scanned or born-digital, tagged or not — and produces a PDF/UA-2 document that
validates with veraPDF (zero failures). It preserves each page as it is and adds the accessibility
the source is missing: a selectable text layer and a structure tree. It runs entirely on the local
machine — no API key, no GPU, no network at runtime.

## What it does

Per page:

- **A page that already has a text layer** (born-digital) is kept verbatim — vector text is
  unchanged — and its text is tagged with reading order and structure.
- **A scanned page** keeps its image and gets an invisible, selectable text layer from on-device
  OCR (RapidOCR on the CPU, models bundled). Scanned pages are deskewed and denoised before
  recognition.
- **A page with no recoverable text** (blank, or an image with no words) is kept as it is and
  reported.

Reading order for multi-column pages is recovered by recursive XY-cut over the text-line boxes,
including the ordinary article layout where a full-width heading sits above the columns and hides
the gutter from a naive cut. A figure's own callout labels are held out of that cut and spliced
back in at the height they sit at, so a labelled diagram cannot invent column structure.

A born-digital page carries markup (font size, tags) that names its own structure directly. A
scanned page, after OCR, does not — recognition returns only text and a bounding box per line. For
those pages, structure is recovered by **inference from geometry**: line height relative to the
page's body-text median, whitespace above and below a line, and alignment of text into recurring
column positions. This is the same signal a sighted reader uses when scanning a page before
reading a word of it, expressed numerically instead of visually.

The structure tree (PDF/UA-2, ISO 14289-2, veraPDF zero failures) carries:

- **Headings** (`/H1`–`/H6`, level-normalized so the sequence starts at H1 and skips no level).
  Born-digital headings come from font size; scanned/OCR headings come from geometry — a line is a
  heading when it is distinctly taller than the body text, set apart by whitespace, and shorter
  than the column width.
- **Paragraphs** (`/P`).
- **Lists** (`/L` → `/LI` → `/LBody`).
- **Tables** (`/Table` → `/TR` → `/TD`) as a regular grid; the top row is header cells (`/TH`)
  scoped to their column, empty cells fill gaps so rows stay aligned, and the table carries its own
  `/Alt` summary (column/row count and header text — never a guess at what the table means).
- **Figures** (`/Figure` with `/Alt`), whether placed as an image or *drawn* with path operators.
  A line-art figure — a schematic, a chart, a labelled diagram — leaves no image behind at all, so
  it is found by anchoring on its caption: everything drawn in the band above a "Fig. N ..."
  caption is that caption's figure. An uncaptioned drawing is left alone rather than guessed at,
  which also keeps a table's rules and a page's furniture from being mistaken for figures.
  Either kind of figure is described automatically from its caption — no app interaction needed —
  including when the caption is split across a page break and its real text sits on another page.
  A caption that is only a bare
  label ("Fig. 8", "Fig. 8 (Continued)") is never accepted as alt text: it would tick a checker's
  box while telling a screen-reader user nothing. Otherwise images are decorative artifacts by
  default; the app shows each one so a description can be typed, which promotes it to a tagged
  figure with alt text.
- **Links** — a working external link (URI) is tagged into the structure tree with an object
  reference back to its annotation. Two kinds are removed rather than carried through: an internal
  link using a legacy page/coordinate destination (PDF/UA-2 requires internal destinations to be
  structure destinations, which nothing before PDF 2.0 could produce), and a link whose target is
  unfollowable — a publisher's auto-linker firing on text that merely looks URL-ish, e.g. the
  numeric range `0.5–0.75` turned into a link to `http:0.5–0.75`.
- **Bookmarks** — an outline built from the recovered headings, nested by level, each entry a real
  PDF 2.0 structure destination into the heading itself.

A scan that arrives having already been through another OCR tool carries its own invisible text
layer. That layer is removed before Rebind's tagged one goes on — otherwise the document holds two
copies of every word, and Tesseract's stand-in font declares a character map veraPDF rejects. Only
invisible text goes; every visible mark is left exactly as it was, so the page is not re-rendered
and looks identical.

Text is never fabricated: every text node traces to recognizer output with a confidence score.
Below threshold it becomes an explicit placeholder — `[text not recoverable from source scan,
p. 214]` — rather than a guess.

## The accessibility report

Rebind walks Adobe's Accessibility Checker rule list against its own output and says, for each
rule, what is true of the document. Four verdicts, and only one of them is a tick: *passes*,
*needs you* (with what it needs), *needs your eye* (the two below, which are unconditional), and
*not applicable* (the document has none of the thing being checked). Nothing is asserted; each
verdict is read off the produced PDF.

## The two checks no tool can pass for you

Adobe's checker reports **Logical Reading Order** and **Colour contrast** as *needs manual check* on
every document, always — both are ultimately about what a person perceives. Rebind can't make them
pass, but it hands you the evidence instead of leaving you to gather it page by page:

- **Reading order** — the order Rebind chose is shown as numbered blocks over a picture of the
  page, but only for the pages where the order was a real decision (columns, a figure in the text
  flow, an ambiguous layout). Pages that read straight down are reported in bulk, so a 300-page
  document is a handful of pages to check, not 300.
- **Colour contrast** — measured, not guessed, and scored against WCAG 2.1 SC 1.4.3 (4.5:1, or 3:1
  for large text). The ink comes from the page's own declaration, which is exact; the paper is
  sampled from the rendered page, because what sits *behind* text — a filled box, a shaded row, a
  photograph — is not stated anywhere. Anything below threshold is listed with its real ratio and a
  swatch of the two colours. Text inside a figure, text over imagery too busy for one colour to
  describe, and invisible same-colour text are all left to a human rather than scored against a
  fiction. If there are failures, Rebind offers to darken exactly those text colours — keeping each
  one's hue, never touching a colour the artwork also uses. That is the only thing Rebind will ever
  do to change how a document looks, and it only happens if you ask.

## Running it

**App.** Install with the Windows installer (`rebind-setup.exe`, built from `packaging/`) or run
`rebind serve`, then use the local browser page it opens. Drop a PDF in, convert, and download the
result. The result view also shows a structure badge: a fast, dependency-free check of what
remediation is expected to have built (not independent conformance validation — that's veraPDF,
dev/CI-only; see ADR 0006). Nothing is uploaded. Closing the tab quits Rebind — the page sends a
heartbeat, and the server exits when it stops.

### The result view

Three columns, with the document at twice the width of either side of it.

**Left — the accessibility report.** Adobe's own rule list, in Adobe's own groups, ticked off one
at a time against the document Rebind actually produced. Every verdict is read off the finished
PDF — the structure tree, the fonts, the annotations — never inferred from what remediation
intended, because a green tick is a claim. A rule the document has nothing to test (no forms, no
tables) is marked *not applicable* rather than passed.

**Middle — the document.** Each element Rebind tagged is drawn over the page and is a tab stop, in
reading order, so tabbing through the page is meeting it as a screen reader will. The element's
type appears in big letters above the page with an explanation of what that type *means*, so
`BlockQuote` is not something you have to already know. `Enter` opens a floating list of every
type; one keystroke sets it.

| key | | key | | key | |
|---|---|---|---|---|---|
| `p` | Paragraph | `q` | Block quote | `s` | Section |
| `1`–`6` | Heading 1–6 | `c` | Caption | `d` | Division |
| `f` | Figure | `t` | Table | `a` | Article |
| `l` | List | `m` | Formula | `r` | Part |
| `e` | Code | `o` | Form field | `i` | Index |
| `n` | No structure | `x` | Not read | `[` `]` | Previous / next page |

Removing an element (`x`) marks its content as an artifact rather than untagging it — untagged
content is a conformance failure, not a fix. Page furniture and text inside figures are drawn
hatched as "not read"; giving one a type puts it into the reading order, so nothing is one-way. A
figure's description is typed in place, above the page (`Space` while it has focus).

**Right — what needs you.** Every rule that could not be ticked, each next to the one thing that
would tick it: the images that need describing, with a thumbnail and a box; the measured contrast
failures, with the button that darkens exactly those colours; the pages where the reading order was
a real decision, as buttons that put that page in the middle column.

Applying rebuilds the document from the corrected plan rather than patching the structure tree
afterwards, so grouping decisions change too. Every offered type has a test that applies it and
validates the result, because what is legal here is not obvious: `/Caption` and `/Quote` are
illegal directly under the document, `/Aside` is not a PDF 2.0 name at all, a grouping element may
not hold content directly (its text is wrapped in a `/P`), a `/Figure` needs an `/Alt`, and a
`/Caption` has to be nested inside the figure or table it captions.

**Command line.**

```
rebind convert input.pdf output.pdf
```

`REBIND_DEBUG=1` prints a full traceback on an unexpected failure. `rebind serve` starts the local
server; this is what the installed application runs on double-click.

## Status

Alpha (v0.16.0). Born-digital and scanned inputs both work end to end.

Implemented: on-device OCR with deskew/denoise restoration, multi-column reading order, and the
structure tree above (headings, paragraphs, lists, tables, figures with caption-based alt text,
links, bookmarks).

Not implemented: full page dewarp for spine-curved scans, detection of an *uncaptioned* drawn
figure, and mathematics/chemistry/music recognition.
Output is not byte-reproducible (see ADR 0003).

## Runtime

Runs on the local machine only — no API key, no GPU, no network at runtime. There are no limits on
page count or number of structure elements. The Windows installer (~82 MB) is unsigned, so
SmartScreen warns on first run.

## License

MIT — see [LICENSE](LICENSE). Bundled third-party components carry their own licenses; the installer
ships the full notices (`packaging/licenses/`).

## Documentation

- [Design specification](docs/superpowers/specs/2026-07-22-rebind-design.md)
- [Contributing](CONTRIBUTING.md)
