"""Tests for in-place remediation: preserve the original, add accessibility."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pypdfium2 as pdfium

from rebind.remediate import _encode_winansi, remediate
from tests.fixtures import born_digital_pdf, pdf_image_only_scan


def _selectable_text(pdf_path: Path) -> str:
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        return " ".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    finally:
        doc.close()


def _all_struct_tags(pdf: pikepdf.Pdf) -> list[str]:
    """Every structure-element type in the tree, in a depth-first walk (e.g. '/H1', '/Table')."""
    out: list[str] = []

    def walk(elem: pikepdf.Object) -> None:
        s = elem.get("/S")
        if s is not None:
            out.append(str(s))
        kids = elem.get("/K")
        if isinstance(kids, pikepdf.Array):
            for kid in kids:
                if isinstance(kid, pikepdf.Dictionary) and kid.get("/Type") == pikepdf.Name.StructElem:
                    walk(kid)
        elif isinstance(kids, pikepdf.Dictionary) and kids.get("/Type") == pikepdf.Name.StructElem:
            walk(kids)

    for kid in pdf.Root.StructTreeRoot.K:
        walk(kid)
    return out


def test_born_digital_is_copied_verbatim_with_metadata(tmp_path: Path):
    # A PDF that already has text is left byte-for-byte as its pages were; only accessibility
    # metadata is added, and no page is re-OCR'd.
    source = born_digital_pdf("<h1>Chapter One</h1><p>The body text is here.</p>",
                              tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"

    result = remediate(source, out, title="My Title", lang="en")

    assert result.ocr_pages == ()          # nothing needed recognizing
    assert result.added_text_layer is False
    with pikepdf.open(out) as pdf:
        assert bool(pdf.Root.MarkInfo.Marked)
        assert str(pdf.Root.Lang) == "en"
        assert str(pdf.docinfo["/Title"]) == "My Title"
    # The original text survives and is still selectable.
    assert "body text" in _selectable_text(out)


def test_malformed_source_xmp_does_not_hide_our_metadata(tmp_path: Path, verapdf_exe: Path):
    # Real-world publisher PDFs (Elsevier's production pipeline, at least) embed a stray,
    # non-namespaced XMP element -- a DRM/fingerprinting artifact -- that is well-formed XML but
    # breaks veraPDF's strict metadata parser: it silently stops seeing OUR dc:title/pdfuaid
    # entries too, even though pikepdf's own reader still finds them (real sample: 1429254.pdf).
    # A namespace-less top-level XMP key is never a legitimate accessibility property, so
    # _set_metadata must strip any that survive from the source before adding its own.
    from rebind.validate import validate_pdf_ua

    clean = born_digital_pdf("<h1>Title</h1><p>Body text.</p>", tmp_path / "clean.pdf")
    source = tmp_path / "in.pdf"
    with pikepdf.open(clean) as pdf:
        with pdf.open_metadata() as meta:
            meta["SomeRandomFingerprintTag"] = "junk"   # no namespace prefix -- the real shape
        pdf.save(source)

    out = tmp_path / "out.pdf"
    remediate(source, out, title="A Title")

    with pikepdf.open(out) as pdf:
        with pdf.open_metadata() as meta:
            assert meta["dc:title"] == "A Title"
            assert meta["pdfuaid:part"] == "2"

    result = validate_pdf_ua(out, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()


def test_internal_link_destinations_are_stripped_not_left_broken(tmp_path: Path, verapdf_exe: Path):
    # A born-digital source can carry Link annotations navigating within the document (a table of
    # contents, cross-references) -- confirmed on a real publisher sample (137 instances in one
    # document). PDF/UA-2 clause 8.8 requires such destinations to be structure destinations, which
    # Rebind does not yet build, so the annotation is dropped rather than left pointing at a legacy
    # page+coordinate target that fails compliance. An external link (a URI action) is untouched.
    from pikepdf import Array, Dictionary, Name, String

    from rebind.validate import validate_pdf_ua

    clean = born_digital_pdf("<h1>Title</h1><p>Body text.</p><p>More.</p>", tmp_path / "clean.pdf")
    source = tmp_path / "in.pdf"
    with pikepdf.open(clean) as pdf:
        page = pdf.pages[0]
        internal_link = pdf.make_indirect(Dictionary(
            Type=Name.Annot, Subtype=Name.Link, Rect=Array([0, 0, 10, 10]),
            Dest=Array([page.obj, Name.XYZ, 0, 792, 0]),
        ))
        external_link = pdf.make_indirect(Dictionary(
            Type=Name.Annot, Subtype=Name.Link, Rect=Array([0, 20, 10, 30]),
            A=Dictionary(S=Name.URI, URI=String("https://example.org")),
        ))
        page.obj.Annots = Array([internal_link, external_link])
        pdf.save(source)

    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    with pikepdf.open(out) as pdf:
        annots = pdf.pages[0].obj.get("/Annots") or []
        kinds = [(a.get("/Dest") is not None, a.get("/A", {}).get("/S")) for a in annots]
        assert (True, None) not in kinds, "internal-destination link should have been removed"
        assert any(k[1] == Name.URI for k in kinds), "external link should survive untouched"

    result = validate_pdf_ua(out, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()


def test_surviving_link_annotation_is_tagged_into_the_structure_tree(tmp_path: Path,
                                                                      verapdf_exe: Path):
    # An external link (kept, unlike an internal-destination one -- see the test above) must have
    # a structure-tree presence: a /StructParent on the annotation, and a /Link structure element
    # whose object reference (OBJR) points back to it. Adobe's checker calls this "Tagged
    # annotations"; a bare, untagged annotation fails it even though it isn't otherwise wrong.
    from pikepdf import Array, Dictionary, Name, String

    from rebind.validate import validate_pdf_ua

    clean = born_digital_pdf("<h1>Title</h1><p>Body text.</p>", tmp_path / "clean.pdf")
    source = tmp_path / "in.pdf"
    with pikepdf.open(clean) as pdf:
        page = pdf.pages[0]
        external_link = pdf.make_indirect(Dictionary(
            Type=Name.Annot, Subtype=Name.Link, Rect=Array([0, 20, 10, 30]),
            A=Dictionary(S=Name.URI, URI=String("https://example.org")),
        ))
        page.obj.Annots = Array([external_link])
        pdf.save(source)

    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    with pikepdf.open(out) as pdf:
        annot = (pdf.pages[0].obj.get("/Annots") or [])[0]
        assert "/StructParent" in annot, "annotation has no structure-tree back-reference"

        def find_link(elem):
            if str(elem.get("/S")) == "/Link":
                return elem
            kids = elem.get("/K")
            if not isinstance(kids, pikepdf.Array):
                return None
            for kid in kids:
                if isinstance(kid, pikepdf.Dictionary) and kid.get("/Type") == pikepdf.Name.StructElem:
                    found = find_link(kid)
                    if found is not None:
                        return found
            return None

        link_elem = find_link(pdf.Root.StructTreeRoot.K[0])
        assert link_elem is not None, "no /Link structure element was found"
        objr = link_elem.K[0]
        assert objr.Obj.objgen == annot.objgen
        # Adobe's "Other elements alternate text" check wants a Link's structure element to carry
        # a description -- an honest, mechanical fact ("Link to <uri>"), never a guess at intent.
        assert "https://example.org" in str(link_elem.Alt)
        # Also set directly on the annotation's own /Contents -- a real sample kept failing
        # Adobe's "Tagged annotations" check with /Alt on the struct element alone.
        assert "https://example.org" in str(annot.Contents)

    result = validate_pdf_ua(out, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()


def test_non_ascii_text_is_tagged_without_corrupting_the_font_encoding(tmp_path: Path,
                                                                        verapdf_exe: Path):
    # The invisible overlay's font declares WinAnsiEncoding; text drawn into it must be encoded as
    # cp1252, not Python's UTF-8 default -- UTF-8 bytes reinterpreted as WinAnsi codepoints land on
    # undefined byte values, which is an invalid Unicode mapping (PDF/UA-2 8.4.5.8/.9). Real
    # academic/scanned text routinely carries curly quotes, em dashes and accents; this is not an
    # edge case (confirmed by a real sample that failed exactly this way, 52 instances).
    from rebind.validate import validate_pdf_ua

    source = born_digital_pdf(
        "<h1>Ti’tle</h1><p>Café — naïve “quoted” text.</p>",
        tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    text = _selectable_text(out)
    assert "Caf" in text   # the recoverable prefix survives; a lone accented char may not

    result = validate_pdf_ua(out, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()


def test_control_characters_become_spaces_not_invalid_glyphs():
    # A real born-digital source can carry a literal tab in its extracted text, used for visual
    # alignment (a real sample: "II.\tImaging Vascular Gene Expression"-style headings, 52
    # instances). WinAnsiEncoding's own glyph table assigns no name to any C0 control character
    # (PDF spec Annex D), so drawing one as a literal glyph always encodes to Unicode 0 regardless
    # of the encoding used -- fails PDF/UA-2 8.4.5.8/.9. A tab is whitespace; encode it as one.
    assert _encode_winansi("II.\tImaging") == b"II. Imaging"
    assert _encode_winansi("a\nb\rc") == b"a b c"


def test_scanned_page_gets_an_invisible_text_layer(tmp_path: Path):
    # An image-only scan has no text; remediation OCRs it and adds a selectable text layer over
    # the untouched image.
    source = pdf_image_only_scan(
        "<h1>Fearless Organization</h1><p>Preventable failure is avoidable.</p>",
        tmp_path / "scan.pdf",
    )
    out = tmp_path / "out.pdf"

    result = remediate(source, out, title="Scan")

    assert result.ocr_pages == (1,) and result.added_text_layer is True
    text = _selectable_text(out).lower()
    assert "preventable" in text
    with pikepdf.open(out) as pdf:
        assert bool(pdf.Root.MarkInfo.Marked)


def test_output_page_count_matches_source(tmp_path: Path):
    source = born_digital_pdf("<p>one</p><p style='page-break-before:always'>two</p>",
                              tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    result = remediate(source, out)
    with pikepdf.open(source) as a, pikepdf.open(out) as b:
        assert len(b.pages) == len(a.pages) == result.page_count


def test_remediated_output_is_tagged_and_pdf_ua_compliant(tmp_path: Path, verapdf_exe: Path):
    """The whole point: the output is a real PDF/UA document, not just a PDF with text on it."""
    from rebind.validate import validate_pdf_ua

    source = born_digital_pdf("<h1>Title</h1><p>A paragraph of body text.</p>", tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="A Title")

    with pikepdf.open(out) as pdf:
        assert "/StructTreeRoot" in pdf.Root
        assert bool(pdf.Root.MarkInfo.Marked)

    result = validate_pdf_ua(out, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()


def test_born_digital_headings_are_tagged_as_headings(tmp_path: Path):
    source = born_digital_pdf(
        "<h1>Chapter One</h1><p>Body paragraph here.</p><h2>A Section</h2><p>More body.</p>",
        tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    with pikepdf.open(out) as pdf:
        tags = [str(elem.S) for elem in pdf.Root.StructTreeRoot.K[0].K]
    assert "/H1" in tags and "/H2" in tags and "/P" in tags
    # A heading must not skip a level: the first heading is H1.
    headings = [t for t in tags if t.startswith("/H")]
    assert headings[0] == "/H1"


def test_a_burst_of_heading_styled_lines_is_not_mistaken_for_headings(tmp_path: Path):
    # A run of several short, distinctly-styled lines in immediate succession -- an author byline
    # broken into fragments around superscript affiliation markers, or a diagram's callout labels
    # ("Ventral", "Baffles", "Air pump", ...) -- each individually clears the length filter above,
    # but a genuine document heading is essentially never adjacent to *another* heading-styled line
    # except a real title+subtitle pair (exactly 2 in a row). Confirmed on the real 28-page sample:
    # a 4-fragment byline and a 20+-label diagram burst both survived the length filter alone.
    source = born_digital_pdf(
        "<h1>Chapter One</h1>"
        "<p style='font-weight:bold; font-size:18pt'>Jane A. Doe*,</p>"
        "<p style='font-weight:bold; font-size:18pt'>, John B. Smith</p>"
        "<p style='font-weight:bold; font-size:18pt'>, Mary C. Lee</p>"
        "<p style='font-weight:bold; font-size:18pt'>and Tom D. Kim</p>"
        "<p>Body paragraph describing the actual chapter content follows here.</p>"
        "<h2>Introduction</h2>"
        "<p>More body text continues the chapter's introduction section.</p>"
        "<h1>Chapter Two</h1>"
        "<h2>A Genuine Subtitle</h2>"
        "<p>Body text for chapter two follows immediately after its subtitle.</p>",
        tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    with pikepdf.open(out) as pdf:
        tags = _all_struct_tags(pdf)
    headings = [t for t in tags if t.startswith("/H")]
    # The 4-fragment byline burst is excluded; the two real chapters (each with a genuine,
    # isolated-or-paired subtitle) remain, in order.
    assert headings == ["/H1", "/H2", "/H1", "/H2"], headings

    text = _selectable_text(out)
    assert "Jane A. Doe" in text, "byline text itself must still survive, just as a paragraph"


def test_bare_short_labels_are_not_mistaken_for_headings(tmp_path: Path):
    # Figure-panel callout labels ("A", "B", "C" ...) are routinely bold, which alone qualifies a
    # style as a heading candidate in profile.py (larger-or-bolder than body, no content check at
    # all) -- confirmed on a real 28-page sample, whose recovered outline surfaced single-letter
    # "headings" from exactly this. A real document heading is never a bare 1-2 character label;
    # requiring a minimum amount of content costs nothing on genuine short headings ("Abstract",
    # a real heading in that same sample, is 8 characters and must still be promoted).
    source = born_digital_pdf(
        "<h1>Chapter One</h1>"
        "<p>Body paragraph here, with an inline figure panel labeled below it.</p>"
        "<p style='font-weight:bold; font-size:18pt'>A</p>"
        "<p>More body text describing the panel goes here.</p>"
        "<h2>Abstract</h2><p>Even more body text follows this genuine short heading.</p>",
        tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    with pikepdf.open(out) as pdf:
        tags = _all_struct_tags(pdf)
    headings = [t for t in tags if t.startswith("/H")]
    assert headings == ["/H1", "/H2"], headings   # exactly the two real headings, bare "A" excluded

    text = _selectable_text(out)
    assert "Chapter One" in text and "Abstract" in text and "panel labeled" in text


def test_heading_levels_never_skip_locally_even_if_the_skipped_level_exists_elsewhere(
        tmp_path: Path, verapdf_exe: Path):
    # veraPDF (PDF/UA-2's own reference validator) is satisfied as long as the GLOBAL SET of
    # heading levels used has no gaps -- but Adobe's stricter "Appropriate nesting" check requires
    # the SEQUENCE itself to never skip locally, even when the skipped level exists somewhere else
    # in the document. Confirmed on a real 28-page sample that validated PDF/UA-2 compliant (0
    # veraPDF failures) yet still failed Adobe's nesting check: raw sequence was
    # ...H2, H3, H2, H2, H2, [H4]... -- a new, smaller style first encountered right after an H2
    # (with no H3 immediately before it) globally ranked as the 4th-largest size in the whole
    # document and so became H4, even though H3 existed earlier in the same document.
    from rebind.validate import validate_pdf_ua

    source = born_digital_pdf(
        "<h1>Chapter</h1>"
        "<p style='font-weight:bold; font-size:14pt'>Subsection A</p>"
        "<p>Body text under subsection A.</p>"
        "<h2>Section Two</h2>"
        "<p style='font-weight:bold; font-size:10pt'>A smaller heading style, first seen here.</p>"
        "<p>Body text under that smaller heading.</p>",
        tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    with pikepdf.open(out) as pdf:
        tags = _all_struct_tags(pdf)
    levels = [int(t[2:]) for t in tags if t.startswith("/H")]
    for i in range(1, len(levels)):
        assert levels[i] <= levels[i - 1] + 1, f"heading skip at index {i}: {levels}"

    result = validate_pdf_ua(out, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()


def test_first_heading_in_reading_order_is_always_h1(tmp_path: Path):
    # Global font-size ranking alone can put a LATER, larger heading at H1 while the document's
    # actual first heading (smaller font, but still a real heading, e.g. a modest "I. Introduction")
    # gets bumped to H2 -- exactly what a real 28-page academic-paper sample produced. PDF/UA and
    # Adobe's own checker require the document's first heading to be H1; a later, incidentally
    # bigger heading (a big "References" header, say) must not usurp that slot.
    source = born_digital_pdf(
        "<h2 style='font-size:20pt'>I. Introduction</h2><p>Body text here in the introduction.</p>"
        "<h1 style='font-size:30pt'>References</h1><p>More body text follows down here.</p>",
        tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    with pikepdf.open(out) as pdf:
        tags = [str(elem.S) for elem in pdf.Root.StructTreeRoot.K[0].K]
    headings = [t for t in tags if t.startswith("/H")]
    assert headings[0] == "/H1", headings
    # The later, bigger heading is also top-level (a sibling), not a usurper or a fabricated H0.
    assert set(headings) == {"/H1"}, headings


def test_ocr_heading_recovered_from_scan(tmp_path: Path):
    # A scan with a large, isolated title and full-width body: the title should be recovered as a
    # heading from OCR (size + isolation + shortness), where before every OCR line was a paragraph.
    source = pdf_image_only_scan(
        "<h1 style='font-size:34pt'>Annual Report</h1>"
        "<p>This first paragraph of ordinary body text runs the full width of the column, so it is "
        "plainly not a heading despite whatever height OCR assigns its box.</p>"
        "<p>A second ordinary paragraph of body text follows here, again spanning the full width "
        "of the text column beneath the title above it.</p>",
        tmp_path / "scan.pdf",
    )
    out = tmp_path / "out.pdf"
    result = remediate(source, out, title="R")

    assert result.ocr_pages == (1,)
    with pikepdf.open(out) as pdf:
        tags = _all_struct_tags(pdf)
    assert "/H1" in tags, tags


def test_ocr_body_only_scan_invents_no_headings(tmp_path: Path):
    # Uniform body text with no title must not manufacture headings from OCR box-height noise
    # (the pernambuco/Failure.pdf regression: an over-tall body line is not a heading).
    source = pdf_image_only_scan(
        "<p>The first paragraph of body text spans the full width of the column here.</p>"
        "<p>The second paragraph of body text also spans the full width of the column.</p>"
        "<p>The third paragraph of body text continues at the same size across the column.</p>",
        tmp_path / "scan.pdf",
    )
    out = tmp_path / "out.pdf"
    remediate(source, out, title="B")
    with pikepdf.open(out) as pdf:
        tags = _all_struct_tags(pdf)
    assert not any(t.startswith("/H") for t in tags), tags


def test_table_is_fully_tagged_with_header_cells(tmp_path: Path):
    from tests.fixtures import born_digital_pdf_with_table
    source = born_digital_pdf_with_table(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    with pikepdf.open(out) as pdf:
        tags = _all_struct_tags(pdf)
        # The table and its parts are present.
        assert "/Table" in tags and "/TR" in tags and "/TD" in tags
        # The header row is tagged as header cells with a column scope, not plain data cells.
        table = next(e for e in pdf.Root.StructTreeRoot.K[0].K if str(e.get("/S")) == "/Table")
        rows = [tr for tr in table.K if str(tr.get("/S")) == "/TR"]
        assert len(rows) >= 3
        header_cells = [c for c in rows[0].K if str(c.get("/S")) == "/TH"]
        assert len(header_cells) >= 3, "first row should be header cells"
        assert str(header_cells[0].A.Scope) == "/Column"
        # The grid is regular: every row has the same number of cells.
        widths = {len(list(tr.K)) for tr in rows}
        assert len(widths) == 1, f"irregular table: rows have {widths} cells"


def test_table_has_an_auto_generated_summary(tmp_path: Path):
    # A /Table needs a summary -- generated from what's already known (column/row count, header
    # text), never a semantic guess at the table's meaning (invariant 1: never fabricate). Set in
    # TWO places: /Alt (the generic PDF/UA alternate-description mechanism) AND the dedicated
    # /Summary Table attribute ISO 32000-2 defines specifically for this -- confirmed necessary on
    # a real sample: Adobe Acrobat's "Tables must have a summary" check kept failing with /Alt
    # alone (the same /A <</O /Table ...>> attribute mechanism already used for /Scope on headers).
    from tests.fixtures import born_digital_pdf_with_table
    source = born_digital_pdf_with_table(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    with pikepdf.open(out) as pdf:
        table = next(e for e in pdf.Root.StructTreeRoot.K[0].K if str(e.get("/S")) == "/Table")
        summary = str(table.Alt)
        assert str(table.A.O) == "/Table"
        table_summary = str(table.A.Summary)
    assert "3 columns" in summary and "4 rows" in summary
    assert "Region" in summary and "Sales" in summary and "Growth" in summary
    assert table_summary == summary, "the /Summary attribute must carry the same text as /Alt"


def test_sparse_table_row_is_kept_as_a_row(tmp_path: Path):
    # A subtotal-style row with an empty middle cell must not fragment the table or vanish: it stays
    # one table, and the sparse row is a /TR with an empty cell filling the gap.
    from tests.fixtures import born_digital_pdf_with_sparse_row_table
    source = born_digital_pdf_with_sparse_row_table(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    with pikepdf.open(out) as pdf:
        tables = [e for e in pdf.Root.StructTreeRoot.K[0].K if str(e.get("/S")) == "/Table"]
        assert len(tables) == 1, f"table fragmented into {len(tables)}"
        rows = [tr for tr in tables[0].K if str(tr.get("/S")) == "/TR"]
        assert len(rows) == 5, f"expected 5 rows (header + 4 data), got {len(rows)}"
        assert {len(list(tr.K)) for tr in rows} == {3}, "every row should have 3 cells"
    # The sparse row's values survive and are selectable.
    text = _selectable_text(out)
    assert "West" in text and "South" in text


def test_tagged_table_is_pdf_ua_compliant(tmp_path: Path, verapdf_exe: Path):
    from rebind.validate import validate_pdf_ua

    from tests.fixtures import born_digital_pdf_with_table
    source = born_digital_pdf_with_table(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    result = validate_pdf_ua(out, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()


def test_outline_is_built_from_recovered_headings_with_structure_destinations(
        tmp_path: Path, verapdf_exe: Path):
    # PDF/UA-2 clause 8.8 requires internal destinations to be structure destinations; nothing
    # before PDF 2.0 could produce that, so a source's own outline is stripped
    # (_strip_legacy_destinations) rather than shipped broken -- but a document that HAD bookmarks
    # then has none, which Adobe flags for "large documents" (real sample: this regressed a
    # previously-passing check). Rebind now builds its own outline from the headings it already
    # recovers, nested by level, each entry a real structure destination (/SD) into the heading
    # element itself -- not a page/coordinate destination.
    from rebind.validate import validate_pdf_ua

    source = born_digital_pdf(
        "<h1>Chapter One</h1><p>Body text here.</p>"
        "<h2>A Section</h2><p>More body text.</p>",
        tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    with pikepdf.open(out) as pdf:
        outlines = pdf.Root.get("/Outlines")
        assert outlines is not None, "no outline was built"
        assert int(outlines.Count) == 1   # one top-level item (Chapter One); A Section nests under it
        top = outlines.First
        assert str(top.Title) == "Chapter One"
        assert int(top.Count) == 1        # one nested child
        child = top.First
        assert str(child.Title) == "A Section"
        # Each destination is a real structure destination -- an /SD reference into the heading
        # element -- never a page/coordinate destination (that's exactly what fails clause 8.8).
        for item in (top, child):
            dest = item.Dest[0]
            assert str(dest.S) == "/XYZ"
            assert "/SD" in dest
            assert str(dest.SD.S).startswith("/H")

    result = validate_pdf_ua(out, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()


def test_figure_is_decorative_until_described(tmp_path: Path):
    from tests.fixtures import born_digital_pdf_with_image
    source = born_digital_pdf_with_image(tmp_path / "in.pdf")

    result = remediate(source, tmp_path / "out.pdf")
    assert len(result.figures) == 1
    fig = result.figures[0]
    assert fig["thumb"].startswith("data:image/png;base64,")
    with pikepdf.open(tmp_path / "out.pdf") as pdf:
        assert not any(str(e.get("/S")) == "/Figure" for e in pdf.Root.StructTreeRoot.K[0].K)

    described = remediate(source, tmp_path / "out2.pdf",
                          alt_texts={fig["id"]: "A red bar chart of sales."})
    assert described.figures == ()
    with pikepdf.open(tmp_path / "out2.pdf") as pdf:
        figs = [e for e in pdf.Root.StructTreeRoot.K[0].K if str(e.get("/S")) == "/Figure"]
        assert len(figs) == 1 and str(figs[0].get("/Alt")) == "A red bar chart of sales."


def test_figure_with_a_caption_is_described_automatically(tmp_path: Path):
    # A figure sitting directly under a "Fig. N ..." caption -- the real, standard convention
    # (confirmed against a real sample: 1429254.pdf, gitignored) -- needs no manual description at
    # all: the author's own caption is reused as /Alt, which is more accurate than anything Rebind
    # could invent AND skips the app's describe step entirely for a figure that already names
    # itself. A figure with no nearby caption still needs one (see the test above).
    from tests.fixtures import born_digital_pdf_with_captioned_image
    source = born_digital_pdf_with_captioned_image(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"

    result = remediate(source, out)

    assert result.figures == (), "a captioned figure should need no manual description"
    with pikepdf.open(out) as pdf:
        figs = [e for e in pdf.Root.StructTreeRoot.K[0].K if str(e.get("/S")) == "/Figure"]
        assert len(figs) == 1
        alt = str(figs[0].get("/Alt"))
    assert alt.startswith("Fig. 1")
    # WeasyPrint wraps the long caption across several physical lines; all of it must be captured,
    # not just the first line.
    assert "photograph of an actual" in alt


def test_figure_with_no_nearby_caption_still_needs_a_description(tmp_path: Path):
    # A caption-shaped line that is NOT actually adjacent to the figure (far below it, past normal
    # body text) must not be mistaken for its caption -- conservative by construction, matching
    # every other heuristic in this module: when in doubt, ask, don't guess.
    from tests.fixtures import born_digital_pdf_with_image
    source = born_digital_pdf_with_image(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"

    result = remediate(source, out)

    assert len(result.figures) == 1, "an uncaptioned figure must still ask for a description"


def test_split_caption_is_found_on_a_different_page(tmp_path: Path):
    # A multi-part figure whose image sits on one page while its real caption sits on another --
    # confirmed on a real sample: an image with only a bare "Fig. 8 (Continued)" nearby (no
    # descriptive content -- WCAG 1.1.1 is explicit that a figure's bare label never serves as its
    # text alternative on its own), while the real, substantial caption is on the following page.
    # A thin/absent local match falls back to searching every page for a fuller caption sharing the
    # same figure number.
    import base64
    import io as _io

    from PIL import Image

    from tests.fixtures import born_digital_pdf

    buf = _io.BytesIO()
    Image.new("RGB", (120, 80), (180, 40, 40)).save(buf, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    html = (
        f"<h1>Report</h1><p>See below.</p>"
        f"<img src='{uri}' width='200' height='133'>"
        # Indented well past the image's right edge -- reproduces the real sample exactly: the
        # marker line does NOT horizontally overlap the image, so _figure_caption's (deliberately
        # stricter, for building an actual caption block) alignment check finds nothing at all,
        # and only the more lenient _nearby_caption_number locates it.
        f"<p style='font-size:8pt; margin-left:200pt'>Fig. 8 (Continued)</p>"
        f"<p style='page-break-before:always; font-size:8pt'>"
        f"Fig. 8 Mounting zebrafish embryos and larvae for time-lapse imaging, showing the "
        f"complete apparatus used in these experiments.</p>"
    )
    source = born_digital_pdf(html, tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"

    result = remediate(source, out)

    assert result.figures == (), "the fuller caption on the next page should have been found"
    with pikepdf.open(out) as pdf:
        figs = [e for e in pdf.Root.StructTreeRoot.K[0].K if str(e.get("/S")) == "/Figure"]
        assert len(figs) == 1
        alt = str(figs[0].get("/Alt"))
    assert "Mounting zebrafish embryos" in alt, alt
    assert "(Continued)" not in alt, "should use the fuller caption, not the bare local marker"
