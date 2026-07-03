"""Port for persisting a session's parametric revision history."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from domain.models.revision import DesignVersion, RevisionHistory


@runtime_checkable
class DesignHistoryRepository(Protocol):
    """Stores and reloads the lineage of `DesignVersion`s for a session.

    Kept separate from `ArtifactRepository`: artifacts are heavy on-disk geometry, whereas
    history is small structured metadata that must round-trip losslessly (spec + params +
    lineage). Implementations own their own storage layout under the workspace.
    """

    def save_version(self, session_id: str, version: DesignVersion) -> None:
        """Append a version to the session's history, persisting immediately."""
        ...

    def load_history(self, session_id: str) -> RevisionHistory:
        """Return the full lineage; an empty `RevisionHistory` if none is recorded yet."""
        ...

    def get_version(self, session_id: str, version: int) -> DesignVersion | None:
        """Fetch a single version by number, or None if it does not exist."""
        ...
