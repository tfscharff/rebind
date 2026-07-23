"""Write page labels into the reconstructed PDF.

Rebind reflows the document, so output page N rarely equals source page N. Page labels keep the
viewer's page field pointing at the right source page. See design spec 5.3.

As of Phase 1, the label written for each output page is the source's *sequential* page number
(1, 2, 3, ...) -- not the source's own printed pagination. A source with roman-numeral front
matter, plate numbers, or an "A-17"-style scheme does not get that scheme back; it gets the plain
ordinal of the source page instead. Extracting and preserving the document's own printed labels is
later work (see `pipeline._page_labels`).

Every label is written as an explicit prefix with no numeric style. This is verbose but exact, and
leaves room to plug in the source's real pagination scheme later without changing this function's
contract: one string label per page, written as-is.
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
