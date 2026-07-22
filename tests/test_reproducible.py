import os
import subprocess
import sys
from pathlib import Path

from rebind.render import render_html_to_pdf
from rebind.reproducible import pin_document_metadata
from rebind.validate import validate_pdf_ua

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


def test_two_runs_produce_identical_bytes(tmp_path: Path):
    """Global constraint: same input at same version yields the same output."""
    first = _build(tmp_path / "one.pdf")
    second = _build(tmp_path / "two.pdf")

    assert first == second, "PDF output is not byte-reproducible"


def test_two_runs_produce_identical_bytes_across_processes_and_hash_seeds(tmp_path: Path):
    """Same proof as above, but crossing a process boundary with different hash seeds.

    A single pytest process has one fixed PYTHONHASHSEED for its entire life, so
    `test_two_runs_produce_identical_bytes` cannot detect nondeterminism that only shows up
    when hash randomization differs between runs. This builds the PDF in two separate
    subprocess invocations, each with a different PYTHONHASHSEED, and requires the resulting
    files to be byte-identical.
    """
    first = _build_in_subprocess(tmp_path / "seed_a.pdf", hash_seed="0")
    second = _build_in_subprocess(tmp_path / "seed_b.pdf", hash_seed="12345")

    if first != second:
        first_len, second_len = len(first), len(second)
        diffs = [
            i
            for i in range(min(first_len, second_len))
            if first[i] != second[i]
        ]
        detail = (
            f"lengths differ: {first_len} vs {second_len}; "
            f"first differing byte offsets (up to 20): {diffs[:20]}"
        )
        assert first == second, (
            "PDF output is not byte-reproducible across hash seeds -- "
            f"{detail}"
        )


def test_pinned_metadata_preserves_conformance(tmp_path: Path, verapdf_exe: Path):
    target = tmp_path / "pinned.pdf"
    _build(target)

    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)
    assert result.compliant, result.summary()
