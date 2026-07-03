"""The single orchestration core for building, validating, and exporting parts.

Both entry points use this: the embedded agent's tools *and* the MCP server. Keeping the
build->render->validate->export pipeline here (not in either interface) is what keeps the
system DRY — a new front end is a thin adapter over this class.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import structlog

from application.session import DesignSession
from config.settings import Settings
from domain.models.design_spec import MATERIAL_LIBRARY, MaterialProfile
from domain.models.errors import CadError
from domain.models.geometry import GeometryArtifact
from domain.models.result import Ok, Result
from domain.models.validation import ValidationReport
from domain.ports.artifact_repository import ArtifactRepository
from domain.ports.cad_executor import CadExecutor
from domain.ports.renderer import Renderer
from domain.ports.validator import GeometryValidator

log = structlog.get_logger(__name__)


def _slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text.strip()]
    return "".join(keep).strip("-") or "part"


class DesignToolkit:
    def __init__(
        self,
        *,
        executor: CadExecutor,
        renderer: Renderer,
        validator: GeometryValidator,
        repository: ArtifactRepository,
        settings: Settings,
    ) -> None:
        self._executor = executor
        self._renderer = renderer
        self._validator = validator
        self._repository = repository
        self._settings = settings

    def build(self, *, session: DesignSession, code: str) -> Result[GeometryArtifact, CadError]:
        """Execute CAD code, render previews, and record the artifact in the session."""
        artifact_id = session.next_artifact_id()
        out_dir = self._repository.artifact_dir(session.session_id, artifact_id)

        result = self._executor.execute(code=code, out_dir=out_dir, artifact_id=artifact_id)
        if result.is_err():
            return result

        artifact = result.unwrap()
        renders = self._renderer.render(
            stl_path=artifact.stl_path,
            out_dir=out_dir,
            views=self._settings.render_views,
        )
        artifact = replace(artifact, render_paths=renders)
        session.add_artifact(artifact)
        return Ok(artifact)

    def validate(self, *, session: DesignSession, artifact: GeometryArtifact) -> ValidationReport:
        return self._validator.validate(artifact=artifact, spec=session.effective_spec())

    def export(
        self, *, session: DesignSession, artifact: GeometryArtifact, title: str | None = None
    ) -> tuple[Path, Path]:
        export_dir = self._repository.export_dir(session.session_id)
        base = _slug(title or session.effective_spec().title)
        step_dest = export_dir / f"{base}.step"
        stl_dest = export_dir / f"{base}.stl"
        shutil.copy2(artifact.step_path, step_dest)
        shutil.copy2(artifact.stl_path, stl_dest)
        session.exports.extend([step_dest, stl_dest])
        return step_dest, stl_dest

    def materials(self) -> list[MaterialProfile]:
        return list(MATERIAL_LIBRARY.values())
