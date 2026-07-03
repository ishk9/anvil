"""End-to-end CAD pipeline smoke test: build -> render -> validate.

Marked `integration` because it needs the full OpenCASCADE/build123d stack. Run inside
the Docker image: `docker compose run --rm checks` (or `pytest -m integration`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from domain.models.design_spec import DesignSpec, Material
from infrastructure.cad.build123d_executor import Build123dExecutor
from infrastructure.rendering.matplotlib_renderer import MatplotlibRenderer
from infrastructure.validation.composite_validator import CompositeValidator
from infrastructure.validation.mass_properties_validator import MassPropertiesValidator
from infrastructure.validation.printability_validator import PrintabilityValidator

pytestmark = pytest.mark.integration

_PLATE_CODE = """
plate = Box(40, 40, 4)
plate = plate - Pos(0, 0, 0) * Cylinder(radius=1.7, height=4)
part = plate
"""

_BROKEN_CODE = "part = Box(10, 10)  # missing a required dimension -> should error"

_NO_CONTRACT_CODE = "widget = Box(10, 10, 10)  # never binds `part`"


def test_build_render_validate(tmp_path: Path) -> None:
    executor = Build123dExecutor(timeout_seconds=60, memory_limit_mb=2048)
    result = executor.execute(code=_PLATE_CODE, out_dir=tmp_path, artifact_id="plate")
    assert result.is_ok(), getattr(result, "error", None)

    artifact = result.unwrap()
    assert artifact.step_path.exists()
    assert artifact.stl_path.exists()
    # 40x40 plate, within a few percent tolerance for the drilled hole.
    assert 39.0 <= artifact.bounding_box.x_mm <= 41.0
    assert 3.5 <= artifact.bounding_box.z_mm <= 4.5

    renders = MatplotlibRenderer(image_size=256).render(
        stl_path=artifact.stl_path, out_dir=tmp_path, views=2
    )
    assert len(renders) == 2
    assert all(p.exists() for p in renders)

    validator = CompositeValidator(
        validators=(
            MassPropertiesValidator(),
            PrintabilityValidator(build_volume_mm=(220, 220, 250), min_wall_thickness_mm=0.8),
        )
    )
    spec = DesignSpec(title="test plate", summary="a plate", material=Material.PETG)
    report = validator.validate(artifact=artifact, spec=spec)
    assert report.passed


def test_execution_error_is_reported(tmp_path: Path) -> None:
    executor = Build123dExecutor(timeout_seconds=60, memory_limit_mb=2048)
    result = executor.execute(code=_BROKEN_CODE, out_dir=tmp_path, artifact_id="broken")
    assert result.is_err()


def test_missing_contract_variable_is_reported(tmp_path: Path) -> None:
    executor = Build123dExecutor(timeout_seconds=60, memory_limit_mb=2048)
    result = executor.execute(code=_NO_CONTRACT_CODE, out_dir=tmp_path, artifact_id="nocontract")
    assert result.is_err()
    assert result.error.kind.value == "contract"  # type: ignore[union-attr]
