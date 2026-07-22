from pathlib import Path

import pytest

from rebind.validate import ValidationResult, _parse_validation_report, validate_pdf_ua


def test_untagged_pdf_is_not_compliant(untagged_pdf: Path, verapdf_exe: Path):
    result = validate_pdf_ua(untagged_pdf, verapdf_exe=verapdf_exe)

    assert isinstance(result, ValidationResult)
    assert result.compliant is False
    assert result.flavour == "ua1"
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
