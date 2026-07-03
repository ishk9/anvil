"""Mutable per-session state shared by tools during a design conversation.

This is an *application* concern (orchestration state), deliberately separate from the
immutable domain value objects it holds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from domain.models.design_spec import DesignSpec, Material
from domain.models.geometry import GeometryArtifact


@dataclass
class DesignSession:
    session_id: str
    spec: DesignSpec | None = None
    artifacts: list[GeometryArtifact] = field(default_factory=list)
    exports: list[Path] = field(default_factory=list)
    _counter: int = 0

    @property
    def latest(self) -> GeometryArtifact | None:
        return self.artifacts[-1] if self.artifacts else None

    def next_artifact_id(self) -> str:
        self._counter += 1
        return f"part_{self._counter:03d}"

    def add_artifact(self, artifact: GeometryArtifact) -> None:
        self.artifacts.append(artifact)

    def effective_spec(self) -> DesignSpec:
        """Fall back to a neutral spec so validation works before one is agreed."""
        return self.spec or DesignSpec(
            title="untitled",
            summary="No design spec recorded yet.",
            material=Material.PETG,
        )
