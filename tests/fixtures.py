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

import pikepdf
from pikepdf import Array, Dictionary, Name
from weasyprint import HTML

_PAGE_CSS_TEMPLATE = """
@page {{ size: letter; margin: {margin}; }}
body {{ font-family: "DejaVu Serif"; font-size: 11pt; line-height: 1.4; }}
h1 {{ font-size: 24pt; font-weight: bold; }}
h2 {{ font-size: 18pt; font-weight: bold; }}
h3 {{ font-size: 14pt; font-weight: bold; }}
"""

# The default every existing fixture call relies on. Chosen originally for realism, but at this
# margin a full-width line of body text never reaches into `profile.EDGE_FRACTION` of the page,
# which is exactly why the body-style-as-artifact bug (Finding 1) went uncaught here -- see
# `margin="0.75in"` below for the regression test that needed a narrower margin to reproduce it.
_DEFAULT_MARGIN = "1in"


def born_digital_pdf(
    html_body: str, target: Path, *, extra_css: str = "", margin: str = _DEFAULT_MARGIN
) -> Path:
    """Render an HTML fragment to an untagged born-digital PDF with a real text layer.

    `margin` controls the `@page` margin (a CSS length, e.g. "1in" or "0.75in"). Callers that
    need to reproduce edge-band-sensitive behaviour -- a narrower margin lets ordinary body text
    reach into `profile.EDGE_FRACTION` of the page, which the default 1in margin never does --
    should pass it explicitly rather than relying on the module default.
    """
    page_css = _PAGE_CSS_TEMPLATE.format(margin=margin)
    document = (
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>fixture</title><style>{page_css}{extra_css}</style></head>"
        f"<body>{html_body}</body></html>"
    )
    HTML(string=document).write_pdf(target)
    return target


def pdf_with_text_in_form_xobject(target: Path, *, text: str = "Text inside a form xobject") -> Path:
    """Build a minimal PDF whose only content is drawn from inside a Form XObject (a PDF
    `/Subtype /Form`, invoked from the page content stream via the `Do` operator) -- the shape
    produced by, among other things, form-overlay layers and some DTP tools' text frames.

    WeasyPrint has no way to emit this directly, so it is built from raw PDF objects with
    pikepdf instead. Regression fixture for Finding 2: with `pdfminer`'s default `LAParams`,
    characters inside a Form XObject are never grouped into text containers at all, so this text
    was invisible to `_line_from_container` and surfaced as a false "image region" placeholder
    instead of the real, fully extractable text it actually is.
    """
    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    pdf = pikepdf.Pdf.new()
    xobj_content = f"BT /F1 24 Tf 72 700 Td ({escaped}) Tj ET".encode("latin-1")
    xobj = pdf.make_stream(xobj_content)
    xobj.Type = Name.XObject
    xobj.Subtype = Name.Form
    xobj.BBox = Array([0, 0, 612, 792])
    xobj.Resources = Dictionary(
        Font=Dictionary(
            F1=Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
        )
    )
    page_content = pdf.make_stream(b"/Fx1 Do")
    page_dict = Dictionary(
        Type=Name.Page,
        MediaBox=Array([0, 0, 612, 792]),
        Resources=Dictionary(XObject=Dictionary(Fx1=xobj)),
        Contents=page_content,
    )
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page_dict)))
    pdf.save(target)
    return target
