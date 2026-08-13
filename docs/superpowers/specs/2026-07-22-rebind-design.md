# Rebind — Design

**Date:** 2026-07-22
**Status:** Approved (design); implementation plan pending
**Author:** Thomas Scharff, with Claude

---

## 1. Problem

Academic libraries receive scanned PDFs they did not create and cannot re-source — interlibrary loan
deliveries, course reserves, digitized departmental documents. A meaningful share of them are
catastrophically degraded: skewed and warped pages, low-contrast photocopies of photocopies, fingers
in frame, multi-column layouts broken up by sidebars and pedagogical apparatus, dense tables,
mathematics, chemical structures, and music notation.

These documents cannot be made accessible with existing tools. Commercial auto-tagging assumes a
document with recoverable structure; there is none in a warped bitmap. Institutional platforms fail on
scale. Manual
remediation in Acrobat is viable for clean documents and prohibitive for damaged ones — the operator
ends up retyping the document.

The result is that the hardest documents, which are disproportionately the ones patrons with print
disabilities need, are the least likely to be remediated.

## 2. Approach: reconstruction, not repair

Rebind does not remediate the source PDF. It treats the scan as **evidence** of a document rather than
as the document, and produces a **new** born-accessible PDF: dewarped, OCR'd, structurally analyzed,
semantically tagged, with original pagination preserved for citation.

This is the central design decision and everything follows from it.

Repairing a warped scan in place means injecting marked-content operators into an existing content
stream — the hardest engineering in this space — and the result is still a warped page with tags
attached. It also cannot satisfy WCAG 1.4.5 (Images of Text), because the page remains an image of
text. Reconstruction sidesteps both problems: because we generate the output, a large share of WCAG
2.1 AA is guaranteed by construction.

**Guaranteed by construction:** contrast (1.4.3), resize/reflow (1.4.4, 1.4.10), text spacing (1.4.12),
images of text (1.4.5), page titled (2.4.2), language of page (3.1.1), multiple ways (2.4.5, via a
generated outline), and consistent structural markup.

**Requires analysis or judgment:** meaningful sequence (1.3.2), info and relationships (1.3.1),
non-text content (1.1.1), language of parts (3.1.2), and sensory characteristics (1.3.3 — unfixable
from source, so flagged rather than mangled).

**Conformance target:** WCAG 2.1 AA for the output document. Verification has two halves — veraPDF for
machine-checkable PDF/UA structural conformance, and a Rebind-generated report covering the AA criteria
that require judgment. PDF/UA and WCAG AA overlap heavily but are not identical; the report makes the
difference explicit rather than implying that a veraPDF pass equals AA conformance.

## 3. Users and scope

**Primary user:** an ILL or accessibility librarian, non-technical, on Windows. Cannot be expected to
install Python, configure paths, obtain API keys, or run Docker. Does not know what an API key is, and
should not have to.

**Scale:** roughly 1,000 documents per year at the originating institution. Typical documents run
20–300 pages; the architecture must survive 1,000+ pages with unattended, resumable runs.

**Explicitly in scope:** degraded scans, born-digital PDFs, mixed documents, multi-column layouts,
sidebars and pedagogical apparatus, tables, mathematics, chemical structures, sheet music (description
only), footnotes, long documents.

**Born-digital and mixed input.** Untagged or badly tagged born-digital PDFs are handled by the same
pipeline on a separate branch: the embedded text layer is extracted directly, with font, size, weight,
and position metadata, and restoration and OCR are skipped. This branch is both faster and
substantially more accurate than the scanned branch, since heading levels, emphasis, and reading order
can be inferred from typography rather than guessed from pixels. Mixed documents — a born-digital
report containing scanned inserts, which is common — are branched per page rather than per document.

A PDF that is *already* well tagged and passes validation is detected at ingest and reported as such
rather than reconstructed. Rebind should not churn documents that are already accessible.

**Explicitly out of scope for v1:** optical music recognition, braille output, PDF forms, documents in
scripts other than Latin (deferred, not designed against), and any cloud service or paid API.

**Non-goals:** matching the source page's visual layout; being the fastest tool; handling clean
documents better than Acrobat does.

