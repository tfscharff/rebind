"""Pages read turned upright or moved to the origin (`turn`), and shown exactly as they were."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pikepdf
import pypdfium2 as pdfium

from rebind.extract import extract_pages
from rebind.remediate import remediate
from rebind.turn import _turn_page
from rebind.validate import validate_pdf_ua
from tests.fixtures import born_digital_pdf, born_digital_pdf_with_table


def _picture(path: Path, number: int = 1) -> np.ndarray:
    return np.asarray(pdfium.PdfDocument(str(path))[number - 1].render(scale=1).to_pil()
                      .convert("L"), dtype=int)


def _sideways(source: Path, target: Path) -> Path:
    """`source` with its page's drawing turned a quarter anticlockwise, as a landscape table is
    printed on a portrait page: its text reads from the bottom up."""
    with pikepdf.open(source) as pdf:
        _turn_page(pdf, pdf.pages[0], 270)
        pdf.save(target)
    return target


def _tables(path: Path) -> list[list[list[tuple[str, int]]]]:
    """Each table's rows, each cell as (tag, how many marked-content pieces it holds)."""
    out = []
    with pikepdf.open(path) as pdf:
        stack = [pdf.Root.StructTreeRoot.K]
        while stack:
            node = stack.pop()
            if isinstance(node, pikepdf.Array):
                stack.extend(node)
            elif isinstance(node, pikepdf.Dictionary) and "/S" in node:
                if node.S == "/Table":
                    out.append([[(str(cell.S)[1:], len(cell.K) if isinstance(cell.K, pikepdf.Array)
                                  else 1) for cell in row.K]
                                for row in node.K if row.get("/S") == "/TR"])
                elif "/K" in node:
                    stack.append(node.K)
    return out


def test_a_sideways_page_is_found_turned(tmp_path: Path):
    source = _sideways(born_digital_pdf_with_table(tmp_path / "table.pdf"), tmp_path / "side.pdf")
    assert next(extract_pages(source)).turn == 270


def test_a_sideways_table_is_read_as_a_table_and_shown_as_it_was(tmp_path: Path, verapdf_exe):
    # Read in the page's own coordinates, a table printed sideways came out with its rows as
    # columns and its cells a scatter of fragments.
    upright = born_digital_pdf_with_table(tmp_path / "table.pdf")
    source = _sideways(upright, tmp_path / "side.pdf")
    out = tmp_path / "out.pdf"
    result = remediate(source, out)

    with pikepdf.open(out) as pdf:
        assert int(pdf.pages[0].obj.Rotate) == 270
    assert np.abs(_picture(out) - _picture(source)).max() == 0
    assert _tables(out) == _tables(remediate(upright, tmp_path / "plain.pdf").pdf_path)
    assert _tables(out) and len(_tables(out)[0]) == 4
    # The editor shows the page the right way up -- here, as the table was set, taller than wide.
    from io import BytesIO

    from PIL import Image
    width, height = Image.open(BytesIO(result.page_images[1])).size
    assert height > width
    assert validate_pdf_ua(out, verapdf_exe=verapdf_exe).compliant


def test_a_page_whose_box_starts_below_the_origin_is_framed_where_it_is(tmp_path: Path):
    # A JSTOR download's media box starts 8 points below the origin, its crop box at it. Measured
    # from one and pictured from the other, every frame in the editor sat 8 points too high.
    body = "".join(f"<p>Paragraph {i} of the running text on this page.</p>" for i in range(8))
    plain = born_digital_pdf(body, tmp_path / "plain.pdf")
    shifted = tmp_path / "shifted.pdf"
    with pikepdf.open(plain) as pdf:
        page = pdf.pages[0]
        width, height = (float(v) for v in page.mediabox[2:])
        page.obj.MediaBox = pikepdf.Array([0, -8, width, height])
        page.obj.CropBox = pikepdf.Array([0, 0, width, height])
        pdf.save(shifted)

    def tops(path: Path, target: Path) -> list[float]:
        return [round(e["top"], 2) for e in remediate(path, target).elements if e["kind"] == "P"]

    out = tmp_path / "out.pdf"
    assert tops(shifted, out) == tops(plain, tmp_path / "plain-out.pdf")
    assert np.abs(_picture(out) - _picture(shifted)).max() == 0


def test_a_page_that_inherits_its_resources_keeps_them(tmp_path: Path, verapdf_exe):
    # Rebind adds its own font to a page's /Resources; a page with none of its own must go on
    # using the ones it inherits from the page tree.
    plain = born_digital_pdf("<p>Words drawn with the page tree's fonts.</p>", tmp_path / "p.pdf")
    inherited = tmp_path / "inherited.pdf"
    with pikepdf.open(plain) as pdf:
        page = pdf.pages[0]
        pdf.Root.Pages.Resources = page.obj.Resources
        del page.obj["/Resources"]
        pdf.save(inherited)
    out = tmp_path / "out.pdf"
    remediate(inherited, out)
    assert np.abs(_picture(out) - _picture(plain)).max() == 0
    assert validate_pdf_ua(out, verapdf_exe=verapdf_exe).compliant
