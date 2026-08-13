from rebind.extract import Page, TextLine
from rebind.profile import Style, build_profile


def line(text, *, page=1, size=11.0, bold=False, y=400.0, font="DejaVuSerif"):
    return TextLine(text=text, page=page, bbox=(72.0, y, 400.0, y + size),
                    font=font, size=size, bold=bold, italic=False)


def page_of(lines, number=1):
    return Page(number=number, width=612.0, height=792.0, lines=tuple(lines), images=())


def test_body_style_is_the_highest_volume_style():
    lines = [line("body text " * 5) for _ in range(20)]
    lines.append(line("A Heading", size=24.0, bold=True))

    profile = build_profile([page_of(lines)])

    assert profile.body == Style(font="DejaVuSerif", size=11.0, bold=False, italic=False)


def test_larger_styles_become_heading_levels_ranked_by_size():
    lines = [line("body") for _ in range(20)]
    lines.append(line("Big", size=24.0, bold=True))
    lines.append(line("Medium", size=18.0, bold=True))

    profile = build_profile([page_of(lines)])

    assert profile.heading_level(Style("DejaVuSerif", 24.0, True, False)) == 1
    assert profile.heading_level(Style("DejaVuSerif", 18.0, True, False)) == 2


def test_recurring_edge_lines_are_artifacts():
    pages = []
    for number in range(1, 11):
        lines = [line("Course Catalog", page=number, y=760.0, size=9.0)]
        lines += [line("body text", page=number, y=400.0) for _ in range(10)]
        pages.append(page_of(lines, number=number))

    profile = build_profile(pages)
    header = line("Course Catalog", y=760.0, size=9.0)

    assert profile.role_of(header, page_height=792.0) == "artifact"


def test_one_page_repeated_style_lines_are_not_artifacts():
    """Recurrence must be counted across distinct pages, not lines on a single page.

    A two-line address/letterhead block sharing a style and edge band on one page must not be
    mistaken for cross-page recurrence -- there is only one page, so nothing on it can have
    recurred.
    """
    address_line_1 = line("123 Main Street", y=760.0, size=9.0)
    address_line_2 = line("Anytown, ST 00000", y=750.0, size=9.0)
    body_lines = [line("body text", y=400.0) for _ in range(10)]

    profile = build_profile([page_of([address_line_1, address_line_2] + body_lines, number=1)])

    assert profile.role_of(address_line_1, page_height=792.0) != "artifact"
    assert profile.role_of(address_line_2, page_height=792.0) != "artifact"


def test_two_page_header_once_per_page_is_still_an_artifact():
    """The per-page fix must not over-correct into never detecting artifacts on short documents."""
    pages = []
    for number in (1, 2):
        header = line("Course Catalog", page=number, y=760.0, size=9.0)
        body_lines = [line("body text", page=number, y=400.0) for _ in range(10)]
        pages.append(page_of([header] + body_lines, number=number))

    profile = build_profile(pages)
    header = line("Course Catalog", y=760.0, size=9.0)

    assert profile.role_of(header, page_height=792.0) == "artifact"


def test_body_style_recurring_at_edge_is_never_an_artifact_key():
    """Regression test for Finding 1: style + position alone must never condemn the body style.

    Every page's first line here uses the exact body style and sits in the top edge band -- the
    shape a document with generous margins (or simply no running header) produces routinely.
    Nothing here is a running header: there is no recurring *text*, just a recurring style and
    position, which is not enough to call it an artifact once the fix is in place.

    The first lines are distinct prose, which is what the docstring above always claimed and what
    real body text is. They used to be one sentence with the page number substituted in -- which
    the text-recurrence rule added later reads, correctly, as a running head, because that is
    exactly the shape of one. The style+position collision this test exists to defend is unchanged.
    """
    openers = ["Consider the harbour", "Rain fell for a week", "The treaty was signed",
               "Nobody expected the verdict", "Winter closed the pass", "She kept the ledger",
               "A second survey followed", "The mill stood empty", "Letters arrived monthly",
               "His account differs"]
    pages = []
    for number in range(1, 11):
        lines = [line(f"{openers[number - 1]}, and the account continues.",
                      page=number, y=760.0)]
        lines += [line("more body text", page=number, y=400.0 - i) for i in range(10)]
        pages.append(page_of(lines, number=number))

    profile = build_profile(pages)

    for number in range(1, 11):
        first_line = line(f"{openers[number - 1]}, and the account continues.",
                          page=number, y=760.0)
        assert profile.role_of(first_line, page_height=792.0) != "artifact", (
            f"page {number}'s first line is ordinary body text and must not be an artifact"
        )


