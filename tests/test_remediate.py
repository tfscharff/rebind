"""Tests for in-place remediation: preserve the original, add accessibility."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
import pytest

from rebind.remediate import EDITABLE_TAGS, _encode_winansi, remediate
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


def test_link_annotation_with_an_unfollowable_target_is_dropped(tmp_path: Path):
    # A publisher's auto-linker can fire on text that merely looks URL-ish and emit a target that
    # resolves to nothing -- confirmed on a real sample, where "0.5-0.75" (a numeric range) became
    # a link to "http:0.5-0.75". Rebind cannot repair that (there is no correct target to guess),
    # and keeping it announces a link that goes nowhere to a screen-reader user. Drop it, exactly
    # as an internal legacy destination is dropped. A usable link is never touched.
    from pikepdf import Array, Dictionary, Name, String

    clean = born_digital_pdf("<h1>Title</h1><p>Body text.</p>", tmp_path / "clean.pdf")
    source = tmp_path / "in.pdf"
    with pikepdf.open(clean) as pdf:
        page = pdf.pages[0]

        def link(y, uri):
            return pdf.make_indirect(Dictionary(
                Type=Name.Annot, Subtype=Name.Link, Rect=Array([0, y, 10, y + 10]),
                A=Dictionary(S=Name.URI, URI=String(uri))))

        page.obj.Annots = Array([
            link(0, "http:0.5–0.75"),        # the real sample's defect, en dash and all
            link(20, "http:theminplace.We"),      # ditto: a sentence boundary, auto-linked
            link(40, "https://example.org/x"),    # a real link
            link(60, "mailto:someone@example.org"),
            link(80, "doi:10.1016/j.example"),    # an unfamiliar scheme is kept, not judged
        ])
        pdf.save(source)

    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    with pikepdf.open(out) as pdf:
        kept = {str(a.A.URI) for a in (pdf.pages[0].obj.get("/Annots") or [])}
    assert kept == {"https://example.org/x", "mailto:someone@example.org", "doi:10.1016/j.example"}


def _mcid_text(pdf_path: Path, page_index: int = 0) -> dict[int, str]:
    """Map each marked-content id on a page to the text drawn under it.

    Text drawn inside an /Artifact sequence has no MCID and never appears here -- that is the whole
    point of an artifact. Text drawn inside a /Figure's sequence lands under the *figure's* MCID,
    because it belongs to the figure rather than standing on its own.
    """
    out: dict[int, str] = {}
    with pikepdf.open(pdf_path) as pdf:
        stack: list[int | None] = []
        for operands, op in pikepdf.parse_content_stream(pdf.pages[page_index]):
            token = str(op)
            if token in ("BMC", "BDC"):
                mcid = None
                if len(operands) > 1 and isinstance(operands[1], pikepdf.Dictionary):
                    raw = operands[1].get("/MCID")
                    mcid = int(raw) if raw is not None else None
                stack.append(mcid if mcid is not None else (stack[-1] if stack else None))
            elif token == "EMC" and stack:
                stack.pop()
            elif token == "Tj" and operands and stack and stack[-1] is not None:
                text = bytes(operands[0]).decode("cp1252", "replace")
                out[stack[-1]] = (out.get(stack[-1], "") + " " + text).strip()
    return out


def _tagged_text_in_order(pdf_path: Path) -> list[str]:
    """The text of the tagged (non-artifact) layer, in marked-content id order."""
    mapping = _mcid_text(pdf_path)
    return [mapping[key] for key in sorted(mapping)]


def test_two_column_text_is_read_down_each_column_not_across_the_gutter(tmp_path: Path):
    # The classic way reading order goes wrong: sorting lines purely top-to-bottom on a two-column
    # page interleaves the columns, and a screen reader reads "LEFT line 1, RIGHT line 1, LEFT
    # line 2..." -- word salad. Rebind's XY-cut recovers the columns; this asserts the pipeline
    # actually applies it, which is the whole point of the recovery.
    from tests.fixtures import born_digital_pdf_two_column

    source = born_digital_pdf_two_column(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    sides = [t.split()[0] for t in _tagged_text_in_order(out) if t.split()]
    sides = [s for s in sides if s in ("LEFT", "RIGHT")]
    assert sides, "no tagged column text was found at all"
    assert sides == sorted(sides, key=lambda s: 0 if s == "LEFT" else 1), (
        f"columns are interleaved rather than read one after the other: {sides}")


def test_columns_are_found_even_under_a_full_width_heading(tmp_path: Path):
    # The ordinary article page: a heading and intro spanning the full width, two columns below.
    # Those full-width lines cross the gutter, so a vertical cut over the whole page finds nothing,
    # and the gap beneath them is far too small for a block cut -- the page collapses to one block
    # and is read straight across. The commonest real layout, and the commonest way to get it wrong.
    from tests.fixtures import born_digital_pdf_heading_over_two_columns

    source = born_digital_pdf_heading_over_two_columns(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    text = _tagged_text_in_order(out)
    sides = [t.split()[0] for t in text if t.split() and t.split()[0] in ("LEFT", "RIGHT")]
    assert sides == sorted(sides, key=lambda s: 0 if s == "LEFT" else 1), (
        f"columns under a heading are interleaved: {sides}")
    heading = next(i for i, t in enumerate(text) if "Annual Review" in t)
    first_column = next(i for i, t in enumerate(text) if t.startswith("LEFT"))
    assert heading < first_column, "the heading must still be read before the columns"


def _structure_sequence(pdf_path: Path) -> list[tuple[str, str]]:
    """The document's top-level structure elements in reading order, as (tag, its text)."""
    mapping = _mcid_text(pdf_path)
    out: list[tuple[str, str]] = []
    with pikepdf.open(pdf_path) as pdf:
        for kid in pdf.Root.StructTreeRoot.K[0].K:
            mcids: list[int] = []

            def collect(elem, into=mcids):
                kids = elem.get("/K")
                for item in (kids if isinstance(kids, pikepdf.Array) else [kids]):
                    if isinstance(item, int):
                        into.append(item)
                    elif isinstance(item, pikepdf.Dictionary) and "/S" in item:
                        collect(item, into)

            collect(kid)
            text = " ".join(mapping.get(m, "") for m in mcids).strip()
            out.append((str(kid.get("/S")).lstrip("/"), text))
    return out


