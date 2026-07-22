"""Write original source pagination into the reconstructed PDF.

Rebind reflows the document, so output page N rarely equals source page N. Page labels keep
citation working: the viewer's page field shows the source's number. See design spec 5.3.

Every label is written as an explicit prefix with no numeric style. This is verbose but exact,
and it handles arbitrary source pagination (roman numerals, plate numbers, "A-17") without
Rebind having to infer a numbering scheme it cannot know.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf


def set_page_labels(pdf_path: Path, labels: list[str]) -> None:
    """Replace the document's page labels. One label per page, in order.

    Deliberately requires exactly one label per page (a `ValueError` otherwise), so partial
    labeling of only some pages is impossible by construction.
    """
    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        if len(labels) != len(pdf.pages):
            raise ValueError(
                f"got {len(labels)} labels for {len(pdf.pages)} pages; they must correspond"
            )

        nums = pikepdf.Array()
        for index, label in enumerate(labels):
            nums.append(index)
            nums.append(pdf.make_indirect(pikepdf.Dictionary(P=pikepdf.String(label))))

        pdf.Root["/PageLabels"] = pdf.make_indirect(pikepdf.Dictionary(Nums=nums))
        pdf.save()
