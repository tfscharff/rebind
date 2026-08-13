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

Within a block, things that sit on **one line are read left to right** — not by which box starts a
shade higher. A footer and the folio beside it, or two pictures side by side, are never level to
the point, so ordering on the top edge alone decides the reading order on a rounding difference and
routinely decides for the one on the right. The rule is deliberately strict about what counts as
one line (boxes sharing most of the shorter one's height) and never reaches across a gutter: in
real two-column text the whole left column is read before the right, and shared rows mean nothing.

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
  than the column width. A title too long for one line is one heading, not two: consecutive lines
  set the same way, stacked with tight leading and overlapping each other horizontally, are joined.
  Two lines is the limit, and deliberately so — a byline that wraps looks exactly like a title that
  wraps, and a run longer than that is the signature of a byline broken around superscript markers
  or a diagram's callout labels, which are demoted to paragraphs rather than admitted as headings.
- **Paragraphs** (`/P`) — whole paragraphs, not one per line. A screen reader pauses at every
  element boundary, so a page tagged line by line is read as a stream of fragments. Lines are
  joined unless the typesetting says they are not the same paragraph: the previous line stopping
  short of the measure (in prose, every line but the last runs to the margin — the strongest signal
  there is), a first-line indent, a gap bigger than the run's own leading, or a change of size,
  weight, slope or face. Where the signals disagree the split is kept: a boundary wrongly removed
  cannot be recovered from the joined text.
- **Page furniture** (`/Artifact`) — running heads, footers and folios, kept out of the reading
  order so a screen reader does not announce the chapter title before every page's first sentence.
  A born-digital document gives itself away by style: the same typeface and position recurring at a
  page edge. A scan cannot — OCR reports no typeface, so every line of a scanned book shares one
  style — so the recurring *words* are what condemn it there, digits stripped so a changing folio
  does not break the match. The bar is a quarter of the document's pages, not half, because a
  book's running head alternates: the chapter title on the verso, the section title on the recto,
  neither able to appear on more than about half the pages.
- **Captions** (`/Caption`) — whole captions, one element each. Which lines make up a caption was
  settled when the caption was found, and that decision is carried through rather than re-derived:
  the paragraph rule cannot hold a caption together, because one set in a narrow margin column has
  no measure to run out to, and on a scan the recogniser drops stray marks between its lines, each
  of which breaks a run of consecutive lines. Those strays are folded into the caption they sit
  inside — near in *both* directions, or a line level with the caption on the far side of the page
  would join it, which is the same mistake as reading across a gutter.

  A caption Rebind was willing to use as a picture's description is, by
  its own reckoning, a caption — so it is tagged as one rather than left as ordinary prose. Two
  PDF/UA-2 rules shape where it goes, both learned by failing them: a figure may hold **at most one**
  caption (Table 5, Figure-Caption), and a caption must be the **first or last child** of its parent
  (clause 8.2.5.27), which one of several siblings cannot be. So each caption is moved inside the
  figure it is *nearest* to — measured between boxes, so a caption in the outer margin goes to the
  picture beside it — and a figure that already has one is not offered another. A caption with
  nothing left to caption stays where it is, as a paragraph, rather than being dropped.
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
  Captions are recognised by label, and the label set is wide (`Fig.`, `Figure`, `Plate`, `Chart`,
  `Diagram`, `Scheme`, `Photograph`, `Exhibit`, `Map`, …, numbered or not), because a caption
  Rebind fails to recognise costs the reader the whole description. Ordinary prose that merely
  happens to sit under a picture is never used: alt text has to be the document's own description
  of the figure. A caption that is only a bare label ("Fig. 8", "Fig. 8 (Continued)") is never
  accepted as alt text either — it would tick a checker's box while telling a screen-reader user
  nothing — but it *is* offered in the editor as a starting point to finish. A caption does not
  always sit under its picture: a book with a wide outer margin stacks them *beside* the figures,
  so the margin is searched too — but only when the answer is unambiguous, since several figures
  share one vertical span and their captions are stacked beside all of them. Exactly one unclaimed
  caption beside a figure is taken; more than one leaves it for the person, because the wrong
  caption is a fabrication and worse than an empty box. A line-break hyphen is healed on the way
  in, so a caption reads "iconographical elements", not "iconograph- ical elements" — that string
  becomes the picture's `/Alt`, and is the reader's only description of it.

  A figure's own callout labels ("A", "B", "3 mm") always belong to the figure, described or not.
  Left loose they became elements in their own right, and a picture entered the reading order as a
  scatter of fragments read out as if they were prose.

  **On a scan there are no images to find.** The whole sheet is one raster, and an illustration
  printed on it is a patch of that raster — not an object in the file — so every picture in a
  scanned book was invisible. They are found from the pixels instead, the way a reader finds them:
  OCR says where the words are, those are masked out, and what is left is closed up into blobs.
  A blob becomes a figure only if it is large (≥1.5% of the page), solid enough to be a picture
  rather than a stray mark, not mostly overlapping text, and does not reach three page edges —
  anything printed on the sheet has a margin on at least two adjacent sides, and what reaches three
  is the scanner's own dark strip down the spine and across the top. That strip cost a page its
  photograph: it is a thin L, so it passed a density test meant to catch a solid black bar, and its
  bounding box covered the left third of the sheet and vetoed the picture inside it as a duplicate.
  A missed figure is one the user marks by hand; an invented one is a picture that does not exist
  being announced to a screen reader, so the thresholds lean toward missing.
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
*needs you* (with what it needs), *needs your eye* (reading order, and only reading order), and
*not applicable* (the document has none of the thing being checked). Nothing is asserted; each
verdict is read off the produced PDF.

**Nothing is reported without a way to act on it.** Naming a fault and leaving the remedy to the
reader is a complaint, not a report, so every open item carries either a fix Rebind can perform
(set the title or language, remove the scripts, describe the images) or the place in the document
to go and correct it by hand — one click, and the middle column turns to that page. There is a test
asserting this holds for every check.

## Colour contrast, and the one check that stays with you

Adobe reports **Logical Reading Order** and **Colour contrast** as *needs manual check* on every
document, always. Rebind settles one of them and makes the other finishable.

**Colour contrast is on the report, and ticked off.** What it is never is a question: nobody can
look at two colours and compute a luminance ratio, so asking would be asking for a judgement that
cannot be made. It is measured against WCAG 2.1 SC 1.4.3 (4.5:1, or 3:1 for large text) and
corrected during remediation; the tick is a receipt, not a task.

The ink comes from the page's own declaration, which is exact; the paper is sampled from the
rendered page, because what sits *behind* text — a filled box, a shaded row, a dark banner — is not
stated anywhere. Each failing colour is moved *away from the paper actually behind it*: text on
white is darkened, text on a dark panel is lightened, and both keep their hue. Every change is made
inside a text object and undone at its end, so a colour shared between a heading and the rule
beneath it corrects the heading and leaves the rule exactly as it was. The result is then
**re-measured**, never assumed.

Two kinds of text are not measured, because in neither case is a colour being chosen:

- **Text with no declared colour**, for the same reason text inside a figure is skipped — a scan's
  words *are* the picture, so sampling them measures the photocopier, and repainting them would
  mean altering the scan.
- **A page whose text is entirely invisible** — an OCR layer laid over a scan in rendering mode 3.
  Those words are not on the page as far as a reader is concerned, and Rebind strips that layer
  anyway. Measuring them reported 939 lines and 49 failures on a real Tesseract-processed scan,
  every one of them text nobody could see.

**Reading order** cannot be settled by any measurement, so it stays with you — but as something
finishable rather than a permanent asterisk. Every element is a tab stop, and Tab runs off the end
of one page onto the next, so checking a document is one unbroken walk. The report counts the pages
you have walked and ticks the check when you have seen them all. It is the only item on the list
that asks for your eye.

## Running it

**App.** Install with the Windows installer (`rebind-setup.exe`, built from `packaging/`) or run
`rebind serve`, then use the local browser page it opens. Drop a PDF in, convert, and download the
result. The result view also shows a structure badge: a fast, dependency-free check of what
remediation is expected to have built (not independent conformance validation — that's veraPDF,
dev/CI-only; see ADR 0006). Nothing is uploaded. Closing the tab quits Rebind — the page sends a
heartbeat, and the server exits when it stops.

### The result view

Three columns, with the document at twice the width of either side of it. Nothing has to be saved:
an edit goes to the server on its own and the document is rebuilt behind you, with the header
saying where that has got to. There is no Apply button to forget, and no state that lives only in
the tab.

Saving happens when a value **changes**, not on every keystroke. A save re-runs the whole
conversion, so typing a description used to queue a rebuild per letter and the page stuttered under
its own autosave; a description is now saved when you accept it or leave the box. A rebuild that
lands on the page you are already looking at replaces the boxes over it and leaves the picture
alone, rather than re-decoding a whole scanned sheet.

**The tab order is the design.** Rebind's own page has to be as walkable as the documents it fixes,
and the run from the top of the page to the first element is a toll paid on every pass. So the
order is: each failing check in the report, in order, then element 1 of the document — and nothing
in between. Every other control is out of the natural order and reached from the thing it belongs
to: the fields under *Needs you* are reached from their own `!` row, the pager has `[` and `]`.
**Download PDF** and **Do another document** are the last two stops on the page, in that order,
because they are what you do when the work is finished and nobody should reach "start over"
without passing the download first.

**Left — the accessibility report.** Adobe's own rule list, in Adobe's own groups, ticked off one
at a time against the document Rebind actually produced. Every verdict is read off the finished
PDF — the structure tree, the fonts, the annotations — never inferred from what remediation
intended, because a green tick is a claim. A rule the document has nothing to test (no forms, no
tables) is marked *not applicable* rather than passed. Anything that did not pass is a button and a
tab stop: activate it and the middle column turns to the page the problem is on, or focus lands on
the field that fixes it.

**Middle — the document and nothing else**, sized to the window so a whole page is always visible
and nothing scrolls. Each element Rebind tagged is drawn over the page and is a tab stop, in
reading order, so tabbing through the page is meeting it as a screen reader will. Tab past the last
element and the next page opens, so a whole document is one unbroken walk.

**Right — the walk, the element, the keys.** The reading-order progress bar at the top (why you are
walking), the element you are standing on in the middle, the key legend at the bottom. Land on an
element and it is named in large type, **with the key that sets it** beside the name, and what that
type means — so the keys are learned by meeting them rather than by reading a legend. Pressing a
key sets the type and moves you straight to the next element, so correcting a page is one stream of
keystrokes with no Tab in between. `Enter` is only for when you cannot remember which key you want:
it opens a floating list of every type.

Land on a **figure** and the right column names it — *Figure*, with the `f` key beside it, like
every other element — and becomes its description, with Rebind's best guess already in the box: the
document's own caption where there is one. It asks there rather than in a sheet
over the page, because the picture you are being asked about is in the middle column and a dialog
on top of it hides the one thing you look at to answer; the key legend folds away to make room for
a box worth writing in.

Landing on it does **not** take focus, because the walk has to stay one unbroken run of `Tab` from
the first element of the document to the last, whether or not there are pictures in the way. `Tab`
takes the guess as it stands and moves on. `Enter` goes into the box to edit it — as does simply
starting to type, since you should not have to ask permission to say what a picture is. From inside
the box, `Enter` or `Tab` accepts and moves to the next element, and `Esc` goes back to the page.
(The cost of typing straight into the box is that the type keys no longer retag a figure: `−` takes
it out of the reading order and `+` puts it back as a paragraph.) Nothing is invented: with no
caption to draw on the box starts empty, and nothing is written into the document unless you leave
it there. Accepting a guess unchanged costs nothing — only a real change saves, so tabbing through
a page of pictures is as quick as tabbing through prose.

| key | | key | | key | |
|---|---|---|---|---|---|
| `p` | Paragraph | `q` | Block quote | `s` | Section |
| `1`–`6` | Heading 1–6 | `c` | Caption | `d` | Division |
| `f` | Figure | `t` | Table | `a` | Article |
| `l` | List | `m` | Formula | `i` | Index |
| `e` | Code | `o` | Form field | `n` | No structure |
| `x` | Not read | `[` `]` | Previous / next page | | |

`+` adds the region you are on to the reading order; `−` takes it out. They are the two edits that
are not "what is this?", so they have their own pair of buttons in the right column as well as
their own keys.

`x` is not a type — it is the one action in the list: take this out of the reading order and let it
be drawn as page furniture instead. It marks the content as an artifact rather than untagging it,
because untagged content is a conformance failure, not a fix. Page furniture and text inside
figures are drawn hatched as "not read"; giving one a type puts it into the reading order, so
nothing is one-way.

Everything that did not pass sits under the report on the left, each next to the one thing that
would fix it — a field for a missing title or language; the pages to go and look at. Descriptions
are not among them: the report says which pages carry an undescribed picture and takes you there,
but the describing happens in the walk, on the picture, at the moment you reach it. A column of
thumbnails asking for the same descriptions out of context is a second place to do one job.

Every change rebuilds the document from the corrected plan rather than patching the structure tree
afterwards, so grouping decisions change too. Every offered type has a test that applies it and
validates the result, because what is legal here is not obvious: `/Caption` and `/Quote` are
illegal directly under the document, `/Aside` is not a PDF 2.0 name at all, a grouping element may
not hold content directly (its text is wrapped in a `/P`), a `/Figure` needs an `/Alt`, and a
`/Caption` has to be nested inside the figure or table it captions. `/Part` is not offered — it is
a container that does nothing `/Sect` and `/Div` do not already do, so it was one more thing to
choose between for no gain to a reader.

**Command line.**

```
rebind convert input.pdf output.pdf
```

`REBIND_DEBUG=1` prints a full traceback on an unexpected failure. `rebind serve` starts the local
server; this is what the installed application runs on double-click.

## Status

Alpha (v0.29.0). Born-digital and scanned inputs both work end to end.

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
