# Rebind

**Accessible PDF reconstruction for damaged library scans.**

> ⚠️ **Status: pre-alpha.** Phase 0 (feasibility spikes) complete — see
> [ADR 0002](docs/decisions/0002-phase-0-findings.md). WeasyPrint reliably produces PDF/UA-1
> tagged output (headings, lists, tables with header associations, figures with alt text, page
> labels) and a frozen, no-system-Python build has been proven to render real PDFs from bundled
> DLLs — but the double-click installer itself has not yet been built or tested end-to-end, and
> output is not byte-reproducible (see ADR 0002/0003). Phase 1 (end-to-end pipeline spine) in
> progress.

Rebind takes a badly scanned PDF — skewed, warped, low-contrast, fingers in frame, multi-column with
sidebars, full of tables and equations — and produces a **new**, born-accessible PDF that conforms to
WCAG 2.1 AA, with the original pagination preserved so it can still be cited.

It runs entirely on your own machine. No API key, no GPU, no cloud service, no per-document cost.

## Why

Libraries receive scanned PDFs they did not create and cannot re-source: interlibrary loan deliveries,
course reserves, digitized departmental documents. The worst of them are also the ones patrons with
print disabilities most need, and they are exactly the documents existing tools fail on.

Commercial auto-tagging assumes recoverable structure, and a warped bitmap has none. Institutional
platforms fall over on scale — one widely used product aborts at 999 structure elements, which a
300-page course catalog exceeds without trying. Manual remediation works, but at a cost per document
that makes a thousand-document backlog impossible.

## How it's different

**Rebind does not remediate the source PDF. It reconstructs the document.**

The scan is treated as *evidence* of a document rather than as the document itself. Rebind dewarps and
cleans each page, recognizes the text, analyzes the layout, understands the reading order, and then
**generates a new document** with real headings, real tables, real alt text, and a real structure tree.

Because the output is generated rather than patched, much of WCAG 2.1 AA is satisfied by construction —
including 1.4.5 (Images of Text), which no approach that preserves the scanned page can ever really
escape.

## Principles

- **Never fabricate.** Every word traces back to recognizer output with a confidence score. Where the
  source is unrecoverable, Rebind says so — `[text not recoverable from source scan, p. 214]` — rather
  than inventing something plausible. A confident lie is worse than an honest gap.
- **Know what you don't know.** Rebind decides everything it can and surfaces only what it can't, as a
  short queue of flagged exceptions rather than a page-by-page review.
- **Free, local, and installable.** A library with no budget, no GPU, and no one who knows what an API
  key is should be able to download an installer and use this.

## Handles

**This table is the design target, not the current state.** Today only the born-digital branch is
built — see [Usage](#usage) below for exactly what works and what does not.

| | |
|---|---|
| Degraded scans | deskew, dewarp, denoise, occlusion detection |
| Born-digital PDFs | untagged or badly tagged files, using the embedded text layer |
| Mixed documents | digital pages and scanned inserts, branched per page |
| Complex layout | multi-column, sidebars, pull quotes, marginalia |
| Tables | header association, spans, cross-page continuation |
| Mathematics | recognized to LaTeX, output as MathML with spoken descriptions |
| Chemistry | recognized to SMILES, re-rendered as clean vector structures |
| Sheet music | detected and described (recognition not attempted) |
| Long documents | 1,000+ pages, resumable, no structure-element limits |

## Usage

Convert a born-digital PDF (one with a real text layer) to a tagged PDF/UA document:

```
rebind convert input.pdf output.pdf
```

This writes `output.pdf` and `output.model.json`. The model is the source of truth; the PDF is a
build artifact regenerable from it.

Recovered structure: headings (levels inferred from a document-wide typographic profile),
paragraphs, and ordered/unordered lists. Running headers, footers and page numbers are detected
and excluded from the reading order. Output pages carry the source's sequential page number (page
1, 2, 3, ...), not the source's own printed pagination — a document with roman-numeral front
matter or plate numbers does not yet get those labels back. Extracting the document's own printed
page labels is later work.

Images become placeholders rather than figures — PDF/UA requires alt text on every figure, and
there is currently no honest way to generate it, so **images are not reproduced in the output**.
Multi-column pages **are** reconstructed: each page is segmented into columns and blocks and its
text emitted in correct reading order (left-to-right across columns, top-to-bottom within them). A
page whose column gutter is only marginal is flagged rather than trusted. **Tables are not detected
at all.** A table's cell text is kept, but it is emitted as ordinary paragraphs in naive reading
order, with no flag — cell text can come out in the wrong order with nothing to warn you. Real
table structuring is a later phase.

Some scans arrive as a page image with an invisible OCR text layer already on top (many
interlibrary-loan deliveries are like this). Rebind detects these, reuses the existing text but
**marks it as recognizer output** — flagged `ocr-source`, with capped confidence — and reports the
affected pages, rather than presenting possibly-garbled OCR as a clean transcription. It does not
yet re-recognize such text or OCR pages that have *no* text layer at all; that is the OCR branch, a
later phase.

Pages without a text layer become honest placeholders and are listed on stderr, so a later OCR
pass knows which pages to revisit. A document with no text layer on any page is a scan, and is
refused outright — the OCR branch is not implemented yet.

Set `REBIND_DEBUG=1` to print a full traceback on an unexpected conversion failure, for bug
reports.

`rebind serve` starts the local server; this is also what the installed desktop application runs
on double-click.

## Documentation

- [Design specification](docs/superpowers/specs/2026-07-22-rebind-design.md)
- [Contributing](CONTRIBUTING.md)

## License

MIT — see [LICENSE](LICENSE). Bundled third-party components carry their own licenses; see the design
specification for details.
