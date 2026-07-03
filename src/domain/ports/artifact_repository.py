"""Port for allocating on-disk locations for session artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class ArtifactRepository(Protocol):
    """Owns the workspace layout so no other layer hard-codes paths."""

    def session_dir(self, session_id: str) -> Path: ...

    def artifact_dir(self, session_id: str, artifact_id: str) -> Path: ...

    def export_dir(self, session_id: str) -> Path: ...
