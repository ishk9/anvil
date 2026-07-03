"""Tests for the extended export surface.

Fast tests cover the pure domain logic (format enum, request normalisation/validation,
filename derivation) and never touch build123d. Real CAD export is exercised behind the
``integration`` marker because it needs the OpenCASCADE stack.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from domain.models.export import (
    BASELINE_FORMATS,
    DERIVED_FORMATS,
    ExportFormat,
    ExportRequest,
)

# --- ExportFormat -----------------------------------------------------------------


def test_baseline_and_derived_partition_the_enum() -> None:
    assert set(ExportFormat) == BASELINE_FORMATS | DERIVED_FORMATS
    assert BASELINE_FORMATS.isdisjoint(DERIVED_FORMATS)
    assert ExportFormat.STEP in BASELINE_FORMATS
    assert ExportFormat.DXF in DERIVED_FORMATS


@pytest.mark.parametrize(
    ("fmt", "suffix"),
    [
        (ExportFormat.STEP, ".step"),
        (ExportFormat.THREE_MF, ".3mf"),
        (ExportFormat.OBJ, ".obj"),
        (ExportFormat.GLTF, ".gltf"),
        (ExportFormat.DXF, ".dxf"),
        (ExportFormat.SVG, ".svg"),
        (ExportFormat.DRAWING, ".drawing.svg"),
    ],
)
def test_suffix(fmt: ExportFormat, suffix: str) -> None:
    assert fmt.suffix == suffix


# --- ExportRequest validation & normalisation -------------------------------------


def test_request_defaults_to_baseline_when_no_formats() -> None:
    req = ExportRequest.from_strings(base_name="bracket", formats=None)
    assert set(req.formats) == BASELINE_FORMATS
    assert req.needs_export_runner() is False
    assert req.derived == ()


def test_request_always_includes_baseline() -> None:
    req = ExportRequest.from_strings(base_name="bracket", formats=["3mf"])
    assert ExportFormat.STEP in req.formats
    assert ExportFormat.STL in req.formats
    assert ExportFormat.THREE_MF in req.formats


def test_request_dedupes_and_orders_deterministically() -> None:
    a = ExportRequest.from_strings(base_name="p", formats=["dxf", "3mf", "dxf"])
    b = ExportRequest.from_strings(base_name="p", formats=["3mf", "dxf"])
    assert a.formats == b.formats
    # enum-declaration order: 3mf precedes dxf
    assert a.formats.index(ExportFormat.THREE_MF) < a.formats.index(ExportFormat.DXF)


def test_request_is_case_and_whitespace_insensitive() -> None:
    req = ExportRequest.from_strings(base_name="p", formats=["  DXF ", "Svg"])
    assert ExportFormat.DXF in req.formats
    assert ExportFormat.SVG in req.formats


def test_request_ignores_empty_tokens() -> None:
    req = ExportRequest.from_strings(base_name="p", formats=["", "  ", "obj"])
    assert ExportFormat.OBJ in req.formats


def test_request_rejects_unknown_format() -> None:
    with pytest.raises(ValueError, match="Unknown export format"):
        ExportRequest.from_strings(base_name="p", formats=["parasolid"])


def test_derived_excludes_baseline() -> None:
    req = ExportRequest.from_strings(base_name="p", formats=["3mf", "dxf"])
    assert set(req.derived) == {ExportFormat.THREE_MF, ExportFormat.DXF}
    assert req.needs_export_runner() is True


def test_filename_derivation() -> None:
    req = ExportRequest.from_strings(base_name="drone-arm", formats=["3mf"])
    assert req.filename_for(ExportFormat.THREE_MF) == "drone-arm.3mf"
    assert req.filename_for(ExportFormat.DRAWING) == "drone-arm.drawing.svg"
    assert req.filename_for(ExportFormat.STEP) == "drone-arm.step"


# --- Real CAD export (integration) ------------------------------------------------


@pytest.fixture
def step_file(tmp_path: Path) -> Path:
    """Build a small solid and write a STEP, mirroring what the build runner produces."""
    from build123d import Box, Cylinder, Pos, export_step

    part = Box(30, 20, 4) - Pos(0, 0, 0) * Cylinder(radius=3, height=4)
    dest = tmp_path / "part.step"
    export_step(part, str(dest))
    return dest


@pytest.mark.integration
@pytest.mark.parametrize(
    "fmt",
    [
        ExportFormat.THREE_MF,
        ExportFormat.OBJ,
        ExportFormat.GLTF,
        ExportFormat.DXF,
        ExportFormat.SVG,
        ExportFormat.DRAWING,
    ],
)
def test_produce_writes_nonempty_file(step_file: Path, tmp_path: Path, fmt: ExportFormat) -> None:
    from infrastructure.cad.export_runner import _load_shape
    from infrastructure.cad.exporters import produce

    shape = _load_shape(step_file)
    dest = tmp_path / f"out{fmt.suffix}"
    produce(fmt, shape, dest, title="Test Part")

    assert dest.exists()
    assert dest.stat().st_size > 0
    if fmt is ExportFormat.OBJ:
        assert dest.with_suffix(".mtl").exists()
    if fmt in (ExportFormat.SVG, ExportFormat.DRAWING):
        assert "<svg" in dest.read_text()


@pytest.mark.integration
def test_export_runner_end_to_end(step_file: Path, tmp_path: Path) -> None:
    """Drive the runner exactly as the executor would: JSON request -> JSON response."""
    import json
    import subprocess
    import sys

    request = tmp_path / "req.json"
    response = tmp_path / "resp.json"
    request.write_text(
        json.dumps(
            {
                "step_path": str(step_file),
                "out_dir": str(tmp_path / "exports"),
                "base_name": "widget",
                "title": "Widget",
                "formats": ["3mf", "dxf", "drawing"],
            }
        )
    )
    subprocess.run(
        [sys.executable, "-m", "infrastructure.cad.export_runner", str(request), str(response)],
        cwd=Path(__file__).resolve().parents[2] / "src",
        check=True,
        capture_output=True,
    )
    payload = json.loads(response.read_text())
    assert payload["ok"] is True, payload
    outputs = payload["outputs"]
    assert set(outputs) == {"3mf", "dxf", "drawing"}
    for path_str in outputs.values():
        assert Path(path_str).stat().st_size > 0
