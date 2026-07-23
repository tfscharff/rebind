"""Generate born-digital PDFs for tests.

`samples/` is gitignored (copyrighted third-party scans in a public repo), so the suite cannot
depend on any real document. Fixtures are rendered with WeasyPrint at test time instead: known
HTML in, PDF out, then back through Rebind so the recovered model can be compared to the
structure that went in.

Limitation, stated so it is not forgotten: WeasyPrint output is unusually well-formed. It does
not reproduce what InDesign, Word or LaTeX emit -- inconsistent font naming, text split mid-word
across spans, headers in margin boxes. These fixtures prove the logic is correct; they do not
prove the heuristics are tuned. See spec section 9.1.
"""

from __future__ import annotations

from pathlib import Path

from weasyprint import HTML

_PAGE_CSS = """
@page { size: letter; margin: 1in; }
body { font-family: "DejaVu Serif"; font-size: 11pt; line-height: 1.4; }
h1 { font-size: 24pt; font-weight: bold; }
h2 { font-size: 18pt; font-weight: bold; }
h3 { font-size: 14pt; font-weight: bold; }
"""


def born_digital_pdf(html_body: str, target: Path, *, extra_css: str = "") -> Path:
    """Render an HTML fragment to an untagged born-digital PDF with a real text layer."""
    document = (
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>fixture</title><style>{_PAGE_CSS}{extra_css}</style></head>"
        f"<body>{html_body}</body></html>"
    )
    HTML(string=document).write_pdf(target)
    return target
