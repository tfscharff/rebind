"""Build the made-up one-page report the README's screenshots show. No real document is in the
repository: the samples used in development are copyrighted.

    PYTHONPATH=. uv run python scripts/demo_document.py annual-report.pdf
"""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from tests.fixtures import born_digital_pdf

CSS = (
    "body{font-family:serif;font-size:11pt;line-height:1.35}"
    "h1{font-size:19pt;line-height:1.2;margin:0 0 4pt}"
    "h2{font-size:13pt;margin:14pt 0 4pt} .by{font-style:italic;margin:0 0 10pt}"
    "p{margin:0 0 8pt} table{width:80%;border-collapse:collapse;margin:6pt 0 12pt}"
    "td,th{border-bottom:1px solid #999;padding:4px 18px 4px 0;text-align:left}"
    ".cap{font-size:9.5pt;margin-top:4pt} img{display:block;margin-top:6pt}"
)


def chart() -> str:
    """A bar chart as a PNG data URI: a raster picture, which is what Rebind finds as a figure."""
    image = Image.new("RGB", (900, 450), "#f3efe6")
    draw = ImageDraw.Draw(image)
    for index, height in enumerate((120, 186, 165, 264, 312, 288)):
        draw.rectangle((90 + 135 * index, 390 - height, 180 + 135 * index, 390), fill="#7a4b2a")
    draw.line((60, 390, 870, 390), fill="#333333", width=4)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def body() -> str:
    rows = (("2020", "1,204", "410", "1,614"), ("2022", "1,187", "655", "1,842"),
            ("2024", "1,231", "902", "2,133"), ("2025", "1,198", "1,037", "2,235"))
    table = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"""
<h1>Requests Filled by the Interlibrary Loan Office, 2020 to 2025</h1>
<p class='by'>Annual report of the library</p>
<h2>Summary</h2>
<p>The office filled more requests in each of the last three years than in any year before them.
Most of the growth came from scanned book chapters, which now make up nearly half of everything
sent. Every scan sent to a patron is checked for accessibility before it leaves the office.</p>
<h2>Requests by year</h2>
<p>Article requests have been steady. Chapter requests have more than doubled since 2020, as the
table shows.</p>
<table><tr><th>Year</th><th>Articles</th><th>Chapters</th><th>Total</th></tr>{table}</table>
<img src="{chart()}" style="width:3.2in;height:1.6in">
<p class='cap'>Figure 1. Chapter requests filled each year, 2020 to 2025.</p>
<h2>What patrons asked for</h2>
<ul><li>Chapters from books the library does not own</li>
<li>Articles from journals with no online access</li>
<li>Older volumes held only in print</li></ul>
"""


if __name__ == "__main__":
    born_digital_pdf(body(), Path(sys.argv[1]), extra_css=CSS)
