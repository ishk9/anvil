"""Unit tests for `SlicerValidator`: cost math, G-code parsing, and the geometric
fallback taken when no slicer binary is on PATH.

No real slicer is required — the CLI is mocked (or reported absent via ``shutil.which``).
Tests that need an actual slicer installed are marked ``integration``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import trimesh

from domain.models.design_spec import DesignSpec, Material
from domain.models.geometry import BoundingBox, GeometryArtifact
from domain.models.validation import Severity
from infrastructure.validation import slicer_validator as sv
from infrastructure.validation.slicer_validator import SlicerValidator

# A minimal PrusaSlicer-style G-code summary block covering the comments we parse. The
# support region contributes 10mm of extrusion; a non-support region contributes 90mm.
_GCODE = """\
;TYPE:Support material
G1 X0 Y0 E0.0
G1 X10 Y0 E10.0
;TYPE:Perimeter
G1 X20 Y0 E100.0
; filament used [mm] = 100.0
; filament used [cm3] = 2.0
; filament used [g] = 2.48
; estimated printing time (normal mode) = 1h 5m 30s
"""


def _artifact(stl_path: Path) -> GeometryArtifact:
    return GeometryArtifact(
        artifact_id="part_001",
        source_code="part = Box(10, 10, 10)",
        step_path=stl_path.with_suffix(".step"),
        stl_path=stl_path,
        bounding_box=BoundingBox(10.0, 10.0, 10.0),
    )


def _write_cube_stl(tmp_path: Path, extent_mm: float = 10.0) -> Path:
    """A watertight cube of known volume (extent^3 mm^3) for the fallback path."""
    mesh = trimesh.creation.box(extents=(extent_mm, extent_mm, extent_mm))
    stl_path = tmp_path / "cube.stl"
    mesh.export(stl_path)
    return stl_path


def _spec(material: Material = Material.PETG) -> DesignSpec:
    return DesignSpec(title="t", summary="s", material=material)


# --- G-code parsing -------------------------------------------------------------------


def test_parse_gcode_extracts_time_filament_and_support() -> None:
    validator = SlicerValidator()
    summary = validator._parse_gcode(_GCODE)

    assert summary.print_time_min == pytest.approx(65.5)  # 1h 5m 30s
    assert summary.filament_g == pytest.approx(2.48)
    assert summary.filament_cm3 == pytest.approx(2.0)
    # 10mm of 100mm total extrusion is support; total volume is 2000 mm^3 -> 200 mm^3.
    assert summary.support_volume_mm3 == pytest.approx(200.0)


def test_parse_time_omits_zero_leading_components() -> None:
    prefix = "; estimated printing time (normal mode) = "
    assert sv._first_time_minutes(prefix + "25m 30s") == pytest.approx(25.5)
    assert sv._first_time_minutes(prefix + "45s") == pytest.approx(0.75)
    assert sv._first_time_minutes(prefix + "1d 2h 0m 0s") == pytest.approx(26 * 60)
    assert sv._first_time_minutes("no time here") is None


def test_support_volume_none_without_totals() -> None:
    # Without filament [mm]/[cm3] totals we can't convert length -> volume.
    assert sv._support_volume_mm3("G1 E1.0\nG1 E2.0", filament_mm=None, filament_cm3=2.0) is None


# --- cost math ------------------------------------------------------------------------


def test_cost_combines_filament_and_machine_time() -> None:
    validator = SlicerValidator(
        machine_rate_usd_per_hour=2.0,
        material_prices_usd_per_kg={Material.PETG: 25.0},
    )
    # 100 g PETG @ $25/kg = $2.50; 60 min @ $2/h = $2.00 -> $4.50.
    cost = validator._cost(filament_g=100.0, print_time_min=60.0, material=Material.PETG)
    assert cost == pytest.approx(4.50)


def test_cost_handles_missing_figures() -> None:
    validator = SlicerValidator()
    assert validator._cost(filament_g=None, print_time_min=None, material=Material.PLA) == 0.0


# --- slicer path (mocked binary) ------------------------------------------------------


def test_validate_uses_slicer_output_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stl_path = _write_cube_stl(tmp_path)
    validator = SlicerValidator(
        machine_rate_usd_per_hour=2.0,
        material_prices_usd_per_kg={Material.PETG: 25.0},
    )

    # Pretend the slicer is installed and returns our canned summary, bypassing subprocess.
    summary = validator._parse_gcode(_GCODE)
    monkeypatch.setattr(validator, "_run_slicer", lambda _stl: (summary, True))

    report = validator.validate(artifact=_artifact(stl_path), spec=_spec(Material.PETG))

    assert report.metrics["slicer.print_time_min"] == pytest.approx(65.5)
    assert report.metrics["slicer.filament_g"] == pytest.approx(2.48)
    assert report.metrics["slicer.support_volume_mm3"] == pytest.approx(200.0)
    # 2.48g @ $25/kg = $0.062; 65.5min @ $2/h = $2.1833 -> ~$2.25.
    assert report.metrics["slicer.cost_usd"] == pytest.approx(2.25, abs=0.01)
    # No fallback warning when the slicer ran.
    assert not any("estimated from geometry" in i.message for i in report.issues)


def test_validate_falls_back_when_slicer_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stl_path = _write_cube_stl(tmp_path, extent_mm=10.0)  # 1000 mm^3 solid
    validator = SlicerValidator()

    # Force "slicer not on PATH".
    monkeypatch.setattr("shutil.which", lambda _cmd: None)

    report = validator.validate(artifact=_artifact(stl_path), spec=_spec(Material.PLA))

    warnings = [i for i in report.issues if i.severity is Severity.WARNING]
    assert any("slicer not installed" in i.message for i in warnings)

    # Fallback filament: 1000 mm^3 * infill 0.55 = 550 mm^3 -> 0.55 cm^3 * 1.24 g/cm^3.
    expected_g = 550.0 / 1000.0 * 1.24
    assert report.metrics["slicer.filament_g"] == pytest.approx(expected_g, abs=0.01)
    # Time: 550 / 8 mm^3/s / 60 -> ~1.15 min (metric is rounded to 1 decimal).
    assert report.metrics["slicer.print_time_min"] == pytest.approx(550.0 / 8.0 / 60.0, abs=0.1)
    # Support volume is not modelled in the fallback.
    assert "slicer.support_volume_mm3" not in report.metrics
    assert report.metrics["slicer.cost_usd"] > 0.0


def test_fallback_flags_machined_material_as_indicative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stl_path = _write_cube_stl(tmp_path)
    validator = SlicerValidator()
    monkeypatch.setattr("shutil.which", lambda _cmd: None)

    report = validator.validate(artifact=_artifact(stl_path), spec=_spec(Material.ALU_6061))
    assert any("machined material" in i.message for i in report.issues)


def test_long_print_emits_warning() -> None:
    validator = SlicerValidator()
    summary = sv._SlicerSummary(
        print_time_min=10 * 60,  # 10h > 8h threshold
        filament_g=50.0,
        filament_cm3=None,
        support_volume_mm3=None,
    )
    report = validator._build_report(
        summary=summary, cost_usd=1.0, material=Material.PETG, slicer_used=True
    )
    assert any(
        i.severity is Severity.WARNING and "Long print" in i.message for i in report.issues
    )


def test_support_heavy_emits_warning() -> None:
    validator = SlicerValidator()
    # 700 mm^3 support of 2000 mm^3 deposited (35%) exceeds the 25% threshold.
    summary = sv._SlicerSummary(
        print_time_min=60.0,
        filament_g=2.5,
        filament_cm3=2.0,
        support_volume_mm3=700.0,
    )
    report = validator._build_report(
        summary=summary, cost_usd=1.0, material=Material.PETG, slicer_used=True
    )
    assert any(
        i.severity is Severity.WARNING and "Support-heavy" in i.message for i in report.issues
    )


def test_report_conforms_to_geometry_validator_protocol() -> None:
    from domain.ports.validator import GeometryValidator

    assert isinstance(SlicerValidator(), GeometryValidator)


# --- integration (requires a real slicer on PATH) -------------------------------------


@pytest.mark.integration
def test_real_slicer_slices_cube(tmp_path: Path) -> None:
    import shutil

    if shutil.which("prusa-slicer") is None:
        pytest.skip("prusa-slicer not installed")

    stl_path = _write_cube_stl(tmp_path, extent_mm=20.0)
    validator = SlicerValidator()
    report = validator.validate(artifact=_artifact(stl_path), spec=_spec(Material.PETG))

    assert report.metrics["slicer.filament_g"] > 0.0
    assert report.metrics["slicer.print_time_min"] > 0.0
    assert report.metrics["slicer.cost_usd"] > 0.0
