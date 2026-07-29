"""Tests for the OCR branch. Uses synthetic image-only scans -- no real sample enters the suite."""

from __future__ import annotations

from pathlib import Path

from rebind.ocr import OcrEngine, recognize, render_page_to_image
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
