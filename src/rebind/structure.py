"""Build a page's PDF/UA structure elements, and the document outline from its headings."""

from __future__ import annotations

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from .edits import CELL_TAGS, GROUPING_TAGS, Edits
from .extract import TextLine
from .tables import _cell_table, _table_row_groups, _tagged_table


def _top_index_for(tops: list, owners: list, anchor: int | None) -> int:
    """Where in `tops` an element anchored before MCID `anchor` belongs."""
    if anchor is None or anchor >= len(owners):
        return len(tops)
    owner = owners[anchor]
    for position, top in enumerate(tops):
        if top is owner or _contains(top, owner):
            return position
    return len(tops)


def _contains(parent, target) -> bool:
    """Whether `target` is `parent` or sits somewhere beneath it in the structure tree."""
    kids = parent.get("/K")
    if not isinstance(kids, Array):
        return False
    for kid in kids:
        if isinstance(kid, Dictionary):
            if kid == target or _contains(kid, target):
                return True
    return False


def _page_structure(pdf: pikepdf.Pdf, lines: list[TextLine], plan: list[dict],
                    mcid_of: list[int | None],
                    document_elem: pikepdf.Object, page_obj: pikepdf.Object,
                    caption_hosts: list | None = None, edits: Edits | None = None,
                    pieces: dict[int, list[tuple[int, float]]] | None = None):
    """Build one page's structure elements from an (already decided, possibly edited) plan.

    Returns (top-level elements in reading order, owners) where `owners[mcid]` is the leaf element
    that directly holds that MCID -- what the page's ParentTree entry indexes.

    A line in `pieces` (by index) was drawn as several marked-content sequences, each
    `(mcid, shift)`: the text on its baseline, held by its element directly, and each superscript
    or subscript, held by a /Span of its own carrying its baseline shift (ISO 32000-2 Layout
    attribute /BaselineShift, positive raised). The shift is part of what the text says -- a "-5"
    raised after "10" is a power, not a subtraction -- and a span is the standard place to say so.
    """
    owners: dict[int, pikepdf.Object] = {}
    tops: list[pikepdf.Object] = []

    def hold(elem: pikepdf.Object, indices: list[int]) -> pikepdf.Object:
        """Give `elem` the marked content of every line in `indices`. Every id is owned; dropping
        one would leave content drawn on the page that no element claims (clause 8.2.2)."""
        kids: list = []
        for i in indices:
            for mcid, shift in (pieces or {}).get(i, [(mcid_of[i], 0.0)]):
                if shift:
                    span = pdf.make_indirect(Dictionary(
                        Type=Name.StructElem, S=Name.Span, P=elem, Pg=page_obj, K=mcid,
                        A=Dictionary(O=Name.Layout, BaselineShift=round(float(shift), 2))))
                    owners[mcid] = span
                    kids.append(span)
                else:
                    owners[mcid] = elem
                    kids.append(mcid)
        elem.K = Array(kids) if len(kids) > 1 else kids[0]
        return elem

    def leaf(index: int | list[int], structure_type, parent,
             extra: dict | None = None) -> pikepdf.Object:
        """One element holding the marked content of one line -- or of several, when more than one
        line lands in the same cell."""
        return hold(pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=structure_type, P=parent, Pg=page_obj, **(extra or {}))),
            [index] if isinstance(index, int) else index)

    def spanning(kind: str, parent, indices: list[int]) -> pikepdf.Object:
        """One element holding every line's marked content directly."""
        return hold(pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=Name("/" + kind), P=parent, Pg=page_obj)), indices)

    captions: list[tuple[int, pikepdf.Object]] = []      # (first line, the caption element)
    table_boxes: dict[tuple, tuple] = {}                 # a table's objgen -> its lines' box
    # Elements marked as cells, waiting for the run of them to end so they can become one table.
    cells: list[tuple[str, list[int]]] = []

    def close_cells() -> None:
        if cells:
            tops.append(_cell_table(pdf, list(cells), lines, document_elem, leaf))
            cells.clear()

    for entry in plan:
        first, last, kind = entry["first"], entry["last"], entry["kind"]
        if any(mcid_of[i] is None for i in range(first, last + 1)):
            continue        # every line of this element was removed by an edit
        indices = list(range(first, last + 1))

        if kind in CELL_TAGS:
            cells.append((kind, indices))
            continue
        close_cells()

        if kind == "Table":
            row_tags = edits.tags if edits else {}
            rows = [(row_id, [(i, lines[i]) for i in members])
                    for row_id, members in _table_row_groups(entry, lines)]
            table = _tagged_table(pdf, rows, document_elem, page_obj, leaf, row_tags)
            table_boxes[table.objgen] = (min(lines[i].bbox[0] for i in indices),
                                         min(lines[i].bbox[1] for i in indices),
                                         max(lines[i].bbox[2] for i in indices),
                                         max(lines[i].bbox[3] for i in indices))
            tops.append(table)
        elif kind == "L":
            lst = pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name.L, P=document_elem, K=Array([])))
            items = []
            for i in indices:
                li = pdf.make_indirect(Dictionary(
                    Type=Name.StructElem, S=Name.LI, P=lst, K=Array([])))
                li.K = Array([leaf(i, Name.LBody, li)])
                items.append(li)
            lst.K = Array(items)
            tops.append(lst)
        elif kind in GROUPING_TAGS:
            # A grouping element may only hold block-level children, never content of its own --
            # tagging one straight over the text fails validation, so the text goes in a /P inside.
            group = pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name("/" + kind), P=document_elem, K=Array([])))
            group.K = Array([spanning("P", group, indices)])
            tops.append(group)
        elif kind == "Figure":
            figure = spanning("Figure", document_elem, indices)
            # Never fabricated: a figure made out of text is described by that text unless the
            # person typed something better, which is what the editor's description box is for.
            figure.Alt = String(entry.get("alt")
                                or " ".join(lines[i].text.strip() for i in indices).strip()
                                or "Figure")
            tops.append(figure)
        elif kind == "Caption":
            box = (min(lines[i].bbox[0] for i in indices),
                   min(lines[i].bbox[1] for i in indices),
                   max(lines[i].bbox[2] for i in indices),
                   max(lines[i].bbox[3] for i in indices))
            captions.append((first, spanning("Caption", document_elem, indices), box))
        else:
            elem = spanning(kind, document_elem, indices)
            # A formula's description in words, where the person gave one. Without it a screen
            # reader reads the formula's own text, which is all Rebind knows of it.
            if kind == "Formula" and entry.get("alt"):
                elem.Alt = String(entry["alt"])
            tops.append(elem)
    close_cells()

    # A /Caption is not legal beside the thing it captions -- it belongs inside it. Each is moved
    # into the figure or table it is nearest to; one with nothing left to caption stays where it
    # is, as a paragraph, rather than being dropped or made non-conformant.
    #
    # Two constraints from PDF/UA-2 shape this, and both were learned by failing them. A figure may
    # hold AT MOST ONE caption (Table 5, Figure-Caption), so a host that already has one is not
    # offered again -- on a page with two pictures, taking "the last figure built" put every
    # caption into the same one. And a caption must be the FIRST OR LAST child of its parent
    # (clause 8.2.5.27), which one of several siblings cannot be.
    taken: set = set()
    for first, caption, box in captions:
        host = _caption_host(list(caption_hosts or []) + tops, box, taken, table_boxes)
        if host is None:
            caption.S = Name.P
            tops.insert(_position_for(tops, first, plan), caption)
            continue
        taken.add(host.objgen)
        caption.P = host
        kids = host.get("/K")
        kids = list(kids) if isinstance(kids, Array) else ([kids] if kids is not None else [])
        # A table's title set above it is read before it, as its first child; anything else is
        # read after what it captions.
        host_box = table_boxes.get(host.objgen)
        above = host_box is not None and (box[1] + box[3]) / 2 > host_box[3]
        host.K = Array([caption] + kids if above else kids + [caption])
    return tops, owners


