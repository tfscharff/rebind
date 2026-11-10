"""Print the fonts the test fixtures are really set in, and how the chemistry page's lines break.

The tests about particular lines assume DejaVu Serif. Where it is not installed, the renderer
substitutes another font without saying so, and those tests fail for no reason they can show.

    PYTHONPATH=. uv run python scripts/fixture_fonts.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pikepdf

from rebind.extract import extract_pages
from tests.fixtures import CHEMISTRY_HTML, born_digital_pdf

if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as folder:
        source = born_digital_pdf(CHEMISTRY_HTML, Path(folder) / "chem.pdf")
        with pikepdf.open(source) as pdf:
            print(sorted({str(font.BaseFont) for page in pdf.pages
                          for font in page.Resources.Font.values()}))
        for line in next(iter(extract_pages(source))).lines:
            print(ascii(line.text))
