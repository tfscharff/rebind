"""Fonts that say the wrong thing about their own glyphs, and what Rebind can say instead.

A PDF's text is whatever its fonts claim each glyph is, and some fonts claim nonsense. A real
chemistry journal set its reaction arrows, plus signs and Greek letters in fonts whose codes are
labelled as ordinary letters: "p1a + e- -> 1 + H" came out of the file as "p1a þ e (cid:2) /1 þ H",
and every "ΔfH" in its tables as "D f H". The labels are in the font program itself (the plus sign
is the glyph named "thorn"), so no reading of the file can fix them -- only the glyphs' shapes are
true.

So each glyph a font draws is rendered and compared with the character it claims to be, drawn in a
reference typeface (DejaVu, bundled). Ordinary text fonts look like what they claim; a font where
most glyphs do not is lying. In a lying font:

* a glyph that still looks like its claim keeps it (that font's "+" and "=" were honest);
* a glyph that looks unmistakably like one other character becomes that character;
* every other glyph becomes U+FFFD, the Unicode character for "unknown" -- never a guess.

Identifying symbols by shape against a typeface they were not drawn in is not reliable enough to
go further: on the real sample an arrow scored as a right arrow only a hair above a two-way one.
A wrong arrow reverses a reaction, so it is not offered. A formula's description is drafted from
its picture by `formula_reader`, which is the better tool for an equation's symbols.
"""

from __future__ import annotations

import functools
import io
import statistics
from pathlib import Path

import numpy as np

from .pagecache import PageCache, file_key

FONT_DIR = Path(__file__).parent / "data" / "fonts"
REFERENCE_FONTS = ("DejaVuSans.ttf", "DejaVuSerif.ttf", "DejaVuSerif-Italic.ttf")
UNKNOWN = "�"

# Glyphs are drawn this big in cells this big (points, rendered at one pixel per point).
EM = 72
CELL = 120
BASELINE = 90           # from the top of a cell
PER_ROW = 10
ROWS_PER_SHEET = 60
SIDE = 24               # compared at this many pixels square

# A glyph looks like its claim at this score or above (ordinary text fonts score 0.5-0.9 on their
# own letters, the real sample's symbol fonts under 0.1 on most of theirs).
CLAIM_HOLDS = 0.5
# A font is lying when the middle of its glyphs' claim scores is under this (ordinary text fonts
# came out at 0.65-0.8 on the real sample, its Greek font at 0.32)...
LYING_MEDIAN = 0.4
# ...or, with a single glyph to go on, when that one scores under this.
LYING_ALONE = 0.15
# A glyph is identified only when its best match scores this, this far clear of the next one.
IDENTIFIED = 0.65
IDENTIFIED_MARGIN = 0.15

# Characters a glyph may be identified as, and so must be told apart from.
CANDIDATES = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    "!\"#%&'()*+,-./:;<=>?@[\\]^_{|}~"
    "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩαβγδεζηθικλμνξοπρστυφχψωϕϑϵ"
    "−×÷±∓→←↔⟶⟵⟷⇌⇄⇒⇐⇔↑↓°′″·•…≈≠≡≤≥≪≫∞∂∇∑∏∫√∝∼≅∈∉⊂⊃∪∩⊥∥∠‡†§¶⦵⊖⊕⊗⊙○●□△★–—‘’“”¢©®"
)
# Characters drawn alike in different typefaces: a claim and a match in one group agree.
LOOK_ALIKE = ("-‐‑‒–—−", "'’‘′`´", "\"“”″", ".·∙•", "x×χ", "oο○", "OΟ0", "lI|1Ι", "pρ", "vν",
              "AΑ", "BΒ", "EΕ", "HΗ", "KΚ", "MΜ", "NΝ", "PΡ", "TΤ", "XΧ", "YΥ", "ZΖ", "~∼")

_found = PageCache()


def _group(ch: str) -> str:
    return next((group for group in LOOK_ALIKE if ch in group), ch)


