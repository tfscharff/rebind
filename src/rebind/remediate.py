"""Remediate a PDF: keep the original appearance, add accessibility (PDF/UA structure).

Each source page is kept as it is -- its own drawing marked as an *artifact* (what the reader
sees) -- with an *invisible*, *tagged* text layer placed over it: the words drawn in render mode 3
(never printed) at their positions, wrapped in marked content and referenced from a PDF/UA
structure tree. The text comes from the page's own text layer where it has one, or from OCR where
it does not. A page is redrawn from a 300-DPI picture of itself only where conformance leaves no
other way (`overlay._rebuild_page` and the three tests beside its call).

The output looks like the input but is now readable by assistive technology, with document
language, title, tags and reading order set. This is remediation, not reconstruction: the page is
never reflowed or restyled.

This module runs the pipeline; each stage lives in its own module:

* `headings` -- which lines are headings, and at what level;
* `figures` -- the pictures on a page and the text that belongs to each; `captions` -- the
  document's own words for them;
* `plan` -- a page's elements (paragraphs, lists, tables) and the person's frames applied on top;
  `tables` -- a table tagged cell by cell;
* `edits` -- the person's corrections, and the element records the editor shows;
* `overlay` -- the invisible tagged text layer drawn over each page;
* `structure` -- the structure tree; `document` -- document-wide fixes and metadata.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import pikepdf
from pikepdf import Name

from . import contrast, formula_reader, layout_model, recolor, review
from .captions import _caption_groups, _document_captions
from .document import _set_metadata, _strip_legacy_destinations, _strip_scripts
from .edits import (
    READ_FIRST,
    Edits,
    _element_records,
    _is_figure_id,
    _place_figures,
    _record_box,
    _records_in_reading_order,
    _untagged_record,
)
from .existing import (
    Described,
    adopt_described_figures,
    existing_descriptions,
    formula_description,
    keep_original_drawing,
    original_of,
    page_drawing,
    save_corrections,
    saved_corrections,
)
from .extract import TextLine, extract_pages
from .figures import (
    PageFigures,
    _figure_anchor,
    _is_full_page_scan,
    _is_speck,
    _margin_furniture,
    _page_figures,
    _vector_figures,
    describe_figures,
    find_figures,
)
from .glyphs import repair_fonts, same_fonts
from .headings import _structure_roles
from .layout import order_page
from .ocr import OcrEngine, recognize, render_page_to_image
from .overlay import (
    OverlayFont,
    _add_font,
    _add_xobject,
    _crop_data_uri,
    _draw_line,
    _figure_xobject,
    _has_marked_content,
    _has_unembedded_font,
    _has_unmappable_glyphs,
    _invisible_text_pages,
    _pieces,
    _png,
    _rebuild_page,
    _strip_invisible_text,
    _tagged_text_stream,
)
from .pagecache import PageCache, file_key
from .plan import _apply_frames, _frame_in_points, _merge_bare_markers, plan_page
from .profile import build_profile
from .structure import StructTree
from .turn import move_box, restore_turns, upright
from .validate import self_check_pdf_ua2

# The page editor shows each page as a picture -- elements are selected on it and their text read
# from it -- rendered once per page at this resolution.
EDITOR_PAGE_DPI = 110
EDITOR_PAGE_PX = 900

# Worked out once per page of a file and reused by every rebuild (`pagecache`): the recognised text
# of a scanned page, and the picture of each page the editor lays its boxes over.
_recognised = PageCache()
_editor_pictures = PageCache()


# A document's pages as read from its text layer, kept while the file is unchanged: every saved
# correction rebuilds the document, and reading a long born-digital file again each time was the
# largest part of the wait. A handful of documents, since each is every line of every page.
_extracted = PageCache(limit=4)


def _pages_of(source: Path) -> list:
    stamp = file_key(source)
    key = stamp and stamp + ("pages",)
    pages = _extracted.get(key)
    if pages is None:
        pages = _extracted.put(key, tuple(extract_pages(source)))
    return list(pages)


@dataclass
class RemediationResult:
    pdf_path: Path
    page_count: int
    ocr_pages: tuple[int, ...] = ()          # pages we recognized (text may contain OCR errors)
    empty_pages: tuple[int, ...] = ()         # scanned pages where OCR recovered nothing
    added_text_layer: bool = False
    # Figures with no description yet: each is {"id", "page", "thumb"} (a small preview data URI).
    # Give a figure a description and re-run with alt_texts to promote it to a tagged /Figure.
    figures: tuple[dict, ...] = ()
    # A fast, dependency-free structural sanity check on the output (validate.self_check_pdf_ua2)
    # -- not independent conformance validation (that's veraPDF, dev/CI-only). Lets the app show an
    # honest "PDF/UA-2 tagged" badge without a JVM runtime dependency.
    structure_ok: bool = True
    structure_issues: tuple[str, ...] = ()
    # Evidence for the two checks Adobe's checker always leaves to a human (see `review`). Rebind
    # cannot make either pass -- no tool can -- but it can show its work: `reading_order` is the
    # order it chose, with the pages where that was a real decision flagged for an eye;
    # `contrast` is a measurement of the rendered page, not a claim about it.
    reading_order: dict = field(default_factory=dict)
    contrast: dict = field(default_factory=dict)
    # Every tagged element, in reading order, for the app's page editor: {id, page, kind, text,
    # left/top/width/height as percentages of the page}. A figure additionally carries "alt".
    elements: tuple[dict, ...] = ()
    # A rendered picture per page (PNG bytes), so the editor can lay elements over the page.
    page_images: dict = field(default_factory=dict)


def _lines_for(source: Path, src_page, engine: OcrEngine, dpi: int, profile,
               known_pictures: tuple = (), reread: bool = False,
               ) -> tuple[list[TextLine], bool, object, layout_model.PageHints]:
    """The page's text lines in reading order, whether OCR was used, the page's layout, and what
    the layout model saw on it (`layout_model.hints_for`; empty when there is no model).
    `known_pictures` are boxes a person or the document itself has said hold a picture.
    `reread` is for a scan that came with an OCR layer of its own: it is recognised again, and what
    that layer missed is added to it (`_missed_by`).

    Reading order comes from `layout.order_page`'s recursive XY-cut, NOT from a top-to-bottom sort.
    On a single-column page the two agree. On a two-column page they emphatically do not: a
    top-to-bottom sort interleaves the columns line by line, so a screen reader reads "left line 1,
    right line 1, left line 2, ..." -- word salad, and precisely the failure Adobe's "Logical
    Reading Order" check exists to catch. The cut was written and tested long before this, but the
    pipeline was still sorting by y; that gap is what `review.page_order` now reports on.
    """
    image = None

    def recognised() -> list[TextLine]:
        # Recognised once per page: a rebuild for a saved correction reads the same scan again.
        nonlocal image
        stamp = file_key(source)
        key = stamp and stamp + (src_page.number, dpi)
        found = _recognised.get(key)
        if found is None:
            image = render_page_to_image(source, src_page.number, dpi=dpi)
            found = _recognised.put(key, recognize(
                image, page_number=src_page.number, page_width=src_page.width,
                page_height=src_page.height, engine=engine))
        return list(found)

    if src_page.has_text_layer:
        lines = list(src_page.lines)
        used_ocr = False
        if reread:
            missed = _missed_by(lines, recognised())
            lines += missed
            used_ocr = bool(missed)
    else:
        lines = recognised()
        used_ocr = True
    page = replace(src_page, lines=tuple(lines))
    # Figure regions are found before ordering, not after, so a diagram's callout labels can be
    # held out of the column cut (see order_page) instead of being read as column structure.
    boxes = _page_figures(page)
    boxes += [(fid, bbox) for fid, bbox, _cap in _vector_figures(page, lines, boxes)]
    regions = layout_model.page_regions(source, src_page.number, src_page.width,
                                        src_page.height, image)
    layout = order_page(page, profile, tuple(bbox for _fid, bbox in boxes) + known_pictures,
                        layout_model.hints_for(lines, regions))
    ordered = _merge_bare_markers([placed.line for placed in layout.lines])
    # Hints are keyed by line identity, and merging a bare bullet into its item makes a new line.
    return ordered, used_ocr, layout, layout_model.hints_for(ordered, regions)


# A recognised line is already in a scan's own OCR layer when a line of that layer covers this much
# of it (or it of the layer's line).
ALREADY_READ_SHARE = 0.3
MISSED_MIN_CONFIDENCE = 0.8


def _missed_by(layer: list[TextLine], recognised: list[TextLine]) -> list[TextLine]:
    """The lines Rebind's recogniser found on a scan that the scan's own OCR layer does not have.

    An OCR layer that arrives with a scan is kept, since it may well be better than Rebind's. But
    it is not always whole: a graphic novel's came with every caption box and not one speech
    balloon drawn over the artwork, and trusting it left the balloons unread."""
    def shared(a, b) -> float:
        w = min(a[2], b[2]) - max(a[0], b[0])
        h = min(a[3], b[3]) - max(a[1], b[1])
        if w <= 0 or h <= 0:
            return 0.0
        smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
        return w * h / smaller if smaller > 0 else 0.0

    # Only what was read with confidence and says something: on artwork the layer rightly left
    # alone, the recogniser also finds hatching it reads as "~ .!.".
    return [line for line in recognised
            if (line.ocr_confidence or 0.0) >= MISSED_MIN_CONFIDENCE
            and sum(ch.isalnum() for ch in line.text) >= 2
            and not any(shared(line.bbox, have.bbox) > ALREADY_READ_SHARE for have in layer)]


@dataclass
class _LineSort:
    """Where each of a page's lines went before anything is drawn. Three kinds, and only one of
    them is the document's content:

    * page furniture (running head, footer, folio) -> an /Artifact, never tagged content;
    * text belonging to a described figure -> drawn INSIDE that figure's marked content, so the
      figure is one thing in the reading order rather than a picture plus a scatter of loose labels
      ("A", "B", "3 mm") a screen reader would read out as if it were prose;
    * everything else -> ordinary content, tagged with its own MCID.
    """

    line_ids: list[str]               # an id for every line, tagged or not
    owner_figure: dict[int, int]      # id(line) -> index into the described figures
    inside_undescribed: set[int]      # id(line) of labels held out with an undescribed figure
    specks: set[int]                  # id(line) of marks that are not read or listed at all
    promoted: set[int]                # line indices the person made content
    frames: dict[str, tuple]          # frames the person drew on this page, in points
    is_artifact: list[bool]


def _sort_lines(src_page, lines: list[TextLine], hints: layout_model.PageHints,
                figs: PageFigures, edits: Edits, profile, scanned: bool) -> _LineSort:
    owner_figure: dict[int, int] = {}
    for k, (fid, fbox) in enumerate(figs.described):
        for line in figs.text_of(lines, fid, fbox):
            owner_figure[id(line)] = k
    # A figure with no description yet stays a decorative artifact (tagging it without an /Alt
    # is a conformance failure), but its own callout labels still belong to it. Left loose they
    # became elements in their own right -- "A", "B", "3 mm" tagged as paragraphs and read out
    # as if they were prose, which is how a picture ends up in the reading order as a scatter
    # of fragments. They are held out here and drawn as artifacts with the picture.
    inside_undescribed: set[int] = set()
    for fid, fbox in figs.undescribed:
        for line in figs.text_of(lines, fid, fbox):
            if id(line) not in owner_figure:
                inside_undescribed.add(id(line))
    # An id for every line on the page, whether or not it ends up tagged. Giving a tag to a line
    # Rebind set aside is how an element is *added*: the exact inverse of removing one, and the
    # answer to "this running head really is a heading" or "that label inside the figure needs
    # reading". Anything with a tag override is content, whatever it would otherwise have been.
    line_ids = [f"p{src_page.number}n{index}" for index in range(len(lines))]
    promoted = {index for index, key in enumerate(line_ids) if key in edits.tags}
    # A frame drawn on this page reaches lines Rebind set aside as well: drawing one over a
    # running head or a stray label is how it gets read, the same as tagging it. A line inside
    # a described picture stays the picture's.
    page_key = re.compile(rf"p{src_page.number}[a-z]")
    # A picture's frame is its box, not a claim on the lines inside it (`_framed_pictures`).
    frames = {key: _frame_in_points(value, src_page.width, src_page.height)
              for key, value in edits.frames.items()
              if page_key.match(key) and not _is_figure_id(key)}
    for index, ln in enumerate(lines):
        if id(ln) in owner_figure:
            continue
        cx, cy = (ln.bbox[0] + ln.bbox[2]) / 2, (ln.bbox[1] + ln.bbox[3]) / 2
        if any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in frames.values()):
            promoted.add(index)
    # Specks: what a recogniser makes of dirt on the scan. Not read, and not listed in the
    # editor either -- a page of them was a page of Tab stops. A frame drawn over one still
    # brings it back, like any line Rebind set aside.
    specks = {id(ln) for ln in lines if scanned and _is_speck(ln, hints)}
    # Text turned sideways in a margin (a download stamp, an archive identifier) and icon
    # glyphs with no characters a reader could say are furniture too.
    specks |= {id(ln) for ln in _margin_furniture(lines, src_page.width, src_page.height)}
    is_artifact = [
        index not in promoted
        and id(ln) not in owner_figure
        and (id(ln) in inside_undescribed or id(ln) in hints.furniture or id(ln) in specks
             or profile.role_of(ln, page_height=src_page.height) == "artifact")
        for index, ln in enumerate(lines)
    ]
    return _LineSort(line_ids, owner_figure, inside_undescribed, specks, promoted, frames,
                     is_artifact)


def _framed_pictures(figs: PageFigures, frames: dict[str, tuple], src_page) -> None:
    """Set each picture a person framed to that box. It is a picture's whole extent: what it crops
    and what it holds, in place of the guess."""
    for index, (fid, _bbox) in enumerate(figs.boxes):
        if fid in frames:
            figs.boxes[index] = (fid, _frame_in_points(frames[fid], src_page.width,
                                                       src_page.height))
            figs.framed.add(fid)


def _read_after_pictures(after: dict[str, str], figs: PageFigures, lines: list[TextLine],
                         content_lines: list[TextLine], plan: list[dict],
                         owner_figure: dict[int, int]) -> dict[str, str]:
    """Where each element inserted after a picture goes in the plan: after the element the picture
    itself is read after (or `READ_FIRST`). The picture cannot be named as the place, because a
    picture is not an element of the plan; named anyway, the inserted element went back to where its
    lines sit on the page the moment the page was rebuilt."""
    boxes = dict(figs.boxes)
    described = [fid for fid, _bbox in figs.described]
    position = {id(line): index for index, line in enumerate(content_lines)}
    at = [position.get(id(line)) for line in lines]
    resolved: dict[str, str] = {}
    for inserted, anchor in after.items():
        if anchor not in boxes:
            continue
        before = _figure_anchor(lines, at, described.index(anchor) if anchor in described else -1,
                                owner_figure, boxes[anchor])
        prior = [entry for entry in plan
                 if before is None or entry["first"] < before]
        resolved[inserted] = prior[-1]["id"] if prior else READ_FIRST
    return resolved


def _page_plan(src_page, lines: list[TextLine], page_roles: list[str],
               hints: layout_model.PageHints, figs: PageFigures, sort: _LineSort, edits: Edits,
               described: list[Described] | tuple = ()):
    """The page's content lines in reading order, the source index of each, and the plan of
    elements over them -- the page's structural judgement, then the person's corrections on top.
    Also the table rows the person took out, listed so they can be put back."""
    # A caption Rebind was willing to use as a picture's description is, self-evidently, a
    # caption -- so it is tagged as one rather than left as an ordinary paragraph. The text
    # already had to be recognised to become the /Alt; not carrying that through to the tag
    # threw the knowledge away at the last step, and a screen reader met the document's own
    # captions as unremarkable prose sitting between the paragraphs.
    # A table's title, as the layout model boxed it ("Table 2 The descriptives of ..."), is a
    # caption too. Without it the title's first line was read as the table's first row and
    # the rest of it as two stray paragraphs beside the table.
    caption_blocks = list(figs.caption_blocks)
    already = {id(line) for block in caption_blocks for line in block}
    for region in sorted(set(hints.captions.values())):
        block = [line for line in lines if hints.captions.get(id(line)) == region
                 and id(line) not in already and id(line) not in sort.owner_figure]
        if block:
            caption_blocks.append(block)
    caption_of = {id(line): group for group, block in enumerate(caption_blocks) for line in block}

    content_lines: list[TextLine] = []
    content_roles: list[str] = []
    content_source: list[int] = []      # content index -> index into `lines`
    for index, (line, role, artifact) in enumerate(zip(lines, page_roles, sort.is_artifact)):
        if artifact or (id(line) in sort.owner_figure and index not in sort.promoted):
            continue
        content_lines.append(line)
        # A displayed equation the layout model boxed is a formula, not a paragraph. Its text is
        # still what the page says; a description in words is the person's to add.
        content_roles.append(
            "Caption" if id(line) in caption_of and role == "P"
            else "Formula" if id(line) in hints.formulas and role == "P" else role)
        content_source.append(index)

    caption_groups = _caption_groups(content_lines, content_roles, caption_of, caption_blocks,
                                     set(hints.blocks))

    # Ids are keyed to the *source* line index, not to a position in the element list, so they
    # survive a re-run in which earlier elements were retagged, merged or removed.
    plan = plan_page(content_lines, content_roles, caption_groups, hints)
    for entry in plan:
        entry["id"] = f"p{src_page.number}n{content_source[entry['first']]}"
        entry["kind"] = edits.tags.get(entry["id"], entry["kind"])
        entry["alt"] = edits.alts.get(entry["id"], "")
        if entry["kind"] == "Formula" and not entry["alt"]:
            members = content_lines[entry["first"]:entry["last"] + 1]
            entry["alt"] = formula_description(
                (min(ln.bbox[0] for ln in members), min(ln.bbox[1] for ln in members),
                 max(ln.bbox[2] for ln in members), max(ln.bbox[3] for ln in members)), described)
    read_after = _read_after_pictures(edits.after, figs, lines, content_lines, plan,
                                      sort.owner_figure)
    # Frames a person drew regroup the lines before anything is numbered. The content lines
    # are re-ordered along with the plan, so every later step still sees each element's lines
    # as one contiguous run.
    # Elements a person inserted: an id of their own (`p{page}a{n}`), whatever type they were
    # given, and the lines their frame encloses. An element made of lines a frame released
    # (`p{page}o{n}`) only exists once that frame is applied, so its own frame is taken the
    # same way -- and it is placed like any released lines, after what is above it in its
    # column (`_given_up_anchors`; its own id as the source, so it joins nothing else).
    added_id = re.compile(rf"p{src_page.number}[ao]\d+x?")
    added = [{"kind": edits.tags.get(key, "P"), "id": key, "alt": edits.alts.get(key, ""),
              **({"_given_up_by": key} if "o" in key[len(f"p{src_page.number}"):] else {})}
             for key in sorted(sort.frames) if added_id.fullmatch(key)]
    order, plan = _apply_frames(
        plan, content_lines, sort.frames,
        lambda index: f"p{src_page.number}o{content_source[index]}", added, edits.removed,
        {**edits.after, **read_after})
    for entry in plan:      # where each element stands in the page's own reading order
        entry["_order"] = content_source[entry.pop("_at", entry["first"])]
        if entry["id"] in read_after:
            entry["_read_after"] = read_after[entry["id"]]
    if order != list(range(len(content_lines))):
        content_lines = [content_lines[i] for i in order]
        content_source = [content_source[i] for i in order]
        for entry in plan:      # a line a frame gave up is an element that can be retagged too
            entry["kind"] = edits.tags.get(entry["id"], entry["kind"])
    # A table row taken out is listed as a row that is not read, so it can be put back.
    dropped_rows = [entry for entry in plan if entry.get("removed_row")]
    plan = [entry for entry in plan if entry["id"] not in edits.removed]
    return content_lines, content_source, plan, dropped_rows


def _page_records(source: Path, src_page, lines: list[TextLine], content_lines: list[TextLine],
                  content_source: list[int], plan: list[dict], mcid_of: list[int | None],
                  dropped_rows: list[dict], sort: _LineSort, edits: Edits) -> list[dict]:
    """The editor's records for the page's text, in the page's reading order."""
    records = _element_records(src_page, plan, content_lines, mcid_of, edits, content_source)
    in_dropped_row: set[int] = set()
    for entry in dropped_rows:
        members = content_lines[entry["first"]:entry["last"] + 1]
        in_dropped_row.update(content_source[i] for i in range(entry["first"], entry["last"] + 1))
        x0 = min(ln.bbox[0] for ln in members)
        y0 = min(ln.bbox[1] for ln in members)
        x1 = max(ln.bbox[2] for ln in members)
        y1 = max(ln.bbox[3] for ln in members)
        records.append({
            **_untagged_record(src_page, entry["id"], members[0], content_source[entry["first"]]),
            "text": " ".join(ln.text.strip() for ln in members).strip()[:300],
            "left": round(100 * x0 / src_page.width, 2),
            "top": round(100 * (src_page.height - y1) / src_page.height, 2),
            "width": round(100 * (x1 - x0) / src_page.width, 2),
            "height": round(100 * (y1 - y0) / src_page.height, 2),
            "row": True,
        })
    # Lines Rebind set aside are listed too, marked as untagged, so the editor can offer them.
    # They sit at the position they occupy on the page, so the list stays the page's order.
    #
    # Except the ones that belong to a figure. Those are already part of it -- drawn inside its
    # marked content, and read out as part of it -- so listing them again puts the picture's
    # own insides into the walk as separate stops. On a scanned architectural plan that is what
    # the recogniser makes of hatching and rules: page 25 of a real book came to 181 elements,
    # 175 of them one- and two-character scraps from inside a single figure, and getting past
    # that page meant 175 presses of Tab.
    planned = {entry["id"] for entry in plan}
    tagged = {content_source[e] for e in range(len(content_source)) if mcid_of[e] is not None}
    for index, line in enumerate(lines):
        if sort.line_ids[index] in planned or not line.text.strip() or index in tagged:
            continue
        if (id(line) in sort.owner_figure or id(line) in sort.inside_undescribed
                or index in in_dropped_row or id(line) in sort.specks):
            continue
        records.append(_untagged_record(src_page, sort.line_ids[index], line, index))
    # A formula with no description yet gets a draft of one, read from its picture
    # (`formula_reader`) -- offered in the editor for the person to check, never written into
    # the document on its own.
    drafts = formula_reader.drafts_for_page(
        source, src_page.number, src_page.width, src_page.height,
        {record["id"]: _record_box(record, src_page) for record in records
         if record.get("kind") == "Formula" and not record.get("alt")})
    for record in records:
        if record["id"] in drafts:
            record["alt_guess"] = drafts[record["id"]]
    return _records_in_reading_order(records)


