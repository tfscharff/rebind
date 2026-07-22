"""Thin wrapper over the veraPDF CLI.

veraPDF validates PDF/UA structural conformance. It is not a WCAG checker — the criteria
requiring human judgment are reported separately by Rebind. See the design spec, section 2.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class VeraPdfNotFound(RuntimeError):
    """Raised when the veraPDF executable cannot be located."""


@dataclass(frozen=True)
class FailedRule:
    clause: str
    test_number: int
    description: str
    failed_checks: int


@dataclass(frozen=True)
class ValidationResult:
    compliant: bool
    flavour: str
    failed_rules: list[FailedRule] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def summary(self) -> str:
        if self.compliant:
            return "PDF/UA-1: PASS (0 failed checks)"
        total = sum(r.failed_checks for r in self.failed_rules)
        return f"PDF/UA-1: FAIL ({len(self.failed_rules)} rules, {total} failed checks)"


def _find_verapdf() -> Path:
    env = os.environ.get("REBIND_VERAPDF")
    if env and Path(env).exists():
        return Path(env)
    found = shutil.which("verapdf")
    if found:
        return Path(found)
    raise VeraPdfNotFound(
        "veraPDF not found. Install it from https://verapdf.org/software/ and set "
        "REBIND_VERAPDF to the full path of verapdf.bat."
    )


def _parse_validation_report(raw: dict) -> ValidationResult:
    """Parse veraPDF's JSON report dict into a ValidationResult.

    Raises RuntimeError if the report does not indicate a normal completion, or if the
    expected `compliant` field is missing (both are signs of a genuine tool failure, not
    a legitimate non-compliance verdict).
    """
    try:
        job = raw["report"]["jobs"][0] if "report" in raw else raw["jobs"][0]
        validation = job["validationResult"]
        if isinstance(validation, list):
            validation = validation[0]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"veraPDF report has an unexpected shape: {exc!r}") from exc

    job_end_status = validation.get("jobEndStatus")
    if job_end_status != "normal":
        raise RuntimeError(
            f"veraPDF job did not complete normally (jobEndStatus={job_end_status!r})."
        )

    if "compliant" not in validation:
        raise RuntimeError("veraPDF report is missing the 'compliant' field.")

    rules = [
        FailedRule(
            clause=str(summary.get("clause", "")),
            test_number=int(summary.get("testNumber", 0)),
            description=str(summary.get("description", "")),
            failed_checks=int(summary.get("failedChecks", 0)),
        )
        for summary in validation.get("details", {}).get("ruleSummaries", [])
    ]

    return ValidationResult(
        compliant=bool(validation["compliant"]),
        flavour="ua1",
        failed_rules=rules,
        raw=raw,
    )


def validate_pdf_ua(
    pdf_path: Path, verapdf_exe: Path | None = None, timeout: int = 300
) -> ValidationResult:
    """Validate a PDF against PDF/UA-1 and return a structured result."""
    exe = Path(verapdf_exe) if verapdf_exe else _find_verapdf()

    proc = subprocess.run(
        [str(exe), "--flavour", "ua1", "--format", "json", str(pdf_path)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    # veraPDF exits non-zero when a document is non-compliant, which is not an error for us.
    # Genuine failures are instead detected via jobEndStatus / the missing compliant key below.
    if not proc.stdout.strip():
        raise RuntimeError(f"veraPDF produced no output. stderr: {proc.stderr[:500]}")

    raw = json.loads(proc.stdout)
    return _parse_validation_report(raw)
