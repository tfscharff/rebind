"""Reproduce the ADR-0003 cross-process determinism experiment.

Builds the same PDF N times, each in its own fresh subprocess, with PYTHONHASHSEED pinned to
an identical value in every child. Prints each output's byte length and SHA-256, then reports
how many distinct hashes resulted.

This exists so the headline claim in docs/decisions/0003-determinism-scope.md ("8 builds
produced 8 distinct SHA-256 hashes") is reproducible from the repo, not just asserted in prose.

Usage:
    uv run python scripts/determinism_probe.py [N]

N defaults to 8 (the ADR's original sample). Pass a larger N (e.g. 20) to check whether the
byte-length split reported in the ADR (four runs at one size, four at another) holds up or
looks more like a two-state effect than address-space-randomization scatter.

Dependency-free (stdlib only) and Windows-safe: uses sys.executable to spawn children rather
than shelling out, and does not rely on any POSIX-only shell built-ins.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

_BUILD_SCRIPT = """
import sys
from pathlib import Path

from rebind.render import render_html_to_pdf
from rebind.reproducible import pin_document_metadata

target = Path(sys.argv[1])
render_html_to_pdf(
    "<h1>Determinism</h1><p>Two runs must produce identical bytes.</p>",
    target,
    title="Determinism",
    lang="en",
)
pin_document_metadata(target, title="Determinism", lang="en")
"""

_HASH_SEED = "0"


def _build_once(target: Path) -> bytes:
    import os

    env = dict(os.environ)
    env["PYTHONHASHSEED"] = _HASH_SEED
    subprocess.run(
        [sys.executable, "-c", _BUILD_SCRIPT, str(target)],
        env=env,
        check=True,
        capture_output=True,
    )
    return target.read_bytes()


def main(argv: list[str]) -> int:
    n = int(argv[1]) if len(argv) > 1 else 8

    print(f"Running {n} independent subprocess builds, all with PYTHONHASHSEED={_HASH_SEED}...")
    print(f"{'run':>3}  {'size(bytes)':>11}  sha256")

    hashes: list[str] = []
    sizes: Counter[int] = Counter()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for i in range(1, n + 1):
            target = tmp_path / f"probe_{i}.pdf"
            data = _build_once(target)
            digest = hashlib.sha256(data).hexdigest()
            hashes.append(digest)
            sizes[len(data)] += 1
            print(f"{i:>3}  {len(data):>11}  {digest}")

    distinct = len(set(hashes))
    print()
    print(f"{distinct} distinct SHA-256 hash(es) out of {n} runs.")
    print("Byte-length distribution:")
    for size, count in sorted(sizes.items()):
        print(f"  {size} bytes: {count} run(s)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
