"""Command line entry point.

`rebind serve` is what the installed desktop app runs; `rebind convert` remediates a PDF (keeps its
appearance, adds accessibility). The frozen bundle's PyInstaller entry point remains `app.py`, so
double-clicking the installed exe still starts the server without going through argument parsing.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

from .extract import ExtractionError
from .remediate import remediate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rebind", description="Make a PDF accessible in place")
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert_parser = subparsers.add_parser(
        "convert", help="make a PDF accessible, preserving its appearance")
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

    try:
        result = remediate(args.source, args.target, title=args.title, lang=args.lang)
    except ExtractionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # The intended user is a librarian, not a developer: a raw traceback reads as "this
        # software is broken" and gives them nothing they can act on. Anything not handled above
        # lands here instead of escaping as a crash.
        print(f"error: could not process {args.source}: {exc}", file=sys.stderr)
        print("this is unexpected and worth reporting", file=sys.stderr)
        if os.environ.get("REBIND_DEBUG"):
            traceback.print_exc()
        return 1

    print(f"wrote {result.pdf_path}")
    if result.ocr_pages:
        print(
            f"note: {len(result.ocr_pages)} scanned page(s) were text-recognized; the recognized "
            "text may contain errors",
            file=sys.stderr,
        )
    if result.empty_pages:
        print(
            f"note: {len(result.empty_pages)} page(s) had no readable text and none could be "
            f"recovered (they may be blank or image-only): "
            f"{', '.join(str(p) for p in result.empty_pages)}",
            file=sys.stderr,
        )
    return 0