def _draw_figures(pdf: pikepdf.Pdf, page_image, src_page, lines: list[TextLine],
                  mcids: list[int | None], figs: PageFigures, owner_figure: dict[int, int],
                  first_mcid: int, read_before: dict[str, int],
                  font: OverlayFont) -> tuple[bytes, dict, list[tuple]]:
    """Draw each described figure (a crop of the rendered region) inside a tagged /Figure,
    together with any text that belongs to it, all under the figure's single MCID. `read_before`
    is the content a person inserted straight after a figure, which settles where it is read.

    Returns the drawing, the image objects it uses, and each figure's
    `(mcid, alt, bbox, anchor mcid)` for the structure tree.
    """
    stream = io.BytesIO()
    xobjects: dict = {}
    specs: list[tuple] = []
    mcid = first_mcid
    for k, (fid, bbox) in enumerate(figs.described):
        xobjects[f"Fig{k}"] = _figure_xobject(pdf, page_image, bbox, src_page.width,
                                              src_page.height)
        x0, y0, x1, y1 = bbox
        stream.write(f"/Figure <</MCID {mcid}>> BDC q {x1 - x0:.2f} 0 0 {y1 - y0:.2f} "
                     f"{x0:.2f} {y0:.2f} cm /Fig{k} Do Q\n".encode())
        for line in lines:
            if owner_figure.get(id(line)) == k:
                _draw_line(stream, line, font)
        stream.write(b"EMC\n")
        specs.append((mcid, figs.alt[fid], bbox,
                      read_before[fid] if fid in read_before
                      else _figure_anchor(lines, mcids, k, owner_figure, bbox)))
        mcid += 1
    return stream.getvalue(), xobjects, specs


