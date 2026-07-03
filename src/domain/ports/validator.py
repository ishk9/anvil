"""Port for geometry validation strategies (mass, printability, FEA, ...)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from domain.models.design_spec import DesignSpec
from domain.models.geometry import GeometryArtifact
from domain.models.validation import ValidationReport


@runtime_checkable
class GeometryValidator(Protocol):
    """Produces a report for a built part in the context of its design intent."""

    @property
    def name(self) -> str: ...

    def validate(self, *, artifact: GeometryArtifact, spec: DesignSpec) -> ValidationReport: ...
