"""HTML to tagged PDF/UA rendering.

Rebind generates its output rather than patching a source PDF, which is what makes most of
WCAG 2.1 AA achievable by construction. See the design spec, section 2.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from weasyprint import HTML

_DOCUMENT_TEMPLATE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  @page {{ size: letter; margin: 1in; }}
  body {{ font-family: "DejaVu Serif", Georgia, serif; font-size: 11pt; line-height: 1.45;
          color: #111; background: #fff; }}
  h1, h2, h3 {{ line-height: 1.2; }}
  table {{ border-collapse: collapse; }}
  th, td {{ border: 1px solid #444; padding: 4pt 6pt; text-align: left; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def _build_document(html_body: str, *, title: str, lang: str) -> str:
    """Normalize headings and wrap the body fragment in `_DOCUMENT_TEMPLATE`.

    Shared by `render_html_to_pdf` and `render_html_to_pdf_with_anchors` so the heading
    normalization and template assembly never drift out of sync between them.
    """
    normalized_body = _normalize_heading_levels(html_body)
    return _DOCUMENT_TEMPLATE.format(
        lang=html.escape(lang, quote=True), title=html.escape(title, quote=True),
        body=normalized_body,
    )


def render_html_to_pdf(html_body: str, target: Path, *, title: str, lang: str = "en") -> Path:
    """Render an HTML body fragment to a tagged PDF/UA-1 file.

    The document colours are fixed to guarantee WCAG 1.4.3 contrast, which we can do
    precisely because we generate the output rather than inherit it.

    Heading levels in `html_body` are normalized before rendering (see
    `_normalize_heading_levels`): PDF/UA-1 clause 7.4.2 requires headings to start at H1 and
    never skip an intervening level, but Rebind reconstructs scans that routinely start
    mid-chapter or yield irregular recognized heading levels (e.g. only H3 headings present,
    or an H1 followed directly by an H3), which would otherwise fail validation outright.
    """
    document = _build_document(html_body, title=title, lang=lang)
    HTML(string=document).write_pdf(
        target,
        pdf_variant="pdf/ua-1",
        uncompressed_pdf=False,
    )
    return target


def render_html_to_pdf_with_anchors(
    html_body: str, target: Path, *, title: str, lang: str = "en"
) -> dict[str, int]:
    """Render as `render_html_to_pdf` does, and report which output page each anchor landed on.

    Rebind reflows, so output page N is not source page N. `pagelabels.set_page_labels` needs
    exactly one label per output page, which means the source page each output page belongs to
    has to be discovered after layout rather than assumed. WeasyPrint exposes `page.anchors`
    for precisely this: after `.render()`, each page in `document.pages` carries a mapping of
    anchor name to its (x, y) position on that page, so anchor names present on a page are
    just that mapping's keys.
    """
    document = _build_document(html_body, title=title, lang=lang)
    rendered = HTML(string=document).render()

    anchor_pages: dict[str, int] = {}
    for index, page in enumerate(rendered.pages, start=1):
        for name in page.anchors:
            anchor_pages.setdefault(name, index)

    rendered.write_pdf(target, pdf_variant="pdf/ua-1", uncompressed_pdf=False)
    return anchor_pages


_HEADING_TAG_RE = re.compile(r"<(/?)h([1-6])((?:\s[^>]*)?)>", re.IGNORECASE)


def _normalize_heading_levels(html_body: str) -> str:
    """Renumber h1-h6 tags so the sequence never skips a level, without changing nesting.

    PDF/UA-1 clause 7.4.2 requires that if heading tags are used, H1 is first and a
    descending sequence never skips an intervening level (H1 -> H3 with no H2 between them is
    non-conformant, and so is a document using only H2 with no H1 at all). Rebind's source
    material is damaged/reconstructed scans, so recognized heading levels routinely start
    mid-chapter (H2/H3 only) or skip a level, and this cannot be fixed by asking the input to
    be well-formed -- it must be normalized on the way to the renderer.

    The algorithm is a standard "logical heading level" renumbering (the same shape used by
    several HTML accessibility remediation tools): walk the headings in document order,
    keeping a stack of (raw_level, mapped_level) pairs for the currently "open" ancestors.
    For each heading, pop the stack while its top is at or above the current raw level (i.e.
    it is a sibling or an uncle, not an ancestor), then the new mapped level is one more than
    whatever remains on top of the stack (or 1 if the stack is now empty). This guarantees:
    - The first heading anywhere in the document always maps to H1, even if its raw level is
      H2 or deeper -- a document that genuinely starts at H2 becomes H1 in the output, which
      is the correct normalization (a document truly does start its own top level wherever
      its first heading is), not a loss of information: relative nesting among the headings
      that follow is preserved exactly, only the absolute levels shift down uniformly.
    - Levels only ever increase by exactly 1 at a time, never skip.
    - A later heading that returns to a shallower raw level maps back to the same mapped
      level its earlier sibling at that raw level used (via the stack). Worked example:
      raw H1, H3, H2, H3 normalizes to mapped H1, H2, H2, H3 -- the first H3 (under H1, no H2
      between them) maps to H2 (one more than its open ancestor); the following raw H2 is
      shallower than that open H3, so it pops back past it and maps using the stack entry the
      original H1 left, giving H2 again; the final H3 then reopens under that new H2, giving H3.

    This never invents structure the source didn't have -- gaps are compressed, not filled with
    invented intermediate headings. It also never merges genuinely distinct levels *within the
    first six* -- but HTML and PDF/UA only define h1 through h6, so `mapped_level` is clamped to
    6 here (`min(..., 6)`), and a document whose recognized heading styles nest more than six
    levels deep genuinely does lose the distinction between level 7+ styles: they collapse
    together into h6 in this rendering. That loss is real, not hypothetical (a long catalog
    routinely has a dozen distinct heading styles); it is visible in the document model, where
    `assemble` flags the affected `Heading` nodes with `heading-level-collapsed` before this
    function ever sees them, rather than being silently absorbed here where a reviewer has no way
    to notice it happened.
    """
    matches = list(_HEADING_TAG_RE.finditer(html_body))
    if not matches:
        return html_body

    stack: list[tuple[int, int]] = []  # (raw_level, mapped_level) for open headings
    mapped_for_open: list[int] = []  # mapped level assigned to each opening tag, in order

    for match in matches:
        if match.group(1) == "/":
            continue
        raw_level = int(match.group(2))
        while stack and stack[-1][0] >= raw_level:
            stack.pop()
        mapped_level = min((stack[-1][1] + 1) if stack else 1, 6)
        stack.append((raw_level, mapped_level))
        mapped_for_open.append(mapped_level)

    pieces: list[str] = []
    last_end = 0
    open_iter = iter(mapped_for_open)
    close_stack: list[int] = []
    for match in matches:
        pieces.append(html_body[last_end:match.start()])
        if match.group(1) == "/":
            mapped_level = close_stack.pop()
            pieces.append(f"</h{mapped_level}>")
        else:
            mapped_level = next(open_iter)
            close_stack.append(mapped_level)
            pieces.append(f"<h{mapped_level}{match.group(3)}>")
        last_end = match.end()
    pieces.append(html_body[last_end:])
    return "".join(pieces)
