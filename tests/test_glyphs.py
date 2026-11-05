"""Fonts that misreport their glyphs: found by shape, and never guessed at."""

from __future__ import annotations

import io
from pathlib import Path

import pikepdf
from fontTools.ttLib import TTFont
from pikepdf import Array, Dictionary, Name

from rebind.extract import extract_pages
from rebind.glyphs import FONT_DIR, UNKNOWN, corrections

# The real journal's trick, reproduced: codes labelled as ordinary characters that draw symbols.
LIES = {0xFE: "plus", 0xBC: "equal", ord("z"): "daggerdbl", ord("q"): "Omega",
        ord("/"): "arrowright", ord("D"): "uni0394"}


def _font_program(lies: dict[int, str]) -> tuple[bytes, list[int]]:
    """DejaVu Sans with the lies written into its character map, and its widths by WinAnsi code
    32-255 as the font dictionary must state them."""
    font = TTFont(str(FONT_DIR / "DejaVuSans.ttf"))
    for table in font["cmap"].tables:
        if table.isUnicode():
            for code, glyph in lies.items():
                table.cmap[code] = glyph
    cmap, scale = font.getBestCmap(), 1000 / font["head"].unitsPerEm
    widths = []
    for code in range(32, 256):
        try:
            ch = bytes([code]).decode("cp1252")
        except UnicodeDecodeError:
            widths.append(0)
            continue
        glyph = cmap.get(ord(ch))
        widths.append(round(font["hmtx"][glyph][0] * scale) if glyph else 0)
    out = io.BytesIO()
    font.save(out)
    return out.getvalue(), widths


def _pdf(path: Path, lies: dict[int, str], text: bytes) -> Path:
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(400, 200))
    data, widths = _font_program(lies)
    program = pdf.make_stream(data)
    descriptor = pdf.make_indirect(Dictionary(
        Type=Name.FontDescriptor, FontName=Name("/ABCDEF+Liar"), Flags=32,
        FontBBox=Array([-1000, -400, 2000, 1200]), ItalicAngle=0, Ascent=900, Descent=-250,
        CapHeight=700, StemV=80, FontFile2=program))
    font = pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.TrueType, BaseFont=Name("/ABCDEF+Liar"), FirstChar=32,
        LastChar=255, Widths=Array(widths), Encoding=Name.WinAnsiEncoding,
        FontDescriptor=descriptor))
    page.obj.Resources = Dictionary(Font=Dictionary(F1=font))
    page.obj.Contents = pdf.make_stream(b"BT /F1 20 Tf 40 100 Td (" + text + b") Tj ET")
    pdf.save(path)
    return path


def test_a_font_drawing_symbols_under_letters_is_caught(tmp_path):
    source = _pdf(tmp_path / "liar.pdf", LIES, b"\xfe \xbc z q / D")
    found = corrections(source)
    # The plus sign is unmistakable; whatever else is not identified is marked unknown, never
    # left as the letter the font claims.
    assert found[("ABCDEF+Liar", "þ")] == "+"
    for claim in ("¼", "z", "q", "/", "D"):
        assert found[("ABCDEF+Liar", claim)] in (UNKNOWN, {"¼": "=", "z": "‡", "q": "Ω",
                                                           "/": "→", "D": "Δ"}[claim])
    text = " ".join(ln.text for page in extract_pages(source) for ln in page.lines)
    assert "+" in text and "þ" not in text and "z" not in text


def test_unknown_symbols_are_reported_with_their_page(tmp_path):
    from rebind.checklist import NEEDS_YOU, PASS, build_checklist
    from rebind.remediate import remediate

    liar = remediate(_pdf(tmp_path / "liar.pdf", LIES, b"\xfe \xbc z q / D"),
                     tmp_path / "liar_out.pdf")
    checks = {c["title"]: c for c in build_checklist(liar.pdf_path, page_count=liar.page_count)}
    unknown = checks["Unreadable symbols"]
    assert unknown["status"] == NEEDS_YOU and unknown["locations"] == [{"page": 1}]

    honest = remediate(_pdf(tmp_path / "honest.pdf", {}, b"Honest words."),
                       tmp_path / "honest_out.pdf")
    checks = {c["title"]: c for c in build_checklist(honest.pdf_path,
                                                     page_count=honest.page_count)}
    assert checks["Unreadable symbols"]["status"] == PASS


def test_a_misreporting_font_is_repaired_and_its_page_kept(tmp_path, verapdf_exe):
    import pypdfium2 as pdfium

    from rebind.remediate import remediate
    from rebind.validate import validate_pdf_ua

    source = _pdf(tmp_path / "liar.pdf", LIES, b"\xfe \xbc z q / D")
    out = tmp_path / "out.pdf"
    remediate(source, out)
    with pikepdf.open(out) as pdf:
        operators = {str(i.operator) for i in pikepdf.parse_content_stream(pdf.pages[0])}
        assert "Tf" in operators and "/Im0" not in str(pdf.pages[0].obj.Resources.get("/XObject"))
    page = pdfium.PdfDocument(str(out))[0]
    assert "+" in page.get_textpage().get_text_range()
    report = validate_pdf_ua(out, verapdf_exe=verapdf_exe)
    assert report.compliant, report.summary()


def test_an_honest_font_is_left_alone(tmp_path):
    source = _pdf(tmp_path / "honest.pdf", {}, b"Honest words: plus + equals = and q z D.")
    assert corrections(source) == {}
    text = " ".join(ln.text for page in extract_pages(source) for ln in page.lines)
    assert "Honest words: plus + equals = and q z D." in text


def test_one_odd_glyph_does_not_make_a_font_a_liar(tmp_path):
    # A single symbol among honest letters: the font tells the truth about the rest, so nothing
    # about it is second-guessed.
    source = _pdf(tmp_path / "mostly.pdf", {0xFE: "plus"}, b"Mostly honest words \xfe here.")
    assert corrections(source) == {}
