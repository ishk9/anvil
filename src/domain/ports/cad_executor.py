"""Port for executing model-authored CAD code into a real solid."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable

from domain.models.errors import CadError
from domain.models.export import ExportFormat
from domain.models.geometry import GeometryArtifact
from domain.models.result import Result


@runtime_checkable
class CadExecutor(Protocol):
    """Runs a parametric CAD script and returns exported geometry, or a typed error.

    Implementations MUST isolate execution (separate process, timeout, resource caps):
    the input is untrusted model output.
    """

    def execute(
        self,
        *,
        code: str,
        out_dir: Path,
        artifact_id: str,
        params: Mapping[str, float] | None = None,
    ) -> Result[GeometryArtifact, CadError]: ...

    def export_extra(
        self,
        *,
        step_path: Path,
        out_dir: Path,
        base_name: str,
        title: str,
        formats: list[ExportFormat],
    ) -> dict[str, Path]:
        """Produce best-effort derived formats (3mf/obj/gltf/dxf/svg/drawing) from a STEP."""
        ...
