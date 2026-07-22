"""Read-only inspection of a PDF's structure tree, for tests and reporting."""

from __future__ import annotations

from pathlib import Path

import pikepdf


def structure_element_types(pdf_path: Path) -> set[str]:
    """Return every structure-element type name present in the document's tag tree."""
    found: set[str] = set()
    with pikepdf.open(pdf_path) as pdf:
        root = pdf.Root.get("/StructTreeRoot")
        if root is None:
            return found
        _walk(root.get("/K"), found)
    return found


def _walk(node, found: set[str]) -> None:
    if node is None:
        return
    if isinstance(node, pikepdf.Array):
        for child in node:
            _walk(child, found)
        return
    if isinstance(node, pikepdf.Dictionary):
        struct_type = node.get("/S")
        if struct_type is not None:
            found.add(str(struct_type).lstrip("/"))
        _walk(node.get("/K"), found)