def test_edited_tags_and_removals_still_produce_a_conformant_document(tmp_path: Path,
                                                                      verapdf_exe: Path):
    # A removed element's content must become an artifact, not merely lose its tag: content that
    # names a structure element the tree cannot resolve reads as untagged content and fails
    # clause 8.2.2. Caught exactly that way -- the self-check passed, veraPDF did not.
    from rebind.remediate import Edits
    from rebind.validate import validate_pdf_ua
    from tests.fixtures import born_digital_pdf

    source = born_digital_pdf(
        "<h1>Title</h1><p>First paragraph.</p><p>Second paragraph.</p>"
        "<p>Third paragraph.</p><p>Fourth paragraph.</p>", tmp_path / "in.pdf")
    plain = remediate(source, tmp_path / "plain.pdf", title="T")
    paragraphs = [e["id"] for e in plain.elements if e["kind"] == "P"]
    assert len(paragraphs) >= 3, plain.elements

    out = tmp_path / "out.pdf"
    result = remediate(source, out, title="T", edits=Edits(
        tags={paragraphs[0]: "H2"}, removed={paragraphs[1]}))

    kinds = {e["id"]: e["kind"] for e in result.elements}
    assert kinds[paragraphs[0]] == "H2"
    assert kinds[paragraphs[1]] == "Artifact", "a removed element must not be read"
    assert validate_pdf_ua(out, verapdf_exe=verapdf_exe).compliant

    # ...and the same element can be given a tag again, which is how it comes back.
    restored = remediate(source, tmp_path / "back.pdf", title="T",
                         edits=Edits(tags={paragraphs[1]: "P"}))
    assert {e["id"]: e["kind"] for e in restored.elements}[paragraphs[1]] == "P"


@pytest.mark.parametrize("tag", EDITABLE_TAGS)
def test_every_offered_tag_produces_a_conformant_document(tag: str, tmp_path: Path,
                                                          verapdf_exe: Path):
    # ISO 32005 Table 5 restricts what a Document element may contain and veraPDF enforces it
    # strictly, so every offered type is checked by applying it and validating the result. Guessing
    # got this wrong repeatedly: /Caption and /Quote are illegal there, /Aside is not a PDF 2.0 name
    # at all, a grouping element may not hold content directly, and /Figure needs an /Alt. One case
    # per tag, so a failure names the tag rather than "one of them".
    from rebind.remediate import Edits
    from rebind.validate import validate_pdf_ua
    from tests.fixtures import born_digital_pdf_with_captioned_drawing

    # A page with a real figure on it, so /Caption has something to be nested inside.
    source = born_digital_pdf_with_captioned_drawing(tmp_path / "in.pdf")
    plain = remediate(source, tmp_path / "plain.pdf", title="T")
    target = next(e["id"] for e in plain.elements if e["kind"] == "P")

    out = tmp_path / f"{tag}.pdf"
    result = remediate(source, out, title="T", edits=Edits(tags={target: tag}))
    assert {e["id"]: e["kind"] for e in result.elements}[target] == tag

    report = validate_pdf_ua(out, verapdf_exe=verapdf_exe)
    assert report.compliant, f"{tag}: {report.summary()}"


def test_every_hotkey_names_a_tag_that_exists():
    from rebind.remediate import TAG_KEYS

    from rebind.remediate import ARTIFACT_KEY

    keys = [key for key, _tag, _label, _what in TAG_KEYS]
    assert len(keys) == len(set(keys)), "hotkeys must be unique"
    for key, tag, label, what in TAG_KEYS:
        assert len(key) == 1, f"{tag}: a hotkey should be one keystroke, got {key!r}"
        assert tag in EDITABLE_TAGS, tag
        assert label, tag
        # The editor shows this when the element has focus. A tag with no explanation is a tag a
        # librarian has to already understand to use, which defeats the point of the editor.
        assert what and what != label, f"{tag}: needs an explanation of what it means"
    assert set(EDITABLE_TAGS) == {tag for _k, tag, _l, _w in TAG_KEYS}, (
        "every offered tag needs a key, or it cannot be reached from the keyboard")
    # "Not read" is an action rather than a type, so it is not among them -- but it still has to
    # answer to a key of its own, and that key must not collide with a type's.
    assert ARTIFACT_KEY not in keys
    # /Artifact is not a structure type and must never be offered as one.
    assert "Artifact" not in EDITABLE_TAGS


