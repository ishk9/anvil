from domain.models.validation import (
    Severity,
    ValidationIssue,
    ValidationReport,
)


def _issue(sev: Severity) -> ValidationIssue:
    return ValidationIssue(check="c", severity=sev, message="m")


def test_report_passes_without_errors() -> None:
    report = ValidationReport(issues=(_issue(Severity.INFO), _issue(Severity.WARNING)))
    assert report.passed
    assert report.has_warnings


def test_report_fails_with_any_error() -> None:
    report = ValidationReport(issues=(_issue(Severity.INFO), _issue(Severity.ERROR)))
    assert not report.passed


def test_merge_concatenates_issues() -> None:
    a = ValidationReport(issues=(_issue(Severity.INFO),))
    b = ValidationReport(issues=(_issue(Severity.ERROR),))
    merged = a.merged_with(b)
    assert len(merged.issues) == 2
    assert not merged.passed
