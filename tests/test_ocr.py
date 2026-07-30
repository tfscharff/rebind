"""Tests for the OCR branch. Uses synthetic image-only scans -- no real sample enters the suite."""

from __future__ import annotations

from pathlib import Path

from rebind.ocr import OcrEngine, ocr_pages, recognize, render_page_to_image
from rebind.extract import extract_pages
from tests.fixtures import pdf_image_only_scan


def test_recognize_recovers_text_from_a_synthetic_scan(tmp_path: Path):
    source = pdf_image_only_scan(
        "<h1>Fearless Organization</h1><p>Preventable failure is avoidable.</p>",
        tmp_path / "scan.pdf",
    )
    image = render_page_to_image(source, 1, dpi=200)
    lines = recognize(image, page_number=1, page_width=612.0, page_height=792.0,
                      engine=OcrEngine())

    text = " ".join(line.text for line in lines).lower()
    assert "fearless" in text
    assert "preventable" in text

    for line in lines:
        assert line.page == 1
        assert line.ocr_confidence is not None and 0.0 <= line.ocr_confidence <= 1.0
        x0, y0, x1, y1 = line.bbox
        # bbox is in PDF points, y-up, inside the page box
        assert 0.0 <= x0 < x1 <= 612.0
        assert 0.0 <= y0 < y1 <= 792.0
        # size is derived from box height so the profile can still rank headings
        assert line.size > 0.0


def _ocr_lines(source, *, restore_images):
    engine = OcrEngine()
    cache = {}
    pages = list(ocr_pages(source, extract_pages(source), engine=engine, cache=cache,
                           restore_images=restore_images))
    return [ln for pg in pages for ln in pg.lines]


def test_deskew_tightens_line_boxes_on_a_crooked_scan(tmp_path):
    # RapidOCR is robust enough to read moderately rotated text, so deskew's measurable, reliable
    # benefit is geometry, not raw word recovery: a tilted line's axis-aligned bounding box is
    # much taller than the text (it spans the tilt), which scrambles the downstream XY-cut reading
    # order and bbox provenance. Deskew collapses those boxes to the true line height.
    import statistics

    body = ("<h1>Deskew Restoration Works</h1>"
            "<p>Preventable failure is largely avoidable with attention.</p>")
    crooked = pdf_image_only_scan(body, tmp_path / "crooked.pdf", dpi=200, rotate_deg=6.0)

    with_restore = _ocr_lines(crooked, restore_images=True)
    without_restore = _ocr_lines(crooked, restore_images=False)

    text = " ".join(ln.text for ln in with_restore).lower()
    assert "preventable" in text and "deskew" in text  # deskew must not lose text

    h_with = statistics.median(ln.bbox[3] - ln.bbox[1] for ln in with_restore)
    h_without = statistics.median(ln.bbox[3] - ln.bbox[1] for ln in without_restore)
    assert h_with < h_without * 0.7, (
        f"deskew did not tighten the tilted line boxes: with={h_with:.1f} without={h_without:.1f}"
    )


# OCR body text is not fabricated into headings: covered end-to-end (over the real structure tree)
# by tests/test_remediate.py::test_ocr_body_only_scan_invents_no_headings.
