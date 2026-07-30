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


def pdf_image_only_scan(html_body: str, target: Path, *, dpi: int = 150,
                        rotate_deg: float = 0.0) -> Path:
    """Build an image-only scanned PDF (no text layer) from an HTML fragment.

    Renders the HTML to a born-digital PDF, rasterizes its first page with pypdfium2, and embeds
    that raster as a full-page JPEG image in a fresh PDF with no text -- the shape of a real scan
    (`samples/Failure.pdf`). The known input text is recoverable only by OCR, which is the point.

    `rotate_deg` skews the raster by that many degrees (white fill), to reproduce a crooked scan
    for testing deskew.
    """
    import io

    import pypdfium2 as pdfium

    source_pdf = target.with_suffix(".source.pdf")
    born_digital_pdf(html_body, source_pdf)

    doc = pdfium.PdfDocument(str(source_pdf))
    page = doc[0]
    width_pt, height_pt = page.get_size()
    bitmap = page.render(scale=dpi / 72.0)
    pil_image = bitmap.to_pil().convert("RGB")
    if rotate_deg:
        pil_image = pil_image.rotate(rotate_deg, expand=False, fillcolor=(255, 255, 255))
    buffer = io.BytesIO()
    pil_image.save(buffer, format="JPEG", quality=90)
    jpeg = buffer.getvalue()
    doc.close()

    pdf = pikepdf.Pdf.new()
    image = pdf.make_stream(jpeg)
    image.Type = Name.XObject
    image.Subtype = Name.Image
    image.Width = pil_image.width
    image.Height = pil_image.height
    image.ColorSpace = Name.DeviceRGB
    image.BitsPerComponent = 8
    image.Filter = Name.DCTDecode
    content = f"q {width_pt} 0 0 {height_pt} 0 0 cm /Im0 Do Q".encode("latin-1")
    page_dict = Dictionary(
        Type=Name.Page,
        MediaBox=Array([0, 0, width_pt, height_pt]),
        Resources=Dictionary(XObject=Dictionary(Im0=image)),
        Contents=pdf.make_stream(content),
    )
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page_dict)))
    pdf.save(target)
    return target


def pdf_scan_with_ocr_layer(target: Path, *, text: str = "recognized text over a scan") -> Path:
    """Build a PDF shaped like an OCR'd scan: a page-covering raster image with a text layer drawn
    on top, the way scanning + OCR tools (and the 1905 bulletin / Chapter 14 samples) produce.

    A 1x1 image scaled by the content-stream matrix to the full MediaBox is the whole "scan"; the
    text is a real, extractable line above it. WeasyPrint cannot emit this shape, so it is built
    from raw PDF objects with pikepdf.
    """
    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    pdf = pikepdf.Pdf.new()
    image = pdf.make_stream(bytes([200, 200, 200]))  # one grey pixel, raw DeviceRGB
    image.Type = Name.XObject
    image.Subtype = Name.Image
    image.Width = 1
    image.Height = 1
    image.ColorSpace = Name.DeviceRGB
    image.BitsPerComponent = 8
    # Draw the image across the whole page, then the OCR text line on top of it.
    content = (
        b"q 612 0 0 792 0 0 cm /Im0 Do Q "
        + f"BT /F1 12 Tf 72 700 Td ({escaped}) Tj ET".encode("latin-1")
    )
    page_dict = Dictionary(
        Type=Name.Page,
        MediaBox=Array([0, 0, 612, 792]),
        Resources=Dictionary(
            XObject=Dictionary(Im0=image),
            Font=Dictionary(
                F1=Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
            ),
        ),
        Contents=pdf.make_stream(content),
    )
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page_dict)))
    pdf.save(target)
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


def born_digital_pdf_with_table(target: Path) -> Path:
    """A born-digital PDF whose body is a 4-row x 3-column grid table (header row + 3 data rows).

    Wide columns with short cell text so the cells land on distinct, recurring column positions --
    the shape `layout.detect_table_lines` recognizes (>=3 rows each spanning >=3 columns). The
    `<th>`/`<td>` HTML distinction is invisible to Rebind (it re-derives structure from line-box
    geometry, not markup); the header row is inferred from being the table's top row.
    """
    html = (
        "<table>"
        "<tr><th>Region</th><th>Sales</th><th>Growth</th></tr>"
        "<tr><td>North</td><td>120</td><td>8</td></tr>"
        "<tr><td>South</td><td>95</td><td>3</td></tr>"
        "<tr><td>East</td><td>140</td><td>12</td></tr>"
        "</table>"
    )
    css = ("table { width: 90%; border-collapse: collapse; } "
           "td, th { border: 1px solid #000; padding: 10px 40px; text-align: left; }")
    return born_digital_pdf(html, target, extra_css=css)


def born_digital_pdf_with_sparse_row_table(target: Path) -> Path:
    """A table whose third row is *sparse* (an empty middle cell), the shape of a subtotal row.

    A sparse row has too few side-by-side cells to be detected as a table row on its own, so a naive
    tagger drops it and fragments the table. It sits between full rows here to prove such internal
    rows are still accounted for -- kept as a row of the one table, with an empty cell in the gap.
    """
    html = (
        "<table>"
        "<tr><th>Region</th><th>Sales</th><th>Growth</th></tr>"
        "<tr><td>North</td><td>120</td><td>8</td></tr>"
        "<tr><td>West</td><td></td><td>5</td></tr>"
        "<tr><td>South</td><td>95</td><td>3</td></tr>"
        "<tr><td>East</td><td>140</td><td>12</td></tr>"
        "</table>"
    )
    css = ("table { width: 90%; border-collapse: collapse; } "
           "td, th { border: 1px solid #000; padding: 10px 40px; text-align: left; }")
    return born_digital_pdf(html, target, extra_css=css)


def born_digital_pdf_with_image(target: Path) -> Path:
    """A born-digital PDF with a small embedded raster image (a figure)."""
    import base64
    import io as _io

    from PIL import Image

    buf = _io.BytesIO()
    Image.new("RGB", (120, 80), (180, 40, 40)).save(buf, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    html = (f"<h1>Report</h1><p>See the chart:</p>"
            f"<img src='{uri}' width='200' height='133'><p>As shown above.</p>")
    return born_digital_pdf(html, target)
