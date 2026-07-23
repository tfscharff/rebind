"""Fonts must be embedded as subsets, not in full.

This exists because the opposite was believed to be true. `libharfbuzz-subset-0.dll` is absent
from the GTK3 runtime and WeasyPrint dlopens it with `allow_fail=True`, which looked like
"subsetting is silently disabled" -- the conclusion recorded in the Phase 0 handoff notes.

It is not. `weasyprint.pdf.fonts.Font.subset` falls back to `_fonttools_subset` whenever the
HarfBuzz subsetter is unavailable *or* HarfBuzz is older than 4.1.0. The bundled HarfBuzz is
older than 4.1.0, so WeasyPrint would decline to use the HarfBuzz subsetter even if the DLL were
supplied -- the missing DLL changes nothing.

Full font embedding would matter: DejaVuSerif alone is ~380 KB, and Rebind's motivating document
is a 300-page catalog. Nothing was checking, so a future GTK runtime upgrade or a change to the
vendored DLL set could regress this silently. This test is that check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pikepdf

from rebind.render import render_html_to_pdf

SAMPLE_HTML = """
<h1>Subsetting</h1>
<p>The quick brown fox jumps over the lazy dog.</p>
"""

# PDF 32000-1 9.6.4: a subset font's BaseFont name is six uppercase letters, a '+', then the
# PostScript name. Its presence is the format's own declaration that the font is a subset.
SUBSET_TAG = re.compile(r"^/[A-Z]{6}\+")

# Comfortably below the smallest full face WeasyPrint would embed (DejaVuSerif is ~380 KB) and
# comfortably above what these few glyphs actually need, so the assertion is not brittle.
MAX_EMBEDDED_BYTES = 100_000


def _embedded_fonts(pdf: pikepdf.Pdf):
    """Yield (base_font_name, embedded_byte_count) for every embedded font in the document."""
    for page in pdf.pages:
        for font in page.get("/Resources", {}).get("/Font", {}).values():
            descendants = font.get("/DescendantFonts")
            candidates = [font] if descendants is None else [font, descendants[0]]
            for candidate in candidates:
                descriptor = candidate.get("/FontDescriptor")
                if descriptor is None:
                    continue
                for key in ("/FontFile", "/FontFile2", "/FontFile3"):
                    stream = descriptor.get(key)
                    if stream is not None:
                        yield str(font.get("/BaseFont")), len(stream.read_bytes())


def test_embedded_fonts_are_subsets(tmp_path: Path):
    target = tmp_path / "subsetting.pdf"
    render_html_to_pdf(SAMPLE_HTML, target, title="Subsetting", lang="en")

    with pikepdf.open(target) as pdf:
        fonts = list(_embedded_fonts(pdf))

    assert fonts, "no embedded fonts found -- the probe is not looking in the right place"

    for base_font, size in fonts:
        assert SUBSET_TAG.match(base_font), (
            f"{base_font} has no subset tag, so the full face is embedded"
        )
        assert size < MAX_EMBEDDED_BYTES, (
            f"{base_font} embeds {size} bytes, which is full-face sized, not a subset"
        )
