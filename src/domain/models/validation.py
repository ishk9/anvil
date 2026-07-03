"""Validation report types shared by all `GeometryValidator` adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum


class Severity(IntEnum):
    INFO = 0
    WARNING = 1
    ERROR = 2


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    check: str
    severity: Severity
    message: str
    value: float | None = None
    limit: float | None = None


@dataclass(frozen=True, slots=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...] = field(default_factory=tuple)
    metrics: Mapping[str, float] = field(default_factory=dict)
    """Structured, machine-readable results keyed by ``<validator>.<metric>`` (e.g.
    ``fea.von_mises_max_mpa``, ``fea.safety_factor``, ``slicer.cost_usd``). Kept alongside
    the human-readable ``issues`` so downstream consumers (the constraint solver, viewer
    HUD) read numbers directly instead of parsing strings."""

    @property
    def passed(self) -> bool:
        return not any(i.severity is Severity.ERROR for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity is Severity.WARNING for i in self.issues)

    def merged_with(self, other: ValidationReport) -> ValidationReport:
        return ValidationReport(
            issues=(*self.issues, *other.issues),
            metrics={**self.metrics, **other.metrics},
        )