def _caption_host(tops: list, box: tuple, taken: set,
                  table_boxes: dict | None = None) -> pikepdf.Object | None:
    """The figure or table a caption belongs inside: the nearest one that has not got one already.

    Nearness is measured between boxes, so a caption in the margin goes to the picture beside it
    rather than to whichever figure happened to be built last. A figure records its own box (in
    `/A /BBox`); a table's comes from `table_boxes`, the box its lines span. Taking the last table
    built instead put the title of a page's first table inside its second.
    """
    table_boxes = table_boxes or {}
    best, best_gap = None, None
    for elem in tops:
        if elem.objgen in taken:
            continue
        if str(elem.get("/S")) == "/Figure":
            layout = elem.get("/A")
            bbox = layout.get("/BBox") if layout is not None else None
        elif str(elem.get("/S")) == "/Table":
            bbox = table_boxes.get(elem.objgen)
        else:
            continue
        if bbox is None:
            continue
        fx0, fy0, fx1, fy1 = (float(v) for v in bbox)
        gap = max(0.0, max(fx0 - box[2], box[0] - fx1)) + max(0.0, max(fy0 - box[3], box[1] - fy1))
        if best_gap is None or gap < best_gap:
            best, best_gap = elem, gap
    if best is not None:
        return best
    for elem in reversed(tops):
        if str(elem.get("/S")) == "/Table" and elem.objgen not in taken:
            return elem
    return None


