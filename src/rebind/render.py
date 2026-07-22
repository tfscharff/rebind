"""HTML to tagged PDF/UA rendering.

Rebind generates its output rather than patching a source PDF, which is what makes most of
WCAG 2.1 AA achievable by construction. See the design spec, section 2.
"""

from __future__ import annotations

from pathlib import Path

from weasyprint import HTML

_DOCUMENT_TEMPLATE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  @page {{ size: letter; margin: 1in; }}
  body {{ font-family: "DejaVu Serif", Georgia, serif; font-size: 11pt; line-height: 1.45;
          color: #111; background: #fff; }}
  h1, h2, h3 {{ line-height: 1.2; }}
  table {{ border-collapse: collapse; }}
  th, td {{ border: 1px solid #444; padding: 4pt 6pt; text-align: left; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def render_html_to_pdf(html: str, target: Path, *, title: str, lang: str = "en") -> Path:
    """Render an HTML body fragment to a tagged PDF/UA-1 file.

    The document colours are fixed to guarantee WCAG 1.4.3 contrast, which we can do
    precisely because we generate the output rather than inherit it.
    """
    document = _DOCUMENT_TEMPLATE.format(lang=lang, title=_escape(title), body=html)
    HTML(string=document).write_pdf(
        target,
        pdf_variant="pdf/ua-1",
        uncompressed_pdf=False,
    )
    return target


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
