"""FEA validator port stub.

Real structural analysis (CalculiX/FEniCS) is a substantial adapter: it needs a mesh,
material model, boundary conditions, and load application derived from the DesignSpec's
load cases. This placeholder conforms to `GeometryValidator` so the rest of the system
is already wired for it — replace it with a solver-backed implementation and rebind in
the container, with no other layer changing.
"""

from __future__ import annotations

from domain.models.design_spec import DesignSpec
from domain.models.geometry import GeometryArtifact
from domain.models.validation import Severity, ValidationIssue, ValidationReport


class NullFeaValidator:
    @property
    def name(self) -> str:
        return "fea"

    def validate(self, *, artifact: GeometryArtifact, spec: DesignSpec) -> ValidationReport:
        detail = (
            f"{len(spec.load_cases)} load case(s) described but no solver configured."
            if spec.load_cases
            else "No load cases described."
        )
        return ValidationReport(
            issues=(
                ValidationIssue(
                    self.name,
                    Severity.INFO,
                    f"FEA not enabled — stress not verified. {detail} Bench-test before flight.",
                ),
            )
        )
