"""The document model -- Rebind's source of truth. The PDF is a build artifact.

Phase 1 implements a deliberate subset of the node types in the governing design. The rest are
not stubbed: a stub invites code to depend on a shape that has not been designed yet.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from hashlib import blake2b

BBox = tuple[float, float, float, float]

# Bboxes are normalized to a fraction of the page dimension, then quantized in that fraction
# space, before hashing. That makes the id tolerant of both sub-point extraction jitter and of
# re-extraction at a different page scale (e.g. OCR at a different DPI). Node identity has to
# survive reprocessing for corrections to be storable as a diff layer over the model (governing
# design 5.7); an id that churns on jitter or rescan resolution would silently orphan every human
# edit. 0.001 of a page dimension is about 0.6pt horizontally and 0.8pt vertically on US Letter --
# comfortably coarser than sub-point jitter and far finer than any real content boundary.
_BBOX_QUANTUM_FRACTION = 0.001


@dataclass
class Node:
    """Base for every node. The provenance fields are not optional anywhere in Rebind."""

    id: str
    page: int
    bbox: BBox
    confidence: float
    stage: str
    flags: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return type(self).__name__


@dataclass
class Heading(Node):
    level: int = 1
    text: str = ""


@dataclass
class Paragraph(Node):
    text: str = ""


@dataclass
class ListItem(Node):
    text: str = ""


@dataclass
class ListNode(Node):
    ordered: bool = False
    items: list[ListItem] = field(default_factory=list)


@dataclass
class Artifact(Node):
    """Running header, footer or page number. Excluded from the reading order on purpose."""

    text: str = ""


@dataclass
class Placeholder(Node):
    """The honest-failure node. Never a plausible guess."""

    reason: str = ""


@dataclass
class PageBreak(Node):
    label: str = ""


_NODE_TYPES = {
    cls.__name__: cls
    for cls in (Heading, Paragraph, ListItem, ListNode, Artifact, Placeholder, PageBreak)
}


def node_id(*, page: int, bbox: BBox, page_width: float, page_height: float, text: str) -> str:
    """A stable id from page, normalized bbox and content fingerprint.

    The bbox is converted to a fraction of the page dimensions before quantizing, so the id does
    not change if the same content is later extracted from a page recorded at a different scale.
    """
    x0, y0, x1, y1 = bbox
    normalized = (
        round(x0 / page_width / _BBOX_QUANTUM_FRACTION) if page_width else 0,
        round(y0 / page_height / _BBOX_QUANTUM_FRACTION) if page_height else 0,
        round(x1 / page_width / _BBOX_QUANTUM_FRACTION) if page_width else 0,
        round(y1 / page_height / _BBOX_QUANTUM_FRACTION) if page_height else 0,
    )
    digest = blake2b(digest_size=8)
    digest.update(f"{page}|{normalized}|{text}".encode("utf-8"))
    return digest.hexdigest()


@dataclass
class Document:
    title: str
    lang: str
    nodes: list[Node] = field(default_factory=list)
    scanned_pages: tuple[int, ...] = ()
    source_was_tagged: bool = False

    def to_json(self) -> str:
        payload = {
            "title": self.title,
            "lang": self.lang,
            "scanned_pages": list(self.scanned_pages),
            "source_was_tagged": self.source_was_tagged,
            "nodes": [{"kind": node.kind, **asdict(node)} for node in self.nodes],
        }
        return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> Document:
        payload = json.loads(raw)
        nodes: list[Node] = []
        for entry in payload["nodes"]:
            data = dict(entry)
            node_cls = _NODE_TYPES[data.pop("kind")]
            if node_cls is ListNode:
                data["items"] = [
                    ListItem(**{**item, "bbox": tuple(item["bbox"])})
                    for item in data.get("items", [])
                ]
            data["bbox"] = tuple(data["bbox"])
            nodes.append(node_cls(**data))
        return cls(
            title=payload["title"],
            lang=payload["lang"],
            nodes=nodes,
            scanned_pages=tuple(payload["scanned_pages"]),
            source_was_tagged=payload["source_was_tagged"],
        )