def test_row_hotkeys_are_well_formed_and_never_offered_as_whole_element_tags():
    # TH/TD only ever make sense as a row inside an already-tagged Table -- offering them as a
    # whole-element retag would let a bare paragraph become a /TH with no /TR or /Table around it,
    # which fails PDF/UA-2 structurally (Table 5 restricts what may hold a /TH directly).
    from rebind.remediate import EDITABLE_TAGS, ROW_TAG_KEYS

    keys = [key for key, _tag, _label, _what in ROW_TAG_KEYS]
    assert len(keys) == len(set(keys)), "row hotkeys must be unique"
    tags = {tag for _key, tag, _label, _what in ROW_TAG_KEYS}
    assert tags == {"TH", "TD"}
    assert tags.isdisjoint(EDITABLE_TAGS), "TH/TD must never be offered as whole-element tags"
    for key, tag, label, what in ROW_TAG_KEYS:
        assert len(key) == 1, f"{tag}: a hotkey should be one keystroke, got {key!r}"
        assert label and what and what != label, f"{tag}: needs a label and an explanation"


def test_edits_accepts_row_tags_alongside_element_tags():
    from rebind.remediate import Edits

    edits = Edits.from_payload({"tags": {"p1n0": "H2", "p1n0r0": "TH", "p1n0r1": "TD",
                                          "p1n0r2": "NotARealTag"}})
    assert edits.tags == {"p1n0": "H2", "p1n0r0": "TH", "p1n0r1": "TD"}


def test_edits_rejects_row_tags_on_a_non_row_id():
    # TH/TD only mean something as a row inside an already-built Table (see ROW_TAG_KEYS) -- a
    # payload trying to set a whole element's id to TH/TD must be dropped, the same way a bogus
    # tag string already is, or a crafted payload could build a bare /TH with no /TR or /Table
    # around it, which fails PDF/UA-2 structurally.
    from rebind.remediate import Edits

    edits = Edits.from_payload({"tags": {"p1n3": "TH", "p1n3r0": "TH", "p3r0": "TD"}})
    assert "p1n3" not in edits.tags
    assert "p3r0" not in edits.tags
    assert edits.tags == {"p1n3r0": "TH"}


def test_a_running_footer_is_an_artifact_not_content(tmp_path: Path):
    # PDF/UA requires page furniture to be marked as an artifact. Tagged as content, a screen
    # reader announces the running head and folio in the middle of the prose on every page.
    from tests.fixtures import born_digital_pdf

    body = "".join(
        f"<p>Body paragraph {i} of the running text on this page.</p>" for i in range(1, 60))
    source = born_digital_pdf(
        body, tmp_path / "in.pdf",
        extra_css=("@page { margin: 50pt; "
                   "@bottom-center { content: 'A Running Footer'; font-size: 8pt; } "
                   "@bottom-right { content: counter(page); font-size: 8pt; } }"))
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    with pikepdf.open(out) as pdf:
        page_count = len(pdf.pages)
    assert page_count > 1, "the fixture needs several pages for a footer to be a running one"

    for index in range(page_count):
        tagged = list(_mcid_text(out, index).values())
        assert any("Body paragraph" in t for t in tagged), f"page {index}: body text must survive"
        assert not any("Running Footer" in t for t in tagged), (
            f"page {index}: the footer should be an artifact, not tagged content: {tagged}")
        assert not any(t.strip().isdigit() for t in tagged), (
            f"page {index}: the folio should be an artifact: {tagged}")


