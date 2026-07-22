import os
import subprocess
import sys

# --- Pin PYTHONHASHSEED for the whole test suite -----------------------------------------
#
# PYTHONHASHSEED must be set *before* the interpreter starts; hash randomization is seeded
# once at process startup, so setting os.environ["PYTHONHASHSEED"] here would have no effect
# on *this* already-running interpreter (see docs/decisions/0003-determinism-scope.md). The
# only mechanism that genuinely works without external tooling (env wrapper scripts, a
# pytest-env dependency, etc.) is to detect the mismatch and relaunch `pytest` in a child
# process with the seed set correctly, before any test collection/import happens, then exit
# with the child's exit code. (A true in-place re-exec via os.execve was tried first, but
# segfaults under this platform's process model -- a spawned child process is the portable
# equivalent.) This block must run before any other import in this file that might rely on
# hash order.
_PINNED_HASH_SEED = "0"

if os.environ.get("PYTHONHASHSEED") != _PINNED_HASH_SEED:
    _env = dict(os.environ)
    _env["PYTHONHASHSEED"] = _PINNED_HASH_SEED
    _result = subprocess.run(
        [sys.executable, "-m", "pytest", *sys.argv[1:]],
        env=_env,
    )
    sys.exit(_result.returncode)

import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

import pikepdf  # noqa: E402
import pytest  # noqa: E402


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
