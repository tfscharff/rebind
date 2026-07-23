"""Command line entry point.

`rebind serve` is what the installed desktop app runs; `rebind convert` is the pipeline. The
frozen bundle's PyInstaller entry point remains `app.py`, so double-clicking the installed exe
still starts the server without going through argument parsing.
"""

from __future__ import annotations

import argparse
import sys
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
    return 0