def test_a_figure_is_one_element_not_a_picture_plus_loose_labels(tmp_path: Path):
    # A diagram's callout labels belong to the diagram. Tagged as separate paragraphs they are
    # read out as if they were prose -- "A", "B", "3 mm" -- and the figure stops being one thing.
    from tests.fixtures import born_digital_pdf_with_labelled_drawing

    source = born_digital_pdf_with_labelled_drawing(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    sequence = _structure_sequence(out)
    figures = [(tag, text) for tag, text in sequence if tag == "Figure"]
    assert len(figures) == 1, sequence
    for label in ("Inlet port", "Outlet port"):
        owners = [tag for tag, text in sequence if label in text]
        assert owners == ["Figure"], (
            f"{label!r} belongs to the figure, not to {owners}: {sequence}")


def test_a_figure_is_read_where_it_sits_not_after_the_whole_page(tmp_path: Path):
    # Reading order is a sequence, so a figure needs a place in it. Appended after everything else,
    # a screen reader meets the page's figures only once it has finished reading the page.
    from tests.fixtures import born_digital_pdf_with_captioned_drawing

    source = born_digital_pdf_with_captioned_drawing(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    tags = [tag for tag, _text in _structure_sequence(out)]
    assert "Figure" in tags, tags
    assert tags.index("Figure") < len(tags) - 1, (
        f"the figure should not be last -- text follows it on the page: {tags}")


def test_a_drawn_figure_is_found_and_described_from_its_caption(tmp_path: Path):
    # A schematic drawn with path operators leaves no /Image behind, so an image-only search misses
    # it entirely -- on the real sample that was six of eight figures, and Acrobat agreed with the
    # undercount because it was reading the same absence. The caption is what identifies it: the
    # page's horizontal rule is vector geometry too and must not become a figure of its own.
    from tests.fixtures import born_digital_pdf_with_captioned_drawing

    source = born_digital_pdf_with_captioned_drawing(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    result = remediate(source, out, title="T")

    assert result.figures == (), "the drawing's own caption should describe it, with no prompt"
    alts = []
    with pikepdf.open(out) as pdf:

        def walk(elem):
            if str(elem.get("/S")) == "/Figure":
                alts.append(str(elem.get("/Alt")))
            kids = elem.get("/K")
            for kid in (kids if isinstance(kids, pikepdf.Array) else [kids]):
                if isinstance(kid, pikepdf.Dictionary):
                    walk(kid)

        walk(pdf.Root.StructTreeRoot)

    assert len(alts) == 1, f"expected exactly one figure, got {len(alts)}: {alts}"
    assert "Preparation of resin" in alts[0]


def test_a_bare_figure_label_is_never_accepted_as_alt_text(tmp_path: Path):
    # A caption that is only its own label ("Fig. 8", or the "(Continued)" page-break artifact)
    # conveys nothing about the image -- WCAG 1.1.1. Accepting it would tick a checker's "has /Alt"
    # box while suppressing the app's prompt, so the figure must instead be reported as still
    # needing a description, i.e. the user gets asked.
    from tests.fixtures import born_digital_pdf_with_captioned_image

    pdf_path = born_digital_pdf_with_captioned_image(
        tmp_path / "in.pdf", caption="Fig. 8 (Continued)")
    result = remediate(pdf_path, tmp_path / "out.pdf", title="T")
    assert len(result.figures) == 1, "a bare label should leave the figure needing a description"


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


def test_a_scans_own_invisible_ocr_layer_is_removed_and_visible_marks_are_kept():
    # A scan that has already been through Tesseract carries the picture plus invisible text
    # (rendering mode 3) in a stand-in font whose ToUnicode CMap veraPDF rejects -- 46 failed
    # checks of clause 8.4.5.8 on a real sample, for a font nothing needs once Rebind has laid
    # down its own tagged invisible layer over the same words. The layer and its font go; every
    # visible mark stays, so the page still looks exactly as it did.
    from rebind.remediate import _strip_invisible_text

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    font = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica))
    page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(Hidden=font, Shown=font))
    page.obj.Contents = pdf.make_stream(
        b"0 0 1 rg 10 10 50 50 re f\n"                                   # a visible mark
        b"BT 3 Tr /Hidden 12 Tf 20 100 Td (scanned words) Tj ET\n"       # the OCR layer
        b"BT 0 Tr /Shown 12 Tf 20 150 Td (real text) Tj ET\n")           # genuine visible text

    assert _strip_invisible_text(pdf, pikepdf.Page(page.obj)) is True

    body = bytes(page.obj.Contents.read_bytes())
    assert b"scanned words" not in body, "the invisible OCR layer survived"
    assert b"real text" in body, "visible text must never be removed"
    assert b"re" in body and b"0 0 1 rg" in body, "the page's visible marks must be untouched"
    # The mode the dropped run set persists past its ET, so it has to be re-stated -- without it
    # the *next* run's rendering mode silently changes.
    assert b"3 Tr" in body
    fonts = page.obj.Resources.Font
    assert "/Shown" in fonts and "/Hidden" not in fonts, "the unusable font must go with its text"


def test_invisible_mode_restored_by_Q_does_not_delete_visible_text():
    # The text rendering mode is part of the graphics state, so `Q` restores it. A stripper that
    # tracked `Tr` but not `q`/`Q` would still believe the mode was 3 after the restore and delete
    # text the page genuinely shows -- silently destroying content, the worst failure available.
    from rebind.remediate import _strip_invisible_text

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    font = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica))
    page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F=font))
    page.obj.Contents = pdf.make_stream(
        b"q 3 Tr BT /F 12 Tf 20 100 Td (hidden) Tj ET Q\n"     # invisible, mode discarded by Q
        b"BT /F 12 Tf 20 150 Td (visible) Tj ET\n")            # mode is 0 again here

    _strip_invisible_text(pdf, pikepdf.Page(page.obj))

    body = bytes(page.obj.Contents.read_bytes())
    assert b"hidden" not in body
    assert b"visible" in body, "text after a restored graphics state was wrongly deleted"


def test_a_picture_guessed_from_a_scan_never_swallows_prose():
    # A figure the *file* declares knows where it is, so ownership of nearby callout labels can
    # grow outward from it. A picture found in a scan's pixels does not: its box is where the ink
    # happened to be. Growing from a guess cascaded across the page and pulled 117 paragraphs of a
    # real scanned book out of the reading order into decorative artifacts -- text hidden from a
    # screen reader, which is the worst outcome available here.
    from rebind.extract import TextLine
    from rebind.remediate import _figure_text, _figure_text_strict

    def line(text: str, box: tuple) -> TextLine:
        return TextLine(text=text, page=1, bbox=box, font="F", size=10, bold=False, italic=False)

    picture = (100.0, 500.0, 400.0, 700.0)
    inside = line("A", (200.0, 590.0, 210.0, 600.0))
    just_outside = line("Bonding", (150.0, 704.0, 220.0, 714.0))
    prose = line("Ordinary body text well below the picture.", (72.0, 300.0, 540.0, 312.0))
    # A full sentence that happens to fall inside the guessed box is prose, not a callout label.
    sentence_within = line(
        "The apparatus was assembled from parts held in the departmental store.",
        (110.0, 520.0, 390.0, 532.0))
    lines = [inside, just_outside, prose, sentence_within]

    strict = _figure_text_strict(lines, picture)
    assert strict == [inside], "a guessed box claims only short labels that sit inside it"
    assert sentence_within not in strict

    # The declared-figure path still reaches a label sitting just past the ink, as it must.
    assert just_outside in _figure_text(lines, picture)
    # Neither path ever reaches the prose.
    assert prose not in _figure_text(lines, picture)


