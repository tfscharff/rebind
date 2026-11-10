# The editor

A reference for the page Rebind opens after converting a document. For a short introduction see the [README](../README.md).

## The result view

Three columns: the report on the left, the document at twice the width in the middle, the walk on
the right.

Nothing has to be saved. An edit goes to the server on its own and the document is rebuilt behind
you; the header says where that has got to. Saving is triggered by a change, not by a keystroke.
A rebuild reuses everything an edit cannot change — each scanned page's OCR, its layout regions,
music and formula readings, and the page pictures — and when it lands only what changed is redrawn:
the boxes are updated in place, the one you are on is never moved or re-focused, and the panel
beside it is left alone unless that element itself changed. Page pictures are fetched as images
when you turn to a page (the pages either side are decoded ahead), not re-sent with every save.

**Tab order:** each failing check in the report, then element 1 of the document, with nothing in
between. A page with nothing to read on it still has one Tab stop, saying so, so Tab carries on
to the next page instead of leaving the document. Other controls are out of the natural order and
reached from what they belong to — a fix field from its `!` row, the pager from `PgUp` and `PgDn`. **Download PDF** and **Do another document**
are the last two stops.

**Left.** Adobe's rule list in Adobe's groups. A rule with nothing to test is *not applicable*, not
passed. Anything that did not pass is a button: activate it and the middle column turns to the page
concerned, or focus lands on the field that fixes it.

**Middle.** The document, sized so a whole page is visible without scrolling. Each tagged element
is drawn over the page and is a tab stop, in reading order.

**Right.** The element you are on, named in large type with the key that sets it, and the key
legend below. Pressing a key sets the type and moves to the next element. `?` (or the **All keys**
button) opens a list of every key on whatever you are on, a picture included; `Enter` opens it
too, except on a picture or formula, where `Enter` writes the description.

On a figure, the right column becomes its description with the caption pre-filled. Landing on it
does not take focus: `Tab` accepts the guess and moves on, `Enter` or any character goes into the
box, and from the box `Enter` or `Tab` accepts while `Esc` returns to the page. `x` still takes the
figure out of the reading order; other type keys type instead.

| key | | key | | key | |
|---|---|---|---|---|---|
| `p` | Paragraph | `q` | Block quote | `l` | List |
| `1`–`6` | Heading 1–6 | `c` | Caption | `m` | Formula |
| `f` | Figure | `t` | Table | `e` | Code |
| `h` | Table header cell | `d` | Table data cell | `o` | Form field |
| `v` | Footnote | `x` | Not read | | |
| `PgUp` `PgDn` | Previous / next page (`[` and `]` work too) | | | | |

`Insert` adds a new element right after the one you are on in the reading order: an empty frame
just below it, which becomes whatever
lines you drag it (or `Shift`+arrow it) over — including lines Rebind set aside as page furniture.
On something marked not read, `Insert` puts it back instead. `Delete` deletes the element (marks it
not read) and moves on; `Backspace` does the same and steps back, for clearing a run of junk
upwards. Deleting an element you inserted just removes its frame. On a table row, `h`/`d` set the
row, `Insert` adds a new row below it, `Delete` takes that row out of the table, and every other
type key retypes the whole table. (`+` and `−` still work as `Insert` and `Delete`.)

**Building a table by hand.** Mark each cell with `h` or `d`. A run of consecutive cells becomes one
`/Table`: rows from vertical overlap (so a wrapped cell stays in its row), columns from clustered left
edges, empty cells to keep the grid regular. A header cell is scoped to its column when its whole row
is headers, otherwise to its row.

**Frames.** Each element's box is the region whose lines it holds, and it can be reshaped: drag an
edge or corner with the mouse (drag the middle to move it), or use `Shift`+arrows for the bottom and
right edges and `Ctrl`+`Shift`+arrows for the top and left. On the rebuild the element becomes
exactly the lines whose centres fall inside the frame — dragging a paragraph's bottom edge over the
lines it was split from merges them; pulling it back up gives them away as an element of their own.
Lines a frame lets go of become an element of their own, with a frame of its own to gather them up
again. A detected table's header and data rows have frames too: drag a row over a cell the table missed and
it joins that row (and the table); shrink a row and the cells it lets go leave the table. A picture's
frame is its box: reshape it when Rebind's guess is off, and exactly the labels inside it are read
as part of the picture.

**Symbols a font misreports.** Some journals set their plus signs, arrows and Greek letters in
fonts that label those glyphs as ordinary letters, so the file says "þ" where the page shows "+".
Rebind compares each glyph's shape with the character its font claims, and in a font that is
mostly wrong about itself it reads a symbol only when the shape leaves no doubt. Every other glyph
is read as unknown (U+FFFD) rather than guessed. The report lists the pages this happened on:
describing the formulas there is what makes them readable. The font's map is repaired too, so
the page itself stays as it was.

**Coming back to a document.** A file Rebind wrote can be uploaded again and the work carries on
where it was left: every correction (types, frames, removed and inserted elements, descriptions)
is saved inside the file, and the editor opens with all of it. Rebind reads the file as the
document it was before, not with its text doubled; a page it had to redraw from a picture keeps
its own drawing too, and is put back exactly. A description already in another tool's tags is
kept and shown as well; what you type replaces it.

`x` is an action, not a type: it marks content as an artifact rather than untagging it, since
untagged content is a conformance failure. Page furniture and text inside figures are drawn hatched
as "not read"; giving one a type puts it back into the reading order.

On a detected table's row, `h` (Header cell) and `d` (Data cell) set that row, so you can correct
which rows are headers without retagging the whole table; every other type key retypes the whole
table. The key legend says so when you land on a row.

Every change rebuilds the document from the corrected plan rather than patching the structure tree,
so grouping decisions change too. Every offered type has a test that applies it and validates the
result: `/Caption` and `/Quote` are illegal directly under the document, `/Aside` is not a PDF 2.0
name, a grouping element may not hold content directly, a `/Figure` needs an `/Alt`, and a
`/Caption` must be nested inside the figure or table it captions. `/Part` is not offered.

The result view also shows a structure badge: a fast, dependency-free check of what remediation is
expected to have built. It is not conformance validation — that is veraPDF, used in development only (ADR 0006).

Closing the tab quits Rebind. A reload does not: the beacon starts a short countdown that the
reloaded page's first heartbeat cancels.
