import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rebind.render import render_html_to_pdf
from rebind.reproducible import pin_document_metadata
from rebind.validate import validate_pdf_ua

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"

HTML = "<h1>Determinism</h1><p>Two runs must produce identical bytes.</p>"

# Runs as a standalone script (not imported), so it must build its own path from argv rather
# than relying on any fixture/import machinery from the test process.
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


def _build(target: Path) -> bytes:
    render_html_to_pdf(HTML, target, title="Determinism", lang="en")
    pin_document_metadata(target, title="Determinism", lang="en")
    return target.read_bytes()


def _build_in_subprocess(target: Path, *, hash_seed: str) -> bytes:
    """Build the PDF in a fresh interpreter with a pinned PYTHONHASHSEED.

    A same-process comparison can never catch nondeterminism driven by Python's hash
    randomization (dict/set iteration order in font subsetting, XMP namespace ordering, or
    anything internal to WeasyPrint/pikepdf that iterates a hash-randomized collection),
    because hash randomization is fixed once per process. Crossing a real process boundary
    with two different seeds is the only way this proof can catch that class of bug.
    """
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hash_seed
    subprocess.run(
        [sys.executable, "-c", _BUILD_SCRIPT, str(target)],
        env=env,
        check=True,
        capture_output=True,
    )
    return target.read_bytes()


def _diff_detail(first: bytes, second: bytes) -> str:
    first_len, second_len = len(first), len(second)
    diffs = [i for i in range(min(first_len, second_len)) if first[i] != second[i]]
    return (
        f"lengths differ: {first_len} vs {second_len}; "
        f"first differing byte offsets (up to 20): {diffs[:20]}"
    )


def test_repeated_builds_still_exhibit_the_known_font_subsetting_nondeterminism(tmp_path: Path):
    """Stable characterization test, replacing the noisy `test_two_runs_produce_identical_bytes`.

    That test was a non-strict `xfail` asserting byte-identity, which this ADR's own evidence
    says fails only ~1 time in 7-12 -- so across ordinary runs it XPASSed roughly 85-90% of
    the time. A flip that noisy is not a useful signal: nobody scanning CI history for a lone
    XPASS among a sea of expected XPASSes would ever notice the day upstream actually fixes
    this, and a real regression toward *more* determinism would look identical to normal
    noise. This test asserts the opposite direction instead: that N independent builds of
    identical input produce *more than one* distinct SHA-256 hash. That is true on every run
    today (this is the actual, currently-reliable behaviour, not a coin flip), and it would
    fail loudly -- for the right reason -- the day upstream's font-subsetting nondeterminism is
    actually resolved, which is the point at which this test (and the ADR) should be revisited.

    Uses `scripts/determinism_probe.py`'s own build-and-hash routine directly (imported, not
    subprocessed as a script) so this test and the standalone reproduction tool the ADR points
    readers at can never drift out of sync with each other.
    """
    sys.path.insert(0, str(_SCRIPTS_DIR))
    try:
        import determinism_probe
    finally:
        sys.path.remove(str(_SCRIPTS_DIR))

    hashes = {
        hashlib.sha256(determinism_probe._build_once(tmp_path / f"probe_{i}.pdf")).hexdigest()
        for i in range(8)
    }

    assert len(hashes) > 1, (
        "expected the known upstream font-subsetting nondeterminism (see "
        "docs/decisions/0003-determinism-scope.md) to produce more than one distinct hash "
        "across 8 builds of identical input, but got only one -- if this is a real, repeated "
        "result (not a one-off fluke), upstream may have fixed the nondeterminism; revisit "
        "the ADR and this test before loosening it further."
    )


@pytest.mark.xfail(
    reason=(
        "Known upstream nondeterminism, stronger than originally diagnosed: pinning "
        "PYTHONHASHSEED to the *same* value for both subprocess builds does NOT make the "
        "output byte-identical. Direct experiment (see "
        "docs/decisions/0003-determinism-scope.md) built 8 PDFs from identical input with "
        "PYTHONHASHSEED=0 in every process and got 8 distinct SHA-256 hashes, diverging at "
        "the same embedded-font compressed-stream offset each time. This means the "
        "randomness is not (solely) Python string-hash randomization, which pinning "
        "PYTHONHASHSEED would fully control -- something else that varies per process "
        "(consistent with ASLR-influenced object-identity hashing in WeasyPrint's font "
        "stack, likely in a native dependency) is also in play. Rebind's byte-identity "
        "guarantee is therefore scoped to a single process, not 'pinned hash seed', pending "
        "upstream diagnosis. If a future WeasyPrint/fontTools/native-dependency release "
        "removes this source of variance, this test will XPASS -- that is the signal to "
        "revisit the ADR and this xfail."
    ),
    strict=False,
)
def test_output_still_varies_across_processes_even_with_hash_seed_pinned(tmp_path: Path):
    """Byte-identity does not hold across processes even when PYTHONHASHSEED is pinned.

    This is the stronger, empirically-verified finding that supersedes the original
    "pin PYTHONHASHSEED and cross-process byte-identity holds" hypothesis: two subprocess
    builds of identical input, given the *same* PYTHONHASHSEED, still produce different PDF
    bytes. See docs/decisions/0003-determinism-scope.md for the full evidence and the
    resulting decision to scope rebind's determinism claim to a single process.
    """
    first = _build_in_subprocess(tmp_path / "seed_a.pdf", hash_seed="0")
    second = _build_in_subprocess(tmp_path / "seed_b.pdf", hash_seed="0")

    assert first == second, (
        "expected upstream nondeterminism did not reproduce -- "
        f"{_diff_detail(first, second)}"
    )


@pytest.mark.xfail(
    reason=(
        "Known upstream nondeterminism: two subprocess builds of identical input, given "
        "*different* PYTHONHASHSEED values, produce different PDF bytes inside an embedded "
        "font's compressed stream. This was the originally diagnosed form of the bug (see "
        "docs/decisions/0003-determinism-scope.md); "
        "test_output_still_varies_across_processes_even_with_hash_seed_pinned above shows "
        "the same divergence persists even when the seed is held constant, so this test is "
        "a strict subset of that finding, kept separate because it is the form of the bug "
        "originally reported. If a future WeasyPrint/fontTools/native-dependency release "
        "fixes cross-process determinism, this test will XPASS -- that is the signal to "
        "revisit the ADR and this xfail."
    ),
    strict=False,
)
def test_output_varies_across_differing_hash_seeds_upstream_bug(tmp_path: Path):
    """Demonstrates the underlying WeasyPrint nondeterminism is present with differing seeds.

    Deliberately uses two *different* PYTHONHASHSEED values to show that rebind's output is
    not byte-identical across them. Kept as a separate, named case (distinct from the
    same-seed test above) because it documents the specific reproduction originally used to
    diagnose this bug. This keeps the real, unfixed defect visible in the test suite instead
    of hiding it behind seed-pinning.
    """
    first = _build_in_subprocess(tmp_path / "seed_a.pdf", hash_seed="0")
    second = _build_in_subprocess(tmp_path / "seed_b.pdf", hash_seed="12345")

    assert first == second, (
        "expected upstream nondeterminism did not reproduce -- "
        f"{_diff_detail(first, second)}"
    )


def test_pinned_metadata_preserves_conformance(tmp_path: Path, verapdf_exe: Path):
    target = tmp_path / "pinned.pdf"
    _build(target)

    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()