def test_a_caption_beside_a_figure_is_found_and_an_ambiguous_one_is_not():
    # A book with a wide outer margin stacks its captions there rather than under the pictures. A
    # search that only looks up and down finds nothing on such a page: a real photograph came out
    # with "Rebind found no caption to guess from" while its caption sat two inches to its right.
    #
    # Reaching sideways is riskier than reaching down, because several figures share one vertical
    # span and their captions are stacked beside all of them. So this returns everything it finds
    # and lets the caller refuse when there is more than one -- the wrong caption is a fabrication,
    # which is worse than an empty box and a question.
    from rebind.extract import TextLine
    from rebind.remediate import _side_captions

    def line(text: str, box: tuple) -> TextLine:
        return TextLine(text=text, page=1, bbox=box, font="F", size=10, bold=False, italic=False)

    picture = (100.0, 500.0, 300.0, 700.0)
    beside = line("Fig. 2.  Head of the statue, in profile.", (340.0, 600.0, 520.0, 612.0))
    far_away = line("Fig. 9.  Something on the other side of the page.",
                    (560.0, 600.0, 720.0, 612.0))
    below_unrelated = line("Ordinary prose under the picture.", (100.0, 470.0, 300.0, 482.0))

    found = _side_captions([beside, far_away, below_unrelated], picture)
    assert len(found) == 1 and found[0].startswith("Fig. 2."), found

    # Two captions stacked in the margin beside one picture: both come back, so the caller declines.
    second = line("Fig. 3.  A coin, obverse.", (340.0, 660.0, 520.0, 672.0))
    assert len(_side_captions([beside, second], picture)) == 2


def test_a_figure_with_no_description_is_still_an_element_in_the_editor(tmp_path: Path):
    # In the document an undescribed figure is a decorative artifact, and has to be: tagging one
    # with no /Alt is a conformance failure. In the EDITOR it must still be an element, because it
    # is the one thing that needs a person and the walk is where they are asked. When it was left
    # out, the only way to reach it was a list of thumbnails in the report -- and once that list
    # was removed, a real photograph on a real scanned page became unreachable and looked for all
    # the world like a figure Rebind had failed to find.
    from tests.fixtures import born_digital_pdf

    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (160, 100), (60, 60, 60)).save(buffer, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

    source = born_digital_pdf(
        "<p>Text above the picture.</p>"
        f'<img src="{data_uri}" style="width:200px;height:125px">'
        "<p>Text below it, with no caption anywhere, so nothing can describe it.</p>",
        tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    result = remediate(source, out, title="T")

    figures = [e for e in result.elements if e["kind"] == "Figure"]
    assert figures, [(e["kind"], e["text"][:30]) for e in result.elements]
    undescribed = [e for e in figures if not e["alt"]]
    assert undescribed, "a figure with no description must still be reachable in the walk"
    # And it is the same figure the app asks about, by id -- the prompt is keyed on it.
    assert {f["id"] for f in undescribed} >= {f["id"] for f in result.figures}


def test_a_paragraph_is_one_element_not_one_per_line(tmp_path: Path):
    # Tagging each line as its own /P is wrong in a way that matters: a screen reader pauses at
    # every element boundary, so a page of prose comes out as a stream of fragments. Lines are
    # joined into one paragraph unless the typesetting says otherwise.
    from tests.fixtures import born_digital_pdf

    source = born_digital_pdf(
        "<p>" + " ".join(f"Sentence number {i} of the first paragraph, long enough that this "
                         "paragraph must wrap over several lines on the page." for i in range(4))
        + "</p>"
        "<p>" + " ".join(f"Sentence number {i} of the second paragraph, also long enough to "
                         "wrap over more than one line of its own." for i in range(4)) + "</p>",
        tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    result = remediate(source, out, title="T")

    paragraphs = [e for e in result.elements if e["kind"] == "P"]
    assert len(paragraphs) == 2, [p["text"][:60] for p in paragraphs]
    # And the two are still two: joining them would lose a boundary a reader needs, and no amount
    # of later processing can put it back.
    assert "first paragraph" in paragraphs[0]["text"]
    assert "second paragraph" in paragraphs[1]["text"]
    # Every line's text survives the join -- this groups lines, it never drops one. (The editor's
    # own preview is capped at 300 characters, so the document itself is what to check.)
    text = " ".join(_selectable_text(out).split())
    assert "Sentence number 3 of the first paragraph" in text
    assert "Sentence number 3 of the second paragraph" in text
    # Each one spans several lines of the page, which is the whole point.
    assert all(p["height"] > 5 for p in paragraphs), paragraphs


def test_a_title_set_across_two_lines_is_one_heading(tmp_path: Path):
    # A title too long for one line is still one title. Left as two /H1 elements it is read as two
    # headings, which is both wrong and unnavigable: a screen reader's heading list gets a phantom
    # entry, and the halves are announced with a pause between them as though unrelated.
    from tests.fixtures import born_digital_pdf

    source = born_digital_pdf(
        "<h1>A Title Too Long For One Line Of This Page</h1>"
        + "".join(f"<p>Paragraph {i} of the body, long enough to wrap onto a second line so that "
                  "the document's body size is the commonest size on the page.</p>"
                  for i in range(4)),
        tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    result = remediate(source, out, title="T")

    headings = [e for e in result.elements if e["kind"].startswith("H")]
    assert len(headings) == 1, [(e["kind"], e["text"][:40]) for e in result.elements]
    # Both halves are in it, and it covers both lines of the page.
    assert "A Title Too Long" in headings[0]["text"] and "This Page" in headings[0]["text"]
    assert headings[0]["height"] > 3, headings[0]


def test_two_separate_headings_of_the_same_level_stay_separate(tmp_path: Path):
    # The other half of the rule: joining is by adjacency, so it must not swallow the next section's
    # heading. What separates them is the space around them, which is what the join tests.
    from tests.fixtures import born_digital_pdf

    source = born_digital_pdf(
        "<h2>First Section</h2><p>A little body text under the first section heading.</p>"
        "<h2>Second Section</h2><p>A little body text under the second section heading.</p>",
        tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    result = remediate(source, out, title="T")

    kinds = [e["kind"] for e in result.elements]
    headings = [k for k in kinds if k.startswith("H")]
    assert len(headings) == 2, [(e["kind"], e["text"][:40]) for e in result.elements]


def test_a_heading_never_joins_the_paragraph_under_it(tmp_path: Path):
    from tests.fixtures import born_digital_pdf

    source = born_digital_pdf(
        "<h1>The Heading</h1>"
        "<p>Body text that follows the heading and runs on long enough to wrap onto a second "
        "line of its own so the join has something to do.</p>",
        tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    result = remediate(source, out, title="T")

    kinds = [e["kind"] for e in result.elements]
    assert kinds == ["H1", "P"], [(e["kind"], e["text"][:40]) for e in result.elements]


def test_a_scans_invisible_ocr_layer_is_not_measured_for_contrast():
    # The measurement read a Tesseract sandwich's invisible text as if it were on the page: 939
    # lines and 49 "contrast failures" on a real scan, every one of them text drawn in rendering
    # mode 3 that nobody can see -- and that Rebind strips out before writing the document. A page
    # whose text is entirely invisible has nothing to measure; what a reader sees is the picture.
    from rebind.remediate import _text_visibility

    pdf = pikepdf.new()
    sandwich = pdf.add_blank_page(page_size=(200, 200))
    ordinary = pdf.add_blank_page(page_size=(200, 200))
    font = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica))
    for page in (sandwich, ordinary):
        page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F=font))
    sandwich.obj.Contents = pdf.make_stream(
        b"BT 3 Tr /F 12 Tf 20 100 Td (scanned words) Tj ET\n")
    ordinary.obj.Contents = pdf.make_stream(
        b"BT /F 12 Tf 20 100 Td (real text) Tj ET\n")

    assert _text_visibility(pikepdf.Page(sandwich.obj)) == (True, False)
    assert _text_visibility(pikepdf.Page(ordinary.obj)) == (True, True)
    # A page with no text at all is neither, so it is never mistaken for a sandwich.
    blank = pdf.add_blank_page(page_size=(200, 200))
    assert _text_visibility(pikepdf.Page(blank.obj)) == (False, False)


def test_scripts_are_removed_only_when_asked_for():
    # A script is behaviour the author put there. The report offers removing it as a fix, so it
    # happens because someone chose it -- never silently, which is the same rule that governs
    # every other change Rebind makes to what a document does.
    from rebind.remediate import _strip_scripts

    def with_scripts():
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(200, 200))
        pdf.Root.Names = pikepdf.Dictionary(JavaScript=pikepdf.Dictionary(Names=pikepdf.Array()))
        pdf.Root.OpenAction = pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS=pikepdf.String("x"))
        page.obj.AA = pikepdf.Dictionary(O=pikepdf.Dictionary(S=pikepdf.Name.JavaScript))
        return pdf

    untouched = with_scripts()
    assert "/JavaScript" in untouched.Root.Names, "the fixture must actually carry scripts"

    pdf = with_scripts()
    assert _strip_scripts(pdf) == 3
    assert "/JavaScript" not in pdf.Root.Names
    assert "/OpenAction" not in pdf.Root
    assert "/AA" not in pdf.pages[0].obj
    assert _strip_scripts(pdf) == 0, "a document with no scripts left is untouched"


