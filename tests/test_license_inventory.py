"""Checks on the third-party license inventory that do not require a built bundle.

The full check (does the mapping match the DLLs actually vendored?) needs a PyInstaller build and
lives in `test_packaging.py`. These run in the default suite because the failure they catch --
the inventory referencing a license text that isn't there -- would ship an installer whose notice
points at missing files, and that is worth catching without a one-minute build.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LICENSES_DIR = REPO_ROOT / "packaging" / "licenses"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from license_inventory import PY_FALLBACK  # noqa: E402


def test_every_fallback_license_text_exists():
    """Distributions with no license in their wheel fall back to a canonical text -- which must
    actually be present on disk, or the generated notice would point at a missing file."""
    missing = {
        filename
        for _spdx, filename in PY_FALLBACK.values()
        if not (LICENSES_DIR / filename).is_file()
    }
    assert not missing, f"PY_FALLBACK references license texts that are absent: {sorted(missing)}"


def test_license_texts_are_not_truncated_or_unfilled_templates():
    """Catch a truncated download, and catch shipping an SPDX template as if it were a license.

    SPDX publishes generic texts (e.g. BSD-3-Clause) whose *opening attribution line* is
    `Copyright (c) <year> <owner>`. Those are unusable as a distributed notice -- they name
    nobody -- so a project must be represented by its own license file instead.

    The check deliberately looks only at the opening lines. `<year>` appears legitimately deeper
    inside several correct texts: the GPL and LGPL carry it in their "How to Apply These Terms"
    appendices, and the FreeType License uses it in the credit wording it asks distributors to
    reproduce. Those are part of the canonical text and must be preserved verbatim, not filled
    in. FTL's credit obligation is discharged separately, in the generated notice.
    """
    for path in sorted(LICENSES_DIR.glob("LICENSE-*.txt")):
        if path.name == "LICENSE-THIRD-PARTY.txt":
            continue  # generated; covered by the inventory tests
        text = path.read_text(encoding="utf-8", errors="replace")
        assert len(text) > 200, f"{path.name} is suspiciously short ({len(text)} chars)"
        opening = "\n".join(text.splitlines()[:3])
        assert "<year>" not in opening and "<owner>" not in opening, (
            f"{path.name} opens with an unfilled SPDX attribution template, not a real notice"
        )


def test_notice_carries_the_freetype_credit():
    """The FreeType License section 3 requires this credit in distributor documentation.

    It is the only per-project credit in this bundle that a license text *obliges* the notice to
    reproduce, so it cannot be left to the generic project listing.
    """
    notice = (LICENSES_DIR / "LICENSE-THIRD-PARTY.txt").read_text(encoding="utf-8")
    assert "The FreeType" in notice and "freetype.org" in notice, (
        "generated notice is missing the credit required by the FreeType License"
    )
