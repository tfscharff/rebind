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


def test_a_caption_does_not_run_on_into_the_wider_column_of_text_under_it():
    # A book's caption set in the margin beside its picture, in a narrow measure, with the body
    # text starting eight points under its last line: the description ran on through the whole
    # column -- "...(H: ca. 1.20 m). powerful impression (fig. 59). The original structure was...".
    from rebind.captions import _side_captions

    picture = (683.0, 366.0, 902.0, 619.0)
    lines = [
        _plain("Fig. 60.  Head of a co-", (583, 407, 673, 416)),
        _plain("lossal statue of Octa-", (583, 396, 668, 405)),
        _plain("vian, with Baroque ad-", (583, 384, 675, 394)),
        _plain("ditions to the hair (H:", (583, 373, 672, 384)),
        _plain("ca. 1.20 m).", (583, 360, 633, 371)),
        _plain("powerful impression (fig.  59). The original structure was eighty-seven me-",
               (582, 335, 911, 352)),
        _plain("ters wide and nearly forty meters high. It consisted of two concentric cylin-",
               (582, 322, 910, 339)),
    ]
    [(text, used)] = _side_captions(lines, picture)
    assert text == ("Fig. 60.  Head of a colossal statue of Octavian, with Baroque additions to "
                    "the hair (H: ca. 1.20 m).")
    assert len(used) == 5


def test_a_caption_does_not_take_the_page_footer_set_off_to_one_side_under_it():
    from rebind.captions import _figure_caption

    picture = (100.0, 121.0, 530.0, 444.0)
    lines = [
        _plain("Figure 2.  Rotating section of the K-coronameter.  The prime focus solar image",
               (96, 104, 527, 113)),
        _plain("relayed to the line array detector, I3.  The rotating retarder creates a",
               (96, 93, 527, 103)),
        _plain("sends two beams with polarization modulation of opposite phases.",
               (96, 82, 444, 91)),
        _plain("Proc. of SPIE Vol. 4843     67", (445, 67, 549, 75)),
    ]
    assert _figure_caption(lines, picture).endswith("of opposite phases.")


def test_a_label_on_a_line_of_its_own_still_takes_the_wider_caption_under_it():
    from rebind.captions import _figure_caption

    picture = (100.0, 300.0, 500.0, 600.0)
    lines = [
        _plain("Fig. 8", (100, 284, 130, 294)),
        _plain("Schematic of the apparatus, with the pump connected via silicone tubing",
               (100, 272, 500, 282)),
        _plain("through two valves to the reservoir, whose level a float keeps steady.",
               (100, 260, 495, 270)),
        _plain("Arrows give the direction of flow.", (100, 248, 280, 258)),
    ]
    assert _figure_caption(lines, picture).endswith("Arrows give the direction of flow.")


def test_a_caption_set_over_the_blank_foot_of_its_own_image_is_found():
    # A placed image is the whole bitmap, white margin and all, and this paper set each caption
    # over the blank strip at the foot of its figure's bitmap: no line was *below* the picture.
    picture = (105.0, 469.0, 558.0, 777.0)
    lines = [
        _plain("MK4 K-CORONAMETER", (259, 719, 340, 725)),
        _plain("MANGIN MIRROR", (452, 516, 503, 522)),
        _plain("Figure 1. Mk4 K-coronameter.  Optics from the objective through calibration tilt",
               (98, 487, 529, 496)),
        _plain("spar.  Optics from the occulting disk to the detectors are mounted in a rotating",
               (98, 477, 529, 486)),
        _plain("in azimuth around the Sun.", (98, 467, 198, 476)),
        _plain("OCCULTER, I1", (128, 356, 170, 362)),
    ]
    assert _described(lines, picture, []) == (
        "Figure 1. Mk4 K-coronameter.  Optics from the objective through calibration tilt "
        "spar.  Optics from the occulting disk to the detectors are mounted in a rotating "
        "in azimuth around the Sun.")


def test_a_figure_number_lettered_in_the_middle_of_a_picture_is_not_its_caption():
    picture = (100.0, 200.0, 500.0, 700.0)
    lines = [
        _plain("Figure 3 of the original report, redrawn here", (150, 440, 420, 450)),
        _plain("ROTATING RETARDER", (160, 300, 230, 306)),
    ]
    assert _described(lines, picture, []) is None


def test_a_caption_over_the_foot_of_an_image_is_not_held_out_as_its_lettering():
    from rebind.figures import _figure_text

    picture = (105.0, 469.0, 558.0, 777.0)
    label = _plain("MANGIN MIRROR", (452, 516, 503, 522))
    caption = _plain("Figure 1. The optics of the Mk4 K-coronameter, drawn to scale.",
                     (98, 472, 400, 481))
    assert _figure_text([label, caption], picture) == [label]