def test_table_cells_sharing_a_column_are_all_owned():
    # Two cells in one row whose left edges fall within the column tolerance of each other snap to
    # the SAME column. Keeping only one of them (which is what dict.setdefault did) silently drops
    # the other's marked content: its MCID is drawn on the page but no structure element claims it,
    # which is untagged content -- PDF/UA clause 8.2.2 -- and trips remediate's own owner assert
    # ("unowned marked content"). Real, not hypothetical: a noisy OCR'd page of a scanned grid
    # produces colliding cells constantly. Both cells must end up owned, in the same /TD.
    from rebind.extract import TextLine
    from rebind.remediate import _page_structure

    def cell(text: str, x: float, y: float) -> TextLine:
        return TextLine(text=text, page=1, bbox=(x, y, x + 4, y + 9), font="F", size=9,
                        bold=False, italic=False)

    lines = [cell("A", 100, 700), cell("B", 104, 700),      # header row, colliding
             cell("C", 100, 680), cell("D", 104, 680)]      # data row, colliding
    plan = [{"kind": "Table", "first": 0, "last": 3, "id": "p1n0", "alt": ""}]

    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    document = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.StructElem, S=pikepdf.Name.Document))
    _tops, owners = _page_structure(pdf, lines, plan, [0, 1, 2, 3], document, page.obj)

    assert sorted(owners) == [0, 1, 2, 3], "a cell's marked content was left unowned"


