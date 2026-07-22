"""Read-only inspection of a PDF's structure tree, for tests and reporting."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf

# A structure tree is finite by construction in every well-formed PDF, but nothing stops a
# malformed or adversarial one from making /K reference an ancestor, which would otherwise
# recurse until the process runs out of stack. This cap is a backstop against runaway
# *acyclic* nesting; true cycles are caught immediately by the visited-path check below.
_MAX_DEPTH = 512


class StructureTreeError(RuntimeError):
    """Raised when a structure tree cannot be safely walked.

    Covers both a cyclic /K reference (an element that is its own ancestor) and nesting
    deeper than `_MAX_DEPTH`, which is treated as a probable cycle we failed to detect by
    identity (e.g. because qpdf gave the repeated element a fresh object number).
    """


@dataclass(frozen=True)
class StructElement:
    """One node of a PDF structure tree, with just enough detail to answer test questions."""

    type: str
    id: bytes | None
    headers: tuple[bytes, ...]
    page_ref: tuple[int, int] | None
    mcids: tuple[int, ...]
    children: tuple["StructElement", ...] = field(default_factory=tuple)


def structure_element_types(pdf_path: Path) -> set[str]:
    """Return every structure-element type name present in the document's tag tree."""
    found: set[str] = set()
    for element in _iter_elements(_structure_tree(pdf_path)):
        found.add(element.type)
    return found


def structure_tree(pdf_path: Path) -> list[StructElement]:
    """Return the document's structure tree as a list of top-level `StructElement` nodes.

    Unlike `structure_element_types`, this preserves parent/child relationships, so callers
    can assert containment (e.g. "every TH is inside a Table") rather than mere presence.
    """
    return _structure_tree(pdf_path)


def elements_of_type(pdf_path: Path, struct_type: str) -> list[StructElement]:
    """Return every element of the given type, wherever it occurs in the tree."""
    return [e for e in _iter_elements(_structure_tree(pdf_path)) if e.type == struct_type]


def is_descendant_of(ancestor: StructElement, struct_type: str) -> bool:
    """Return whether any element of `struct_type` is somewhere under `ancestor`."""
    return any(child.type == struct_type for child in _iter_elements(ancestor.children))


def page_labels(pdf_path: Path) -> list[str]:
    """Read back the per-page labels written by rebind.pagelabels.set_page_labels."""
    with pikepdf.open(pdf_path) as pdf:
        tree = pdf.Root.get("/PageLabels")
        if tree is None:
            return []
        nums = tree.get("/Nums")
        if nums is None:
            return []
        labels: list[str] = []
        for i in range(1, len(nums), 2):
            entry = nums[i]
            prefix = entry.get("/P")
            labels.append(str(prefix) if prefix is not None else "")
        return labels


def table_header_associations(pdf_path: Path) -> list[tuple[str, list[str]]]:
    """For each table data cell that carries a /Headers attribute, resolve it.

    WeasyPrint associates table headers by giving header cells (TH) an /ID and giving data
    cells an /A attribute dict with a /Headers array of those /ID values — it does not use
    /Scope. This walks the structure tree, resolves each data cell's /Headers references
    back to the header elements they name, and extracts the visible text of both sides from
    the content stream (via each element's MCIDs and each font's /ToUnicode CMap).

    Returns a list of (data_cell_text, [header_cell_text, ...]) in document order. A header
    ID with no matching element in the tree is omitted from that cell's header list rather
    than raising, since a dangling /Headers reference is itself a fact worth a test being
    able to see (the resulting list will simply be shorter than expected).
    """
    with pikepdf.open(pdf_path) as pdf:
        elements = _structure_tree(pdf_path, pdf=pdf)

        by_id: dict[bytes, StructElement] = {}
        for element in _iter_elements(elements):
            if element.id is not None:
                by_id[element.id] = element

        page_text_cache: dict[tuple[int, int], dict[int, str]] = {}

        def text_of(element: StructElement) -> str:
            return _element_text(element, pdf, page_text_cache)

        results: list[tuple[str, list[str]]] = []
        for element in _iter_elements(elements):
            if element.type != "TD" or not element.headers:
                continue
            header_texts = [
                text_of(by_id[header_id]) for header_id in element.headers if header_id in by_id
            ]
            results.append((text_of(element), header_texts))
    return results


