"""Pin the parts of a PDF that would otherwise differ between identical runs.

PDFs record a creation timestamp and a random document ID. Both make byte comparison
useless, which breaks the golden-file testing Phase 1 depends on. See the design spec's
determinism invariant.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pikepdf

# An arbitrary fixed instant. The value does not matter; only that it never varies.
FIXED_TIMESTAMP = "D:20000101000000Z"


def pin_document_metadata(pdf_path: Path, *, title: str, lang: str) -> None:
    """Make the file byte-reproducible and set the metadata PDF/UA requires.

    The document ID is derived from the title so it stays stable across runs while
    still differing between documents.
    """
    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            meta["dc:title"] = title
            meta["dc:language"] = lang
            meta["xmp:CreateDate"] = "2000-01-01T00:00:00Z"
            meta["xmp:ModifyDate"] = "2000-01-01T00:00:00Z"
            meta["xmp:MetadataDate"] = "2000-01-01T00:00:00Z"

        # Do NOT open a second `pdf.open_metadata()` block here (even a no-op one). With its
        # default `set_pikepdf_as_editor=True`, pikepdf re-stamps xmp:MetadataDate to
        # `datetime.now()` on exit, which silently reintroduces nondeterminism -- this was
        # the actual cause of a one-field byte mismatch between two otherwise-identical runs.

        pdf.docinfo["/Title"] = title
        pdf.docinfo["/CreationDate"] = FIXED_TIMESTAMP
        pdf.docinfo["/ModDate"] = FIXED_TIMESTAMP
        pdf.Root["/Lang"] = pikepdf.String(lang)

        digest = hashlib.sha256(title.encode("utf-8")).digest()[:16]
        pdf.trailer["/ID"] = pikepdf.Array(
            [pikepdf.String(digest.decode("latin-1")), pikepdf.String(digest.decode("latin-1"))]
        )

        pdf.save(deterministic_id=False, preserve_pdfa=True)
