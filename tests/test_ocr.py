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


def test_a_scanned_line_crowded_with_scripts_is_read_not_dropped(tmp_path: Path):
    # The recognizer's text finder ran "Sulfate, SO4 2-, binds Ca2+ ..." into the lines around it
    # through their superscripts and subscripts, and dropped it: a whole line of the page unread.
    from tests.fixtures import CHEMISTRY_HTML

    source = pdf_image_only_scan(CHEMISTRY_HTML, tmp_path / "chem.pdf", dpi=300)
    image = render_page_to_image(source, 1, dpi=300)
    lines = recognize(image, page_number=1, page_width=612.0, page_height=792.0,
                      engine=OcrEngine())
    text = " ".join(line.text for line in lines)
    assert "Sulfate" in text and "binds" in text and "confirmed" in text
    water = next(line for line in lines if line.text.startswith("Water"))
    marked = [(water.text[s.start:s.end], s.raised) for s in water.scripts]
    assert marked[0] == ("2", False) and ("+", True) in marked
    rate = next(line for line in lines if line.text.startswith("The rate"))
    assert ("3", True) in [(rate.text[s.start:s.end], s.raised) for s in rate.scripts]


def test_pieces_of_a_line_are_joined_with_their_scripts():
    from rebind.ocr import _join_rows, _Read

    def piece(text, left, top, right, bottom):
        box = (left, top, right, bottom)
        return _Read(box, text, 0.9, tight=box)

    body = piece("binds Ca", 100, 100, 260, 130)
    charge = piece("2+", 262, 92, 285, 110)
    after = piece("strongly", 295, 100, 420, 130)
    elsewhere = piece("Another line", 100, 200, 300, 230)
    joined = _join_rows([body, charge, after, elsewhere])
    assert [read.text for read in joined] == ["binds Ca2+ strongly", "Another line"]
    start, end, shift = joined[0].scripts[0]
    assert joined[0].text[start:end] == "2+" and shift > 0


def test_pieces_of_a_row_with_nothing_small_are_left_apart():
    # Two cells of a table, or two columns, side by side: nothing joins them.
    from rebind.ocr import _join_rows, _Read

    left = _Read((100, 100, 200, 130), "Cell one", 0.9, tight=(100, 100, 200, 130))
    right = _Read((215, 100, 320, 130), "Cell two", 0.9, tight=(215, 100, 320, 130))
    assert [read.text for read in _join_rows([left, right])] == ["Cell one", "Cell two"]
