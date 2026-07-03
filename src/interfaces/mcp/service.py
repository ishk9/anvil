"""MCP-facing facade over `DesignToolkit`.

Holds per-`session_id` state and returns plain data (text + image paths), so it is fully
testable without importing any MCP framework types. The FastMCP layer (`server.py`) is a
thin adapter over this.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from application.design_toolkit import DesignToolkit
from application.formatting import format_materials, format_validation_report
from application.session import DesignSession
from domain.models.design_spec import DesignSpec, Material
from domain.models.geometry import GeometryArtifact


@dataclass(frozen=True, slots=True)
class BuildOutcome:
    ok: bool
    message: str
    image_paths: list[Path] = field(default_factory=list)


class McpDesignService:
    def __init__(self, *, toolkit: DesignToolkit) -> None:
        self._toolkit = toolkit
        self._sessions: dict[str, DesignSession] = {}

    def list_materials(self) -> str:
        return format_materials(self._toolkit.materials())

    def build(self, *, session_id: str, code: str) -> BuildOutcome:
        session = self._session(session_id)
        result = self._toolkit.build(session=session, code=code)
        if result.is_err():
            return BuildOutcome(
                ok=False,
                message="Build failed. Fix the code and call build_part again.\n\n"
                + result.error.as_feedback(),  # type: ignore[union-attr]
            )
        artifact = result.unwrap()
        b = artifact.bounding_box
        message = (
            f"Built '{artifact.artifact_id}'. Bounding box: "
            f"{b.x_mm:.1f} x {b.y_mm:.1f} x {b.z_mm:.1f} mm. "
            f"Inspect the attached renders. Use artifact_id='{artifact.artifact_id}' "
            f"to validate_part or export_part."
        )
        return BuildOutcome(ok=True, message=message, image_paths=list(artifact.render_paths))

    def validate(self, *, session_id: str, artifact_id: str, material: str) -> str:
        session, artifact = self._require_artifact(session_id, artifact_id)
        session.spec = self._spec_with_material(session, material)
        report = self._toolkit.validate(session=session, artifact=artifact)
        return format_validation_report(report)

    def export(self, *, session_id: str, artifact_id: str, title: str | None) -> str:
        session, artifact = self._require_artifact(session_id, artifact_id)
        step_dest, stl_dest = self._toolkit.export(session=session, artifact=artifact, title=title)
        return f"Exported:\n- {step_dest}\n- {stl_dest}"

    # --- internals ---

    def _session(self, session_id: str) -> DesignSession:
        return self._sessions.setdefault(session_id, DesignSession(session_id=session_id))

    def _require_artifact(
        self, session_id: str, artifact_id: str
    ) -> tuple[DesignSession, GeometryArtifact]:
        session = self._session(session_id)
        for artifact in session.artifacts:
            if artifact.artifact_id == artifact_id:
                return session, artifact
        known = ", ".join(a.artifact_id for a in session.artifacts) or "none"
        raise ValueError(
            f"Unknown artifact_id '{artifact_id}' in session '{session_id}'. Known: {known}."
        )

    def _spec_with_material(self, session: DesignSession, material: str) -> DesignSpec:
        mat = Material(material)
        if session.spec is not None:
            return replace(session.spec, material=mat)
        return DesignSpec(title="untitled", summary="", material=mat)
