from pathlib import Path

import pikepdf

from rebind.render import render_html_to_pdf_with_anchors


def test_anchors_map_to_the_output_pages_they_land_on(tmp_path: Path):
    target = tmp_path / "anchored.pdf"
    body = (
        '<span id="rebind-page-1"></span><h1>One</h1>'
        + "<p>filler</p>" * 200
        + '<span id="rebind-page-2"></span><h2>Two</h2><p>after</p>'
    )

    anchors = render_html_to_pdf_with_anchors(body, target, title="T")

    assert anchors["rebind-page-1"] == 1
    assert anchors["rebind-page-2"] > 1
    with pikepdf.open(target) as pdf:
        assert anchors["rebind-page-2"] <= len(pdf.pages)
