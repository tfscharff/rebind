"""Command line entry point.

`rebind serve` is what the installed desktop app runs; `rebind convert` is the pipeline. The
frozen bundle's PyInstaller entry point remains `app.py`, so double-clicking the installed exe
still starts the server without going through argument parsing.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

from .extract import ExtractionError
from .pipeline import NoTextLayerError, convert


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rebind", description="Accessible PDF reconstruction")
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert_parser = subparsers.add_parser("convert", help="convert a born-digital PDF")
    convert_parser.add_argument("source", type=Path)
    convert_parser.add_argument("target", type=Path)
    convert_parser.add_argument("--title", default=None)
    convert_parser.add_argument("--lang", default="en")

    subparsers.add_parser("serve", help="start the local Rebind server")

    args = parser.parse_args(argv)

    if args.command == "serve":
        from .app import main as serve_main

        serve_main()
        return 0

    # `NoTextLayerError` is a subclass of `ExtractionError`; it must be caught first or the
    # scanned-source case would be reported with the generic message instead of its own.
    try:
        result = convert(args.source, args.target, title=args.title, lang=args.lang)
    except NoTextLayerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ExtractionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # The intended user is a librarian, not a developer: a raw traceback reads as "this
        # software is broken" and gives them nothing they can act on or report. Everything not
        # already handled above (bad page-label counts, a read-only network share rejecting the
        # write, a WeasyPrint rendering failure, ...) lands here instead of escaping as a crash.
        print(f"error: could not convert {args.source}: {exc}", file=sys.stderr)
        print("this is unexpected and worth reporting", file=sys.stderr)
        if os.environ.get("REBIND_DEBUG"):
            traceback.print_exc()
        return 1

    print(f"wrote {result.pdf_path}")
    if result.model_path is not None:
        print(f"wrote {result.model_path}")
    if result.scanned_pages:
        print(
            f"note: {len(result.scanned_pages)} page(s) had no text layer and became "
            f"placeholders: {', '.join(str(p) for p in result.scanned_pages)}",
            file=sys.stderr,
        )
    if result.source_was_tagged:
        print(
            "note: the source already declares a structure tree; it may already be accessible",
            file=sys.stderr,
        )
    ocr_source_pages = {
        node.page for node in result.document.nodes if "ocr-source" in node.flags
    }
    if ocr_source_pages:
        print(
            f"note: {len(ocr_source_pages)} page(s) look like OCR'd scans; their text is "
            "recognizer output and may contain errors",
            file=sys.stderr,
        )
    multi_column_pages = {
        node.page for node in result.document.nodes if "multi-column-suspected" in node.flags
    }
    if multi_column_pages:
        print(
            f"note: {len(multi_column_pages)} page(s) had a marginal column gutter, so the "
            "reconstructed multi-column reading order is uncertain; check them by hand",
            file=sys.stderr,
        )
    degraded_count = sum(1 for node in result.document.nodes if "degraded-region" in node.flags)
    if degraded_count:
        print(
            f"note: {degraded_count} region(s) had low-confidence text and are flagged "
            "'degraded-region' in the model for review",
            file=sys.stderr,
        )
    return 0