def _features(ink: np.ndarray):
    """A glyph's shape (its ink, cropped and scaled into a square, keeping its proportions) and
    where it sits: aspect, height and centre against the em. None for a blank glyph."""
    from PIL import Image

    ys, xs = np.nonzero(ink)
    if len(ys) == 0:
        return None
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    crop = ink[y0:y1, x0:x1]
    h, w = crop.shape
    side = max(h, w)
    square = np.zeros((side, side), np.uint8)
    square[(side - h) // 2:(side - h) // 2 + h, (side - w) // 2:(side - w) // 2 + w] = crop * 255
    small = np.asarray(Image.fromarray(square).resize((SIDE, SIDE), Image.BILINEAR), float) / 255
    # Spread a little, so a stroke a pixel off in another typeface still meets it.
    spread = small.copy()
    for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0)):
        spread += 0.5 * np.roll(np.roll(small, dy, 0), dx, 1)
    vector = spread.ravel()
    vector /= np.linalg.norm(vector) or 1.0
    return vector, w / h, h / EM, (y0 + y1) / 2 / EM


def _similarity(a, b) -> float:
    va, aspect_a, height_a, centre_a = a
    vb, aspect_b, height_b, centre_b = b
    return float(va @ vb) * float(
        np.exp(-abs(np.log(aspect_a / aspect_b)))
        * np.exp(-2 * abs(np.log(max(height_a, 0.01) / max(height_b, 0.01))))
        * np.exp(-3 * abs(centre_a - centre_b)))


@functools.lru_cache(maxsize=4096)
def _reference(ch: str) -> tuple:
    """`ch` drawn in each reference typeface that has it."""
    from PIL import Image, ImageDraw, ImageFont

    out = []
    for name in REFERENCE_FONTS:
        font = ImageFont.truetype(str(FONT_DIR / name), EM)
        if not ch.strip() or font.getmask(ch).getbbox() is None:
            continue
        # A typeface without the character draws its "missing" box, which is no evidence at all.
        if font.getmask(ch).getbbox() == font.getmask("￿").getbbox() and ch != "￿":
            continue
        image = Image.new("L", (CELL, CELL), 0)
        ImageDraw.Draw(image).text((20, BASELINE), ch, fill=255, font=font, anchor="ls")
        features = _features(np.asarray(image) > 128)
        if features is not None:
            out.append(features)
    return tuple(out)


def _score(glyph, ch: str) -> float | None:
    """How much `glyph` looks like `ch`, or None when no reference typeface has `ch`."""
    references = _reference(ch)
    return max(_similarity(glyph, ref) for ref in references) if references else None


def _used_codes(pdf) -> dict[tuple, tuple]:
    """Every simple embedded font the document draws with, by object, and the codes it draws."""
    import pikepdf

    used: dict[tuple, tuple] = {}

    def walk(stream_owner, resources, seen: set) -> None:
        fonts = (resources or pikepdf.Dictionary()).get("/Font") or pikepdf.Dictionary()
        xobjects = (resources or pikepdf.Dictionary()).get("/XObject") or pikepdf.Dictionary()
        current = None
        try:
            instructions = pikepdf.parse_content_stream(stream_owner)
        except Exception:  # noqa: BLE001 -- an unreadable stream says nothing about its fonts
            return
        for ins in instructions:
            op = str(ins.operator)
            if op == "Tf" and ins.operands:
                current = fonts.get(str(ins.operands[0]))
            elif op in ("Tj", "'", '"', "TJ") and current is not None and _checkable(current):
                strings = ([s for s in ins.operands[0] if isinstance(s, pikepdf.String)]
                           if op == "TJ" else [ins.operands[-1]])
                entry = used.setdefault(current.objgen, (current, set()))
                for string in strings:
                    entry[1].update(bytes(string))
            elif op == "Do" and ins.operands:
                xobject = xobjects.get(str(ins.operands[0]))
                if (xobject is not None and xobject.get("/Subtype") == pikepdf.Name.Form
                        and xobject.objgen not in seen):
                    walk(xobject, xobject.get("/Resources") or resources, seen | {xobject.objgen})

    for page in pdf.pages:
        walk(page, page.obj.get("/Resources"), set())
    return used


def _checkable(font) -> bool:
    """A one-byte font with its own glyphs in the file: the only kind whose claims can be wrong
    in a way this can see. A font left out of the file is drawn from a stand-in that does have
    the characters claimed."""
    import pikepdf

    if font.get("/Subtype") not in (pikepdf.Name.Type1, pikepdf.Name.TrueType,
                                    pikepdf.Name.MMType1):
        return False
    descriptor = font.get("/FontDescriptor")
    return descriptor is not None and any(
        key in descriptor for key in ("/FontFile", "/FontFile2", "/FontFile3"))