def test_a_running_head_in_the_body_style_is_furniture():
    """The signal the style rule cannot see: the same *words* at the same page edge, page after page.

    On a scan there is only one style -- the recognizer reports a height per line, not a typeface --
    so a running head shares the body style by construction and the style rule can never condemn
    it. It was coming out as a heading on every page of a real scanned book: 29 phantom entries in
    the outline, and the chapter title read aloud before each page's first sentence.
    """
    pages = []
    for number in range(1, 11):
        # On the verso only, as a book's running head is -- the recto carries a different one. This
        # is why the threshold cannot be half the document: neither side can ever reach it.
        # The folio changes page to page; the words do not. That is what makes it a running head.
        body = [line(f"Body sentence {i} on page {number}.", page=number, y=400.0 - i * 12)
                for i in range(10)]
        head = [line(f"{number}  The Power of Images in the Age of Augustus",
                     page=number, y=760.0)] if number % 2 == 0 else []
        pages.append(page_of(head + body, number=number))

    profile = build_profile(pages)

    for number in (2, 6, 10):
        head = line(f"{number}  The Power of Images in the Age of Augustus", page=number, y=760.0)
        assert profile.role_of(head, page_height=792.0) == "artifact", number
    # ...and the body it sits above is untouched.
    body = line("Body sentence 3 on page 5.", page=5, y=400.0)
    assert profile.role_of(body, page_height=792.0) == "body"


def test_a_line_that_merely_repeats_mid_page_is_not_furniture():
    """Recurrence only counts at a page edge. A phrase that happens to repeat in the middle of the
    text is prose, and dropping it would delete real content on the strength of a coincidence."""
    pages = []
    for number in range(1, 11):
        lines = [line("The Power of Images in the Age of Augustus", page=number, y=400.0)]
        lines += [line(f"Body {i} on {number}", page=number, y=300.0 - i * 12) for i in range(10)]
        pages.append(page_of(lines, number=number))

    profile = build_profile(pages)

    repeated = line("The Power of Images in the Age of Augustus", page=5, y=400.0)
    assert profile.role_of(repeated, page_height=792.0) == "body"


def test_first_page_title_at_top_is_not_an_artifact():
    """Position alone must not condemn a line -- a title also sits at the top of the page."""
    pages = []
    title = line("The Only Title", page=1, y=760.0, size=24.0, bold=True)
    pages.append(page_of([title] + [line("body", page=1) for _ in range(10)], number=1))
    for number in range(2, 11):
        pages.append(page_of([line("body", page=number) for _ in range(10)], number=number))

    profile = build_profile(pages)

    assert profile.role_of(title, page_height=792.0) != "artifact"


def test_confidence_is_one_for_exact_body_match_and_lower_for_rare_styles():
    lines = [line("body") for _ in range(50)]
    lines.append(line("Odd", size=13.0))

    profile = build_profile([page_of(lines)])

    assert profile.confidence_for(line("body"), page_height=792.0) == 1.0
    assert profile.confidence_for(line("Odd", size=13.0), page_height=792.0) < 1.0


def test_heading_confidence_reflects_evidence_not_a_flat_value():
    """Regression test for Finding 3: `confidence_for` used to return a flat 0.9 for every
    heading style regardless of how much of the document it actually covers. A heading style
    that recurs across many pages (a real section-heading style) must score higher than one seen
    only a couple of characters anywhere in the document (a barely-evidenced guess)."""
    lines = [line("body text " * 5) for _ in range(200)]
    # A well-evidenced heading style: many section headings, each with substantial text.
    for i in range(20):
        lines.append(line(f"Section heading number {i}", size=18.0, bold=True))
    # A barely-seen heading style: appears exactly once, three characters.
    lines.append(line("Xyz", size=24.0, bold=True))

    profile = build_profile([page_of(lines)])

    well_evidenced = line("Section heading number 0", size=18.0, bold=True)
    barely_seen = line("Xyz", size=24.0, bold=True)
    assert profile.heading_level(Style("DejaVuSerif", 18.0, True, False))
    assert profile.heading_level(Style("DejaVuSerif", 24.0, True, False))

    confidence_well_evidenced = profile.confidence_for(well_evidenced, page_height=792.0)
    confidence_barely_seen = profile.confidence_for(barely_seen, page_height=792.0)

    assert confidence_well_evidenced > confidence_barely_seen
    assert 0.0 <= confidence_barely_seen <= 1.0
    assert 0.0 <= confidence_well_evidenced <= 1.0


def test_document_with_no_text_yields_no_body_style():
    profile = build_profile([page_of([])])

    assert profile.body is None
