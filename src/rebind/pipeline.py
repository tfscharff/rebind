"""Stage orchestration for the born-digital branch.

extract -> profile -> assemble -> write model -> emit -> render -> page labels -> validate

The document model is the deliverable; the PDF is a build artifact regenerable from it. The model
is written immediately after assembly, before rendering or page-labelling run, because a failed
run there is exactly when the model is most useful to inspect -- and it is the two extraction
passes, not rendering, that are expensive to redo.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pikepdf

from .assemble import assemble
from .emit import PAGE_ANCHOR_PREFIX, to_html
from .extract import ExtractionError, Page, extract_pages, source_is_tagged
from .model import Document, PageBreak
from .pagelabels import set_page_labels
from .profile import build_profile
from .render import render_html_to_pdf_with_anchors
from .validate import ValidationResult, validate_pdf_ua


class NoTextLayerError(ExtractionError):
    """No page in the source has a text layer, so this is a scan, not a born-digital PDF."""


@dataclass
class ConversionResult:
    document: Document
    pdf_path: Path
    model_path: Path | None
    validation: ValidationResult | None
    scanned_pages: tuple[int, ...]
    source_was_tagged: bool


def _counting(pages: Iterable[Page], counter: list[int]) -> Iterator[Page]:
    """Pass pages through unchanged while counting them in `counter[0]`.

    Counting a page is O(1) extra state, unlike materializing the pages themselves into a
    list, so pass one's bounded-memory property (style statistics only, never held pages) is
    preserved even though the pipeline also needs to know whether the source had any pages
    at all -- distinguishing "zero pages" from "pages with no text" further down.
    """
    for page in pages:
        counter[0] += 1
        yield page


# Emitted for an output page that precedes every anchor when no anchor is available to derive a
# label from at all (e.g. `anchor_pages` is empty). An empty string is a legal PDF page label and
# reads unambiguously as "no source page identified" -- unlike "1", it can never be mistaken for a
# real page number the source never had.
_UNKNOWN_SOURCE_PAGE_LABEL = ""


def _page_labels(
    anchor_pages: dict[str, int], output_page_count: int, document: Document
) -> list[str]:
    """One label per output page: the source page whose anchor most recently appeared.

    A source page that reflows across several output pages gives all of them the same label,
    which is the honest answer -- they are all that source page.

    Labels are read from the document's `PageBreak` nodes (keyed by source page number), not
    reconstructed from the anchor id -- `PageBreak.label` is where roman numerals or labels like
    "A-17" will live once real source pagination is extracted, and the anchor id is only ever a
    page *number*. Falling back to the page number itself only covers the (currently impossible)
    case of a source page with no matching `PageBreak` node.
    """
    label_of_source_page = {
        node.page: node.label for node in document.nodes if isinstance(node, PageBreak)
    }

    # Several source-page anchors can land on the same output page (a source page shorter than an
    # output page). Which one should win is a decision this module must make explicitly: sorting
    # only on output page (as WeasyPrint's `page.anchors` iteration order would otherwise decide
    # implicitly) makes the winner depend on undocumented renderer behaviour. Sorting on
    # `(output_page, source_page)` with the source page parsed as an int -- not compared as a
    # string, which would order page "10" before page "2" -- makes the highest source page the
    # deterministic winner for a given output page, independent of insertion order.
    entries = sorted(
        (
            (output_page, int(name.removeprefix(PAGE_ANCHOR_PREFIX)))
            for name, output_page in anchor_pages.items()
            if name.startswith(PAGE_ANCHOR_PREFIX)
        ),
        key=lambda entry: (entry[0], entry[1]),
    )

    start_of: dict[int, int] = {}
    for output_page, source_page in entries:
        start_of[output_page] = source_page

    labels: list[str] = []
    # The initial value for output pages before the first anchor is derived from the first anchor
    # itself, never fabricated: inventing a source page number for output pages that precede any
    # known anchor is exactly what this project forbids (see Finding 5).
    current_source_page: int | None = entries[0][1] if entries else None
    for index in range(1, output_page_count + 1):
        current_source_page = start_of.get(index, current_source_page)
        if current_source_page is None:
            labels.append(_UNKNOWN_SOURCE_PAGE_LABEL)
        else:
            labels.append(label_of_source_page.get(current_source_page, str(current_source_page)))
    return labels


def convert(
    source: Path,
    target: Path,
    *,
    title: str | None = None,
    lang: str = "en",
    verapdf_exe: Path | None = None,
    write_model: bool = True,
) -> ConversionResult:
    source, target = Path(source), Path(target)
    tagged = source_is_tagged(source)

    # Pass one. Only style statistics are retained, so this does not hold the document. A
    # source page count is tallied alongside it (O(1) extra state) purely to tell "zero pages"
    # apart from "pages with no extractable text" below -- both would otherwise present
    # identically as `profile.body is None`.
    page_count = [0]
    profile = build_profile(_counting(extract_pages(source), page_count))
    if page_count[0] == 0:
        raise ExtractionError(f"{source} has no pages to convert")
    if profile.body is None:
        raise NoTextLayerError(
            f"{source} has no extractable text on any page. This is a scanned document; the "
            "OCR branch is not implemented yet."
        )

    # Pass two. `extract_pages` is a generator exhausted by pass one, so this is a fresh call,
    # not the same iterator -- reusing the exhausted one would silently assemble zero nodes.
    document = assemble(
        extract_pages(source),
        profile,
        title=title or source.stem,
        lang=lang,
        source_was_tagged=tagged,
    )

    # Written now, before rendering or page-labelling run, so a WeasyPrint failure or a
    # `set_page_labels` `ValueError` never throws away a model that cost two extraction passes to
    # build (Finding 2).
    model_path = target.with_suffix(".model.json")
    if write_model:
        model_path.write_text(document.to_json(), encoding="utf-8")

    anchor_pages = render_html_to_pdf_with_anchors(
        to_html(document), target, title=document.title, lang=lang
    )

    with pikepdf.open(target) as pdf:
        output_page_count = len(pdf.pages)
    set_page_labels(target, _page_labels(anchor_pages, output_page_count, document))

    validation = None
    if verapdf_exe is not None:
        validation = validate_pdf_ua(target, verapdf_exe=verapdf_exe)

    return ConversionResult(
        document=document,
        pdf_path=target,
        model_path=model_path if write_model else None,
        validation=validation,
        scanned_pages=document.scanned_pages,
        source_was_tagged=tagged,
    )
