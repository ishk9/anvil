"""Mass / volume / center-of-mass validation from the exported mesh."""

from __future__ import annotations

import structlog
import trimesh

from domain.models.design_spec import DesignSpec
from domain.models.geometry import GeometryArtifact
from domain.models.validation import Severity, ValidationIssue, ValidationReport

log = structlog.get_logger(__name__)


class MassPropertiesValidator:
    """Reports mass (from material density), volume, CoM, and watertightness.

    build123d works in millimetres, so mesh volume is mm^3. Material density is g/cm^3,
    hence mass_g = volume_mm3 / 1000 * density.
    """

    @property
    def name(self) -> str:
        return "mass_properties"

    def validate(self, *, artifact: GeometryArtifact, spec: DesignSpec) -> ValidationReport:
        mesh = trimesh.load(artifact.stl_path, force="mesh")
        if not isinstance(mesh, trimesh.Trimesh):
            return ValidationReport(
                issues=(
                    ValidationIssue(
                        check=self.name,
                        severity=Severity.ERROR,
                        message="Exported mesh could not be loaded for measurement.",
                    ),
                )
            )

        profile = spec.material_profile()
        volume_mm3 = float(mesh.volume)
        mass_g = volume_mm3 / 1000.0 * profile.density_g_cm3
        com = tuple(round(float(c), 2) for c in mesh.center_mass)

        issues: list[ValidationIssue] = [
            ValidationIssue(self.name, Severity.INFO, f"Volume: {volume_mm3 / 1000:.2f} cm^3"),
            ValidationIssue(
                self.name,
                Severity.INFO,
                f"Mass: {mass_g:.1f} g in {profile.material.value} "
                f"(density {profile.density_g_cm3} g/cm^3)",
                value=mass_g,
            ),
            ValidationIssue(self.name, Severity.INFO, f"Center of mass (mm): {com}"),
        ]
        if not mesh.is_watertight:
            issues.append(
                ValidationIssue(
                    self.name,
                    Severity.WARNING,
                    "Mesh is not watertight; mass figures are approximate and it may not slice.",
                )
            )
        return ValidationReport(
            issues=tuple(issues),
            metrics={"mass.mass_g": mass_g, "mass.volume_mm3": volume_mm3},
        )
