"""Unit tests for the CalculiX FEA validator.

These run WITHOUT gmsh or CalculiX installed. The dependency check is forced open with a
monkeypatch and the subprocess solve is replaced by a fake, so we exercise the pure logic:
safety-factor severity thresholds, metric population, load-case handling, FRD parsing, and
the graceful-degradation path when the solver stack is absent. Anything that needs the real
mesher/solver is marked ``@pytest.mark.integration`` and skipped when they're missing.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

from domain.models.design_spec import DesignSpec, LoadCase, Material
from domain.models.geometry import BoundingBox, GeometryArtifact
from domain.models.validation import Severity
from infrastructure.validation import calculix_fea_validator as fea_mod
from infrastructure.validation.calculix_fea_validator import (
    CalculiXFeaValidator,
    _FeaResult,
    _slug,
)
from infrastructure.validation.fea_meshing import _parse_frd, _von_mises


def _artifact(tmp_path: Path) -> GeometryArtifact:
    step = tmp_path / "part.step"
    stl = tmp_path / "part.stl"
    step.write_text("dummy")  # existence is all the adapter checks
    stl.write_text("dummy")
    return GeometryArtifact(
        artifact_id="part",
        source_code="part = Box(1, 1, 1)",
        step_path=step,
        stl_path=stl,
        bounding_box=BoundingBox(10.0, 10.0, 10.0),
    )


def _spec(*load_cases: LoadCase, material: Material = Material.ALU_6061) -> DesignSpec:
    return DesignSpec(
        title="bracket",
        summary="a bracket",
        material=material,
        load_cases=load_cases,
    )


def _force_dependencies_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the adapter believe gmsh + ccx are available so we reach the solve path."""
    monkeypatch.setattr(fea_mod.CalculiXFeaValidator, "_missing_dependency", lambda self: None)


def _stub_solver(result: _FeaResult | None) -> object:
    """Return a fake ``_solve_case`` that always yields ``result`` (or None for failure)."""

    def _fake(self: object, **_kwargs: object) -> _FeaResult | None:
        return result

    return _fake


# --- graceful degradation ---------------------------------------------------------------


def test_missing_solver_degrades_to_warning(tmp_path: Path) -> None:
    v = CalculiXFeaValidator(solver_cmd="definitely-not-a-real-binary-xyz")
    report = v.validate(artifact=_artifact(tmp_path), spec=_spec())
    # No solver → single WARNING, never raises, no ERROR.
    assert report.passed  # a WARNING does not fail the report
    assert report.has_warnings
    assert any("not available" in i.message for i in report.issues)
    assert report.metrics == {}


def test_missing_dependency_reports_gmsh_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    v = CalculiXFeaValidator()
    assert v._missing_dependency() == "gmsh not importable"


# --- load-case handling -----------------------------------------------------------------


def test_no_force_load_cases_emit_info_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_dependencies_present(monkeypatch)
    spec = _spec(LoadCase(name="gust", description="wind gust", force_newtons=None))
    v = CalculiXFeaValidator()
    report = v.validate(artifact=_artifact(tmp_path), spec=spec)
    assert report.passed
    assert not report.has_warnings
    assert any(i.severity is Severity.INFO and "skipped" in i.message for i in report.issues)
    assert report.metrics == {}  # nothing solved


def test_unknown_material_degrades(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _force_dependencies_present(monkeypatch)
    # Remove one material from the elastic table to simulate a gap.
    patched = dict(fea_mod._ELASTIC)
    patched.pop(Material.PLA)
    monkeypatch.setattr(fea_mod, "_ELASTIC", patched)
    v = CalculiXFeaValidator()
    report = v.validate(
        artifact=_artifact(tmp_path), spec=_spec(material=Material.PLA)
    )
    assert report.has_warnings
    assert any("elastic constants" in i.message for i in report.issues)


# --- safety-factor severity thresholds --------------------------------------------------


@pytest.mark.parametrize(
    ("von_mises", "expected"),
    [
        # material = ALU_6061, tensile = 310 MPa
        (400.0, Severity.ERROR),    # SF = 0.78 < 1 -> ERROR
        (200.0, Severity.WARNING),  # SF = 1.55, in [1, 2) -> WARNING
        (50.0, Severity.INFO),      # SF = 6.2 >= 2 -> INFO
    ],
)
def test_safety_factor_severity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    von_mises: float,
    expected: Severity,
) -> None:
    _force_dependencies_present(monkeypatch)
    # Skip the real render (no .frd on disk here).
    monkeypatch.setattr(
        fea_mod.CalculiXFeaValidator, "_render_stress_map", lambda self, r, out_dir: None
    )
    fake = _FeaResult(
        von_mises_max=von_mises,
        displacement_max=0.5,
        mesh_nodes=1234,
        frd_path=tmp_path / "job.frd",
        load_case="",
        safety_factor=0.0,
    )
    monkeypatch.setattr(
        fea_mod.CalculiXFeaValidator, "_solve_case", _stub_solver(fake)
    )

    spec = _spec(
        LoadCase(name="tip load", description="500N at tip", force_newtons=500.0),
        material=Material.ALU_6061,  # tensile 310 MPa
    )
    v = CalculiXFeaValidator()
    report = v.validate(artifact=_artifact(tmp_path), spec=spec)

    # The safety-factor issue carries limit=1.0; find it and check its severity.
    sf_issues = [i for i in report.issues if i.message.startswith("Safety factor")]
    assert len(sf_issues) == 1
    assert sf_issues[0].severity is expected

    # Metrics are always populated on a successful solve.
    assert report.metrics["fea.von_mises_max_mpa"] == von_mises
    assert report.metrics["fea.mesh_nodes"] == 1234.0
    assert report.metrics["fea.safety_factor"] == pytest.approx(310.0 / von_mises)


