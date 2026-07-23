"""Stage orchestration for the born-digital branch.

extract -> profile -> assemble -> emit -> render -> page labels -> validate

The document model is the deliverable; the PDF is a build artifact regenerable from it. Both are
written, and the model is written even when validation fails, because a failed run is exactly
when the model is most useful to inspect.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pikepdf

from .assemble import assemble
from .emit import PAGE_ANCHOR_PREFIX, to_html
from .extract import ExtractionError, Page, extract_pages, source_is_tagged
from .model import Document
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
    model_path: Path
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


def _page_labels(anchor_pages: dict[str, int], output_page_count: int) -> list[str]:
    """One label per output page: the source page whose anchor most recently appeared.

    A source page that reflows across several output pages gives all of them the same label,
    which is the honest answer -- they are all that source page.
    """
    start_of = {
        page: name.removeprefix(PAGE_ANCHOR_PREFIX)
        for name, page in sorted(anchor_pages.items(), key=lambda item: item[1])
        if name.startswith(PAGE_ANCHOR_PREFIX)
    }
    labels: list[str] = []
    current = "1"
    for index in range(1, output_page_count + 1):
        current = start_of.get(index, current)
        labels.append(current)
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

    anchor_pages = render_html_to_pdf_with_anchors(
        to_html(document), target, title=document.title, lang=lang
    )

    with pikepdf.open(target) as pdf:
        output_page_count = len(pdf.pages)
    set_page_labels(target, _page_labels(anchor_pages, output_page_count))

    model_path = target.with_suffix(".model.json")
    if write_model:
        model_path.write_text(document.to_json(), encoding="utf-8")

    validation = None
    if verapdf_exe is not None:
        validation = validate_pdf_ua(target, verapdf_exe=verapdf_exe)

    return ConversionResult(
        document=document,
        pdf_path=target,
        model_path=model_path,
        validation=validation,
        scanned_pages=document.scanned_pages,
        source_was_tagged=tagged,
    )