def _position_for(tops: list, first: int, plan: list[dict]) -> int:
    """Where an element starting at line `first` belongs among the page's elements."""
    seen = 0
    for entry in plan:
        if entry["first"] >= first:
            return min(seen, len(tops))
        seen += 1
    return len(tops)


def _build_outline(pdf: pikepdf.Pdf, heading_entries: list[tuple[int, pikepdf.Object, str]]) -> None:
    """Build a document outline (bookmarks) from the headings remediate() already recovered, nested
    by level, using real PDF 2.0 **structure destinations** -- an `/SD` reference straight into the
    heading's own structure element -- rather than a page/coordinate destination, the kind PDF/UA-2
    clause 8.8 forbids and `_strip_legacy_destinations` removes from the source.

    A source's own outline (built by whatever authored it) always used the legacy kind and is
    stripped; without this, a document that HAD bookmarks would end up with none at all -- a real
    navigation loss for long documents (confirmed: Adobe's checker flags "Bookmarks are present in
    large documents" for exactly this). `heading_entries` is `(level, struct_elem, title)` in
    reading order, collected as headings are built (`remediate()`'s main loop); a heading whose
    level jumps deeper than the currently open ancestor simply nests one level under it (the
    normal case, since `_structure_roles` already guarantees levels never skip).
    """
    if not heading_entries:
        return

    outlines = pdf.make_indirect(Dictionary(Type=Name.Outlines))

    def append_child(parent: pikepdf.Object, item: pikepdf.Object) -> None:
        if "/First" not in parent:
            parent.First = item
            parent.Last = item
        else:
            item.Prev = parent.Last
            parent.Last.Next = item
            parent.Last = item
        parent.Count = int(parent.get("/Count", 0)) + 1

    stack: list[tuple[int, pikepdf.Object]] = []   # (level, item) for each currently open ancestor
    for level, elem, title in heading_entries:
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = stack[-1][1] if stack else outlines
        item = pdf.make_indirect(Dictionary(
            Title=String(title), Parent=parent,
            Dest=Array([Dictionary(S=Name.XYZ, SD=elem)]),
        ))
        append_child(parent, item)
        stack.append((level, item))

    pdf.Root.Outlines = outlines


def _link_alt(annot: pikepdf.Object) -> str:
    """An honest, non-fabricated /Alt for a /Link structure element (Adobe's 'Other elements
    alternate text' check). Built entirely from the annotation's own action -- a mechanical fact
    ("Link to <uri>"), never a guess at the link's purpose. A source PDF's own broken/malformed
    URI (confirmed in a real sample: a publisher production defect, not something Rebind
    introduces) is reported as-is -- honestly stating a broken thing is broken, not fabricating a
    plausible-looking replacement.
    """
    action = annot.get("/A")
    if action is not None:
        if action.get("/S") == Name.URI and "/URI" in action:
            return f"Link to {action.URI}"
        if action.get("/S") == Name.GoToR and "/F" in action:
            return f"Link to {action.F}"
    return "Link"


# PDF/UA-2 (ISO 14289-2) namespace. All the structure types Rebind writes (/P, /H1-/H6, /Table,
# /TR, /TD, /TH, /L, /LI, /LBody, /Figure) keep their names as-is in the PDF 2.0 Standard Structure
# Namespace -- confirmed against `verapdf -f ua2` (see docs/decisions/ for the spike). /Note is the
# one exception: it still needs the RoleMap entry below. Only the root Document element needs /NS
# set explicitly; every descendant inherits it. Getting the namespace URI wrong (e.g. the plausible
# but incorrect "http://iso.org/pdf/ssn") fails clause 8.2.5.2 silently -- confirmed by trial.
PDF2_SSN_NAMESPACE = "http://iso.org/pdf2/ssn"


