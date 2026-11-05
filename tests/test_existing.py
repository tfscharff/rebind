"""What an uploaded PDF already says, and what a person does around pictures, surviving a rebuild."""

from __future__ import annotations

import base64
import io

import pikepdf
import pytest
from PIL import Image
from pikepdf import Array, Dictionary, Name, String

from rebind.edits import READ_FIRST, Edits
from rebind.existing import (
    Described,
    adopt_described_figures,
    existing_descriptions,
    original_of,
)
from rebind.extract import Page, TextLine, extract_pages
from rebind.figures import _vector_figures
from rebind.layout import _band_under_columns
from rebind.overlay import (
    _has_marked_content,
    _has_unembedded_font,
    _has_unmappable_glyphs,
    _overlay_text,
)
from rebind.plan import _follow_anchors
from rebind.remediate import remediate
from rebind.structure import _top_index_for

from tests.fixtures import born_digital_pdf


def _picture_pdf(path) -> None:
    """A page with text around a real image, and no caption to describe it."""
    buffer = io.BytesIO()
    Image.new("RGB", (240, 180), (120, 40, 40)).save(buffer, format="PNG")
    png = base64.b64encode(buffer.getvalue()).decode()
    born_digital_pdf(
        "<p>An opening paragraph about the picture below it on the page.</p>"
        f'<img src="data:image/png;base64,{png}" style="width:240pt;height:180pt">'
        "<p>A paragraph set some way below the picture.</p>"
        "<p>Another paragraph after that one, to read last.</p>", path)


