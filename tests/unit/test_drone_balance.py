"""Unit tests for the drone balance validator using trimesh primitives.

We build real meshes with ``trimesh.creation.box`` and export them to a temporary STL so
the validator exercises its actual load path. build123d is not needed here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import trimesh

from domain.models.design_spec import MATERIAL_LIBRARY, DesignSpec, Material
from domain.models.geometry import BoundingBox, GeometryArtifact
from domain.models.validation import Severity
from infrastructure.validation.drone_balance_validator import DroneBalanceValidator


def _artifact(stl_path: Path, bbox: BoundingBox) -> GeometryArtifact:
    return GeometryArtifact(
        artifact_id="test",
        source_code="",
        step_path=stl_path,
        stl_path=stl_path,
        bounding_box=bbox,
    )


def _spec(material: Material = Material.PLA) -> DesignSpec:
    return DesignSpec(title="frame", summary="test frame", material=material)


def test_symmetric_box_is_balanced(tmp_path: Path) -> None:
    """A box centred on the origin has CoM == geometric centre, so XY offset ~ 0."""
    mesh = trimesh.creation.box(extents=(40.0, 30.0, 10.0))  # centred at origin
    stl = tmp_path / "box.stl"
    mesh.export(stl)

    report = DroneBalanceValidator(com_tolerance_mm=2.0).validate(
        artifact=_artifact(stl, BoundingBox(40.0, 30.0, 10.0)),
        spec=_spec(),
    )

    assert report.passed
    assert not report.has_warnings
    assert report.metrics["drone.com_offset_xy_mm"] < 1e-6
    assert report.metrics["drone.com_offset_z_mm"] < 1e-6


def test_inertia_units_and_ordering(tmp_path: Path) -> None:
    """Principal moments are positive g*mm^2 and follow the box's mass distribution.

    For a solid box the moment about an axis grows with the extents *perpendicular* to it.
    With extents X>Y>Z, the largest perpendicular spread is about Z (spanning X and Y),
    so Izz is the largest; Ixx (spans Y,Z, the two smallest) is the smallest.
    Analytic value: Iii = m/12 * (a^2 + b^2) for the two perpendicular extents.
    """
    ex, ey, ez = 40.0, 30.0, 10.0
    mesh = trimesh.creation.box(extents=(ex, ey, ez))
    stl = tmp_path / "box.stl"
    mesh.export(stl)

    validator = DroneBalanceValidator(com_tolerance_mm=2.0)
    report = validator.validate(artifact=_artifact(stl, BoundingBox(ex, ey, ez)), spec=_spec())

    ixx = report.metrics["drone.ixx_g_mm2"]
    iyy = report.metrics["drone.iyy_g_mm2"]
    izz = report.metrics["drone.izz_g_mm2"]

    assert ixx > 0 and iyy > 0 and izz > 0
    assert izz > iyy > ixx  # ordering follows perpendicular extents

    # Cross-check Izz against the analytic solid-box formula: m/12 * (ex^2 + ey^2).
    density_g_cm3 = MATERIAL_LIBRARY[Material.PLA].density_g_cm3
    volume_mm3 = ex * ey * ez
    mass_g = volume_mm3 / 1000.0 * density_g_cm3
    izz_expected = mass_g / 12.0 * (ex * ex + ey * ey)
    assert izz == pytest.approx(izz_expected, rel=1e-3)


def test_offset_box_trips_warning(tmp_path: Path) -> None:
    """Shifting the mesh in XY moves the CoM off the geometric centre by that amount.

    The geometric centre is derived from the mesh bounds, which shift with the mesh, so a
    uniform translation alone would *not* create an offset. Instead we weld an asymmetric
    lump onto one side so the CoM genuinely drifts from the bounding-box centre.
    """
    body = trimesh.creation.box(extents=(40.0, 30.0, 10.0))
    lump = trimesh.creation.box(extents=(10.0, 10.0, 10.0))
    lump.apply_translation((30.0, 0.0, 0.0))  # hangs off the +X face
    mesh = trimesh.util.concatenate([body, lump])
    stl = tmp_path / "lumpy.stl"
    mesh.export(stl)

    lo, hi = mesh.bounds
    bbox = BoundingBox(
        float(hi[0] - lo[0]), float(hi[1] - lo[1]), float(hi[2] - lo[2])
    )
    report = DroneBalanceValidator(com_tolerance_mm=2.0).validate(
        artifact=_artifact(stl, bbox), spec=_spec()
    )

    assert report.metrics["drone.com_offset_xy_mm"] > 2.0
    assert report.has_warnings
    warnings = [i for i in report.issues if i.severity is Severity.WARNING]
    assert any("off the geometric centre" in i.message for i in warnings)


def test_unloadable_mesh_warns_without_raising(tmp_path: Path) -> None:
    stl = tmp_path / "empty.stl"
    stl.write_text("not a real stl")

    report = DroneBalanceValidator(com_tolerance_mm=2.0).validate(
        artifact=_artifact(stl, BoundingBox(1.0, 1.0, 1.0)), spec=_spec()
    )

    assert report.has_warnings
    assert not report.metrics