# --- tree construction -------------------------------------------------------------------


def _structure_tree(pdf_path: Path, pdf: pikepdf.Pdf | None = None) -> list[StructElement]:
    def build(opened: pikepdf.Pdf) -> list[StructElement]:
        root = opened.Root.get("/StructTreeRoot")
        if root is None:
            return []
        return _build_elements(root.get("/K"), visited=set(), depth=0, inherited_page=None)

    if pdf is not None:
        return build(pdf)
    with pikepdf.open(pdf_path) as opened:
        return build(opened)


def _iter_elements(elements: tuple[StructElement, ...] | list[StructElement]):
    for element in elements:
        yield element
        yield from _iter_elements(element.children)


def _node_key(node: pikepdf.Object) -> object:
    """A key that identifies `node` across the *current recursion path*, for cycle detection.

    Indirect objects share identity via their (obj, gen) pair; direct (inline) objects have
    no independent identity in the PDF at all, so a fresh Python wrapper is created on every
    access and `id()` can never collide between genuinely distinct direct objects — which is
    correct, since a direct object cannot participate in a cycle in the first place.
    """
    try:
        objgen = node.objgen
    except AttributeError:
        return id(node)
    if objgen != (0, 0):
        return objgen
    return id(node)


def _build_elements(
    node: object,
    *,
    visited: set[object],
    depth: int,
    inherited_page: tuple[int, int] | None,
) -> list[StructElement]:
    if node is None:
        return []
    if depth > _MAX_DEPTH:
        raise StructureTreeError(
            f"structure tree nesting exceeds {_MAX_DEPTH} levels; this usually indicates "
            "a cyclic /K reference that identity-based cycle detection did not catch"
        )
    if isinstance(node, pikepdf.Array):
        result: list[StructElement] = []
        for child in node:
            result.extend(
                _build_elements(
                    child, visited=visited, depth=depth, inherited_page=inherited_page
                )
            )
        return result
    if not isinstance(node, pikepdf.Dictionary):
        # A /K leaf that is neither an array nor a dictionary is a marked-content reference:
        # a bare integer MCID (the common case), or elsewhere a numeric content identifier
        # inside an /MCR dict (handled below). There is no further tree to walk into — the
        # integer itself is captured by the caller as one of this element's `mcids`.
        return []

    key = _node_key(node)
    if key in visited:
        raise StructureTreeError(
            f"cyclic /K reference detected in structure tree at object {key!r}: an element "
            "references one of its own ancestors"
        )

    struct_type = node.get("/S")
    if struct_type is None:
        # Non-structure-element dictionaries can appear under /K too — most commonly /MCR
        # (marked-content reference) or /OBJR (object reference) dicts, which carry no /S
        # and no nested /K of their own. Correctly skipping them (rather than misreading
        # them as untyped structure elements) is intentional, not an oversight.
        return []

    visited = visited | {key}
    type_name = str(struct_type).lstrip("/")

    elem_id_obj = node.get("/ID")
    elem_id = bytes(elem_id_obj) if elem_id_obj is not None else None

    headers = _extract_headers(node.get("/A"))

    pg = node.get("/Pg")
    page_ref = pg.objgen if pg is not None else inherited_page

    k = node.get("/K")
    mcids: tuple[int, ...] = ()
    children: list[StructElement] = []
    if isinstance(k, pikepdf.Array):
        mcids = tuple(int(c) for c in k if isinstance(c, int))
        for c in k:
            if not isinstance(c, int):
                children.extend(
                    _build_elements(c, visited=visited, depth=depth + 1, inherited_page=page_ref)
                )
    elif isinstance(k, int):
        mcids = (int(k),)
    elif isinstance(k, pikepdf.Dictionary):
        children.extend(
            _build_elements(k, visited=visited, depth=depth + 1, inherited_page=page_ref)
        )

    return [
        StructElement(
            type=type_name,
            id=elem_id,
            headers=headers,
            page_ref=page_ref,
            mcids=mcids,
            children=tuple(children),
        )
    ]


