"""FDM printability checks: does it fit the bed, and will it slice cleanly."""

from __future__ import annotations

import structlog
import trimesh

from domain.models.design_spec import DesignSpec
from domain.models.geometry import GeometryArtifact
from domain.models.validation import Severity, ValidationIssue, ValidationReport

log = structlog.get_logger(__name__)


class PrintabilityValidator:
    """Bounding-box-vs-bed and watertightness checks.

    Minimum wall-thickness detection requires an SDF/ray sweep and is intentionally left
    as a future adapter; it is not faked here to avoid misleading results.
    """

    def __init__(
        self,
        *,
        build_volume_mm: tuple[float, float, float],
        min_wall_thickness_mm: float,
    ) -> None:
        self._bed = build_volume_mm
        self._min_wall = min_wall_thickness_mm

    @property
    def name(self) -> str:
        return "printability"

    def validate(self, *, artifact: GeometryArtifact, spec: DesignSpec) -> ValidationReport:
        bbox = artifact.bounding_box
        bed_x, bed_y, bed_z = self._bed
        issues: list[ValidationIssue] = [
            ValidationIssue(
                self.name,
                Severity.INFO,
                f"Bounding box (mm): {bbox.x_mm:.1f} x {bbox.y_mm:.1f} x {bbox.z_mm:.1f}",
            )
        ]

        for axis, size, limit in (
            ("X", bbox.x_mm, bed_x),
            ("Y", bbox.y_mm, bed_y),
            ("Z", bbox.z_mm, bed_z),
        ):
            if size > limit:
                issues.append(
                    ValidationIssue(
                        self.name,
                        Severity.ERROR,
                        f"{axis} extent {size:.1f}mm exceeds build volume {limit:.1f}mm.",
                        value=size,
                        limit=limit,
                    )
                )

        mesh = trimesh.load(artifact.stl_path, force="mesh")
        if isinstance(mesh, trimesh.Trimesh) and not mesh.is_watertight:
            issues.append(
                ValidationIssue(
                    self.name,
                    Severity.ERROR,
                    "Mesh is not watertight; a slicer will likely reject or misinterpret it.",
                )
            )

        return ValidationReport(issues=tuple(issues))
