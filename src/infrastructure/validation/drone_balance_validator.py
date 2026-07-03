"""Drone flight-balance checks: principal inertia and CoM-vs-geometric-centre offset.

A multirotor flies best when its mass is centred on the geometric centre it rotates
about; a CoM that drifts in the XY plane forces the flight controller to trim constantly
and eats into control authority. This validator reports the principal moments of inertia
(useful for tuning rate loops) and flags an off-centre CoM.

Unit math (mirrors ``mass_properties_validator.py``): build123d works in millimetres, so
the mesh lives in mm and ``mesh.volume`` is mm^3. Material density is g/cm^3; since
1 cm^3 = 1000 mm^3, density in g/mm^3 is ``density_g_cm3 / 1000``. trimesh reports the
inertia tensor for *unit* density (units of mm^5, i.e. ``mm^2`` per ``mm^3`` of volume);
setting ``mesh.density`` to the real g/mm^3 value makes ``mesh.moment_inertia`` come back
as mass-weighted g*mm^2 directly.
"""

from __future__ import annotations

import structlog
import trimesh

from domain.models.design_spec import DesignSpec
from domain.models.geometry import GeometryArtifact
from domain.models.validation import Severity, ValidationIssue, ValidationReport

log = structlog.get_logger(__name__)


class DroneBalanceValidator:
    """Principal moments of inertia and CoM balance for airframes.

    The imbalance flag looks only at the XY-plane offset between the centre of mass and
    the bounding-box centre: that is the axis a multirotor has to trim against in level
    flight. A Z offset is reported for reference (it shifts the CoM relative to the prop
    plane) but is not treated as a fault on its own.
    """

    def __init__(self, *, com_tolerance_mm: float) -> None:
        self._com_tolerance_mm = com_tolerance_mm

    @property
    def name(self) -> str:
        return "drone_balance"

    def validate(self, *, artifact: GeometryArtifact, spec: DesignSpec) -> ValidationReport:
        try:
            mesh = trimesh.load(artifact.stl_path, force="mesh")
        except Exception as exc:  # trimesh raises a grab-bag of load errors
            log.warning("drone_balance.load_failed", path=str(artifact.stl_path), error=str(exc))
            mesh = None

        # A malformed or empty STL can still deserialise into a face-less Trimesh whose
        # ``bounds``/``center_mass`` are ``None``; treat those as unloadable rather than
        # letting an inertia computation blow up.
        if not isinstance(mesh, trimesh.Trimesh) or mesh.bounds is None or len(mesh.faces) == 0:
            return ValidationReport(
                issues=(
                    ValidationIssue(
                        check=self.name,
                        severity=Severity.WARNING,
                        message="Exported mesh could not be loaded; balance not checked.",
                    ),
                )
            )

        profile = spec.material_profile()
        # g/cm^3 -> g/mm^3 (1 cm^3 = 1000 mm^3). With this density, trimesh's inertia
        # tensor comes back in g*mm^2 rather than the unit-density mm^5.
        mesh.density = profile.density_g_cm3 / 1000.0

        # Diagonal of the inertia tensor about the CoM. For a symmetric box aligned to
        # the axes this diagonal *is* the principal set; we report it as such.
        inertia = mesh.moment_inertia
        ixx = float(inertia[0][0])
        iyy = float(inertia[1][1])
        izz = float(inertia[2][2])

        com = mesh.center_mass
        bbox = artifact.bounding_box
        # Bounding-box centre in mesh coordinates. The mesh may not sit at the origin, so
        # derive the geometric centre from the mesh bounds rather than assuming (0,0,0).
        lower, upper = mesh.bounds
        geo_center = [(float(lo) + float(hi)) / 2.0 for lo, hi in zip(lower, upper, strict=True)]

        dx = float(com[0]) - geo_center[0]
        dy = float(com[1]) - geo_center[1]
        dz = float(com[2]) - geo_center[2]
        offset_xy = (dx * dx + dy * dy) ** 0.5
        offset_z = abs(dz)

        metrics: dict[str, float] = {
            "drone.ixx_g_mm2": ixx,
            "drone.iyy_g_mm2": iyy,
            "drone.izz_g_mm2": izz,
            "drone.com_offset_xy_mm": offset_xy,
            "drone.com_offset_z_mm": offset_z,
        }

        issues: list[ValidationIssue] = [
            ValidationIssue(
                self.name,
                Severity.INFO,
                f"Principal inertia (g*mm^2): "
                f"Ixx={ixx:.1f}, Iyy={iyy:.1f}, Izz={izz:.1f}",
            ),
            ValidationIssue(
                self.name,
                Severity.INFO,
                f"Center of mass (mm): "
                f"({float(com[0]):.2f}, {float(com[1]):.2f}, {float(com[2]):.2f}); "
                f"Z offset from geometric centre {offset_z:.2f} mm",
                value=offset_z,
            ),
        ]

        if offset_xy > self._com_tolerance_mm:
            issues.append(
                ValidationIssue(
                    self.name,
                    Severity.WARNING,
                    f"CoM is {offset_xy:.2f} mm off the geometric centre in XY "
                    f"(limit {self._com_tolerance_mm:.2f} mm); the airframe will fly "
                    f"nose-heavy/lopsided and burn control authority trimming for it. "
                    f"Rebalance mass or relocate payload.",
                    value=offset_xy,
                    limit=self._com_tolerance_mm,
                )
            )
        else:
            issues.append(
                ValidationIssue(
                    self.name,
                    Severity.INFO,
                    f"CoM is centred in XY within {offset_xy:.2f} mm "
                    f"(limit {self._com_tolerance_mm:.2f} mm).",
                    value=offset_xy,
                    limit=self._com_tolerance_mm,
                )
            )

        # Bounding box carried on the artifact is informational context for the log; the
        # geometric centre used above is derived from the mesh itself for consistency with
        # the CoM, which shares the mesh coordinate frame.
        log.debug(
            "drone_balance.computed",
            offset_xy_mm=offset_xy,
            offset_z_mm=offset_z,
            bbox_mm=bbox.as_tuple(),
        )
        return ValidationReport(issues=tuple(issues), metrics=metrics)
