"""Document-wide fixes: scripts, legacy navigation, and the XMP metadata PDF/UA requires."""

from __future__ import annotations

from urllib.parse import urlsplit

import pikepdf
from pikepdf import Array, Dictionary, Name, String


def _strip_scripts(pdf: pikepdf.Pdf) -> int:
    """Remove every script the document carries. Returns how many were removed.

    Not done automatically: a script is behaviour the author put there, and removing behaviour
    without being asked is precisely what Rebind does not do. The app offers this as the fix
    alongside the check that reports it, so it happens because someone chose it.
    """
    removed = 0
    names = pdf.Root.get("/Names")
    if names is not None and "/JavaScript" in names:
        del names.JavaScript
        removed += 1
    if "/OpenAction" in pdf.Root:
        del pdf.Root.OpenAction
        removed += 1
    if "/AA" in pdf.Root:
        del pdf.Root.AA
        removed += 1
    for page in pdf.pages:
        if "/AA" in page.obj:
            del page.obj.AA
            removed += 1
        for annot in page.obj.get("/Annots") or []:
            if "/AA" in annot:
                del annot.AA
                removed += 1
            action = annot.get("/A")
            if action is not None and action.get("/S") == Name.JavaScript:
                del annot["/A"]
                removed += 1
    return removed


def _strip_legacy_destinations(pdf: pikepdf.Pdf) -> None:
    """Remove document navigation that PDF/UA-2 clause 8.8 forbids.

    PDF/UA-2 requires every internal destination (an outline/bookmark entry, a named destination,
    an OpenAction, or a Link annotation's GoTo target) to be a *structure destination* -- one that
    names a structure element, not a page + coordinates. Nothing before PDF 2.0 could produce that,
    so a born-digital source PDF's own navigation (an outline built by whatever authored it -- Word,
    LaTeX, WeasyPrint -- or in-text cross-reference/TOC links) is carried through verbatim on a page
    kept verbatim, and it fails this clause (confirmed on a real publisher sample: 137 Link-
    annotation and Outline destinations in one document). Rebind does not yet build its own
    structure-destination navigation (a real feature, tracked separately), so the safe choice is to
    drop the legacy kind rather than ship a document that claims PDF/UA-2 and fails it. This is real
    navigation lost, not merely a formality -- worth restoring properly, see progress notes.

    A *working* external link (a URI action, or GoToR into another file) is untouched -- clause 8.8
    is only about destinations *within* this document. A URI link whose target is unusable is
    dropped too, for a different reason entirely; see `_is_usable_uri`.
    """
    root = pdf.Root
    if "/Outlines" in root:
        del root.Outlines
    if "/OpenAction" in root:
        del root.OpenAction
    names = root.get("/Names")
    if names is not None and "/Dests" in names:
        del names.Dests

    for page in pdf.pages:
        annots = page.obj.get("/Annots")
        if annots is None:
            continue
        keep = []
        for annot in annots:
            is_link = annot.get("/Subtype") == Name.Link
            action = annot.get("/A") or Dictionary()
            is_internal_link = is_link and ("/Dest" in annot or action.get("/S") == Name.GoTo)
            is_broken_link = (
                is_link
                and action.get("/S") == Name.URI
                and not _is_usable_uri(str(action.get("/URI", "")))
            )
            if not is_internal_link and not is_broken_link:
                keep.append(annot)
        if len(keep) != len(annots):
            page.obj.Annots = Array(keep)


# Schemes whose targets are meaningless without an authority ("//host"). A URI in one of these
# with no host is not a link anyone can follow, whatever the producer meant by it.
_AUTHORITY_SCHEMES = frozenset({"http", "https", "ftp", "ftps"})


def _is_usable_uri(uri: str) -> bool:
    """Whether a Link annotation's URI target is something a reader could actually follow.

    A publisher's own auto-linker can fire on text that merely *looks* like a URL and emit a target
    that resolves to nothing -- confirmed in a real sample (1429254.pdf), where all four URI links
    are production defects: "http:0.5<en-dash>0.75" over a numeric range, "http:theminplace.We"
    over a sentence boundary. Rebind cannot repair those (there is no correct target to guess), and
    keeping them costs twice: a screen-reader user is announced a link that goes nowhere, and Adobe
    Acrobat's "Tagged annotations" / "Other elements alternate text" checks fail on them regardless
    of how the tagging is done. So they are dropped, exactly as legacy internal destinations are --
    removing a thing that never worked, never inventing a plausible-looking replacement.

    Deliberately permissive: anything with a scheme this can't reason about (doi:, urn:, tel:, a
    custom handler) is kept. Only a clearly unusable target is removed.
    """
    uri = uri.strip()
    if not uri or any(ch.isspace() or ord(ch) < 0x20 for ch in uri):
        return False
    parsed = urlsplit(uri)
    if not parsed.scheme:
        return False
    if parsed.scheme.lower() in _AUTHORITY_SCHEMES:
        # A bare host is legal DNS but never how a real document links out; requiring a dot is what
        # separates "example.com" from the auto-linker's "0.5<en-dash>0.75" garbage... which also
        # has dots, hence the hostname character check.
        host = parsed.hostname or ""
        return "." in host and all(c.isalnum() or c in "-." for c in host)
    if parsed.scheme.lower() == "mailto":
        return "@" in parsed.path
    return True


def _set_metadata(pdf: pikepdf.Pdf, *, title: str, lang: str) -> None:
    """Language, title, PDF/UA identifier, and the marked / display-title flags a reader needs."""
    pdf.Root.Lang = String(lang)
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.Root.ViewerPreferences = Dictionary(DisplayDocTitle=True)
    # The source's own XMP is discarded outright rather than edited in place. Two publisher
    # artifacts, confirmed in real samples, both survive an in-place edit and both break veraPDF's
    # strict metadata parser badly enough that it stops seeing OUR dc:title/pdfuaid entries too
    # (clauses 5 and 8.11.1 both fail), even though pikepdf's own reader still finds them fine:
    #   * A stray, non-namespaced top-level element (an Elsevier DRM/fingerprinting artifact) --
    #     well-formed XML, but noise no accessibility-relevant property ever has.
    #   * A second <rdf:Description> holding the publisher's own dc:title, written without an
    #     rdf:about attribute (a real sample, `1087881.pdf`). pikepdf's editor manages only the
    #     rdf:about="" description it owns, so this one is invisible to `meta.keys()` and a
    #     selective per-key strip can never reach it -- the publisher's title and ours both end up
    #     in the packet, and that duplication is what trips the parser, not either title alone.
    # Rebind does not need anything the source's XMP carries -- title and language are about to be
    # set explicitly -- so there is nothing to lose and a second undiscovered case to pre-empt by
    # not inheriting any of it.
    if Name.Metadata in pdf.Root:
        del pdf.Root[Name.Metadata]
    with pdf.open_metadata() as meta:
        meta["dc:title"] = title
        meta["dc:language"] = lang
        meta["pdfuaid:part"] = "2"          # PDF/UA-2 identification (veraPDF clause 5)
        meta["pdfuaid:rev"] = "2024"        # required alongside part for PDF/UA-2
    pdf.docinfo["/Title"] = title