def test_worst_case_wins_across_load_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_dependencies_present(monkeypatch)
    monkeypatch.setattr(
        fea_mod.CalculiXFeaValidator, "_render_stress_map", lambda self, r, out_dir: None
    )

    # Two forced load cases; the harsher one (higher stress) must drive the report.
    results = iter(
        [
            _FeaResult(80.0, 0.2, 10, tmp_path / "a.frd", "", 0.0),
            _FeaResult(300.0, 0.9, 10, tmp_path / "b.frd", "", 0.0),
        ]
    )

    def _fake(self: object, **_kwargs: object) -> _FeaResult:
        return next(results)

    monkeypatch.setattr(fea_mod.CalculiXFeaValidator, "_solve_case", _fake)

    spec = _spec(
        LoadCase(name="light", description="light", force_newtons=100.0),
        LoadCase(name="heavy", description="heavy", force_newtons=900.0),
        material=Material.ALU_6061,
    )
    report = CalculiXFeaValidator().validate(artifact=_artifact(tmp_path), spec=spec)
    # Worst is 300 MPa -> SF = 310/300 ≈ 1.03 -> WARNING.
    assert report.metrics["fea.von_mises_max_mpa"] == 300.0
    sf_issue = next(i for i in report.issues if i.message.startswith("Safety factor"))
    assert sf_issue.severity is Severity.WARNING


def test_solver_failure_for_a_case_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_dependencies_present(monkeypatch)
    monkeypatch.setattr(fea_mod.CalculiXFeaValidator, "_solve_case", _stub_solver(None))
    spec = _spec(LoadCase(name="tip", description="tip", force_newtons=500.0))
    report = CalculiXFeaValidator().validate(artifact=_artifact(tmp_path), spec=spec)
    assert report.has_warnings
    assert report.metrics == {}


# --- pure helpers -----------------------------------------------------------------------


def test_von_mises_uniaxial() -> None:
    # Pure uniaxial stress SXX = 100 → von Mises == 100.
    assert _von_mises([100.0, 0, 0, 0, 0, 0]) == pytest.approx(100.0)


def test_von_mises_pure_shear() -> None:
    # Pure shear SXY = 50 → von Mises == sqrt(3)*50.
    assert _von_mises([0, 0, 0, 50.0, 0, 0]) == pytest.approx(50.0 * 3**0.5)


def test_slug_is_filesystem_safe() -> None:
    assert _slug("Tip Load @ 500N!") == "tip_load___500n"
    assert _slug("!!!") == "case"


def test_parse_frd_extracts_maxima(tmp_path: Path) -> None:
    # Minimal hand-written FRD with a DISP and a STRESS block, two nodes each.
    # Node 1 displacement magnitude = 3-4-5 triangle scaled: (0.3,0.4,0.0)->0.5.
    # Node 2 larger. Stress node 2 is uniaxial 200 -> von Mises 200 (the max).
    frd = tmp_path / "job.frd"
    frd.write_text(
        "\n".join(
            [
                "    1C",
                " -4  DISP        4    1",
                " -1         1 3.00000E-01 4.00000E-01 0.00000E+00",
                " -1         2 0.00000E+00 0.00000E+00 1.00000E+00",
                " -3",
                " -4  STRESS      6    1",
                " -1         1 1.00000E+02 0.00000E+00 0.00000E+00 "
                "0.00000E+00 0.00000E+00 0.00000E+00",
                " -1         2 2.00000E+02 0.00000E+00 0.00000E+00 "
                "0.00000E+00 0.00000E+00 0.00000E+00",
                " -3",
            ]
        )
        + "\n"
    )
    vm_max, disp_max = _parse_frd(frd)
    assert vm_max == pytest.approx(200.0)
    assert disp_max == pytest.approx(1.0)


# --- real solver (skipped unless the stack is installed) --------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    importlib.util.find_spec("gmsh") is None or shutil.which("ccx") is None,
    reason="requires gmsh + CalculiX (ccx)",
)
def test_real_solve_on_box(tmp_path: Path) -> None:
    """End-to-end sanity: a cantilever box under a tip load yields a finite safety factor."""
    from build123d import Box, export_step, export_stl  # local: heavy import

    box = Box(10, 10, 40)
    step = tmp_path / "beam.step"
    stl = tmp_path / "beam.stl"
    export_step(box, str(step))
    export_stl(box, str(stl))
    artifact = GeometryArtifact(
        artifact_id="beam",
        source_code="Box(10,10,40)",
        step_path=step,
        stl_path=stl,
        bounding_box=BoundingBox(10, 10, 40),
    )
    spec = _spec(
        LoadCase(name="tip", description="200N tip load", force_newtons=200.0),
        material=Material.ALU_6061,
    )
    report = CalculiXFeaValidator(mesh_size_mm=4.0).validate(artifact=artifact, spec=spec)
    assert "fea.safety_factor" in report.metrics
    assert report.metrics["fea.von_mises_max_mpa"] > 0