## 4. Key decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | Reconstruct rather than repair | Only path to AA on degraded scans; avoids content-stream surgery |
| 2 | Output is PDF; pagination preserved | Required downstream; citation must survive reflow |
| 3 | Autonomous with a flagged-exception queue | Silent confident errors are the primary risk; full review doesn't scale to 1,000/yr |
| 4 | Targeted editing of flagged nodes, plus honest placeholders | Correct what's correctable; admit what isn't; never fabricate |
| 5 | Fully local, free, open source; no API key, no GPU required | Adoption floor for small libraries; reproducibility for review |
| 6 | Alt text derived from document text, not generated by a model | Captions already say what figures mean; explainable, reproducible, free |
| 7 | Classical-first pipeline, borrowing Docling's table model | CPU-viable, bundle-able, inspectable, per-stage confidence, cannot hallucinate |
| 8 | Mathematics is first-class; LaTeX internal, MathML output | Models emit LaTeX, humans edit LaTeX, assistive tech consumes MathML |
| 9 | Chemistry is first-class; SMILES internal, re-rendered output | OCSR is mature enough; re-rendering beats tagging a warped structure |
| 10 | Sheet music is description-only | OMR on degraded scans is research-grade; honest description is achievable |
| 11 | Windows-first, unsigned, double-click installer | Target user's platform; signing cost rejected; OpenRefine precedent |
| 12 | Documents are durable, resumable jobs | 300+ page runs must survive crashes and reboots |
| 13 | Born-digital and mixed input handled on a parallel branch | Same pipeline, better input; page-level branching covers mixed documents |

## 5. Architecture

### 5.1 Runtime

A local Python service with a browser-based UI, packaged as a Windows installer bundling the Python
runtime, Tesseract, and all model weights. No configuration, no network access required at runtime.
The user double-clicks an icon; a browser tab opens; they drag in a PDF.

Jobs are persisted in a local SQLite store with page-level checkpointing. Closing the browser or
rebooting does not lose work; a job resumes at the last completed stage of the last completed page.

### 5.2 Pipeline

Ten stages, each an independent module with a single responsibility, its own confidence output, and
cached per-page results.

1. **Ingest** — render page images at working DPI; classify **each page** as scanned, born-digital, or
   mixed; detect existing valid tagging; capture original page labels.
2. **Restoration** *(scanned pages only)* — deskew, dewarp, denoise, contrast normalization, page-edge
   and occlusion (finger) detection.
3. **Layout analysis** — detect and classify regions: heading, body text, figure, caption, table,
   formula, chemical structure, music, running header/footer, sidebar, page number. On born-digital
   pages this is informed by embedded text position and font metrics rather than by pixels alone.
4. **Reading order** — column detection, sidebar placement, cross-page continuation. Where WCAG 1.3.2
   is decided.
5. **Text acquisition** — two branches converging on the same output shape (text spans with position,
   style, and confidence):
   - *Scanned pages:* Tesseract via OCRmyPDF machinery, retaining per-word confidence.
   - *Born-digital pages:* direct text-layer extraction with font, size, and weight metadata.
     Confidence is high by construction, so these pages produce few flags.
6. **Specialist recognizers** — tables, mathematics, chemistry, music, figures (see 5.4).
7. **Assembly** — build the semantic document tree (see 5.3).
8. **Confidence and flagging** — aggregate node confidence, apply thresholds, emit the review queue.
9. **Render** — document model → semantic HTML → tagged PDF/UA with page labels.
10. **Verify** — veraPDF plus the Rebind AA report.

### 5.3 Document model

A tree serialized as JSON, and the single source of truth. The PDF is a build artifact, always
regenerable.

**Node types:** Document, Section, Heading, Paragraph, List, ListItem, BlockQuote, Table, Figure,
Formula, ChemicalStructure, Music, Footnote, FootnoteRef, PageBreak, Artifact, Placeholder.

**Every node carries:** a stable id, provenance (source page and bounding box), a confidence score, the
producing stage, and any flags.

Two node types are load-bearing:

- **Artifact** — running headers, footers, page numbers, decorative rules. Present in the source,
  deliberately excluded from the reading order so assistive technology does not announce the chapter
  title seventy times.
- **Placeholder** — the honest-failure node, rendering as
  `[text not recoverable from source scan, p. 214]` and retaining page and bbox for audit.

**PageBreak** nodes carry the original page label. These set PDF page labels (so the viewer's page
field shows the original number) and emit an inline marker in the text flow, so a screen-reader user
can hear where a page boundary fell and cite it accurately.

