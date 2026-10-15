"""Fetch the page-layout model into `models/` (gitignored), verifying its SHA-256.

The PP-DocLayoutV3 model (PaddleOCR, Apache-2.0) is 130 MB -- over GitHub's per-file limit, so it
is not committed. Run this once in a fresh clone and before building the bundle; the PyInstaller
spec refuses to build without it. Rebind itself never downloads anything at runtime: without the
file, it falls back to its geometric rules (`rebind.layout_model`).

    uv run python scripts/fetch_models.py
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from rebind.layout_model import MODEL_FILE, MODEL_SHA256, MODEL_URL  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    target = REPO_ROOT / "models" / MODEL_FILE
    if target.is_file() and sha256(target) == MODEL_SHA256:
        print(f"{target} is present and verified")
        return 0
    target.parent.mkdir(exist_ok=True)
    partial = target.with_suffix(".part")
    print(f"downloading {MODEL_URL}")
    urllib.request.urlretrieve(MODEL_URL, partial)
    actual = sha256(partial)
    if actual != MODEL_SHA256:
        partial.unlink()
        print(f"checksum mismatch: got {actual}, expected {MODEL_SHA256}", file=sys.stderr)
        return 1
    partial.replace(target)
    print(f"saved {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
