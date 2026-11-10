# How Rebind works

What Rebind does to a page, and why. For installing and using it, see the [README](../README.md); for the editor, see [editor.md](editor.md).

## Per page

- **Born-digital** — kept verbatim, vector text unchanged, tagged with reading order and structure.
- **Scanned** — image kept, with an invisible selectable text layer from on-device OCR (RapidOCR on
  the CPU, models bundled), read from a 300-DPI render of the page as it is.
- **No recoverable text** (blank, or an image with no words) — kept as is, and reported.

A scan that already carries another tool's invisible OCR layer has it removed first, so the output
holds one copy of each word. Visible marks are untouched and the page is not re-rendered. That layer
is kept as the page's text, but the page is recognised again too, and any line it missed (a graphic
novel's came without a single speech balloon) is added, when read with confidence.

**Sideways and offset pages.** A page whose text is mostly turned (a landscape table printed on a
portrait page), a page with a `/Rotate` of its own, and a page whose box does not start at the
origin or is cropped (every JSTOR download: its media box starts 8 points below) are read from a
copy with that page turned upright and its shown area moved to the origin. Every stage downstream
sees an ordinary page, the editor shows it the right way up, and the page written out is given the
`/Rotate` that shows it exactly as the original was. Without this a sideways table was read as a
scatter of fragments, and on a JSTOR file every frame, and the text layer, sat 8 points too high.

**Drop caps.** A paragraph's large first letter is joined to the word it begins ("Breastfeeding", not
"B reastfeeding"), wherever pdfminer had grouped it. "A" and "I" are joined too, unless the page uses
the rest ("study") as a word of its own.

Text is never fabricated. Every line traces to the file's own text layer or to recognizer output
with a confidence score. A line the recogniser is not confident of is left out, not guessed at;
nothing marks where it was, so a faint or damaged page should be checked against the scan.

## Reading order

Recovered by recursive XY-cut over the text-line boxes, including the layout where a full-width
heading sits above the columns and hides the gutter. A figure's callout labels are held out of the
cut and spliced back in at the height they sit at.

Within a block, items on one line read left to right. Two lines count as one line when their boxes
share most of the shorter one's height. The rule never crosses a gutter: in two-column text the
whole left column is read before the right.

Page furniture and table grids are held out of the cut and put back afterwards, because both
mislead it — a running head spanning two columns hides the gutter, and a table's inter-cell gaps
look exactly like gutters. Furniture goes back at the height it sits at, so a running head is the
first stop on the page rather than the last. A table goes back as one unbroken run, read row by
row: a grid the cut had split down the middle is one `/Table` with one header row, not two tables
each inventing a header, with the body paragraph that sat between the halves back outside it.

Born-digital pages name their own structure through markup. Scanned pages do not, so structure is
inferred from geometry: line height against the body median, whitespace above and below, and
alignment into recurring column positions.

### Layout model

Each page is also looked at as a picture by a document-layout model — PP-DocLayoutV3 (PaddleOCR,
Apache-2.0) on onnxruntime, on the CPU, about two seconds a page, remembered between rebuilds of an
unchanged file. It boxes the page's regions: running heads and folios, paragraphs, section titles,
tables and their titles, footnotes. Where it boxed a line, its boxes decide:

- **Paragraphs** — the lines in one text block are one paragraph, and lines in different blocks are
  not, whatever the margins suggest.
- **Tables** — a table is exactly the lines inside its table region, so prose in the next column
  that lines up with the table's columns stays prose.
- **Table titles** — a title next to a table becomes the table's `/Caption`, inside it, first.
- **Page furniture** — running heads, feet and folios the model is confident of become artifacts.
- **Headings** — a large line run in with other lines in one text block is not a heading.
- **Equations** — a displayed equation, with the number set beside it, becomes one `/Formula`.

Where it boxed nothing, or is unsure, the geometric rules decide as before. The model groups lines;
it never supplies text, so a wrong box can only group wrongly, which the editor corrects.

## Structure tree

