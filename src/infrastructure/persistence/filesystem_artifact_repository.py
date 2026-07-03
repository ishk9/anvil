"""`ArtifactRepository` backed by the local filesystem (a mounted Docker volume).

Layout:
    <workspace>/<session_id>/
        artifacts/<artifact_id>/   # step, stl, renders, sandbox io
        exports/                   # user-facing final files
"""

from __future__ import annotations

from pathlib import Path


class FilesystemArtifactRepository:
    def __init__(self, *, workspace_dir: Path) -> None:
        self._root = workspace_dir

    def session_dir(self, session_id: str) -> Path:
        path = self._root / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def artifact_dir(self, session_id: str, artifact_id: str) -> Path:
        path = self.session_dir(session_id) / "artifacts" / artifact_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def export_dir(self, session_id: str) -> Path:
        path = self.session_dir(session_id) / "exports"
        path.mkdir(parents=True, exist_ok=True)
        return path
