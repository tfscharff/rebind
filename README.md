# Rebind

**Accessible PDF reconstruction for damaged library scans.**

> ⚠️ **Status: alpha.** Both the born-digital and scanned branches work end to end. A scanned or
> born-digital PDF goes in; a tagged PDF/UA document that veraPDF passes comes out, plus a
> source-of-truth model. Scanned pages are restored (deskew/denoise) and recognized with on-device
> OCR (no API key, GPU or network). Layout analysis reconstructs multi-column reading order;
> suspected tables and other uncertain regions are flagged for review, never silently trusted. A
> local browser app drives the whole thing, and the double-click Windows installer now builds. Not
> yet done: full page dewarp, table reconstruction, and tuning against a wider range of real scans.
> Output is not byte-reproducible (see ADR 0003).

Rebind takes any PDF — a scan or a born-digital file, tagged or not — and produces an accessible
PDF that **looks exactly like the original**, conforming to WCAG 2.1 AA, so it can still be read,
cited and printed the way it always was.

It runs entirely on your own machine. No API key, no GPU, no cloud service, no per-document cost.

## Why

Libraries receive scanned PDFs they did not create and cannot re-source: interlibrary loan deliveries,
course reserves, digitized departmental documents. The worst of them are also the ones patrons with
print disabilities most need, and they are exactly the documents existing tools fail on.

Commercial auto-tagging assumes recoverable structure, and a warped bitmap has none. Institutional
platforms fall over on scale — one widely used product aborts at 999 structure elements, which a
300-page course catalog exceeds without trying. Manual remediation works, but at a cost per document
that makes a thousand-document backlog impossible.

## How it works

**Rebind preserves your original page and adds only the accessibility it's missing.**

It does not reconstruct or reflow the document — that can never look like the original. Instead it
keeps each page exactly as it is (vector text stays crisp; a scan stays a scan), lays an invisible,
selectable text layer over it — from the page's own text where it has one, or from on-device OCR
where it doesn't — and builds a real PDF/UA structure tree: reading order, headings, language and
title. The output looks like the input but validates as **PDF/UA-1** (verified with veraPDF, zero
failures), the standard behind WCAG 2.1 AA for PDFs.

Everything runs on your own machine — no API key, no GPU, no cloud service, no per-document cost.

## Principles

- **Never fabricate.** Every word traces back to recognizer output with a confidence score. Where the
  source is unrecoverable, Rebind says so — `[text not recoverable from source scan, p. 214]` — rather
  than inventing something plausible. A confident lie is worse than an honest gap.
- **Know what you don't know.** Rebind decides everything it can and surfaces only what it can't, as a
  short queue of flagged exceptions rather than a page-by-page review.
- **Free, local, and installable.** A library with no budget, no GPU, and no one who knows what an API
  key is should be able to download an installer and use this.

## Handles

**This table is the long-term design target, not the current state.** Today Rebind preserves the
page and adds a selectable text layer + PDF/UA structure (headings, reading order) — see
[Usage](#usage) below for exactly what works and what does not yet.

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

**The app.** Install with the Windows installer (`rebind-setup.exe`, built from `packaging/`) or
run `rebind serve`, then open the local page it launches. Drop a PDF in and download the accessible
version. The page it hands back looks exactly like the one you gave it — the original is kept, not
rebuilt — but its text is now selectable and readable by assistive technology, and it carries a
PDF/UA structure tree (reading order, headings, language, title). Everything runs on your machine;
nothing is uploaded.

What it does, per page:

- **A page that already has text** (born-digital) is kept verbatim — vector text stays crisp — and
  its words are tagged with reading order and heading structure.
- **A scanned page** keeps its image and gets an invisible, selectable OCR text layer laid over it
  (on-device OCR — RapidOCR on the CPU, models bundled, no API key, GPU or network). The image
  looks identical; the text is now there for a screen reader.
- **A page where no text can be recovered** (blank, or an image with no words) is kept as-is and
  reported, so you know it carries no readable text.

The output validates as **PDF/UA-1** (checked with veraPDF), the standard behind WCAG 2.1 AA for
PDFs. The tag tree carries headings, paragraphs, lists and tables. Headings are recovered from
born-digital pages; scanned text stays flat paragraphs (its font sizes are OCR noise, and inventing
a heading hierarchy from them would be fabrication). **Figures** get the one thing a machine can't
supply — a description: images are decorative by default, and the app shows each one so you can type
what it depicts, which turns it into a described, screen-reader-visible figure.

**The command line.** The same thing, scriptable:

```
rebind convert input.pdf output.pdf
```

Set `REBIND_DEBUG=1` to print a full traceback on an unexpected failure, for bug reports.

`rebind serve` starts the local server; this is also what the installed desktop application runs
on double-click.

## Documentation

- [Design specification](docs/superpowers/specs/2026-07-22-rebind-design.md)
- [Contributing](CONTRIBUTING.md)

## License

MIT — see [LICENSE](LICENSE). Bundled third-party components carry their own licenses; see the design
specification for details.
