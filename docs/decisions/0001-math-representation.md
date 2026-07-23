# ADR 0001: How mathematics reaches the output PDF

**Date:** 2026-07-22
**Status:** Accepted

## Context

The design spec makes mathematics first-class: recognized to LaTeX, converted to MathML for
assistive technology, with a Speech Rule Engine string as the accessible description. This
task tested whether the renderer (WeasyPrint, via `rebind.render.render_html_to_pdf`) can
carry native MathML into a tagged `Formula` structure element.

## Findings

Tested with WeasyPrint 69.0 and veraPDF (PDF/UA-1 flavour `ua1`), invoked exactly as
`rebind.validate.validate_pdf_ua` and `rebind.inspect.structure_element_types` are used
elsewhere in this repo.

**Correction (final whole-branch review):** an earlier version of this section stated that
WeasyPrint "renders" the MathML equation and merely mis-tags it. That is false, verified
directly, not merely re-asserted. **WeasyPrint 69.0 has no MathML support at all** — a
recursive search of the installed package (`site-packages/weasyprint/`) for anything named
`mathml` finds nothing. WeasyPrint does not recognize `<math>` as MathML markup with meaning;
it treats the element as unknown HTML and falls back to rendering its descendant text nodes as
ordinary inline text. The finding below is therefore stronger than originally recorded: this is
**semantic loss and glyph loss**, not tagging loss alone.

**`test_mathml_produces_a_formula_element` (native `<math>` element, MathML namespace,
`alttext` attribute set on the root):**

- The PDF **renders successfully and passes PDF/UA-1 validation** —
  `validate_pdf_ua(...).compliant` is `True`, with `failed_rules == []`
  (`summary()` reports `"PDF/UA-1: PASS (0 failed checks)"`). WeasyPrint does not error or
  warn; some content reaches the page.
- However, `structure_element_types(target)` for this document is
  `{'Document', 'NonStruct', 'P', 'Span'}`. **No `Formula` element is present.** WeasyPrint
  tags the `<math>` subtree as generic `NonStruct`/`Span` content nested inside the
  paragraph, indistinguishable in the tag tree from any other unrecognized inline markup.
- **What is actually on the page is not the equation, visually.** Direct inspection of the
  content stream's text-positioning operators (`Tm`) shows every character of the equation —
  the numerator, the `±`, the radical's contents, and the denominator alike — placed at the
  *same* baseline y-coordinate, one after another, with no distinguishing vertical offset and
  no drawn fraction bar or radical sign. WeasyPrint is not laying out `<mfrac>`, `<msqrt>`, or
  `<msup>` as a fraction, radical, or superscript at all; it is concatenating the `<math>`
  subtree's character data — `x=-b±b2-4ac2a` — as one run of plain inline text, exactly as it
  would if the same characters had appeared inside an unrecognized custom element. There is no
  fraction bar, no superscript exponent, no radical sign anywhere in the rendered output.
- **The √ glyph is not merely mis-tagged — it is absent from the embedded font entirely.**
  The font's `/ToUnicode` CMap has 29 `bfchar` entries in total; none of them map to U+221A
  (√). WeasyPrint never asked its font subsetter to include a radical glyph, because it never
  recognized `<msqrt>` as requesting one — it only ever saw a run of plain characters, and the
  literal `√` character does not appear anywhere in the MathML source (radicals are expressed
  structurally, via `<msqrt>`, not as a Unicode character), so no glyph for it was ever
  requested. This was confirmed directly, not inferred: `<0073> <00b1>` (± decodes correctly)
  is present in the CMap; no entry decodes to U+221A anywhere in it.
- The `±` character glyph itself **is** present and decodes correctly (`<0073>` maps to
  `<00b1>` in the CMap — verified directly, see the correction below); it is simply rendered as
  plain inline text on the same baseline as everything else, with no operator layout around it.
- Practical consequence: the test fails specifically on
  `assert "Formula" in structure_element_types(target)`, not on the PDF/UA compliance
  assertion — the document is a *valid* PDF/UA-1 file, but it visually and semantically
  misrepresents the equation as a flat run of characters, with at least one symbol (√) dropped
  from the page entirely. A screen reader would encounter the surviving characters as
  untagged/generic inline text with no indication it is an equation, no exposed `alttext`
  (WeasyPrint does not surface the `alttext` attribute anywhere in the tag tree or its
  properties), and a sighted reader looking at the same PDF would see a garbled, un-fractioned,
  un-radicaled run of characters rather than a rendered equation.
- **Root cause not localized.** This task determined *that* WeasyPrint has no MathML
  recognition at all — not merely that its tagging stage lacks a `Formula` rule — but did not
  instrument WeasyPrint's HTML5/foreign-content parser to pinpoint exactly where in its
  pipeline `<math>` stops being treated as anything other than an unknown element. That would
  require reading or instrumenting WeasyPrint's internals, out of scope here. This ADR should
  not be read as implying that diagnostic depth was reached.
- Marked `@pytest.mark.xfail` (non-strict, default) with
  `reason="WeasyPrint does not tag native MathML as Formula; see ADR 0001"` rather than
  deleted, per the brief's instruction, so a future WeasyPrint release that adds MathML support
  will surface as an XPASS and prompt revisiting this ADR.

