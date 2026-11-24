"""Build the frozen bundle and compile the installer from that same bundle.

    uv run python scripts/build_installer.py            # bundle in packaging/dist
    uv run python scripts/build_installer.py --alt      # ... in packaging/dist_alt, when Windows
                                                        # is holding a handle on packaging/dist

The two steps are one command because run apart they went wrong: PyInstaller writes to `dist`
under whatever folder it is run from, the installer script reads `packaging/dist`, and a release
build run from the repository root compiled an installer from a bundle five weeks and six
versions old without a word. Here the bundle's folder is named to both, and the installer is not
compiled unless the bundle was written by this run.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

PACKAGING = Path(__file__).resolve().parent.parent / "packaging"
ISCC = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"


def main() -> int:
    suffix = "_alt" if "--alt" in sys.argv[1:] else ""
    dist, work = PACKAGING / f"dist{suffix}", PACKAGING / f"build{suffix}"
    started = time.time()
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "rebind.spec", "--noconfirm",
         "--distpath", str(dist), "--workpath", str(work)],
        cwd=PACKAGING, check=True)
    exe = dist / "rebind" / "rebind.exe"
    if not exe.is_file() or exe.stat().st_mtime < started:
        print(f"{exe} was not written by this build; not compiling an installer from it.",
              file=sys.stderr)
        return 1
    subprocess.run([str(ISCC), f"/DDistDir={dist.name}", "rebind.iss"], cwd=PACKAGING, check=True)
    print(PACKAGING / "Output" / "rebind-setup.exe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
