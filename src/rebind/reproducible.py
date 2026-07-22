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
    still differing between documents. Note this makes the ID a determinism knob, not a
    content identifier: two different documents that happen to share a title get the same
    /ID even though their bodies differ.
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

        # Pass the raw bytes straight to pikepdf.String rather than round-tripping through
        # `str` (e.g. via latin-1 decode/encode): pikepdf's text-string encoding path applies
        # PDFDocEncoding/UTF-16 heuristics to `str` input, which is wrong for an opaque binary
        # hash and can silently mutate bytes that happen to look like text.
        digest = hashlib.sha256(title.encode("utf-8")).digest()[:16]
        pdf.trailer["/ID"] = pikepdf.Array(
            [pikepdf.String(digest), pikepdf.String(digest)]
        )

        # deterministic_id=True: without it, qpdf regenerates the trailer's *second* /ID
        # element from wall-clock time and PID on every save, silently overriding the fixed
        # value we just assigned above and breaking byte-reproducibility across processes
        # (caught by test_two_runs_produce_identical_bytes_across_processes_and_hash_seeds).
        pdf.save(deterministic_id=True, preserve_pdfa=True)