- **Headings** `/H1`–`/H6`, level-normalized so the sequence starts at H1 and skips no level.
  Born-digital from font size; scanned from geometry — distinctly taller than body text, set apart
  by whitespace, shorter than the column. A title wrapping onto a second line is one heading:
  consecutive lines set the same way, stacked with tight leading and overlapping horizontally, are
  joined. Two lines is the limit, since a wrapped byline is geometrically identical to a wrapped
  title; longer runs are demoted to paragraphs.
  The document's title (its largest heading size, used by one run of lines only) may run to six
  lines, and nothing smaller read before it is a heading: a library's cover sheet came first on a
  real file, H1 was anchored to one of its lines, and every heading after it came out H1. A font
  named with a bare weight suffix (`.B`, `.I`) is read as bold or italic, which is what ranks a
  journal's section headings above its subsection headings when both are the same size.
- **Paragraphs** `/P` — whole paragraphs, not one per line. Lines join unless the typesetting says
  otherwise: the previous line stopping short of the measure, a first-line indent, a gap wider than
  the run's leading, or a change of size, weight, slope or face. Where signals disagree the split
  is kept.
- **Superscripts and subscripts** — each a `/Span` inside its paragraph, heading, caption, list
  item or cell, carrying the standard Layout attribute `/BaselineShift` (positive raised, negative
  lowered, in points). The words around them stay one element, so "Ca²⁺" and "10⁻⁵" are not lost
  as a charge and a power. Born-digital: read from where the PDF places each character's baseline.
  A line pdfminer breaks at a subscript followed by a superscript ("SO₄²⁻") is rejoined. Scanned:
  each figure and sign is measured against its line's own baseline and cap height. A line the text
  finder drops because its scripts crowd the lines around it is found from the ink left unread and
  read again on its own, and pieces read separately are joined back with their scripts. Letters are
  not judged on a scan (`K_sp` is caught only when it comes back as its own piece). The text stays
  as printed ("H2O"), and most screen readers do not yet announce a baseline shift.
- **Page furniture** `/Artifact` — running heads, footers, folios. Born-digital: the same style
  recurring at a page edge. Scanned: the same *words* recurring at a page edge, digits stripped so
  a changing folio still matches. The threshold is a quarter of the document's pages, because a
  running head alternates between verso and recto.
- **Captions** `/Caption` — one element per caption. Grouped by which caption a line belongs to
  rather than by adjacency, so stray marks between a caption's lines do not split it. Each caption
  is moved inside the figure or table nearest it, measured between boxes; a table's title set above
  it becomes its first child. PDF/UA-2 permits at most one
  caption per figure and requires it to be the first or last child; a caption with no figure left
  to attach to stays a paragraph.
- **Lists** `/L` → `/LI` → `/LBody`.
- **Tables** `/Table` → `/TR` → `/TD` as a regular grid. Top row is `/TH` scoped to its column,
  empty cells fill gaps, and the table carries an `/Alt` summary of column/row count and header
  text. In a table without rules between its rows, where cells wrap onto several lines, a row
  starts where the first column starts a new entry (its wrapped lines hang indented, or carry on
  mid-phrase), and the other lines join the row above, so "2007 (United / States)" is one cell.
- **Formulas** `/Formula` — displayed equations, found by the layout model or marked with `m`.
  Undescribed, a screen reader reads the formula's own text; the editor takes a description in
  words exactly as it does for a picture (Enter or Space on the formula), which becomes its `/Alt`.
  The box comes with a draft: the equation is read from its picture into LaTeX by LaTeX-OCR
  (pix2tex, on the CPU, about half a second each) and put into words — "omega sub d equals the
  square root of omega sub 0 squared minus gamma squared". The reader slips (a double prime read
  as a prime, a zero as the letter O), so the draft is never written into the document unless the
  person accepts it, and the box says to check it against the page. The report lists undescribed
  formulas and goes to them.
- **Figures** `/Figure` with `/Alt` — see below.
- **Links** — external URIs tagged with an object reference to the annotation. Removed rather than
  carried through: internal links using legacy page/coordinate destinations (PDF/UA-2 requires
  structure destinations), and links whose target is unfollowable, such as `http:0.5–0.75` from an
  auto-linker firing on a numeric range.
- **Bookmarks** — an outline from the recovered headings, nested by level, each a PDF 2.0 structure
  destination.

