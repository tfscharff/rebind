"""Document model -> semantic HTML, the input to `render.render_html_to_pdf`.

HTML is an intermediate representation, not an output. It exists because WeasyPrint's tagged
PDF/UA generation is driven from semantic HTML, and because generating rather than patching is
what makes most of WCAG 2.1 AA true by construction.
"""

from __future__ import annotations

import html as html_escape

from .model import (
    Artifact,
    Document,
    Heading,
    ListNode,
    PageBreak,
    Paragraph,
    Placeholder,
)

PAGE_ANCHOR_PREFIX = "rebind-page-"


def _esc(text: str) -> str:
    return html_escape.escape(text, quote=False)


def _esc_attr(text: str) -> str:
    return html_escape.escape(text, quote=True)


def to_html(document: Document) -> str:
    parts: list[str] = []

    for node in document.nodes:
        if isinstance(node, Artifact):
            # Deliberately dropped from the reading order so assistive technology does not
            # announce the running header on every page. Provenance is retained in the model.
            continue

        if isinstance(node, PageBreak):
            # An empty anchor, used after rendering to map output pages back to source page
            # labels (see pipeline). It carries no text, so it adds nothing to the spoken flow.
            # The id is built from the node's page number, not its label, so it never needs
            # escaping as an attribute value; the label itself is provenance-only here.
            parts.append(f'<span id="{PAGE_ANCHOR_PREFIX}{node.page}"></span>')
            continue

        if isinstance(node, Heading):
            level = min(max(node.level, 1), 6)
            parts.append(f"<h{level}>{_esc(node.text)}</h{level}>")
            continue

        if isinstance(node, Paragraph):
            parts.append(f"<p>{_esc(node.text)}</p>")
            continue

        if isinstance(node, ListNode):
            tag = "ol" if node.ordered else "ul"
            items = "".join(f"<li>{_esc(item.text)}</li>" for item in node.items)
            parts.append(f"<{tag}>{items}</{tag}>")
            continue

        if isinstance(node, Placeholder):
            # The honest-failure rendering. It is visible and spoken on purpose: a silent gap
            # would let a reader believe they had the whole document. The reason is included
            # (escaped, since it is text extracted from the scan's provenance data, not a
            # trusted literal) because it is the only clue a human reviewer gets for what to
            # go check in the source.
            parts.append(
                f'<p class="rebind-placeholder">[content not recoverable from source, '
                f"p. {node.page}: {_esc(node.reason)}]</p>"
            )
            continue

        # Every other Node subclass (e.g. a bare ListItem that never made it into a ListNode)
        # has no rendering rule here. Silently skipping it would drop content the reader never
        # gets to see -- exactly the failure mode this project exists to prevent. Fail loudly
        # instead of fabricating a rendering or fabricating its absence.
        raise ValueError(
            f"emit.to_html: no rendering rule for node kind {node.kind!r} (id={node.id!r})"
        )

    return "\n".join(parts)