def _editor_picture(source: Path, render_source: Path, src_page, page_image,
                    darken_contrast: bool) -> bytes:
    """The picture of the page the editor lays its elements over -- reusing the render already
    made for a figure crop or a rebuild where there is one, and the picture from the last rebuild
    where the page has not changed (an edit never changes how a page looks)."""
    stamp = file_key(source)
    key = stamp and stamp + (src_page.number, darken_contrast)
    picture = _editor_pictures.get(key)
    if picture is None:
        image = page_image if page_image is not None else render_page_to_image(
            render_source, src_page.number, dpi=EDITOR_PAGE_DPI)
        picture = _editor_pictures.put(key, _png(
            image, (0.0, 0.0, src_page.width, src_page.height),
            src_page.width, src_page.height, max_side=EDITOR_PAGE_PX))
    return picture


def remediate(source: Path, target: Path, *, title: str | None = None, lang: str = "en",
              dpi: int = 300, alt_texts: dict[str, str] | None = None,
              darken_contrast: bool = True, strip_scripts: bool = False,
              edits: Edits | None = None) -> RemediationResult:
    """Write `target`: the source made accessible, looking exactly like the original.

    The original pages are kept verbatim (vector text stays crisp, a scan stays a scan) and marked
    as an artifact; an invisible, tagged text layer is added over them and referenced from a PDF/UA
    structure tree. Embedded figures are decorative by default (compliant); pass `alt_texts`
    (keyed by the figure ids in a prior result's `.figures`) to promote a figure to a tagged
    `/Figure` with that description.

    `darken_contrast` is the one thing that changes how the document *looks*: text failing WCAG AA
    is darkened just enough to pass, keeping its hue (see `recolor`). On by default -- an
    accessible document is the job, and a contrast failure Rebind can fix is not worth handing back
    as homework. It is conservative by construction: a correction is confined to the text object
    it is made in, so artwork in the same colour is untouched, and a page with nothing failing
    keeps its original bytes. Pass False to measure and report without correcting.
    """
    source, target = Path(source), Path(target)
    # With no corrections given, a file Rebind wrote is opened with the ones it was saved with.
    if edits is None:
        saved = saved_corrections(source)
        edits = Edits.from_payload(saved.get("edits") if saved else None)
    alt_texts = {**edits.alts, **(alt_texts or {})}
    title = title or source.stem
    # What the file's own tags already describe is read before anything else, because a file Rebind
    # wrote is then read as what it was before: its own layer read back in would double every word.
    described = existing_descriptions(source)
    source = original_of(source)
    source_pages = _pages_of(source)
    # A page printed sideways is read, drawn over and shown in the editor turned upright, and
    # written turned back (`turn`).
    source, source_pages, turns, moved = upright(source, source_pages)
    described = {number: [replace(item, bbox=move_box(moved[number], item.bbox))
                          for item in items] if number in moved else items
                 for number, items in described.items()}
    profile = build_profile(source_pages)

    engine = OcrEngine()
    ocr_pages: list[int] = []
    empty_pages: list[int] = []
    added_layer = False

    # A scan that arrived with an OCR layer of its own.
    invisible_pages = _invisible_text_pages(source)

    # Pass one: recover each page's text lines (from its text layer or OCR) in reading order.
    per_page: list[tuple] = []
    layouts: list[tuple] = []
    page_hints: list[layout_model.PageHints] = []
    for src_page in source_pages:
        # Another tool's pictures, which Rebind may not find itself. Rebind's own are found again
        # as they were, and a box the person drew comes back with their frames, below.
        known = tuple(item.bbox for item in described.get(src_page.number, ())
                      if item.kind == "Figure" and not item.exact)
        known += tuple(_frame_in_points(frame, src_page.width, src_page.height)
                       for key, frame in edits.frames.items()
                       if _is_figure_id(key) and key.startswith(f"p{src_page.number}")
                       and not key[len(f"p{src_page.number}")].isdigit())
        lines, used_ocr, layout, hints = _lines_for(source, src_page, engine, dpi, profile, known,
                                                    reread=src_page.number in invisible_pages)
        layouts.append((src_page, layout))
        page_hints.append(hints)
        if used_ocr and lines:
            ocr_pages.append(src_page.number)
            added_layer = True
        elif used_ocr:
            empty_pages.append(src_page.number)
        per_page.append((src_page, lines, used_ocr))
    roles = _structure_roles(per_page, profile, [hints.not_headings for hints in page_hints])
    layout_model.drop_ornaments(page_hints)
    document_captions = _document_captions(per_page)

    # Pass two: build the tagged, appearance-preserving output.
    pdf = pikepdf.open(source)
    # Runs before the per-page loop below (not after, as originally written) specifically so that
    # loop only ever sees the annotations that survive -- the external links it goes on to tag into
    # the structure tree -- never the legacy-destination ones this call is about to remove.
    _strip_legacy_destinations(pdf)
    if strip_scripts:
        _strip_scripts(pdf)
    # A font that misreports its glyphs gets a map saying what they are (`glyphs`), so its page can
    # stay as it is rather than be redrawn from a picture to get rid of glyphs with no character.
    repair_fonts(pdf, source)

    # Contrast is measured first and corrected from that measurement -- each failing colour against
    # the paper actually sampled behind it, rather than against an assumed white page. A human eye
    # cannot compute a luminance ratio, so this is settled here rather than asked about later.
    #
    # Recolouring happens before the page's own content is wrapped as an artifact. Anything that
    # rasterizes a page (a rebuilt page, a figure crop, the contrast re-measurement) reads from a
    # *file*, not from this in-memory Pdf, so the corrected document is written out once here and
    # used as the render source from then on -- otherwise those renders would quietly show the
    # original, uncorrected colours.
    render_source = source
    recoloured = 0
    if darken_contrast:
        corrections = recolor.corrections_for(
            contrast.measure(source, source_pages, skip_pages=invisible_pages))
        for page in pdf.pages:
            recoloured += recolor.apply_corrections(pdf, page, corrections)
        if recoloured:
            render_source = target.with_name(target.stem + ".recoloured.tmp.pdf")
            pdf.save(render_source)
            try:
                same_fonts(source, render_source)
            except Exception:  # noqa: BLE001 -- only saves work; the fonts are looked at again
                pass

    tree = StructTree(pdf, len(source_pages))
    font = OverlayFont(pdf)
    undescribed_figures: list[dict] = []
    page_elements: list[dict] = []
    page_images: dict[int, bytes] = {}
    figure_boxes: dict[int, tuple] = {}

    for struct_parent, (page, (src_page, lines, used_ocr), page_roles, hints) in enumerate(
        zip(pdf.pages, per_page, roles, page_hints)
    ):
        # The page at full resolution, rendered once and only if something needs it.
        rendered: list = []

        def full_render(number: int = src_page.number, held: list = rendered):
            if not held:
                held.append(render_page_to_image(render_source, number, dpi=dpi))
            return held[0]

        # "Scanned" cannot mean "Rebind ran OCR on it": a scan that arrived already OCR'd has a
        # text layer, so nothing is recognized here and that test is false on exactly the documents
        # this is for. A page is a scan when its words are an invisible layer over a picture, or
        # when one image covers the whole sheet -- both of which are known by now.
        scanned = used_ocr or src_page.number in invisible_pages or _is_full_page_scan(src_page)
        figs = find_figures(source, render_source, src_page, lines, hints, scanned=scanned,
                            dpi=dpi, full_render=full_render)
        on_page = described.get(src_page.number, [])
        figs.boxes, carried = adopt_described_figures(figs.boxes, on_page, src_page.number)
        _framed_pictures(figs, edits.frames, src_page)
        # What a person typed here wins over what the document already said, and either over a
        # caption found near the picture.
        describe_figures(figs, lines, {**carried, **alt_texts}, document_captions, edits.removed,
                         src_page.width * src_page.height)
        rebuild = (_has_marked_content(page) or _has_unmappable_glyphs(lines)
                   or _has_unembedded_font(page))
        page_image = full_render() if rebuild or figs.boxes else None

        sort = _sort_lines(src_page, lines, hints, figs, edits, profile, scanned)
        content_lines, content_source, plan, dropped_rows = _page_plan(
            src_page, lines, page_roles, hints, figs, sort, edits, on_page)

        # Marked-content ids, in reading order. A line an edit removed is drawn as an artifact. A
        # line with superscripts or subscripts takes one id per piece (`_pieces`), so each of them
        # can be tagged as a span of its own -- except in a figure or a formula made of text, which
        # is read as one thing.
        kind_of = {i: entry["kind"] for entry in plan
                   for i in range(entry["first"], entry["last"] + 1)}
        mcid_of: list[int | None] = []
        mcids: list[int | None] = [None] * len(lines)
        drawn_pieces: dict[int, list] = {}      # page line index -> (mcid, start, end, shift)
        tagged_pieces: dict[int, list] = {}     # content index -> (mcid, shift)
        next_mcid = 0
        for index, line in enumerate(content_lines):
            if index not in kind_of:
                mcid_of.append(None)
                continue
            mcid_of.append(next_mcid)
            mcids[content_source[index]] = next_mcid
            split = _pieces(line) if kind_of[index] not in ("Figure", "Formula") else []
            if split:
                drawn = [(next_mcid + k, start, end, shift)
                         for k, (start, end, shift) in enumerate(split)]
                drawn_pieces[content_source[index]] = drawn
                tagged_pieces[index] = [(mcid, shift) for mcid, _s, _e, shift in drawn]
            next_mcid += max(len(split), 1)
        page_elements.extend(_page_records(source, src_page, lines, content_lines, content_source,
                                           plan, mcid_of, dropped_rows, sort, edits))

        # The first element a person inserted after each picture, in the order the page is read.
        followed_by: dict[str, str] = {}
        read_before: dict[str, int] = {}
        for entry in plan:
            picture = edits.after.get(entry["id"])
            if picture in dict(figs.boxes) and picture not in followed_by:
                followed_by[picture] = entry["id"]
                if mcid_of[entry["first"]] is not None:
                    read_before[picture] = mcid_of[entry["first"]]

        figure_stream, extra_xobjects, figure_specs = _draw_figures(
            pdf, page_image, src_page, lines, mcids, figs, sort.owner_figure, next_mcid,
            read_before, font)
        overlay = _tagged_text_stream(lines, font, mcids, drawn_pieces) + figure_stream
        if rebuild:
            keep_original_drawing(pdf, page)
            _rebuild_page(pdf, page, page_image, overlay, extra_xobjects, font,
                          src_page.width, src_page.height)
        else:
            # Before the page is wrapped as an artifact: a scan that arrived already OCR'd carries
            # its own invisible text layer, which Rebind is about to duplicate with a tagged one.
            # The page as it was is kept, so opening the file again reads that layer, not Rebind's.
            drawing = page_drawing(page)
            if _strip_invisible_text(pdf, page):
                keep_original_drawing(pdf, page, drawing)
            page.contents_add(pdf.make_stream(b"/Artifact BMC\n"), prepend=True)
            page.contents_add(pdf.make_stream(b"EMC\n" + overlay), prepend=False)
            _add_font(pdf, page, font)
            for name, obj in extra_xobjects.items():
                _add_xobject(pdf, page, obj, name)
        page.obj.StructParents = struct_parent
        page.obj.Tabs = Name.S

        tree.add_page(page, struct_parent, content_lines, plan, mcid_of,
                      next_mcid + len(figure_specs), figure_specs, edits, tagged_pieces)
        tree.add_links(page)

        figure_boxes[src_page.number] = tuple(bbox for _fid, bbox in figs.boxes)
        _place_figures(page_elements, src_page,
                       [(fid, figs.alt[fid], bbox) for fid, bbox in figs.described]
                       + [(fid, "", bbox) for fid, bbox in figs.undescribed],
                       set(figs.music_of), followed_by)
        page_images[src_page.number] = _editor_picture(source, render_source, src_page,
                                                       page_image, darken_contrast)
        for fid, bbox in figs.undescribed:
            undescribed_figures.append({
                "id": fid, "page": src_page.number, "alt_guess": figs.draft(lines, fid, bbox),
                "thumb": _crop_data_uri(page_image, bbox, src_page.width, src_page.height)})

    tree.finish()
    font.finish()
    _set_metadata(pdf, title=title, lang=lang)
    # So the file can be opened in Rebind again and carry on where the person left off.
    restore_turns(pdf, turns)
    save_corrections(pdf, {"version": 1, "edits": {**edits.to_payload(), "alts": alt_texts}})
    pdf.save(target, min_version="2.0")   # PDF/UA-2 requires a PDF 2.0 base (ISO 32000-2)
    pdf.close()
    self_check = self_check_pdf_ua2(target)

    # Evidence for the two checks a machine may never sign off on. Both read the SOURCE: contrast
    # is a property of the page a reader sees, which remediation deliberately leaves untouched, and
    # the reading order is the one just built above.
    orders = [review.page_order(src_page, layout, figure_boxes.get(src_page.number, ()))
              for src_page, layout in layouts]
    # After recolouring, the pages extracted at the top of this function still carry the *old*
    # declared ink colours, and the ink is what `contrast` trusts the declaration for. Re-read them
    # from the corrected document so the report describes what was actually produced.
    measured_pages = list(extract_pages(render_source)) if render_source != source else source_pages
    measured = contrast.measure(render_source, measured_pages, figures=figure_boxes,
                                skip_pages=invisible_pages)
    if render_source != source:
        render_source.unlink(missing_ok=True)

    return RemediationResult(
        pdf_path=target, page_count=len(source_pages),
        ocr_pages=tuple(ocr_pages), empty_pages=tuple(empty_pages), added_text_layer=added_layer,
        figures=tuple(undescribed_figures),
        structure_ok=self_check.ok, structure_issues=self_check.issues,
        reading_order=review.summarize(orders),
        contrast=contrast.summarize(measured, darkened=recoloured),
        elements=tuple(page_elements), page_images=page_images,
    )
