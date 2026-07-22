from pathlib import Path

from rebind.validate import ValidationResult, validate_pdf_ua


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