def _tagged_by_someone_else(path, alt: str, *, named_properties: bool = False) -> tuple:
    """A one-page PDF tagged as Acrobat might: a /Figure with an /Alt over a placed image."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    image = pdf.make_stream(b"\x80\x20\x20" * 4, Type=Name.XObject, Subtype=Name.Image, Width=2,
                            Height=2, ColorSpace=Name.DeviceRGB, BitsPerComponent=8)
    resources = Dictionary(XObject=Dictionary(Im0=image))
    marker = b"/Figure /MC0 BDC" if named_properties else b"/Figure <</MCID 0>> BDC"
    if named_properties:
        resources.Properties = Dictionary(MC0=Dictionary(MCID=0))
    page.obj.Resources = resources
    page.obj.Contents = pdf.make_stream(marker + b" q 200 0 0 100 100 500 cm /Im0 Do Q EMC\n")
    figure = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Figure, Pg=page.obj, K=0,
                                          Alt=String(alt)))
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot, K=Array([figure])))
    figure.P = root
    pdf.Root.StructTreeRoot = root
    pdf.save(path)
    return (100.0, 500.0, 300.0, 600.0)


def _figures(result, page: int = 1) -> list[dict]:
    return [e for e in result.elements if e["page"] == page and e["kind"] == "Figure"]


def test_a_description_in_another_tools_tags_is_read(tmp_path):
    source = tmp_path / "tagged.pdf"
    box = _tagged_by_someone_else(source, "A red square.")
    found = existing_descriptions(source)[1]
    assert [(d.kind, d.alt) for d in found] == [("Figure", "A red square.")]
    assert found[0].bbox == box


def test_a_marked_content_id_held_in_the_resources_counts(tmp_path):
    source = tmp_path / "tagged.pdf"
    box = _tagged_by_someone_else(source, "A red square.", named_properties=True)
    assert existing_descriptions(source)[1][0].bbox == box


@pytest.mark.parametrize("junk", [
    "IMG_2041.jpeg", "Image of Fig. 1", "Image of Fig. 12", "Figure 3", "Figure", "Picture 2",
    "Alternative_Text\x00\x00\x00g\x00P\x00D",
])
def test_a_placeholder_is_not_a_description(tmp_path, junk):
    source = tmp_path / "tagged.pdf"
    _tagged_by_someone_else(source, junk)
    assert existing_descriptions(source) == {}


@pytest.mark.parametrize("words", [
    "Figure 3 shows the energy of the reaction.", "Image of a red square.", "A graph.",
])
def test_real_words_are_a_description(tmp_path, words):
    source = tmp_path / "tagged.pdf"
    _tagged_by_someone_else(source, words)
    assert existing_descriptions(source)[1][0].alt == words


def test_a_described_picture_is_shown_described_in_the_editor(tmp_path):
    source = tmp_path / "tagged.pdf"
    _tagged_by_someone_else(source, "A red square.")
    result = remediate(source, tmp_path / "out.pdf")
    assert [e["alt"] for e in _figures(result)] == ["A red square."]
    assert result.figures == ()


def test_what_a_person_types_beats_what_the_document_said(tmp_path):
    source = tmp_path / "tagged.pdf"
    _tagged_by_someone_else(source, "A red square.")
    result = remediate(source, tmp_path / "out.pdf",
                       edits=Edits(alts={_figure_id(source, tmp_path): "Better words."}))
    assert [e["alt"] for e in _figures(result)] == ["Better words."]


def _figure_id(source, tmp_path) -> str:
    return _figures(remediate(source, tmp_path / "probe.pdf"))[0]["id"]


def test_adopting_keeps_the_guess_and_a_picture_rebind_missed():
    guess = (95.0, 95.0, 205.0, 205.0)
    described = [Described("Figure", "One", (100.0, 100.0, 120.0, 120.0)),
                 Described("Figure", "Two", (300.0, 300.0, 400.0, 400.0)),
                 Described("Formula", "Not a picture", (10.0, 10.0, 20.0, 20.0))]
    boxes, alts = adopt_described_figures([("p1f0", guess)], described, 1)
    assert boxes == [("p1f0", guess), ("p1e0", (300.0, 300.0, 400.0, 400.0))]
    assert alts == {"p1f0": "One", "p1e0": "Two"}


def test_a_box_rebind_wrote_replaces_the_guess():
    drawn = (100.0, 100.0, 120.0, 120.0)
    boxes, _alts = adopt_described_figures([("p1f0", (95.0, 95.0, 205.0, 205.0))],
                                           [Described("Figure", "One", drawn, exact=True)], 1)
    assert boxes == [("p1f0", drawn)]


def test_a_saved_description_survives_uploading_the_result(tmp_path):
    source = tmp_path / "source.pdf"
    _picture_pdf(source)
    first = remediate(source, tmp_path / "first.pdf")
    picture = _figures(first)[0]["id"]
    remediate(source, tmp_path / "saved.pdf", edits=Edits(alts={picture: "My description."}))
    again = remediate(tmp_path / "saved.pdf", tmp_path / "again.pdf")
    assert [e["alt"] for e in _figures(again)] == ["My description."]


def test_uploading_a_result_does_not_double_its_text(tmp_path):
    source = tmp_path / "source.pdf"
    _picture_pdf(source)
    first = remediate(source, tmp_path / "first.pdf")
    again = remediate(tmp_path / "first.pdf", tmp_path / "again.pdf")
    words = [e["text"] for e in first.elements if e.get("text")]
    assert words and [e["text"] for e in again.elements if e.get("text")] == words


def test_a_results_own_layer_comes_back_off(tmp_path):
    source = tmp_path / "source.pdf"
    _picture_pdf(source)
    picture = _figures(remediate(source, tmp_path / "probe.pdf"))[0]["id"]
    remediate(source, tmp_path / "saved.pdf", edits=Edits(alts={picture: "Described."}))
    original = original_of(tmp_path / "saved.pdf")
    assert original != tmp_path / "saved.pdf"
    assert original_of(source) == source
    with pikepdf.open(original) as pdf:
        operators = {str(i.operator) for i in pikepdf.parse_content_stream(pdf.pages[0])}
        assert not operators & {"BDC", "BMC", "EMC"}
        assert "/StructTreeRoot" not in pdf.Root
        fonts = pdf.pages[0].obj.Resources.get("/Font") or {}
        assert "/RebindF" not in fonts
    assert ([ln.text for ln in next(iter(extract_pages(original))).lines]
            == [ln.text for ln in next(iter(extract_pages(source))).lines])


def test_a_picture_can_be_given_the_box_a_person_drew(tmp_path):
    source = tmp_path / "source.pdf"
    _picture_pdf(source)
    picture = _figures(remediate(source, tmp_path / "probe.pdf"))[0]["id"]
    frame = (5.0, 10.0, 60.0, 30.0)
    result = remediate(source, tmp_path / "out.pdf", edits=Edits(frames={picture: frame}))
    figure = _figures(result)[0]
    assert (figure["left"], figure["top"], figure["width"], figure["height"]) == frame


def test_a_pictures_frame_does_not_turn_it_into_a_text_element(tmp_path):
    source = tmp_path / "source.pdf"
    _picture_pdf(source)
    plain = remediate(source, tmp_path / "probe.pdf")
    figure = _figures(plain)[0]
    frame = (figure["left"] - 0.5, figure["top"] - 0.2, figure["width"] + 1.0,
             figure["height"] + 0.4)
    result = remediate(source, tmp_path / "out.pdf", edits=Edits(frames={figure["id"]: frame}))
    assert ({e["id"] for e in result.elements} == {e["id"] for e in plain.elements})


def test_an_element_inserted_after_a_picture_stays_after_it(tmp_path):
    source = tmp_path / "source.pdf"
    _picture_pdf(source)
    plain = remediate(source, tmp_path / "probe.pdf")
    picture = _figures(plain)[0]["id"]
    # The last line on the page, which is nowhere near the picture in the reading order.
    last = [e for e in plain.elements if e["kind"] == "P"][-1]
    frame = (last["left"] - 1, last["top"] - 0.5, last["width"] + 2, last["height"] + 1)
    result = remediate(source, tmp_path / "out.pdf", edits=Edits(
        alts={picture: "Described."}, frames={"p1a0": frame}, after={"p1a0": picture}))
    order = [e["id"] for e in result.elements if e["kind"] != "Artifact"]
    assert order[order.index(picture) + 1] == "p1a0"

    with pikepdf.open(tmp_path / "out.pdf") as pdf:
        kinds = [str(kid.S) for kid in pdf.Root.StructTreeRoot.K[0].K]
    figure_at = kinds.index("/Figure")
    assert figure_at + 1 < len(kinds)       # read straight after the figure, not before it


def test_read_first_goes_ahead_of_everything():
    assert _follow_anchors(["a", "b", "c"], str, {"c": READ_FIRST}) == ["c", "a", "b"]


def test_a_picture_followed_by_its_own_caption_keeps_its_place():
    first, second = pikepdf.Dictionary(S=Name.P), pikepdf.Dictionary(S=Name.P)
    caption = pikepdf.Dictionary(S=Name.Caption)
    # The anchor's owner is inside a figure not spliced in yet: the next one along decides.
    assert _top_index_for([first, second], [first, caption, second], 1) == 1
    assert _top_index_for([first, second], [first, caption], 1) == 2
    assert _top_index_for([first, second], [first, second], None) == 2


def _line(text: str, x0: float, y0: float, x1: float, height: float = 10.0) -> TextLine:
    return TextLine(text=text, page=1, bbox=(x0, y0, x1, y0 + height), font="F", size=height,
                    bold=False, italic=False)


def test_a_caption_under_both_columns_is_cut_off_from_them():
    left = [_line("left text", 50, 700 - 12 * i, 290) for i in range(10)]
    right = [_line("right text", 320, 700 - 12 * i, 560) for i in range(10)]
    caption = [_line("FIG. 5. A caption across the whole measure", 50, 100, 560),
               _line("that ends with a short line.", 50, 88, 200)]
    band = _band_under_columns(left + right + caption, 290.0, 30.0)
    assert band is not None and 110 < band < 592


def test_a_line_crossing_the_gutter_mid_page_is_not_a_band():
    left = [_line("left text", 50, 700 - 12 * i, 290) for i in range(10)]
    right = [_line("right text", 320, 700 - 12 * i, 560) for i in range(10)]
    crossing = _line("a line across the gutter", 50, 500, 560)
    below = [_line("left below", 50, 400 - 12 * i, 290) for i in range(5)]
    right_below = [_line("right below", 320, 400 - 12 * i, 560) for i in range(5)]
    assert _band_under_columns(left + right + [crossing] + below + right_below, 290.0, 30.0) is None


class _Drawing:
    def __init__(self, bbox):
        self.bbox = bbox


def test_a_drawing_beside_its_caption_is_boxed_whole():
    lines = [_line("FIG. 2. Potential energy diagram for the formation", 380, 300, 560),
             _line("of 3 from 2a and HCN, derived from calculations.", 380, 288, 560),
             _line("2a + HNC", 330, 289, 370)]
    # Above the caption's first line, level with it, and running on below its last.
    drawings = [_Drawing((50.0 + 40 * i, 320.0, 80.0 + 40 * i, 340.0)) for i in range(4)]
    drawings += [_Drawing((60.0, 280.0, 90.0, 305.0)), _Drawing((100.0, 200.0, 140.0, 300.0)),
                 _Drawing((150.0, 180.0, 300.0, 260.0))]
    page = Page(number=1, width=612.0, height=792.0, lines=tuple(lines), images=(),
                drawings=tuple(drawings))
    held: dict[str, list[TextLine]] = {}
    (fid, bbox, caption), = _vector_figures(page, lines, [], held)
    assert bbox == (50.0, 180.0, 300.0, 340.0)
    assert "2a + HNC" not in caption and caption.startswith("FIG. 2.")
    assert [ln.text for ln in held[fid]] == [lines[0].text, lines[1].text]


def test_a_full_width_caption_claims_nothing_below_it():
    lines = [_line("FIG. 1. A caption across the measure.", 50, 400, 560)]
    drawings = [_Drawing((60.0 + 30 * i, 420.0, 80.0 + 30 * i, 500.0)) for i in range(6)]
    drawings += [_Drawing((60.0, 100.0, 300.0, 200.0))]
    page = Page(number=1, width=612.0, height=792.0, lines=tuple(lines), images=(),
                drawings=tuple(drawings))
    (_fid, bbox, _caption), = _vector_figures(page, lines, [])
    assert bbox[1] == 420.0


def test_marked_content_without_an_id_is_not_tagged_content(tmp_path):
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(100, 100))
    page.obj.Resources = Dictionary(Properties=Dictionary(MC0=Dictionary(Name=String("Layer"))))
    page.obj.Contents = pdf.make_stream(b"/Document /MC0 BDC 0 0 10 10 re f EMC\n")
    assert not _has_marked_content(page)
    page.obj.Contents = pdf.make_stream(b"/P <</MCID 0>> BDC 0 0 10 10 re f EMC\n")
    assert _has_marked_content(page)


def test_a_ligature_is_spelled_out_in_the_invisible_text():
    assert _overlay_text("efﬁcient ﬂow") == "efficient flow"


def test_a_private_use_character_is_read_as_unknown():
    assert _overlay_text("icon  here") == "icon � here"


def test_a_font_left_out_of_the_file_is_noticed_even_inside_a_form():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(100, 100))
    missing = Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Courier)
    form = pdf.make_stream(b"", Type=Name.XObject, Subtype=Name.Form,
                           BBox=Array([0, 0, 10, 10]),
                           Resources=Dictionary(Font=Dictionary(F1=missing)))
    page.obj.Resources = Dictionary(XObject=Dictionary(Fm0=form))
    assert _has_unembedded_font(page)
    # Rebind's own invisible font is never rendered, so the rule does not reach it.
    page.obj.Resources = Dictionary(Font=Dictionary(RebindF=missing))
    assert not _has_unembedded_font(page)


def test_glyphs_with_no_character_are_noticed():
    assert _has_unmappable_glyphs([_line("private  use", 0, 0, 10)])
    assert _has_unmappable_glyphs([_line("e (cid:2) 1", 0, 0, 10)])
    assert not _has_unmappable_glyphs([_line("plain text", 0, 0, 10)])
