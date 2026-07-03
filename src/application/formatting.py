"""Shared, UI-agnostic formatting of domain objects into readable text."""

from __future__ import annotations

from domain.models.design_spec import MaterialProfile
from domain.models.validation import Severity, ValidationReport

_SEVERITY_TAG = {Severity.INFO: "info", Severity.WARNING: "warn", Severity.ERROR: "FAIL"}


def format_validation_report(report: ValidationReport) -> str:
    lines = [f"[{_SEVERITY_TAG[i.severity]}] {i.check}: {i.message}" for i in report.issues]
    verdict = "PASS" if report.passed else "FAIL (blocking issues present)"
    return f"Validation: {verdict}\n" + "\n".join(lines)


def format_materials(profiles: list[MaterialProfile]) -> str:
    lines = [
        f"- {p.material.value}: density {p.density_g_cm3} g/cm^3, "
        f"tensile ~{p.tensile_mpa} MPa — {p.notes}"
        for p in profiles
    ]
    return "Available materials:\n" + "\n".join(lines)