def _extract_headers(attrs: object) -> tuple[bytes, ...]:
    """Pull a /Headers array of /ID values out of a structure element's /A attribute dict.

    /A is normally a single dict but the spec permits an array of them; WeasyPrint emits a
    single dict, but we handle both so a future generator change doesn't silently drop them.
    """
    candidates: list[pikepdf.Object] = []
    if isinstance(attrs, pikepdf.Dictionary):
        candidates = [attrs]
    elif isinstance(attrs, pikepdf.Array):
        candidates = [a for a in attrs if isinstance(a, pikepdf.Dictionary)]

    for candidate in candidates:
        headers = candidate.get("/Headers")
        if headers is not None:
            return tuple(bytes(h) for h in headers)
    return ()


# --- text resolution ---------------------------------------------------------------------


def _element_text(
    element: StructElement,
    pdf: pikepdf.Pdf,
    page_text_cache: dict[tuple[int, int], dict[int, str]],
) -> str:
    parts: list[str] = []
    if element.page_ref is not None and element.mcids:
        mcid_text = page_text_cache.get(element.page_ref)
        if mcid_text is None:
            page = pdf.get_object(element.page_ref)
            mcid_text = _page_mcid_text(page)
            page_text_cache[element.page_ref] = mcid_text
        for mcid in element.mcids:
            text = mcid_text.get(mcid)
            if text:
                parts.append(text)
    for child in element.children:
        text = _element_text(child, pdf, page_text_cache)
        if text:
            parts.append(text)
    return " ".join(parts).strip()


def _page_mcid_text(page: pikepdf.Object) -> dict[int, str]:
    """Map each MCID marked on `page` to the text shown while it was the active tag.

    pikepdf has no text-extraction API, so this parses the content stream directly: it
    tracks the current font (Tf) and the innermost active MCID (from BDC/EMC nesting), and
    decodes each shown string (Tj/TJ/'/") through that font's /ToUnicode CMap.

    SCOPE WARNING -- diagnostic and test-only, do not extend into production:
    This hand-rolled extractor (here and in `_font_decoders` / `_decode_tounicode_cmap` /
    `_decode_string` below) only handles what WeasyPrint's own generated PDFs need: Identity-H
    2-byte Type0 fonts and simple 1-byte fonts, with a single, self-contained /ToUnicode CMap
    per font. It does not handle `usecmap` references, Type3 fonts, multi-dictionary /A or
    /ToUnicode edge cases, or the many malformed/legacy encodings found in real-world scanned
    and born-digital third-party PDFs. It exists solely so this module's own tests can assert
    on rendered text; it must not be hardened or grown into rebind's production text-extraction
    path. If/when rebind needs real text extraction for born-digital PDFs, adopt `pdfminer.six`
    (MIT, pure Python, Windows-bundleable) rather than extending this parser.
    """
    resources = page.get("/Resources")
    font_maps, font_widths = _font_decoders(resources)

    try:
        instructions = pikepdf.parse_content_stream(page)
    except Exception:
        return {}

    mcid_parts: dict[int, list[str]] = {}
    mc_stack: list[int | None] = []
    current_font: str | None = None

    for instr in instructions:
        op = str(instr.operator)
        if op == "BDC":
            mcid = None
            if len(instr.operands) >= 2:
                props = instr.operands[1]
                if isinstance(props, pikepdf.Dictionary) and "/MCID" in props:
                    mcid = int(props["/MCID"])
            mc_stack.append(mcid)
        elif op == "BMC":
            mc_stack.append(None)
        elif op == "EMC":
            if mc_stack:
                mc_stack.pop()
        elif op == "Tf" and instr.operands:
            current_font = str(instr.operands[0])
        elif op in ("Tj", "'", '"') and instr.operands:
            active = _active_mcid(mc_stack)
            if active is not None:
                text = _decode_string(instr.operands[-1], current_font, font_maps, font_widths)
                if text:
                    mcid_parts.setdefault(active, []).append(text)
        elif op == "TJ" and instr.operands:
            active = _active_mcid(mc_stack)
            if active is not None:
                pieces = []
                for item in instr.operands[0]:
                    if isinstance(item, (bytes, pikepdf.String)):
                        pieces.append(_decode_string(item, current_font, font_maps, font_widths))
                text = "".join(pieces)
                if text:
                    mcid_parts.setdefault(active, []).append(text)

    return {mcid: "".join(pieces) for mcid, pieces in mcid_parts.items()}


