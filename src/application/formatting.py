"""Shared, UI-agnostic formatting of domain objects into readable text."""

from __future__ import annotations

from domain.models.design_spec import MaterialProfile
from domain.models.revision import DesignVersion, RevisionHistory, VersionDiff
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


def format_diff(diff: VersionDiff) -> str:
    if diff.is_empty:
        return f"v{diff.from_version} -> v{diff.to_version}: no changes."
    lines = [f"Diff v{diff.from_version} -> v{diff.to_version}:"]
    for p in diff.param_changes:
        lines.append(f"  param {p.name}: {p.before} -> {p.after}")
    for s in diff.spec_changes:
        lines.append(f"  spec {s.field}: {s.before!r} -> {s.after!r}")
    if diff.code_changed:
        lines.append("  code: changed")
    return "\n".join(lines)


def format_version_diff(version: DesignVersion, diff: VersionDiff) -> str:
    return f"Recorded v{version.version} (parent v{diff.from_version}).\n" + format_diff(diff)


def format_history(history: RevisionHistory) -> str:
    if not history.versions:
        return "No design history yet."
    lines = ["Design history:"]
    for v in history.versions:
        parent = f" <- v{v.parent_version}" if v.parent_version else ""
        note = f" — {v.note}" if v.note else ""
        lines.append(f"  v{v.version}{parent} [{v.artifact_id}]{note}")
    return "\n".join(lines)
