"""Fetch the model files into `models/` (gitignored), each checked against its SHA-256.

Not normally needed: a source checkout fetches them on first use (`rebind.weights.ensure_weights`,
called by the `rebind` command, the PyInstaller spec and the tests that need a model). This is the
explicit version, for fetching ahead of time.

    uv run python scripts/fetch_models.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rebind.weights import ensure_weights, model_dir  # noqa: E402

if __name__ == "__main__":
    if not ensure_weights():
        sys.exit(1)
    print(f"all models present in {model_dir()}")
