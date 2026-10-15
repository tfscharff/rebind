"""Fetch the page-layout model into `models/` (gitignored), verifying its SHA-256.

Not normally needed: a source checkout fetches it on first use (`rebind.layout_model.ensure_model`,
called by the `rebind` command, the PyInstaller spec and the test suite). This is the explicit
version, for fetching ahead of time.

    uv run python scripts/fetch_models.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rebind.layout_model import ensure_model  # noqa: E402

if __name__ == "__main__":
    path = ensure_model()
    if path is None:
        sys.exit(1)
    print(f"{path} is present")
