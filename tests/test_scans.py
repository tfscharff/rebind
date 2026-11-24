"""What is read on a scanned page: a scan's own OCR layer completed, and the words on a picture."""

from __future__ import annotations

from rebind.extract import TextLine


def _line(text: str, x0: float, top: float, x1: float, height: float = 10.0,
          confidence: float | None = None) -> TextLine:
    return TextLine(text=text, page=1, bbox=(x0, top - height, x1, top), font="", size=height,
                    bold=False, italic=False, ocr_confidence=confidence)


def test_what_a_scans_own_ocr_layer_missed_is_added():
    # A graphic novel's OCR layer had every caption box and not one speech balloon.
    from rebind.remediate import _missed_by

    layer = [_line("Infectious and unpleasant. Acne is", 129, 777, 309)]
    recognised = [
        _line("Infectious and unpleasant. Acne is", 130, 778, 308, confidence=0.98),
        _line("Acne literally marks", 435, 680, 542, confidence=0.96),
        _line("~ .!.", 300, 300, 320, confidence=0.9),          # hatching, not words
        _line("blurred", 100, 200, 160, confidence=0.4),         # not read with confidence
    ]
    assert [line.text for line in _missed_by(layer, recognised)] == ["Acne literally marks"]


def test_a_speech_balloon_on_a_guessed_picture_is_read_not_held_out():
    from rebind.figures import _figure_text_strict

    balloon = [_line("If these inflamed", 63, 727, 154), _line("infections are also", 60, 713, 158),
               _line("part of my", 80, 699, 138)]
    label = _line("Fig. A", 300, 500, 330)
    owned = _figure_text_strict(balloon + [label], (40, 200, 560, 760))
    assert owned == [label]


def test_a_rule_typed_as_a_row_of_dashes_is_not_read():
    from rebind.figures import _margin_furniture

    rule = _line("-" * 40, 72, 700, 540)
    words = _line("The copyright law of the United States", 72, 680, 540)
    assert _margin_furniture([rule, words], 612, 792) == [rule]


def _first_lines(tmp_path, paragraphs: list[str]) -> list[str]:
    from rebind.extract import extract_pages
    from tests.fixtures import born_digital_pdf

    css = ("p { width: 4in; } "
           ".cap { float: left; font-size: 3.2em; line-height: 0.85; margin: 2pt 3pt 0 0; }")
    body = "".join(f"<p><span class='cap'>{text[0]}</span>{text[1:]}</p>" for text in paragraphs)
    page = next(extract_pages(born_digital_pdf(body, tmp_path / "caps.pdf", extra_css=css)))
    return [line.text for line in page.lines]


def test_a_drop_cap_is_read_with_the_word_it_begins(tmp_path):
    # pdfminer left a gap after the cap: a screen reader said "B reastfeeding".
    filler = " and has been shown to enhance health for many years in many places" * 3
    texts = " ".join(_first_lines(tmp_path, [
        "Breastfeeding is considered the ideal source of nutrition" + filler,
        "Among the studies, few were randomised" + filler,
        "A study of doulas found the same" + filler + " in a study of mothers",
    ]))
    assert "Breastfeeding" in texts and "B reastfeeding" not in texts
    assert "Among" in texts and "A mong" not in texts
    assert "A study" in texts


def test_a_title_set_over_five_lines_stays_one_heading():
    # A journal's five-line title was demoted for being a run of more than two heading lines, and
    # the authors' names under it became the H1.
    from rebind.headings import _demote_heading_bursts

    assert _demote_heading_bursts([1, 1, 1, 1, 1, 2, 0], title_level=1) == [1, 1, 1, 1, 1, 2, 0]
    assert _demote_heading_bursts([2, 2, 2, 0]) == [0, 0, 0, 0]          # a byline still is not


def _plain(text: str, box: tuple):
    from rebind.extract import TextLine

    return TextLine(text=text, page=1, bbox=box, font="F", size=10, bold=False, italic=False)


def _described(lines, bbox, elsewhere):
    """The description `describe_figures` gives one placed image, with `elsewhere` standing for
    the lines of another page of the document."""
    from rebind.captions import _document_captions
    from rebind.figures import PageFigures, describe_figures

    figs = PageFigures([("p1f0", bbox)])
    captions = _document_captions([(None, lines, False), (None, elsewhere, False)])
    describe_figures(figs, lines, {}, captions, set(), 612.0 * 792.0)
    return figs.alt["p1f0"]


def test_a_scheme_is_not_described_with_the_caption_of_the_figure_of_the_same_number():
    # A chemistry paper numbers its schemes and its figures separately. "Scheme 1." under a
    # reaction scheme is a bare label, so the caption was looked for elsewhere by number alone --
    # and the scheme was described, wrongly, as "FIG. 1. Potential energy diagram...".
    scheme = (86.0, 588.0, 528.0, 729.0)
    label = _plain("Scheme 1.", (286.0, 570.0, 328.0, 580.0))
    figure_caption = _plain("FIG. 1. Potential energy diagram for the formation of 2 from HCN.",
                            (62.0, 550.0, 552.0, 560.0))

    assert _described([label], scheme, [figure_caption]) is None


def test_a_bare_label_still_finds_its_own_caption_on_another_page():
    # What looking elsewhere is for, and it must keep working however the label is spelled.
    picture = (86.0, 588.0, 528.0, 729.0)
    label = _plain("Fig. 8 (Continued)", (86.0, 570.0, 200.0, 580.0))
    caption = _plain("Figure 8. The apparatus, with its two glass syringes.",
                     (62.0, 550.0, 552.0, 560.0))

    assert _described([label], picture, [caption]) == caption.text


def test_a_chapter_numbered_exhibit_is_matched_by_its_whole_number():
    # "Exhibit 7.5" was read as number 7, and so was "Exhibit 7.19": one exhibit took the other's
    # words.
    picture = (86.0, 588.0, 528.0, 729.0)
    label = _plain("Exhibit 7.5", (86.0, 570.0, 160.0, 580.0))
    other = _plain("Exhibit 7.19 Interview Guide for the Wilderness Program",
                   (62.0, 550.0, 552.0, 560.0))

    assert _described([label], picture, [other]) is None


def test_a_sentence_that_goes_on_about_a_figure_is_not_its_caption():
    from rebind.captions import _is_caption_marker

    assert not _is_caption_marker("Fig. 1, which is derived from the CBS-QB3 energies at 0 K.")
    assert _is_caption_marker("Figure 4a, 4b, & 4c.  Prime focus height mask.")
    assert _is_caption_marker("Fig. 9.  Denarius of C. M. Limetanus. Rome, 82 B.C.")
