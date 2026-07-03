"""Port for executing model-authored CAD code into a real solid."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from domain.models.errors import CadError
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
    ) -> Result[GeometryArtifact, CadError]: ...