### 5.4 Specialist recognizers

One interface, several implementations. Given an image region and its document context, a recognizer
returns four things: a visual rendering, a semantic representation, an accessible description, and a
confidence score.

| Region | Visual | Semantic | Accessible description |
|---|---|---|---|
| Mathematics | SVG | LaTeX → MathML | Speech string via Speech Rule Engine |
| Chemistry | SVG re-rendered from SMILES (RDKit) | SMILES / InChI | Description including molecular formula |
| Table | — (native table markup) | Cell grid with header associations | Caption plus summary |
| Music | Source image | — | Descriptive alt text (instrumentation, clef, key, systems) |
| Figure | Source image | — | Caption-derived alt text (see 5.5) |

LaTeX is the internal representation for mathematics because recognition models emit it and humans can
edit it; MathML is generated for assistive technology; the speech string becomes the `/Alt` on the
Formula element, with MathML attached as an associated file where supported.

Adding chemistry variants, OMR, or other content types later is a new implementation of this interface,
not a redesign.

### 5.5 Alt text derivation

Alt text is derived from the document, in order of preference:

1. **The figure's caption** — usually present and usually correct, since it is what the author said the
   figure means.
2. **The body-text reference** — "as shown in Figure 4.2, the mitochondrion…" — used to synthesize a
   description that is not merely a verbatim duplicate of the caption.
3. **Region type plus context** — an honest structural description where nothing better is available.
4. **Ask the operator** — the figure enters the review queue.

Verbatim duplication of a visible caption satisfies WCAG 1.1.1 but causes a screen-reader user to hear
the same sentence twice. Where caption and body reference can be combined into something
non-redundant, they are. Bare minimum is the floor, not the target.

### 5.6 Round-trip verification

For recognizers that produce a semantic representation which can be re-rendered — mathematics and
chemistry — Rebind renders the recognized representation back to an image and compares it to the source
region. High visual agreement indicates correct recognition; divergence raises a flag.

This yields a self-checking recognizer for exactly the content types where silent errors are most
damaging, requires no ground truth and no human, and generalizes to any future recognizer with a
renderable semantic form. It is expected to be the project's most novel contribution.

### 5.7 Corrections as a diff layer

Human corrections are stored as a diff layer over the document model, never baked into it. Reprocessing
a document with an improved pipeline therefore does not discard human work.

This requires node identity to survive reprocessing. Edits are anchored to a composite of page number,
normalized bounding box, and a content fingerprint, and re-attached after a re-run by fuzzy matching.
Corrections that cannot be re-attached surface as **orphaned corrections** for review rather than being
silently dropped.

## 6. Review workflow

The system makes every decision it can and surfaces only what it cannot resolve confidently.

**Flag types:** unrecoverable text; low-confidence text; ambiguous reading order; missing or weak alt
text; uncertain table structure; uncertain formula or chemical structure (including round-trip
divergence); sensory-characteristic prose ("see the box at right"); probable foreign-language passage.