def _render(pdf, used: dict) -> dict[tuple, object]:
    """Every (font, code) drawn in a cell of its own, and each cell's shape features."""
    import pikepdf
    import pypdfium2 as pdfium

    cells = [(font, code) for font, codes in used.values() for code in sorted(codes)]
    features: dict[tuple, object] = {}
    per_sheet = PER_ROW * ROWS_PER_SHEET
    for start in range(0, len(cells), per_sheet):
        sheet = cells[start:start + per_sheet]
        rows = (len(sheet) + PER_ROW - 1) // PER_ROW
        out = pikepdf.new()
        page = out.add_blank_page(page_size=(CELL * PER_ROW, CELL * rows))
        names: dict[tuple, str] = {}
        fonts = pikepdf.Dictionary()
        content = []
        for k, (font, code) in enumerate(sheet):
            if font.objgen not in names:
                names[font.objgen] = f"/F{len(names)}"
                fonts[names[font.objgen]] = out.copy_foreign(font)
            x = (k % PER_ROW) * CELL + 20
            y = CELL * rows - (k // PER_ROW) * CELL - BASELINE
            content.append(f"BT {names[font.objgen]} {EM} Tf {x} {y} Td <{code:02X}> Tj ET")
        page.obj.Resources = pikepdf.Dictionary(Font=fonts)
        page.obj.Contents = out.make_stream("\n".join(content).encode("ascii"))
        buffer = io.BytesIO()
        out.save(buffer)
        document = pdfium.PdfDocument(buffer.getvalue())
        try:
            image = np.asarray(document[0].render(scale=1).to_pil().convert("L")) < 128
        finally:
            document.close()
        for k, (font, code) in enumerate(sheet):
            x, y = (k % PER_ROW) * CELL, (k // PER_ROW) * CELL
            features[(font.objgen, code)] = _features(image[y:y + CELL, x:x + CELL])
    return features


def _claims(source: Path) -> dict[tuple[str, int], str]:
    """What pdfminer -- and so the text Rebind reads -- says each (font name, code) is."""
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdffont import PDFUnicodeNotDefined
    from pdfminer.pdfinterp import PDFResourceManager
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdfparser import PDFParser
    from pdfminer.pdftypes import resolve1

    claims: dict[tuple[str, int], str] = {}
    manager = PDFResourceManager()
    with open(source, "rb") as handle:
        document = PDFDocument(PDFParser(handle))

        def visit(resources, seen: set) -> None:
            resources = resolve1(resources) or {}
            for spec in (resolve1(resources.get("Font")) or {}).values():
                try:
                    font = manager.get_font(None, resolve1(spec))
                except Exception:  # noqa: BLE001 -- pdfminer could not load it either
                    continue
                for code in range(256):
                    key = (font.fontname, code)
                    if key in claims:
                        continue
                    try:
                        claims[key] = font.to_unichr(code)
                    except (PDFUnicodeNotDefined, KeyError, IndexError):
                        claims[key] = f"(cid:{code})"
                    except Exception:  # noqa: BLE001
                        continue
            for xobject in (resolve1(resources.get("XObject")) or {}).values():
                stream = resolve1(xobject)
                attrs = getattr(stream, "attrs", {})
                if resolve1(attrs.get("Subtype")) is not None and str(
                        resolve1(attrs.get("Subtype"))).endswith("Form") and id(stream) not in seen:
                    visit(attrs.get("Resources"), seen | {id(stream)})

        for page in PDFPage.create_pages(document):
            visit(page.resources, set())
    return claims


def _analyse(source: Path) -> dict[str, dict[int, tuple[str, str]]]:
    """For each font that needs it, by name: every code it draws, as (what it claims, what to read).
    A font that is honest about every glyph it draws is not listed."""
    import pikepdf

    stamp = file_key(source)
    key = stamp and stamp + ("glyphs",)
    known = _found.get(key)
    if known is not None:
        return known
    out: dict[str, dict[int, tuple[str, str]]] = {}
    with pikepdf.open(source) as pdf:
        used = _used_codes(pdf)
        if used:
            features = _render(pdf, used)
            claims = _claims(source)
            for font, codes in used.values():
                name = str(font.get("/BaseFont", "")).lstrip("/")
                fixed = _font_corrections(name, codes, features, font.objgen, claims)
                if fixed:
                    by_claim = {claim: read for (_name, claim), read in fixed.items()}
                    out[name] = {code: (claims[(name, code)],
                                        by_claim.get(claims[(name, code)], claims[(name, code)]))
                                 for code in codes if claims.get((name, code)) is not None}
    return _found.put(key, out)


def same_fonts(original: Path, copy: Path) -> None:
    """Say that `copy` is `original` with the same fonts (a working copy with pages turned, or with
    colours corrected), so its fonts are not rendered and compared all over again."""
    stamp = file_key(copy)
    if stamp is not None:
        _found.put(stamp + ("glyphs",), _analyse(original))


def corrections(source: Path) -> dict[tuple[str, str], str]:
    """What to read instead, by (font name as pdfminer gives it, the text it claims): a character
    identified from its shape, or U+FFFD. Only for glyphs in fonts that misreport their own."""
    return {(name, claim): read for name, codes in _analyse(source).items()
            for claim, read in codes.values() if read != claim}


def repair_fonts(pdf, source: Path) -> int:
    """Give each misreporting font in `pdf` (opened from `source`) a ToUnicode map that says what
    its glyphs are, as far as `corrections` could tell -- so the page itself, not only Rebind's
    text layer, reads true when copied or searched, and a glyph with no character at all maps to
    U+FFFD rather than to nothing (which PDF/UA-2 clause 8.4.5.8 fails, even in an artifact).

    Only the map is written; the font's glyphs and the page's drawing are untouched. Returns how
    many fonts were repaired."""

    found = _analyse(source)
    if not found:
        return 0
    repaired = 0
    for font, _codes in _used_codes(pdf).values():
        codes = found.get(str(font.get("/BaseFont", "")).lstrip("/"))
        if not codes:
            continue
        lines = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
                 "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
                 "/CMapName /Rebind-Repaired def", "/CMapType 2 def",
                 "1 begincodespacerange", "<00> <FF>", "endcodespacerange"]
        entries = sorted(codes.items())
        for start in range(0, len(entries), 100):
            chunk = entries[start:start + 100]
            lines.append(f"{len(chunk)} beginbfchar")
            for code, (_claim, read) in chunk:
                read = UNKNOWN if read.startswith("(cid:") else read
                lines.append(f"<{code:02X}> <{read.encode('utf-16-be').hex().upper()}>")
            lines.append("endbfchar")
        lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
        font.ToUnicode = pdf.make_stream("\n".join(lines).encode("ascii"))
        repaired += 1
    return repaired


def _font_corrections(name: str, codes, features, objgen, claims) -> dict[tuple[str, str], str]:
    glyphs = {code: features.get((objgen, code)) for code in codes}
    glyphs = {code: glyph for code, glyph in glyphs.items() if glyph is not None}
    claimed = {code: claims.get((name, code)) for code in glyphs}
    evidence: dict[int, float] = {}
    for code, glyph in glyphs.items():
        claim = claimed[code]
        if claim is None:
            continue
        if claim.startswith("(cid:"):
            evidence[code] = 0.0
        elif len(claim) == 1:
            score = _score(glyph, claim)
            if score is not None:
                evidence[code] = score
    if not evidence:
        return {}
    scores = list(evidence.values())
    lying = (statistics.median(scores) < LYING_MEDIAN if len(scores) > 1
             else scores[0] < LYING_ALONE)
    unmapped = {code for code, claim in claimed.items() if claim and claim.startswith("(cid:")}
    out: dict[tuple[str, str], str] = {}
    for code, glyph in glyphs.items():
        claim = claimed[code]
        if claim is None or (not lying and code not in unmapped):
            continue
        if code not in unmapped and evidence.get(code, 0.0) >= CLAIM_HOLDS:
            continue
        ranked = sorted(((score, ch) for ch in CANDIDATES
                         if (score := _score(glyph, ch)) is not None), reverse=True)
        best_score, best = ranked[0] if ranked else (0.0, UNKNOWN)
        if claim and len(claim) == 1 and _group(claim) == _group(best):
            continue                # the shape agrees with the claim after all
        # Clear of every other character, look-alikes included: an apostrophe and a prime are drawn
        # alike and mean different things, so a match between them is no match.
        rival = ranked[1][0] if len(ranked) > 1 else 0.0
        sure = best_score >= IDENTIFIED and best_score - rival >= IDENTIFIED_MARGIN
        out[(name, claim)] = best if sure else UNKNOWN
    # A glyph with no character at all is read as unknown even in a font honest about the rest.
    for code in unmapped:
        out.setdefault((name, claimed[code]), UNKNOWN)
    return out