def test_tagged_table_is_pdf_ua_compliant(tmp_path: Path, verapdf_exe: Path):
    from rebind.validate import validate_pdf_ua

    from tests.fixtures import born_digital_pdf_with_table
    source = born_digital_pdf_with_table(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    result = validate_pdf_ua(out, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()


def test_a_row_can_be_promoted_to_header_by_id(tmp_path: Path, verapdf_exe: Path):
    # Rebind's default guess -- the first detected row is the header -- is sometimes wrong (a
    # table with no header row at all, or one whose header is really its second row). The row
    # sub-elements from Task 3 exist so a person can correct exactly one row without retagging the
    # whole table.
    from rebind.remediate import Edits
    from rebind.validate import validate_pdf_ua
    from tests.fixtures import born_digital_pdf_with_table

    source = born_digital_pdf_with_table(tmp_path / "in.pdf")
    plain = remediate(source, tmp_path / "plain.pdf", title="T")
    table = next(e for e in plain.elements if e["kind"] == "Table")

    out = tmp_path / "out.pdf"
    # Swap the header from row 0 to row 1 ("North" becomes the header row instead of "Region").
    result = remediate(source, out, title="T", edits=Edits(
        tags={f"{table['id']}r0": "TD", f"{table['id']}r1": "TH"}))

    rows = [e for e in result.elements if e.get("row") and e["id"].startswith(table["id"] + "r")]
    assert [r["kind"] for r in rows] == ["TD", "TH", "TD", "TD"]

    with pikepdf.open(out) as pdf:
        tbl = next(e for e in pdf.Root.StructTreeRoot.K[0].K if str(e.get("/S")) == "/Table")
        trs = list(tbl.K)
        first_row_cell_types = {str(c.get("/S")) for c in trs[0].K}
        second_row_cell_types = {str(c.get("/S")) for c in trs[1].K}
        assert first_row_cell_types == {"/TD"}
        assert second_row_cell_types == {"/TH"}

    result_report = validate_pdf_ua(out, verapdf_exe=verapdf_exe)
    assert result_report.compliant, result_report.summary()


def test_table_rows_are_offered_as_editable_sub_elements(tmp_path: Path):
    # A table's header row is a guess (today: always the first detected row). Exposing each row as
    # its own element -- with its own id and bbox -- is what lets a person correct that guess for
    # one row without retagging the whole table.
    from tests.fixtures import born_digital_pdf_with_table

    source = born_digital_pdf_with_table(tmp_path / "in.pdf")
    result = remediate(source, tmp_path / "out.pdf", title="T")

    table = next(e for e in result.elements if e["kind"] == "Table")
    rows = [e for e in result.elements if e.get("row") and e["id"].startswith(table["id"] + "r")]
    assert len(rows) == 4, rows          # header + 3 data rows, per born_digital_pdf_with_table
    assert rows[0]["id"] == f"{table['id']}r0"
    assert rows[0]["kind"] == "TH"
    assert [r["kind"] for r in rows[1:]] == ["TD", "TD", "TD"]
    for row in rows:
        assert row["editable"] is True
        # A row's box sits inside the table's box -- it did not escape onto some other part of the
        # page.
        assert table["top"] <= row["top"] <= row["top"] + row["height"] <= table["top"] + table["height"] + 0.5


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


def test_footnote_tag_builds_as_note(tmp_path: Path, verapdf_exe: Path):
    # /Note is PDF 2.0's structure type for a footnote or endnote -- content read separately from
    # the body text it annotates, not inline with it. Its content is built the same simple way as
    # P, H1-H6 and BlockQuote (it takes its element's marked content directly), but unlike them it
    # needs a RoleMap entry to /P or PDF/UA-2 clause 8.2.5.14 rejects it -- see the RoleMap comment
    # in remediate.py.
    from rebind.remediate import Edits
    from rebind.validate import validate_pdf_ua
    from tests.fixtures import born_digital_pdf

    source = born_digital_pdf(
        "<h1>Title</h1><p>Body text.</p><p>1. A footnote at the bottom of the page.</p>",
        tmp_path / "in.pdf")
    plain = remediate(source, tmp_path / "plain.pdf", title="T")
    target = next(e["id"] for e in plain.elements
                  if e["kind"] == "P" and "footnote" in e["text"])

    out = tmp_path / "out.pdf"
    remediate(source, out, title="T", edits=Edits(tags={target: "Note"}))

    with pikepdf.open(out) as pdf:
        notes = [e for e in pdf.Root.StructTreeRoot.K[0].K if str(e.get("/S")) == "/Note"]
        assert len(notes) == 1

    result = validate_pdf_ua(out, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()


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


def test_a_caption_broken_across_lines_is_healed_not_hyphenated():
    # A typesetter's line-break hyphen is not part of the word. Joined naively the caption reads
    # "iconograph- ical elements", which is what a screen reader then says -- and that string goes
    # into the document as the picture's /Alt, so it is the reader's only description of it.
    from rebind.remediate import _join_caption_lines

    assert _join_caption_lines(
        ["Fig. 2.  Head of the statue. Hairstyle and facial features show the same iconograph-",
         "ical elements as Hellenistic ruler portraits."]
    ) == ("Fig. 2.  Head of the statue. Hairstyle and facial features show the same "
          "iconographical elements as Hellenistic ruler portraits.")
    # A dash before a capital is an aside, not a broken word, and a plain line break is a space.
    assert _join_caption_lines(["Fig. 3.  The forum -", "Rome, that is."]) == \
        "Fig. 3.  The forum - Rome, that is."
    assert _join_caption_lines(["Fig. 4.  A coin,", "obverse."]) == "Fig. 4.  A coin, obverse."


def test_a_caption_is_one_element_even_when_the_scan_drops_marks_between_its_lines():
    # A caption cannot be held together by the paragraph rule. Set in a narrow margin column it has
    # no measure to run out to, and on a scan the recogniser leaves stray marks between its lines --
    # a "~" here, a "|" there -- each of which breaks a run of "consecutive lines with the same
    # role" and splits one caption into three elements. Which lines are one caption was settled
    # when the caption was found; this carries that decision through.
    from rebind.extract import TextLine
    from rebind.remediate import _caption_groups, plan_page

    def line(text: str, box: tuple) -> TextLine:
        return TextLine(text=text, page=1, bbox=box, font="F", size=10, bold=False, italic=False)

    first = line("Fig. 3.  Tetradrachm with portrait of the last", (385.0, 600.0, 520.0, 612.0))
    stray = line("~", (367.0, 586.0, 369.0, 597.0))
    second = line("Macedonian king, Perseus V.", (385.0, 585.0, 500.0, 597.0))
    prose = line("Ordinary body text far below.", (72.0, 300.0, 540.0, 312.0))
    lines = [first, stray, second, prose]
    roles = ["Caption", "P", "Caption", "P"]
    caption_of = {id(first): 0, id(second): 0}

    groups = _caption_groups(lines, roles, caption_of, [[first, second]])
    assert groups == [0, 0, 0, None], groups
    assert roles[1] == "Caption", "the stray between the caption's lines joins the caption"

    plan = plan_page(lines, roles, groups)
    captions = [entry for entry in plan if entry["kind"] == "Caption"]
    assert len(captions) == 1, plan
    assert (captions[0]["first"], captions[0]["last"]) == (0, 2)
    # ...and prose well clear of it is untouched, whatever else is on the page.
    assert {"kind": "P", "first": 3, "last": 3} in plan


def test_a_line_level_with_a_caption_across_the_page_does_not_join_it():
    # The guard: nearness has to hold both ways. A line sharing the caption's vertical span but
    # sitting on the other side of the page is a different column, and reaching across for it is
    # the same mistake as reading across a gutter.
    from rebind.extract import TextLine
    from rebind.remediate import _caption_groups

    def line(text: str, box: tuple) -> TextLine:
        return TextLine(text=text, page=1, bbox=box, font="F", size=10, bold=False, italic=False)

    caption = line("Fig. 3.  A coin.", (385.0, 600.0, 520.0, 612.0))
    far = line("Text in the left column, level with it.", (72.0, 599.0, 300.0, 611.0))
    roles = ["Caption", "P"]

    groups = _caption_groups([caption, far], roles, {id(caption): 0}, [[caption]])
    assert groups == [0, None], groups
    assert roles[1] == "P"


def test_a_guessed_picture_with_no_caption_anywhere_is_not_a_picture():
    # A picture guessed from a scan's pixels is ink that is not text. On a page of maps and plans
    # that is mostly the drawing's own hatching, and two real pages produced a scatter of figures
    # that are not there. A picture the document never captions is one Rebind has nothing to say
    # about and no way to check, so it goes back to being part of the page.
    from rebind.extract import TextLine
    from rebind.remediate import _side_captions

    # The rule itself is one line in the page loop; what it depends on is that nothing anywhere
    # offered a caption. This pins the "nothing offered" half, which is what makes it safe.
    def line(text: str, box: tuple) -> TextLine:
        return TextLine(text=text, page=1, bbox=box, font="F", size=10, bold=False, italic=False)

    plan_drawing = (100.0, 400.0, 400.0, 700.0)
    prose = line("Ordinary prose, which is not a caption and must never be taken for one.",
                 (100.0, 380.0, 400.0, 392.0))
    assert _side_captions([prose], plan_drawing) == []


def test_a_figure_can_be_taken_out_by_an_edit(tmp_path: Path):
    # Figures live outside the plan, so the edit that removes an element never reached them:
    # pressing "x" on a wrongly-found picture did nothing at all. On a page with two of them that
    # is a wall the walk cannot get past.
    import base64
    import io

    from PIL import Image

    from rebind.remediate import Edits
    from tests.fixtures import born_digital_pdf

    buffer = io.BytesIO()
    Image.new("RGB", (160, 100), (60, 60, 60)).save(buffer, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    source = born_digital_pdf(
        f'<p>Above.</p><img src="{data_uri}" style="width:200px;height:125px"><p>Below.</p>',
        tmp_path / "in.pdf")

    before = remediate(source, tmp_path / "a.pdf", title="T")
    figure = next(e for e in before.elements if e["kind"] == "Figure")

    after = remediate(source, tmp_path / "b.pdf", title="T",
                      edits=Edits(removed={figure["id"]}))
    assert not [e for e in after.elements if e["kind"] == "Figure"], \
        "a figure the person took out must stay out"


def test_text_inside_a_figure_is_not_also_an_element_of_its_own(tmp_path: Path):
    # A figure's own text is drawn inside it and read as part of it. Listing it again puts the
    # picture's insides into the walk as separate stops -- on a scanned architectural plan, 175 of
    # them from one figure, and getting past that page meant 175 presses of Tab.
    from tests.fixtures import born_digital_pdf

    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (240, 140), (90, 90, 90)).save(buffer, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    source = born_digital_pdf(
        '<div style="position:relative">'
        f'<img src="{data_uri}" style="width:300px;height:170px">'
        '<span style="position:absolute;left:30px;top:60px">A</span>'
        '<span style="position:absolute;left:200px;top:60px">B</span></div>'
        "<p>Fig. 1.  A schematic of the apparatus, with its two chambers marked.</p>",
        tmp_path / "in.pdf")
    result = remediate(source, tmp_path / "out.pdf", title="T")

    figures = [e for e in result.elements if e["kind"] == "Figure"]
    assert figures, [(e["kind"], e["text"][:30]) for e in result.elements]
    loose = [e for e in result.elements if e["text"].strip() in {"A", "B"}]
    assert not loose, f"a figure's callout labels must not be elements too: {loose}"
