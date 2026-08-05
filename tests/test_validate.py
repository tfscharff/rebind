from pathlib import Path

import pikepdf
import pytest

from rebind.remediate import remediate
from rebind.validate import (
    ValidationResult,
    _parse_validation_report,
    self_check_pdf_ua2,
    validate_pdf_ua,
)
from tests.fixtures import born_digital_pdf


def test_self_check_passes_on_real_remediate_output(tmp_path: Path):
    # No veraPDF/JVM needed -- this is the always-available badge the app shows.
    source = born_digital_pdf("<h1>Title</h1><p>Body text.</p>", tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    remediate(source, out, title="T")

    result = self_check_pdf_ua2(out)
    assert result.ok, result.issues


def test_self_check_reports_issues_on_a_plain_pdf(tmp_path: Path):
    # A PDF with none of remediate()'s structure should fail every check, honestly, not silently
    # report success.
    target = tmp_path / "plain.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.save(target)

    result = self_check_pdf_ua2(target)
    assert not result.ok
    assert result.issues


def test_conformant_pdf_is_compliant(tmp_path: Path, verapdf_exe: Path):
    """The wrapper must also report True on a genuinely conformant document.

    Every prior test here only exercised non-compliant PDFs, so a wrapper with an
    inverted check would have passed all of them. This closes that blind spot.
    """
    source = born_digital_pdf("<h1>Title</h1><p>Body text.</p>", tmp_path / "in.pdf")
    target = tmp_path / "conformant.pdf"
    remediate(source, target, title="Conformance Check", lang="en")

    result = validate_pdf_ua(target, verapdf_exe=verapdf_exe)

    assert result.compliant is True
    assert result.failed_rules == []


def test_untagged_pdf_is_not_compliant(untagged_pdf: Path, verapdf_exe: Path):
    result = validate_pdf_ua(untagged_pdf, verapdf_exe=verapdf_exe)

    assert isinstance(result, ValidationResult)
    assert result.compliant is False
    # `flavour` is read back from veraPDF's own report (see `_parse_validation_report`), not
    # hardcoded, so this asserts on veraPDF's actual profile name rather than the short
    # `--flavour` spelling rebind passes on the command line.
    assert "UA-2" in result.flavour, result.flavour
    assert len(result.failed_rules) > 0


def test_failed_rules_are_parsed_with_detail(untagged_pdf: Path, verapdf_exe: Path):
    """A wrapper that returns 'False' but can't say why is useless for debugging."""
    result = validate_pdf_ua(untagged_pdf, verapdf_exe=verapdf_exe)

    rule = result.failed_rules[0]
    assert rule.clause, "clause must be populated"
    assert rule.description, "description must be populated"
    assert rule.failed_checks >= 1


def test_validate_pdf_ua_raises_on_invalid_pdf(tmp_path: Path, verapdf_exe: Path):
    """A non-PDF input should surface as a genuine error, not a silent 'non-compliant'."""
    bogus = tmp_path / "not_a_pdf.pdf"
    bogus.write_text("this is not a PDF file at all")

    with pytest.raises(RuntimeError):
        validate_pdf_ua(bogus, verapdf_exe=verapdf_exe)


def test_parse_validation_report_normal_noncompliant_report_shape():
    """The report.jobs[] schema, with a normal jobEndStatus, still returns a verdict."""
    raw = {
        "report": {
            "jobs": [
                {
                    "validationResult": [
                        {
                            "jobEndStatus": "normal",
                            "compliant": False,
                            "details": {
                                "ruleSummaries": [
                                    {
                                        "clause": "7.1",
                                        "testNumber": 8,
                                        "description": "missing metadata",
                                        "failedChecks": 1,
                                    }
                                ]
                            },
                        }
                    ]
                }
            ]
        }
    }

    result = _parse_validation_report(raw)

    assert isinstance(result, ValidationResult)
    assert result.compliant is False
    assert len(result.failed_rules) == 1


def test_parse_validation_report_alternate_jobs_shape():
    """The top-level jobs[] schema (no 'report' wrapper) must also parse correctly."""
    raw = {
        "jobs": [
            {
                "validationResult": {
                    "jobEndStatus": "normal",
                    "compliant": True,
                    "details": {"ruleSummaries": []},
                }
            }
        ]
    }

    result = _parse_validation_report(raw)

    assert isinstance(result, ValidationResult)
    assert result.compliant is True
    assert result.failed_rules == []


def test_parse_validation_report_raises_on_abnormal_job_end_status():
    raw = {
        "report": {
            "jobs": [
                {
                    "validationResult": [
                        {
                            "jobEndStatus": "exception",
                            "compliant": False,
                            "details": {"ruleSummaries": []},
                        }
                    ]
                }
            ]
        }
    }

    with pytest.raises(RuntimeError):
        _parse_validation_report(raw)


def test_parse_validation_report_raises_on_missing_compliant_key():
    raw = {
        "report": {
            "jobs": [
                {
                    "validationResult": [
                        {
                            "jobEndStatus": "normal",
                            "details": {"ruleSummaries": []},
                        }
                    ]
                }
            ]
        }
    }

    with pytest.raises(RuntimeError):
        _parse_validation_report(raw)
