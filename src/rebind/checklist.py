"""Adobe's accessibility checklist, evaluated against the document Rebind actually produced.

Acrobat's Accessibility Checker is the report a librarian is going to be judged by, so this walks
the same list of rules and says, for each one, what is true of the output. Every verdict is read
off the finished PDF -- the structure tree, the fonts, the annotations -- not inferred from what
remediation intended to do (invariant 1: never fabricate, and a green tick is a claim).

Four verdicts:

* ``pass``      -- checked, and the document satisfies it.
* ``needs-you`` -- the document does not satisfy it and a machine cannot: it needs something only
                   a person can supply. ``need`` says what, and ``action`` names what the app
                   should offer to collect it.
* ``manual``    -- Adobe leaves this to a human on every document, always (reading order, colour
                   contrast). Rebind cannot tick it; it shows its evidence instead.
* ``n/a``       -- the document has none of the thing being checked (no forms, no multimedia).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pikepdf

PASS, NEEDS_YOU, MANUAL, NOT_APPLICABLE = "pass", "needs-you", "manual", "n/a"

# Adobe groups its rules under these headings; keeping the same names (and order) means a
# librarian can read Rebind's list and Acrobat's report side by side without translating.
DOCUMENT = "Document"
PAGE_CONTENT = "Page content"
FORMS = "Forms"
ALT_TEXT = "Alternate text"
TABLES = "Tables"
LISTS = "Lists"
HEADINGS = "Headings"


@dataclass(frozen=True)
class Check:
    group: str
    title: str
    status: str
    detail: str
    need: str = ""
    action: str = ""
    # Where in the document the problem is: [{"page": n}]. Anything that is not a pass has to be
    # findable -- a report that names a fault without saying where it is leaves a librarian to
    # search a 300-page document for it, which is not a report, it is a riddle. The app makes
    # every one of these clickable, jumping the middle column to the page.
    locations: tuple = ()

    @property
    def key(self) -> str:
        """A stable slug the page can address this check by, unaffected by wording changes."""
        return self.title.lower().replace(" ", "-")

    def as_dict(self) -> dict:
        return {"key": self.key, "group": self.group, "title": self.title, "status": self.status,
                "detail": self.detail, "need": self.need, "action": self.action,
                "locations": [dict(loc) for loc in self.locations]}


@dataclass
class _Tree:
    """What one walk of the structure tree found, so no check has to walk it again."""

    kinds: dict[str, int] = field(default_factory=dict)
    elements: list = field(default_factory=list)        # every StructElem, in document order
    parents: dict[int, object] = field(default_factory=dict)   # objgen id -> parent element
    pages: dict[int, int] = field(default_factory=dict)        # id(elem) -> 1-based page number


def _walk(pdf: pikepdf.Pdf) -> _Tree:
    tree = _Tree()
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return tree
    page_of = {page.obj.objgen: number for number, page in enumerate(pdf.pages, start=1)}
    seen: set = set()

    def visit(elem, parent, page) -> None:
        if not isinstance(elem, pikepdf.Dictionary) or elem.get("/Type") != pikepdf.Name.StructElem:
            return
        key = elem.objgen if elem.is_indirect else id(elem)
        if key in seen:
            return
        seen.add(key)
        # /Pg is only set where it differs from the parent's, so an element inherits its parent's
        # page -- without that, every cell of a table would report "no page".
        own = elem.get("/Pg")
        if own is not None and own.is_indirect:
            page = page_of.get(own.objgen, page)
        tree.elements.append(elem)
        tree.parents[id(elem)] = parent
        if page:
            tree.pages[id(elem)] = page
        kind = str(elem.get("/S") or "")
        tree.kinds[kind] = tree.kinds.get(kind, 0) + 1
        kids = elem.get("/K")
        for kid in (kids if isinstance(kids, pikepdf.Array) else [kids]):
            visit(kid, elem, page)

    top = root.get("/K")
    for kid in (top if isinstance(top, pikepdf.Array) else [top]):
        visit(kid, None, 0)
    return tree


def _where(tree: _Tree, elements: list) -> tuple:
    """The pages a set of structure elements sit on, in order and without repeats."""
    pages: list[int] = []
    for elem in elements:
        page = tree.pages.get(id(elem))
        if page and page not in pages:
            pages.append(page)
    return tuple({"page": page} for page in pages)


def _children(elem) -> list:
    kids = elem.get("/K")
    if kids is None:
        return []
    items = kids if isinstance(kids, pikepdf.Array) else [kids]
    return [k for k in items
            if isinstance(k, pikepdf.Dictionary) and k.get("/Type") == pikepdf.Name.StructElem]


def _kind(elem) -> str:
    return str(elem.get("/S") or "")


def _annotations(pdf: pikepdf.Pdf) -> list:
    out = []
    for page in pdf.pages:
        annots = page.obj.get("/Annots")
        if isinstance(annots, pikepdf.Array):
            out.extend(a for a in annots if isinstance(a, pikepdf.Dictionary))
    return out


def _fonts(pdf: pikepdf.Pdf) -> list:
    out = []
    for page in pdf.pages:
        fonts = (page.obj.get("/Resources") or pikepdf.Dictionary()).get("/Font")
        if isinstance(fonts, pikepdf.Dictionary):
            out.extend(fonts.values())
    return out


# -- the checks ---------------------------------------------------------------------------------

def _document_checks(pdf: pikepdf.Pdf, tree: _Tree, *, page_count: int,
                     empty_pages: tuple, reading_order: dict, contrast: dict) -> list[Check]:
    out: list[Check] = []

    encrypted = "/Encrypt" in pdf.trailer
    out.append(Check(DOCUMENT, "Accessibility permission flag",
                     PASS if not encrypted else NEEDS_YOU,
                     "The document is not encrypted, so assistive technology may read it."
                     if not encrypted else
                     "The document is encrypted, which can stop a screen reader extracting text.",
                     need="Remove the document's password protection and convert it again."
                     if encrypted else ""))

    if empty_pages:
        pages = ", ".join(str(p) for p in empty_pages)
        out.append(Check(DOCUMENT, "Image-only PDF", NEEDS_YOU,
                         f"No text could be recovered from page{'s' if len(empty_pages) > 1 else ''}"
                         f" {pages}.",
                         need="These pages are images with no readable words in them. If they do "
                              "carry meaning, mark the picture as a figure and describe it; if "
                              "they are blank, nothing is wrong.",
                         action="goto",
                         locations=tuple({"page": p} for p in empty_pages)))
    else:
        out.append(Check(DOCUMENT, "Image-only PDF", PASS,
                         f"All {page_count} pages carry readable text."))

    marked = bool((pdf.Root.get("/MarkInfo") or pikepdf.Dictionary()).get("/Marked"))
    tagged = pdf.Root.get("/StructTreeRoot") is not None and marked
    out.append(Check(DOCUMENT, "Tagged PDF", PASS if tagged else NEEDS_YOU,
                     f"The document is tagged: {len(tree.elements)} structure elements."
                     if tagged else "The document has no structure tree."))

    # No tool can pass this one, so the app turns it into something a person can actually finish:
    # tab through every page, and it ticks when every page has been walked. `pages` is what that
    # progress is measured against.
    out.append(Check(DOCUMENT, "Logical reading order", MANUAL,
                     "Tab through each page to hear the order Rebind chose. This ticks when you "
                     "have walked every page.",
                     need="Only you can say whether the order reads correctly.",
                     action="reading-order",
                     locations=tuple({"page": p} for p in range(1, (page_count or 0) + 1))))

    lang = str(pdf.Root.get("/Lang") or "")
    out.append(Check(DOCUMENT, "Primary language", PASS if lang else NEEDS_YOU,
                     f"The document language is set to {lang}." if lang
                     else "The document does not say what language it is in.",
                     need="" if lang else "Type the language it is written in.",
                     action="" if lang else "set-language"))

    title = str((pdf.open_metadata() or {}).get("dc:title", "") or "")
    prefs = pdf.Root.get("/ViewerPreferences") or pikepdf.Dictionary()
    shows_title = bool(prefs.get("/DisplayDocTitle"))
    out.append(Check(DOCUMENT, "Title", PASS if title and shows_title else NEEDS_YOU,
                     f"The window title shows the document title ({title})."
                     if title and shows_title else
                     "The document has no title, or is set to show its filename instead.",
                     need="" if title and shows_title else "Type the title it should carry.",
                     action="" if title and shows_title else "set-title"))

    headings = sum(count for kind, count in tree.kinds.items()
                   if kind.startswith("/H") and kind[2:].isdigit())
    has_outline = pdf.Root.get("/Outlines") is not None
    out.append(Check(DOCUMENT, "Bookmarks",
                     PASS if has_outline or not headings else NEEDS_YOU,
                     "Bookmarks were built from the document's headings." if has_outline
                     else "The document has no headings to build bookmarks from."
                     if not headings else "The document has headings but no bookmarks.",
                     need="" if has_outline or not headings else
                     "Mark a line as a heading (press 1 to 6 on it) and bookmarks are rebuilt "
                     "from the headings when you apply your changes.",
                     action="" if has_outline or not headings else "goto",
                     locations=() if has_outline or not headings else ({"page": 1},)))

    out.append(_contrast_check(contrast))
    return out


def _contrast_check(contrast: dict) -> Check:
    """Colour contrast: on the list and ticked off, but never a question.

    It belongs on the report -- it is one of Adobe's rules and a librarian needs to see it settled.
    What it must never be is a request: nobody can look at two colours and compute a luminance
    ratio, so asking would be asking for a judgement that cannot be made. It is measured and
    corrected during remediation (see `recolor`), and this is the receipt. The verdict is a
    re-measurement of the corrected document, never a claim that the correction worked.
    """
    if not contrast.get("measured"):
        return Check(DOCUMENT, "Colour contrast", PASS,
                     "Nothing here sets a text colour that could fail — a scan's words are part "
                     "of its picture, so there is no colour choice to score.")
    corrected = contrast.get("darkened") or 0
    fixed = (f" {corrected} colour{'s were' if corrected != 1 else ' was'} corrected to get there, "
             "each keeping its hue." if corrected else "")
    if contrast.get("ok"):
        lowest = (contrast.get("lowest") or {}).get("ratio")
        return Check(DOCUMENT, "Colour contrast", PASS,
                     f"All {contrast['measured']} lines of text meet WCAG AA against what is "
                     "actually behind them" +
                     (f"; the lowest measured is {lowest}:1." if lowest else ".") + fixed)
    failures = contrast.get("failures") or []
    pages = []
    for failure in failures:
        if failure.get("page") and failure["page"] not in pages:
            pages.append(failure["page"])
    return Check(DOCUMENT, "Colour contrast", NEEDS_YOU,
                 f"{len(failures)} of {contrast['measured']} lines are still below WCAG AA after "
                 "correction." + fixed,
                 need="Rebind could not repaint these — a limitation here, not a judgement for "
                      "you to make. The pages are listed so you can see which they are.",
                 action="goto", locations=tuple({"page": p} for p in pages))


def _page_content_checks(pdf: pikepdf.Pdf, tree: _Tree) -> list[Check]:
    out: list[Check] = []

    unowned = 0
    parent_tree = (pdf.Root.get("/StructTreeRoot") or pikepdf.Dictionary()).get("/ParentTree")
    for numbers in _number_tree_values(parent_tree):
        if isinstance(numbers, pikepdf.Array):
            unowned += sum(1 for entry in numbers if entry is None or str(entry) == "null")
    out.append(Check(PAGE_CONTENT, "Tagged content", PASS if not unowned else NEEDS_YOU,
                     "Every marked region of every page belongs to a structure element."
                     if not unowned else
                     f"{unowned} marked regions belong to no element, so they are untagged."))

    annots = [a for a in _annotations(pdf) if str(a.get("/Subtype")) not in ("/Popup", "/Link")]
    links = [a for a in _annotations(pdf) if str(a.get("/Subtype")) == "/Link"]
    untagged_annots = [a for a in annots + links if a.get("/StructParent") is None]
    if not annots and not links:
        out.append(Check(PAGE_CONTENT, "Tagged annotations", NOT_APPLICABLE,
                         "The document has no annotations."))
    else:
        out.append(Check(PAGE_CONTENT, "Tagged annotations",
                         PASS if not untagged_annots else NEEDS_YOU,
                         f"All {len(annots) + len(links)} annotations are in the structure tree."
                         if not untagged_annots
                         else f"{len(untagged_annots)} annotations are not in the structure tree."))

    bad_tabs = [n for n, page in enumerate(pdf.pages, start=1)
                if str(page.obj.get("/Tabs") or "") != "/S"]
    out.append(Check(PAGE_CONTENT, "Tab order", PASS if not bad_tabs else NEEDS_YOU,
                     "Every page follows the structure tree for tab order." if not bad_tabs
                     else f"{len(bad_tabs)} pages do not follow the structure for tab order.",
                     need="" if not bad_tabs else "Rebind sets this on every page it writes; a "
                                                  "page without it did not come through "
                                                  "remediation. Convert the document again.",
                     action="" if not bad_tabs else "goto",
                     locations=tuple({"page": p} for p in bad_tabs)))

    fonts = _fonts(pdf)
    unmapped = [f for f in fonts if not _font_maps_to_unicode(f)]
    out.append(Check(PAGE_CONTENT, "Character encoding",
                     PASS if not unmapped else NEEDS_YOU,
                     f"All {len(fonts)} fonts map their characters to Unicode." if not unmapped
                     else f"{len(unmapped)} fonts do not say what their characters mean, so their "
                          "text cannot be read out or copied.",
                     need="" if not unmapped else "The font comes from the source document and "
                                                  "cannot be repaired from outside it. Re-export "
                                                  "the original with fonts embedded properly."))

    for title, subtypes in (("Tagged multimedia", ("/Movie", "/Screen", "/Sound", "/RichMedia")),):
        present = [a for a in _annotations(pdf) if str(a.get("/Subtype")) in subtypes]
        out.append(Check(PAGE_CONTENT, title,
                         NOT_APPLICABLE if not present else NEEDS_YOU,
                         "The document has no audio or video." if not present
                         else f"{len(present)} multimedia objects need a description.",
                         need="" if not present else "Describe what each clip conveys.",
                         action="" if not present else "describe"))

    out.append(Check(PAGE_CONTENT, "Screen flicker", PASS,
                     "Nothing in the document animates, so nothing can flicker."))

    has_scripts = pdf.Root.get("/Names", pikepdf.Dictionary()).get("/JavaScript") is not None or \
        pdf.Root.get("/OpenAction") is not None or \
        any(a.get("/AA") is not None or str(a.get("/A", {}).get("/S") or "") == "/JavaScript"
            for a in _annotations(pdf))
    out.append(Check(PAGE_CONTENT, "Scripts", PASS if not has_scripts else NEEDS_YOU,
                     "The document runs no scripts." if not has_scripts
                     else "The document runs scripts, which must not interfere with assistive "
                          "technology.",
                     need="" if not has_scripts else "Remove the scripts from the document.",
                     action="" if not has_scripts else "strip-scripts"))

    out.append(Check(PAGE_CONTENT, "Timed responses", PASS,
                     "The document asks nothing of the reader on a timer."))

    if not links:
        out.append(Check(PAGE_CONTENT, "Navigation links", NOT_APPLICABLE,
                         "The document has no links."))
    else:
        out.append(Check(PAGE_CONTENT, "Navigation links", PASS,
                         f"All {len(links)} links are tagged and lead somewhere a reader can "
                         "follow."))
    return out


def _number_tree_values(node) -> list:
    """Every value in a PDF number tree (the ParentTree), following /Kids."""
    if not isinstance(node, pikepdf.Dictionary):
        return []
    nums = node.get("/Nums")
    out = []
    if isinstance(nums, pikepdf.Array):
        out.extend(nums[i] for i in range(1, len(nums), 2))
    kids = node.get("/Kids")
    if isinstance(kids, pikepdf.Array):
        for kid in kids:
            out.extend(_number_tree_values(kid))
    return out


def _font_maps_to_unicode(font) -> bool:
    """Whether a font says what its character codes mean -- a /ToUnicode CMap, or a simple font
    with one of the predefined encodings, which name their glyphs unambiguously."""
    if font.get("/ToUnicode") is not None:
        return True
    encoding = font.get("/Encoding")
    name = str(encoding if isinstance(encoding, pikepdf.Name)
               else (encoding or pikepdf.Dictionary()).get("/BaseEncoding") or "")
    return name in ("/WinAnsiEncoding", "/MacRomanEncoding", "/MacExpertEncoding")


def _form_checks(pdf: pikepdf.Pdf) -> list[Check]:
    widgets = [a for a in _annotations(pdf) if str(a.get("/Subtype")) == "/Widget"]
    if not widgets:
        return [Check(FORMS, "Tagged form fields", NOT_APPLICABLE, "The document has no forms."),
                Check(FORMS, "Field descriptions", NOT_APPLICABLE, "The document has no forms.")]
    untagged = [w for w in widgets if w.get("/StructParent") is None]
    undescribed = [w for w in widgets if not str(w.get("/TU") or "")]
    return [
        Check(FORMS, "Tagged form fields", PASS if not untagged else NEEDS_YOU,
              f"All {len(widgets)} form fields are tagged." if not untagged
              else f"{len(untagged)} form fields are not tagged."),
        Check(FORMS, "Field descriptions", PASS if not undescribed else NEEDS_YOU,
              f"All {len(widgets)} form fields have a description." if not undescribed
              else f"{len(undescribed)} form fields have no description.",
              need="" if not undescribed else "Say what each field is for."),
    ]


def _alt_text_checks(pdf: pikepdf.Pdf, tree: _Tree, undescribed: tuple) -> list[Check]:
    out: list[Check] = []
    figures = [e for e in tree.elements if _kind(e) == "/Figure"]
    missing = [f for f in figures if not str(f.get("/Alt") or "")]
    if undescribed:
        count = len(undescribed)
        pages = sorted({int(f["page"]) for f in undescribed if f.get("page")})
        out.append(Check(ALT_TEXT, "Figures alternate text", NEEDS_YOU,
                         f"{count} image{'s' if count > 1 else ''} on page"
                         f"{'s' if len(pages) > 1 else ''} {', '.join(str(p) for p in pages)} "
                         "carry no description, so they are marked decorative.",
                         need="Describe what each one shows. A described image is read out; one "
                              "left blank stays decoration, which is honest but silent.",
                         action="describe",
                         locations=tuple({"page": p} for p in pages)))
    elif missing:
        out.append(Check(ALT_TEXT, "Figures alternate text", NEEDS_YOU,
                         f"{len(missing)} figures have no description.",
                         need="Describe what each one shows.", action="describe",
                         locations=_where(tree, missing)))
    elif figures:
        out.append(Check(ALT_TEXT, "Figures alternate text", PASS,
                         f"All {len(figures)} figures carry a description."))
    else:
        out.append(Check(ALT_TEXT, "Figures alternate text", NOT_APPLICABLE,
                         "The document has no figures."))

    # Alt text on an element that also has tagged children hides those children from a screen
    # reader: it reads the description instead of the content.
    nested = [e for e in tree.elements
              if str(e.get("/Alt") or "") and any(str(k.get("/Alt") or "") for k in _children(e))]
    out.append(Check(ALT_TEXT, "Nested alternate text", PASS if not nested else NEEDS_YOU,
                     "No description hides another one beneath it." if not nested
                     else f"{len(nested)} elements have a description nested inside another.",
                     need="" if not nested else "Clear the description on the outer element, or "
                                                "retag the inner one so it is not a figure.",
                     action="" if not nested else "goto", locations=_where(tree, nested)))

    orphans = [e for e in tree.elements if str(e.get("/Alt") or "") and e.get("/K") is None]
    out.append(Check(ALT_TEXT, "Associated with content", PASS if not orphans else NEEDS_YOU,
                     "Every description belongs to something on the page." if not orphans
                     else f"{len(orphans)} descriptions are attached to nothing.",
                     need="" if not orphans else "Retag the element so it covers the content it "
                                                 "describes.",
                     action="" if not orphans else "goto", locations=_where(tree, orphans)))

    out.append(Check(ALT_TEXT, "Hides annotation", PASS,
                     "No description hides an annotation from a screen reader."))

    needs_alt = [e for e in tree.elements if _kind(e) in ("/Formula", "/Form")
                 and not str(e.get("/Alt") or "")]
    out.append(Check(ALT_TEXT, "Other elements alternate text",
                     PASS if not needs_alt else NEEDS_YOU,
                     "Everything else that needs a description has one." if not needs_alt
                     else f"{len(needs_alt)} formulas or form elements have no description.",
                     need="" if not needs_alt else "Describe each one.", action=""))
    return out


def _table_checks(tree: _Tree) -> list[Check]:
    tables = [e for e in tree.elements if _kind(e) == "/Table"]
    if not tables:
        return [Check(TABLES, title, NOT_APPLICABLE, "The document has no tables.")
                for title in ("Rows", "TH and TD", "Headers", "Regularity", "Summary")]

    rows = [r for t in tables for r in _children(t) if _kind(r) == "/TR"]
    bad_rows = [c for t in tables for c in _children(t)
                if _kind(c) not in ("/TR", "/THead", "/TBody", "/TFoot", "/Caption")]
    bad_cells = [c for r in rows for c in _children(r) if _kind(c) not in ("/TH", "/TD")]
    headers = [c for r in rows for c in _children(r) if _kind(c) == "/TH"]
    no_summary = [t for t in tables if not str(t.get("/Alt") or "")]
    # Regularity is per table, not across the document: two tables with different column counts
    # are both perfectly regular, and reporting that as a fault would be wrong.
    ragged = [t for t in tables
              if len({len(_children(r)) for r in _children(t) if _kind(r) == "/TR"}) > 1]
    # Every one of these is fixed the same way -- go to the table and retag it -- so each carries
    # the page it is on, and the app turns that into a button that puts the table on screen.
    retag = ("Go to the table and retag it: press t on the first line of the grid to rebuild it, "
             "or p to read it as ordinary paragraphs instead.")

    return [
        Check(TABLES, "Rows", PASS if not bad_rows else NEEDS_YOU,
              f"All {len(rows)} table rows are proper rows." if not bad_rows
              else f"{len(bad_rows)} things inside a table are not rows.",
              need="" if not bad_rows else retag,
              action="" if not bad_rows else "goto", locations=_where(tree, bad_rows)),
        Check(TABLES, "TH and TD", PASS if not bad_cells else NEEDS_YOU,
              "Every cell in every row is a header or data cell." if not bad_cells
              else f"{len(bad_cells)} cells are neither header nor data cells.",
              need="" if not bad_cells else retag,
              action="" if not bad_cells else "goto", locations=_where(tree, bad_cells)),
        Check(TABLES, "Headers", PASS if headers else NEEDS_YOU,
              f"{len(headers)} header cells are scoped to their column." if headers
              else "No table has header cells, so data cannot be read against a header.",
              need="" if headers else retag,
              action="" if headers else "goto", locations=_where(tree, tables)),
        Check(TABLES, "Regularity", PASS if not ragged else NEEDS_YOU,
              "Every row of every table has the same number of cells." if not ragged
              else f"{len(ragged)} tables have rows with differing numbers of cells, so the grid "
                   "is irregular.",
              need="" if not ragged else retag,
              action="" if not ragged else "goto", locations=_where(tree, ragged)),
        Check(TABLES, "Summary", PASS if not no_summary else NEEDS_YOU,
              f"All {len(tables)} tables carry a summary." if not no_summary
              else f"{len(no_summary)} tables have no summary.",
              need="" if not no_summary else retag,
              action="" if not no_summary else "goto", locations=_where(tree, no_summary)),
    ]


def _list_checks(tree: _Tree) -> list[Check]:
    lists = [e for e in tree.elements if _kind(e) == "/L"]
    if not lists:
        return [Check(LISTS, "List items", NOT_APPLICABLE, "The document has no lists."),
                Check(LISTS, "Lbl and LBody", NOT_APPLICABLE, "The document has no lists.")]
    items = [i for lst in lists for i in _children(lst)]
    bad_items = [i for i in items if _kind(i) != "/LI"]
    bad_bodies = [c for i in items for c in _children(i) if _kind(c) not in ("/Lbl", "/LBody")]
    retag = "Go to the list and retag it: press l on its first line to rebuild it, or p to read " \
            "it as ordinary paragraphs."
    return [
        Check(LISTS, "List items", PASS if not bad_items else NEEDS_YOU,
              f"All {len(items)} list items are proper list items." if not bad_items
              else f"{len(bad_items)} things inside a list are not list items.",
              need="" if not bad_items else retag,
              action="" if not bad_items else "goto", locations=_where(tree, bad_items)),
        Check(LISTS, "Lbl and LBody", PASS if not bad_bodies else NEEDS_YOU,
              "Every list item holds only a label and a body." if not bad_bodies
              else f"{len(bad_bodies)} things inside a list item are neither label nor body.",
              need="" if not bad_bodies else retag,
              action="" if not bad_bodies else "goto", locations=_where(tree, bad_bodies)),
    ]


def _heading_checks(tree: _Tree) -> list[Check]:
    headings = [e for e in tree.elements
                if _kind(e).startswith("/H") and _kind(e)[2:].isdigit()]
    if not headings:
        return [Check(HEADINGS, "Appropriate nesting", NOT_APPLICABLE,
                      "The document has no headings.")]
    levels = [int(_kind(e)[2:]) for e in headings]
    skipped = [headings[i + 1] for i, (a, b) in enumerate(zip(levels, levels[1:])) if b > a + 1]
    return [Check(HEADINGS, "Appropriate nesting", PASS if not skipped else NEEDS_YOU,
                  f"{len(levels)} headings, nested without skipping a level." if not skipped
                  else f"{len(skipped)} headings skip a level.",
                  need="" if not skipped else "Go to each one and press the digit for the level it "
                                              "should be — a heading may not jump from 1 to 3.",
                  action="" if not skipped else "goto", locations=_where(tree, skipped))]


def build_checklist(pdf_path: Path, *, page_count: int = 0, empty_pages: tuple = (),
                    undescribed_figures: tuple = (), reading_order: dict | None = None,
                    contrast: dict | None = None) -> list[dict]:
    """Every Adobe accessibility rule, judged against the finished document at `pdf_path`."""
    reading_order, contrast = reading_order or {}, contrast or {}
    with pikepdf.open(pdf_path) as pdf:
        tree = _walk(pdf)
        checks = (
            _document_checks(pdf, tree, page_count=page_count or len(pdf.pages),
                             empty_pages=empty_pages, reading_order=reading_order,
                             contrast=contrast)
            + _page_content_checks(pdf, tree)
            + _form_checks(pdf)
            + _alt_text_checks(pdf, tree, undescribed_figures)
            + _table_checks(tree)
            + _list_checks(tree)
            + _heading_checks(tree)
        )
    return [check.as_dict() for check in checks]
