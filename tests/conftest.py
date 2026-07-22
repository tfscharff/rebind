import os
import shutil
from pathlib import Path

import pikepdf
import pytest


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