### Figures

Found five ways:

1. **Printed music**, by its staves — see below.
2. **Placed images**, read from the file.
3. **Drawn figures** — line art leaves no image object, so it is found by anchoring on its caption:
   everything drawn in the band above a `Fig. N …` caption.
4. **Pictures on a scan, boxed by the layout model.** A scanned sheet is one raster and an
   illustration on it is a patch of that raster, not an object. The layout model looks at the whole
   page and boxes pictures and charts as such (score ≥ 0.6).
5. **Regions of a scan, from its pixels**, for whatever the model did not box. OCR says where the
   words are, those are masked out, and the remaining ink is closed into blobs. A blob qualifies if
   it covers ≥1.5% of the page, is solid enough to be a picture, is not mostly text, and does not
   reach three page edges (what does is the scanner's dark strip at the spine or platen edge).

A picture the model boxed is kept whether or not anything captions it: an uncaptioned one goes to
the editor to be described. A pixel region is a guess — on a page of plans it is mostly hatching —
so it is kept only if the document captions it somewhere, or if it covers at least 5% of the page
(a comic's panels are captioned by nothing). Smaller and uncaptioned, it is treated as part of the
page and can be marked by hand with `f`. Declared images are always kept. Lines stacked inside a
guessed picture (a speech balloon, a caption box) are read as text, not held out as its labels.

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
inside it rather than left loose in the reading order — so do the scraps a recogniser makes of
the picture's own lettering. Only labels: a line of a paragraph is never claimed, so a picture set
beside a title block or under a run of paragraphs cannot pull them out of the reading order.

A sentence that names a figure ("Figure 3A. is FTIR spectra of…", "Fig. 2 shows…") is prose, not a
caption: a label followed by a verb is never taken as one.

**Tables are tables, not figures.** A figure found from drawings or pixels whose lines are mostly a
table's (as the layout model boxes it, or the geometric detector finds it) gives way to the table.
A table described in a sentence would pass a checker, but it fails WCAG 1.3.1: a screen reader can
move through a tagged table cell by cell, hearing each cell's column header, and no description
gives that back. Every table already carries a drafted `/Summary` (columns, rows and headers).

A figure is read where it sits in its own column: after the text above it that shares any of its
width. On a two-column page, a figure in the left column is no longer read in the middle of the
right one.

**Music.** PDF/UA has no tag for notation, so a music example is a figure, and what a reader needs
is a description. It is found by its staves: five long, evenly spaced lines, closer together than
lines of text. The search runs strip by strip across the page and chains the pieces, so a tilted or
warped scan and a stave with worn-away stretches still come out as one stave each, while two
examples set side by side on one line stay two. Staves stacked with no prose or caption between
them are one example; a sentence between them, an "Ex. 25." label, or an exercise number over the
left end of a stave starts the next. Five lines with words in their spaces are a ruled table, not
music. Born-digital pages are searched too when they carry drawings or images.

The example's box takes in what is printed on and around its staves — ledger lines, figures under a
bass, a tempo — and everything read as text on the staves belongs to the figure, so a screen reader
no longer meets note heads read as letters. The example's label stays outside it, read in its own
right.

A music example always goes to the editor to be described, with a draft: its own label, then how it
is laid out and its clefs — "Ex. 238. Music in two lines, each on two staves: treble and bass
clefs." Lines are told apart by the clefs repeating down the example; a clef that cannot be told is
left out, and so is the layout with it. Key signatures, time signatures and bar counts were tried
and left out: on two real scanned books they came out wrong too often to offer, and a wrong key is
worse than none. The draft is only a start; what the music is — the piece, the passage, what it
shows — is the person's to add.

**Not read.** Text turned sideways in a margin (a download stamp, an archive identifier — rejoined
first, since it arrives one character per line), lines made only of icon glyphs, a rule typed out
as a row of one mark ("------"), and text set off the page entirely are page furniture.

**Specks.** On a scanned page, a line the recogniser read out of dirt is not read and not listed in
the editor: a line of nothing but marks ("|", "~", "."), or one or two characters the layout model
found outside every region where words are expected. Born-digital text is never treated this way,
and a frame drawn over a speck still brings it back.

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
