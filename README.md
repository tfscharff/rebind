# Rebind

**Accessible PDF reconstruction for damaged library scans.**

> ⚠️ **Status: pre-alpha.** Design approved 2026-07-22; implementation beginning. Nothing here works yet.

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

| | |
|---|---|
| Degraded scans | deskew, dewarp, denoise, occlusion detection |
| Complex layout | multi-column, sidebars, pull quotes, marginalia |
| Tables | header association, spans, cross-page continuation |
| Mathematics | recognized to LaTeX, output as MathML with spoken descriptions |
| Chemistry | recognized to SMILES, re-rendered as clean vector structures |
| Sheet music | detected and described (recognition not attempted) |
| Long documents | 1,000+ pages, resumable, no structure-element limits |

## Documentation

- [Design specification](docs/superpowers/specs/2026-07-22-rebind-design.md)
- [Contributing](CONTRIBUTING.md)

## License

MIT — see [LICENSE](LICENSE). Bundled third-party components carry their own licenses; see the design
specification for details.