**Correction: the "± decodes as U+FFFD" claim was false; removed.** An earlier version of
this ADR (and the deferred list in ADR 0002) stated that the diagnostic CMap decoder failed to
decode `±` cleanly, returning `U+FFFD` (the Unicode replacement character), and attributed this
to a limitation of the test-only extractor. This was re-verified directly and is false: the
font's `/ToUnicode` CMap maps `<0073>` to `<00b1>` (U+00B1, `±`), and
`rebind.inspect._add_mapping` decodes that `bfchar` entry correctly — `mcid_text[6] == "±"`
when inspected programmatically, confirmed character-by-character (`ord(c) == 0xb1`), not
`0xfffd`. The original observation was almost certainly a console/terminal encoding artifact
from printing the string directly (Windows consoles commonly render un-encodable characters as
a replacement glyph that looks like `U+FFFD` even when the underlying Python string is
correct), not a defect in the decoder. No corresponding change to `rebind.inspect` was needed;
only the false claim is retracted here (and in ADR 0002's deferred-items list).

**`test_svg_fallback_is_conformant` (image with `alt` text inside a `<figure role="math"
aria-label="...">`, spoken-form string as both `alt` and `aria-label`):**

- **Passes outright**, no `xfail` needed. `validate_pdf_ua(...).compliant` is `True`,
  `failed_rules == []`.
- `structure_element_types(target)` is `{'Document', 'Figure', 'NonStruct', 'P', 'Span'}` —
  the `<figure>` element is tagged as `Figure`, a real PDF/UA structure type, and (unlike the
  MathML case) this is a case where the tag tree correctly reflects the semantic role of the
  content, even though `Figure` is a weaker signal than a dedicated `Formula` tag would be.
- This confirms the fallback path (render each equation as an image, alt/aria-label carrying
  the spoken-form string) is viable end-to-end with rebind's existing rendering and
  validation pipeline, using only what already exists in `render_html_to_pdf` — no new
  runtime code was needed to make this test pass.

**Full suite after this change:** 23 passed, 3 xfailed, 1 xpassed (previously 22 passed,
3 xfailed, 1 xpassed — the one new test here, `test_mathml_glyphs_are_present_in_content_stream`,
is a plain passing assertion of what was actually observed, not another `xfail`; no existing
test was weakened, skipped, or deleted).

## Decision

**SVG (image) with spoken alt text.** WeasyPrint 69.0 **has no MathML support at all** — it
does not carry `<math>` through as a recognized semantic construct, it renders the equation's
descendant text as a flat, un-fractioned, un-radicaled run of plain inline characters on a
single baseline, drops at least one glyph (√) from the embedded font entirely because it never
recognized the structural markup requesting one, tags the result as generic `NonStruct`/`Span`,
and does not expose the `alttext` attribute anywhere in the tag tree. This finding is stronger
than an earlier version of this decision recorded (which described only a tagging defect on
content that render correctly) — the defect is semantic loss and glyph loss, not tagging loss
alone. Because rebind's design spec requires mathematics to be identifiable and correctly
represented, not merely present in a document that happens to still pass a structural validator,
native MathML does not meet the bar Phase 3 needs, even though the resulting document is not
strictly disallowed by PDF/UA-1. This stronger finding makes the decision below *better*
supported than before, not merely unchanged: it was never a viable path, and it is now clear it
was never even rendering the equation correctly in the first place.

Phase 3 must therefore render each recognized equation (from its LaTeX recognition result) to
an image (SVG rasterized to a bitmap for embedding, or a WeasyPrint-renderable image format),
tag it as a `Figure` (the structure type actually observed to work, given `Formula` is
unavailable) inside `role="math"`/`aria-label` markup carrying the Speech Rule Engine spoken
string as the accessible description, matching the pattern validated by
`test_svg_fallback_is_conformant`. MathML should still be generated by the LaTeX-to-MathML
step and attached to the PDF as an associated file (per PDF/UA best practice for supplementary
machine-readable content) for the subset of assistive technology and downstream tooling that
can consume embedded MathML directly, even though rebind cannot rely on the renderer to
surface it as accessible structure on its own.

## Consequences

- Phase 3 needs an equation-to-image rendering step (LaTeX → image) in addition to the
  planned LaTeX → MathML step; the image is what actually reaches readers via the tag tree,
  and MathML becomes a secondary, best-effort artifact rather than the primary accessible
  representation.
- Phase 3 needs a LaTeX-to-Speech-Rule-Engine (or equivalent) step to produce the spoken-form
  string used as `alt`/`aria-label` text, since that string — not the MathML itself — is what
  assistive technology will actually receive through this PDF's tag tree.
- Capability lost: assistive technology that understands embedded PDF MathML natively (where
  it exists) gets no benefit from rebind's tagging today, since MathML cannot currently be
  exposed as first-class accessible PDF structure through this rendering pipeline; only the
  spoken-form alt text is guaranteed to reach the reader. Concretely, that means a screen
  reader user gets one undifferentiated spoken string for the whole equation and **cannot
  navigate into its sub-expressions** — there is no way to step into just the numerator,
  just the denominator, or just the radicand and have that piece re-spoken on its own, the
  way a `Formula`-tagged native-MathML structure (or MathML consumed directly by a
  math-aware reader) would allow. That per-part navigation is the main practical advantage
  MathML holds over a flat text description, and it is exactly what this rendering path
  gives up.
- This finding is scoped to WeasyPrint 69.0. `test_mathml_produces_a_formula_element` remains
  in the suite, non-strict `xfail`, specifically so a WeasyPrint upgrade that adds `Formula`
  tagging support is caught automatically (XPASS) rather than silently going unnoticed.
- No new runtime code was added in this task; the decision is scoped to the rendering
  strategy Phase 3 must implement, not new interfaces here.
