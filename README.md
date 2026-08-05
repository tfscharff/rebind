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

Reading order for multi-column pages is recovered by recursive XY-cut over the text-line boxes.

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
- **Figures** (`/Figure` with `/Alt`). Images are decorative artifacts by default; the app shows
  each one so a description can be typed, which promotes it to a tagged figure with alt text.
- **Links** — an external link (URI) is tagged into the structure tree with an object reference
  back to its annotation. An internal link using a legacy page/coordinate destination is removed
  rather than left non-conformant (PDF/UA-2 requires internal destinations to be structure
  destinations, which nothing before PDF 2.0 could produce).
- **Bookmarks** — an outline built from the recovered headings, nested by level, each entry a real
  PDF 2.0 structure destination into the heading itself.

Text is never fabricated: every text node traces to recognizer output with a confidence score.
Below threshold it becomes an explicit placeholder — `[text not recoverable from source scan,
p. 214]` — rather than a guess.

## Running it

**App.** Install with the Windows installer (`rebind-setup.exe`, built from `packaging/`) or run
`rebind serve`, then use the local browser page it opens. Drop a PDF in, convert, and download the
result. Images that need a description are listed so you can type one in the app. The result view
shows a structure badge: a fast, dependency-free check of what remediation is expected to have
built (not independent conformance validation — that's veraPDF, dev/CI-only; see ADR 0006). Nothing
is uploaded.

**Command line.**

```
rebind convert input.pdf output.pdf
```

`REBIND_DEBUG=1` prints a full traceback on an unexpected failure. `rebind serve` starts the local
server; this is what the installed application runs on double-click.

## Status

Alpha (v0.8.0). Born-digital and scanned inputs both work end to end.

Implemented: on-device OCR with deskew/denoise restoration, multi-column reading order, and the
structure tree above (headings, paragraphs, lists, tables, figures, links, bookmarks).

Not implemented: full page dewarp for spine-curved scans, figure/caption association, and
mathematics/chemistry/music recognition. Output is not byte-reproducible (see ADR 0003).

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