class StructTree:
    """The document's structure tree, built a page at a time."""

    def __init__(self, pdf: pikepdf.Pdf, page_count: int):
        self.pdf = pdf
        self.root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
        namespace = pdf.make_indirect(
            Dictionary(Type=Name.Namespace, NS=String(PDF2_SSN_NAMESPACE)))
        self.document = pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=Name.Document, NS=namespace, P=self.root, K=Array([])))
        self.root.K = Array([self.document])
        # PDF/UA-2 clause 8.2.5.14: "the Note standard structure type shall not be present in
        # conforming documents unless role mapped to a structure element in the PDF 2.0 namespace"
        # -- unlike its neighbours, /Note fails validation without this line. Confirmed by trial:
        # removing it reproduces that exact veraPDF failure.
        self.root.RoleMap = Dictionary(Note=Name.P)
        self.root.Namespaces = Array([namespace])
        self.parent_tree = Array([])
        # Page StructParents keys use 0..page_count-1 (a page's index); annotation StructParent
        # keys start right after, so neither numbering ever collides in the flat Nums array.
        self.next_parent_key = page_count
        self.headings: list[tuple[int, pikepdf.Object, str]] = []   # (level, element, title)

    def add_page(self, page: pikepdf.Page, struct_parent: int, lines: list[TextLine],
                 plan: list[dict], mcid_of: list[int | None], mcid_count: int,
                 figure_specs: list[tuple], edits: Edits,
                 pieces: dict[int, list[tuple[int, float]]] | None = None) -> None:
        """Add one page's elements: its planned content and its described figures, each figure
        `(mcid, alt, bbox, anchor mcid)` spliced into the reading order where it sits."""
        pdf = self.pdf
        # Figures are built before the rest of the page's structure, because a /Caption the user
        # marked has to be put *inside* the figure it captions and so needs it to exist already.
        figure_elems = [
            pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name.Figure, P=self.document, Pg=page.obj, K=fmcid,
                Alt=String(alt),
                A=Dictionary(O=Name.Layout, BBox=Array([round(v, 2) for v in bbox]))))
            for fmcid, alt, bbox, _anchor in figure_specs
        ]
        tops, owner_of_mcid = _page_structure(pdf, lines, plan, mcid_of, self.document,
                                              page.obj, caption_hosts=figure_elems, edits=edits,
                                              pieces=pieces)
        # The parent tree is indexed BY marked-content id, so every slot must hold the element that
        # owns that id. Appending here instead of assigning (as this briefly did) shifts the figure
        # entries past their own ids and leaves nulls behind -- content that names a structure
        # element the tree cannot resolve, which reads as untagged content and fails clause 8.2.2.
        owners: list = [None] * mcid_count
        for key, elem in owner_of_mcid.items():
            owners[key] = elem
        assert all(o is not None for o in owners[:mcid_count - len(figure_specs)]), \
            "unowned marked content"
        for entry in plan:
            kind, first = entry["kind"], entry["first"]
            if kind.startswith("H") and kind[1:].isdigit() and lines[first].text.strip():
                owner = owner_of_mcid.get(mcid_of[first])
                if owner is not None and owner.get("/S") == Name.Span:
                    owner = owner.P     # a heading that opens with a superscript
                if owner is not None:
                    self.headings.append((int(kind[1:]), owner, lines[first].text.strip()))
        # A figure goes into the reading order where it actually sits, not after everything else.
        # `tops` holds the page's top-level elements in reading order; each figure is spliced in at
        # the element that owns the first content line below it. Appending them all at the end (as
        # this did originally) put every figure after the whole page's prose -- so a screen reader
        # met a page's figures only once it had finished reading the page.
        placements: list[tuple[int, pikepdf.Object]] = []
        for (fmcid, _alt, _bbox, anchor), figure_elem in zip(figure_specs, figure_elems):
            owners[fmcid] = figure_elem
            placements.append((_top_index_for(tops, owners, anchor), figure_elem))
        for position, figure_elem in sorted(placements, key=lambda p: -p[0]):
            tops.insert(position, figure_elem)
        self.document.K.extend(tops)
        self.parent_tree.append(struct_parent)
        self.parent_tree.append(pdf.make_indirect(Array(owners)))

    def add_links(self, page: pikepdf.Page) -> None:
        """Tag every surviving annotation on the page (an external URI/GoToR link --
        `_strip_legacy_destinations` already removed any internal-destination one).

        Each becomes a /Link element whose sole child is an object reference (OBJR) to the
        annotation, cross-linked via the annotation's own /StructParent key into a NEW slot in the
        parent tree. Annotation keys share the flat Nums numbering space with the pages' but must
        never collide with them, so they start only after every page's is used.
        """
        for annot in page.obj.get("/Annots") or []:
            link_description = _link_alt(annot)
            link_elem = self.pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name.Link, P=self.document,
                K=Array([Dictionary(Type=Name.OBJR, Obj=annot, Pg=page.obj)]),
                Alt=String(link_description),
            ))
            # Also set directly on the annotation's own /Contents -- a real sample kept failing
            # Adobe Acrobat's "Tagged annotations" check with /Alt on the struct element alone.
            annot.Contents = String(link_description)
            annot.StructParent = self.next_parent_key
            self.parent_tree.append(self.next_parent_key)
            self.parent_tree.append(link_elem)
            self.document.K.append(link_elem)
            self.next_parent_key += 1

    def finish(self) -> None:
        """Attach the finished tree to the document, with the outline built from its headings."""
        self.root.ParentTree = self.pdf.make_indirect(Dictionary(Nums=self.parent_tree))
        self.root.ParentTreeNextKey = self.next_parent_key
        self.pdf.Root.StructTreeRoot = self.root
        _build_outline(self.pdf, self.headings)