**Interface:** a split view showing the cropped source image beside the editable node, driven by the
keyboard — accept, edit, reject, next — so an operator can clear fifty flags without reaching for the
mouse. Safe batch actions are available ("accept all caption-derived alt text", "mark all as
decorative"). The job displays open flag counts and a clear *ready to build* state.

**Resolution options per flag:** accept the system's proposal, edit it against the source crop, or mark
the region unrecoverable — which emits a Placeholder rather than a guess.

## 7. Invariants

- **Never fabricate.** Every text node traces to recognizer output with a confidence score. Below
  threshold, content is flagged or becomes a Placeholder. No code path invents content.
- **Everything has provenance.** Every node knows its source page and bounding box.
- **Deterministic.** Identical input and version produce identical output.
- **No arbitrary limits.** No cap on structure elements, pages, or document size.

## 8. Failure handling

Failure is per-page, never per-job. Any stage may fail on a page without terminating the run; the page
is marked failed, processing continues, and the failure appears in the final report. A recognizer that
raises degrades its region to a figure with a placeholder description and a flag.

Encrypted or corrupt inputs are detected at ingest and reported in plain language. Memory is bounded by
page-at-a-time processing. Checkpoints are written after each stage of each page.

## 9. Testing and evaluation

**Unit tests** per stage against fixture images. **Golden-file tests** on the serialized document model,
which is practical because the pipeline is deterministic and the model is diffable JSON. **veraPDF runs
as a CI gate** against generated output, so a regression in PDF/UA conformance fails the build.

**Synthetic degradation corpus.** Clean born-digital PDFs are programmatically degraded — skew, warp,
blur, compression artifacts, contrast loss, simulated page curl and occlusion — producing damaged scans
whose correct content is known exactly. This provides quantitative accuracy measurement at arbitrary
severity, and is publishable as an independent artifact.

**Metrics:** character error rate against the synthetic corpus; reading-order accuracy; alt-text
coverage; veraPDF pass rate; throughput. The primary metric is **flag precision and recall** — when the
system reports uncertainty, is it actually wrong, and when confident, is it actually right? A
calibrated confidence model is the central claim and must be measured, not asserted.

**Baselines for comparison:** Adobe Acrobat auto-tagging, an existing remediation tool, and
Docling.

## 10. Distribution

Windows-first, unsigned, double-click installer bundling the Python runtime, Tesseract, and model
weights. Estimated 1.5–2.5 GB. macOS support is acceptable if cheap but is not a goal.

Because the installer is the primary distribution channel from the outset, every dependency must be
bundle-able on Windows. Libraries with native dependencies that are painful to bundle are disqualified
regardless of other merits — this constrains the PDF generation choice in particular and must be
validated early with a spike.

**License:** MIT for Rebind's own code. Bundled components carry their own licenses (OCRmyPDF is
MPL-2.0, Tesseract Apache-2.0, veraPDF dual GPLv3/MPLv2, RDKit BSD). Bundling obligations must be
reviewed and documented before the first public release. For the GTK3 runtime the frozen bundle
already vendors, this is done: all 80 DLLs are mapped in `packaging/licenses/DLL-INVENTORY.md`,
generated by `scripts/license_inventory.py`, which fails if the vendored set and the mapping
disagree. The components listed above still need the same treatment as they are adopted.

**Signing:** unsigned for now. EV certificates lost their instant SmartScreen bypass in 2024, so
the plan is free OV-level signing via SignPath Foundation once a CI-built release exists, with
Azure Artifact Signing (~$9.99/month) as the fallback. See ADR 0004.

## 11. Publication plan

- **JOSS** for the software. Requires an OSI license, tests, documentation, contribution guidelines,
  and `paper.md` — established from the first commit rather than retrofitted.
- **Code4Lib Journal or ITAL** for the practitioner paper: the reconstruction method, the confidence
  model, and the evaluation.
- The **synthetic degradation corpus** may warrant separate deposit with its own DOI.

## 12. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| PDF/UA generation from HTML has unacceptable Windows bundling cost | High | Spike first, before any pipeline work |
| Reading order on complex layouts is worse than hoped | High | Flag aggressively; measure honestly; it is the primary research problem |
| Confidence scores are not well calibrated | High | Measure flag precision/recall early; recalibrate per stage |
| Institutional endpoint protection blocks the installer outright on managed library machines | High | Not mitigable by Rebind alone if it occurs — the remedy lies with the institution's IT. Test on a real managed machine early, via Allie at Wheaton, before the installer design is settled (ADR 0004) |
| Unsigned binary triggers SmartScreen warnings that read as "this is malware" to a librarian | Medium | No option grants instant trust, so treat as a documentation problem: say exactly what warning appears and what to click. Pursue free OSS signing via SignPath Foundation once a CI-built release exists (ADR 0004) |
| Installer engineering consumes the schedule | Medium | Spike packaging early with a trivial payload |
| Math/chemistry recognizers underperform on degraded input | Medium | Round-trip verification catches failures; flags absorb them |
| Inline chemistry subscripts (H₂SO₄) mangled by OCR | Medium | Chemistry-aware text post-processor; known-hard, tracked |
| Python 3.14 lacks wheels for parts of the CV/ML stack | Low | Pin 3.12 |
| Bundled-component license obligations conflict | Low | Review before first release |

## 13. Open questions

- Which PDF generation library survives the Windows bundling spike.
- Whether table structure recognition needs Docling's model or a lighter alternative suffices.
- Confidence threshold defaults, which can only be set empirically.
- Whether the review UI needs a page-level view in addition to the flag queue.
