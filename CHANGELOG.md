# Changelog

What changed in each release, newest first. Each entry is a summary; the full notes are on the
[releases page](https://github.com/tfscharff/rebind/releases). Versions follow
[Semantic Versioning](https://semver.org/).

## 1.12.0 (2026-11-10)

- **Page Up** and **Page Down** turn the page. `[` and `]` still do.
- Section, Article, Index and No structure are no longer offered as types. Each is a container
  a screen reader announces as nothing, so one holding a paragraph read exactly as the
  paragraph. A file saved with one opens with a paragraph in its place.
- Removed what the page stopped using: the separate endpoint for picture descriptions, and the
  reading-order evidence worked out on every conversion.
- The two tests of the app quitting itself no longer end the test run, and run with the rest.

## 1.11.2 (2026-11-10)

- In a short window the list of keys scrolls, and the element's **Insert** and **Delete** buttons
  stay in view. They used to be pushed out of sight.
- Fixed: removing a scan's old hidden text layer could take a font away from another page that
  shared it, leaving that page's visible text without one.
- Fixed: a model downloaded after Rebind had first looked for it was not used until the next run.
- A conversion opens the PDF once to draw its pages, not once for every page drawn.
- The tests now run on GitHub on every push, on Windows, with veraPDF installed.

## 1.11.1 (2026-11-10)

- The third-party notices are complete and no longer padded. The notice generator kept only the
  first license file from each package, so PDFium, GEOS and qpdf went uncredited; it now takes
  every one. Python's own license and two packages the installer picks up (brotli, setuptools) are
  added. Twenty-one license texts for software Rebind no longer includes were removed.
- A failed conversion closes the document and removes its working copy.
- Checking colour contrast, and finding scans that carry their own text layer, are done once per
  file, not on every saved correction.
- Removed code nothing used: the image clean-up module, the old document model, and several
  unused helpers. Box arithmetic that was written out in seven places is in one.
- The README is rewritten in plain language and has a screenshot.

## 1.11.0 (2026-11-10)

- Faster: recognising a scanned page takes about half as long, and reading a page's layout about a
  quarter less. A saved correction on a long born-digital document no longer re-reads the whole
  file, and scanned pages are not searched for pictures again on every save.
- Working copies left in the temp folder by earlier sessions are deleted when Rebind starts.
- Fixed: after darkening text set in a page's own colour space, later colours on that page could
  be drawn wrong.
- Fixed: the report could not say which page a faulty table row, cell or list item was on.
- Fixed: the key legend on a table row named `b` for a data cell; the key is `d`.
- Documentation rewritten: a shorter README, with how Rebind works and the editor reference moved
  to `docs/`. Two claims the code did not bear out were corrected: scans are not deskewed or
  denoised before recognition, and a line the recogniser is unsure of is left out, not replaced
  with a placeholder.

## 1.10.1 (2026-11-10)

- Heading levels follow the document's title: the title is Heading 1, authors and main sections
  Heading 2, subsections Heading 3. A library cover sheet in front of an article no longer
  flattens every heading to Heading 1.
- Fonts that mark bold and italic only with a `.B` or `.I` suffix are read as such.

## 1.10.0 (2026-11-10)

- Tables printed sideways are read the right way up, and shown upright in the editor.
- Pages whose boxes do not start at the page corner (every JSTOR download) are framed and drawn
  where they are; frames and the text layer used to sit 8 points too high.
- A table whose cells wrap over several lines, with no rules between rows, is one row per row.
- Drop caps are read with the word they begin.
- A scan's own OCR layer is completed with what it missed. Large uncaptioned pictures on a scan
  are kept. A page with nothing to read has a Tab stop.
- The Division type is gone; table data cell is now `d`.

## 1.9.1 (2026-11-05)

- `?` opens the list of keys on anything, a picture included; there is an **All keys** button too.

## 1.9.0 (2026-11-05)

- Every correction is saved inside the file Rebind writes: upload it again and carry on.
- Greek letters, arrows and minus signs survive in the text layer. Fonts that mislabel their own
  symbols are read by the shapes of their glyphs, or marked unknown, never guessed.
- A table with group labels over column labels has both rows as headers.

## 1.8.0 (2026-11-05)

- Descriptions already in a PDF's tags are kept and shown.
- A file Rebind wrote can be uploaded again without its text doubling.
- Table cells are placed in the column they are printed in, whatever their alignment.

## 1.7.0 (2026-10-20)

- Superscripts and subscripts are tagged as such, in born-digital files and scans.

## 1.6.0 (2026-10-20)

- A picture no longer claims paragraphs set beside it. A table is never taken for a figure.
- Sideways text in a margin (a download stamp) is rejoined and treated as page furniture.

## 1.5.0 (2026-10-15)

- Printed music is found by its staves and becomes a figure with a drafted description of its
  layout and clefs.

## 1.4.0 (2026-10-15)

- Uncaptioned pictures on scans are kept for describing. Repeated ornaments and stray marks from
  the scanner are left out.

## 1.3.0 (2026-10-15)

- Each equation gets a drafted description in words, read from its picture, for you to check.

## 1.2.0 (2026-10-15)

- Displayed equations are found and tagged as formulas, and can be described like pictures.

## 1.1.1 (2026-10-15)

- Running from source downloads the layout model on first use. Rebind no longer locks its own
  folder after it exits.

## 1.1.0 (2026-10-15)

- A page-layout model (PP-DocLayoutV3) groups the lines Rebind already has: whole paragraphs,
  better tables, captions and page furniture. It never supplies text.

## 1.0.4 to 1.0.11 (2026-09-22 to 2026-10-07)

- The editor gains frames that can be dragged, tables built by hand, table rows with frames,
  Insert and Delete, and inserted elements that keep their place in the reading order.
- Fixes to paragraphs split a line at a time on two-page-spread scans, to tables split across
  columns, and to where page furniture sits in the walk.

## 1.0.2 and 1.0.3 (2026-08-26)

- Footnote type (`v`); a table's header rows can be corrected a row at a time.
- The editor walks the page in reading order on tilted and two-column scans; headings are found
  on a tilted scan.

## 1.0.0 and 1.0.1 (2026-08-13)

- First release: a PDF in, the same PDF out with a tagged text layer and structure tree that
  validates PDF/UA-2, and an editor in the browser for what a machine cannot decide.
