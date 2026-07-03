"""Composite that fans a part out across every registered validator."""

from __future__ import annotations

from collections.abc import Sequence

import structlog

from domain.models.design_spec import DesignSpec
from domain.models.geometry import GeometryArtifact
from domain.models.validation import Severity, ValidationIssue, ValidationReport
from domain.ports.validator import GeometryValidator

log = structlog.get_logger(__name__)


class CompositeValidator:
    """Runs child validators and merges their reports.

    A child that itself raises is downgraded to a WARNING issue rather than aborting the
    whole run — one flaky check shouldn't sink the others.
    """

    def __init__(self, validators: Sequence[GeometryValidator]) -> None:
        self._validators = tuple(validators)

    @property
    def name(self) -> str:
        return "composite"

    def validate(self, *, artifact: GeometryArtifact, spec: DesignSpec) -> ValidationReport:
        report = ValidationReport()
        for validator in self._validators:
            try:
                report = report.merged_with(validator.validate(artifact=artifact, spec=spec))
            except Exception as exc:
                log.warning("validator.failed", validator=validator.name, error=str(exc))
                report = report.merged_with(
                    ValidationReport(
                        issues=(
                            ValidationIssue(
                                validator.name,
                                Severity.WARNING,
                                f"Validator '{validator.name}' failed to run: {exc}",
                            ),
                        )
                    )
                )
        return report
