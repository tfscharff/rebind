import os
import shutil
from pathlib import Path

import pikepdf
import pytest

# --- On PYTHONHASHSEED -------------------------------------------------------------------
#
# This suite does not relaunch pytest to pin PYTHONHASHSEED for the whole run. It previously
# did, but that machinery bought only cosmetic reproducibility at one fixed seed: per
# docs/decisions/0003-determinism-scope.md, pinning the seed does not restore cross-process
# byte-identity anyway (8 subprocess builds with an identical pinned seed produced 8 distinct
# hashes), so the relaunch's original justification no longer holds. What remains is a real
# cost: an untraced child process silently breaks IDE debugger breakpoints and coverage
# collection for every test in the suite, not just the ones that care about the seed.
#
# Tests that genuinely need a controlled PYTHONHASHSEED (see test_reproducible.py) set it
# directly in the environment of their own subprocess build, which is the correct scope for
# that control and does not affect the process running pytest itself.


@pytest.fixture(scope="session")
def verapdf_exe() -> Path:
    """Locate verapdf.bat, preferring the REBIND_VERAPDF env var."""
    env = os.environ.get("REBIND_VERAPDF")
    if env and Path(env).exists():
        return Path(env)
    for candidate in (
        Path(r"C:\Program Files\veraPDF\verapdf.bat"),
        Path(r"C:\veraPDF\verapdf.bat"),
    ):
        if candidate.exists():
            return candidate
    found = shutil.which("verapdf")
    if found:
        return Path(found)
    pytest.skip("veraPDF not installed; set REBIND_VERAPDF to verapdf.bat")


@pytest.fixture
def untagged_pdf(tmp_path: Path) -> Path:
    """A minimal PDF with no tags at all. Must fail PDF/UA validation."""
    target = tmp_path / "untagged.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.save(target)
    return target