def _active_mcid(mc_stack: list[int | None]) -> int | None:
    for mcid in reversed(mc_stack):
        if mcid is not None:
            return mcid
    return None


def _font_decoders(
    resources: object,
) -> tuple[dict[str, dict[bytes, str]], dict[str, int]]:
    font_maps: dict[str, dict[bytes, str]] = {}
    font_widths: dict[str, int] = {}
    if not isinstance(resources, pikepdf.Dictionary):
        return font_maps, font_widths
    fonts = resources.get("/Font")
    if not isinstance(fonts, pikepdf.Dictionary):
        return font_maps, font_widths
    for name, font in fonts.items():
        to_unicode = font.get("/ToUnicode")
        if to_unicode is None:
            continue
        font_maps[str(name)] = _decode_tounicode_cmap(to_unicode.read_bytes())
        # Type0 (CID-keyed) fonts use 2-byte codes under the Identity-H/V encodings that
        # WeasyPrint emits; simple fonts use 1-byte codes.
        font_widths[str(name)] = 2 if font.get("/Subtype") == pikepdf.Name("/Type0") else 1
    return font_maps, font_widths


def _decode_tounicode_cmap(data: bytes) -> dict[bytes, str]:
    """Parse a /ToUnicode CMap's bfchar and bfrange sections into a code -> text map."""
    mapping: dict[bytes, str] = {}
    text = data.decode("latin-1")

    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for code_hex, dst_hex in re.findall(r"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>", block):
            _add_mapping(mapping, bytes.fromhex(code_hex), bytes.fromhex(dst_hex))

    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for lo_hex, hi_hex, dst_hex in re.findall(
            r"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>", block
        ):
            width = len(lo_hex) // 2
            lo, hi = int(lo_hex, 16), int(hi_hex, 16)
            base = int(dst_hex, 16)
            dst_width = max(2, len(dst_hex) // 2)
            for offset, code_int in enumerate(range(lo, hi + 1)):
                code = code_int.to_bytes(width, "big")
                dst = (base + offset).to_bytes(dst_width, "big")
                _add_mapping(mapping, code, dst)

    return mapping


def _add_mapping(mapping: dict[bytes, str], code: bytes, dst: bytes) -> None:
    try:
        mapping[code] = dst.decode("utf-16-be")
    except UnicodeDecodeError:
        pass


def _decode_string(
    item: object,
    font_name: str | None,
    font_maps: dict[str, dict[bytes, str]],
    font_widths: dict[str, int],
) -> str:
    raw = bytes(item)
    mapping = font_maps.get(font_name or "", {})
    width = font_widths.get(font_name or "", 1)
    out = []
    for i in range(0, len(raw), width):
        out.append(mapping.get(raw[i : i + width], ""))
    return "".join(out)
