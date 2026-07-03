"""Orchestrates parametric revisions and versioned design history.

This is the application-layer core behind "make the arms 10% thicker": rather than a fresh
build, `revise` clones a parent `DesignVersion`, applies numeric param overrides (and
optionally new code/spec), re-runs the SAME stored code against the merged params via an
injected build callable, records the resulting version, and diffs it against its parent.

Kept interface-agnostic: every method returns domain value objects (`DesignVersion`,
`VersionDiff`, `RevisionHistory`) or a `Result`, never formatted text. The MCP/CLI layers
format. The build step is injected as a callable so this service does not depend on the
concrete toolkit signature — the caller wires in `DesignToolkit.build` (params-aware).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import structlog

from domain.models.design_spec import DesignSpec
from domain.models.errors import CadError, CadErrorKind
from domain.models.geometry import GeometryArtifact
from domain.models.result import Err, Ok, Result
from domain.models.revision import (
    DesignVersion,
    Params,
    RevisionHistory,
    VersionDiff,
    diff_versions,
)
from domain.ports.history_repository import DesignHistoryRepository

log = structlog.get_logger(__name__)

# Signature the caller must supply: build `code` with `params` seeded into the sandbox and
# record the artifact in the session. Threads through `DesignToolkit.build(session, code,
# params)`. Session identity is closed over by the caller, so it is not a parameter here.
BuildFn = Callable[[str, Params], Result[GeometryArtifact, CadError]]


class RevisionService:
    def __init__(self, *, history: DesignHistoryRepository) -> None:
        self._history = history

    def record_initial(
        self,
        *,
        session_id: str,
        spec: DesignSpec,
        code: str,
        artifact_id: str,
        params: Params | None = None,
        note: str = "",
    ) -> DesignVersion:
        """Record the root (or next) version after a successful first build.

        The version number is allocated from the persisted history, so recording is safe
        across process restarts (the in-memory session counter is not the source of truth).
        """
        history = self._history.load_history(session_id)
        version = DesignVersion(
            version=history.next_version,
            spec=spec,
            code=code,
            params=dict(params or {}),
            parent_version=history.latest.version if history.latest else None,
            artifact_id=artifact_id,
            note=note,
            timestamp=_now(),
        )
        self._history.save_version(session_id, version)
        return version

    def revise(
        self,
        *,
        session_id: str,
        base_version: int,
        param_overrides: Params,
        build: BuildFn,
        code: str | None = None,
        spec: DesignSpec | None = None,
        note: str = "",
    ) -> Result[tuple[DesignVersion, VersionDiff], CadError]:
        """Clone `base_version`, apply overrides, re-run, then record + diff the result.

        On build failure NO version is recorded — history stays a lineage of things that
        actually built. On success the returned diff is computed parent -> child so the
        caller can report exactly what the revision changed.
        """
        history = self._history.load_history(session_id)
        parent = history.get(base_version)
        if parent is None:
            known = ", ".join(str(v.version) for v in history.versions) or "none"
            return Err(
                CadError(
                    kind=CadErrorKind.INTERNAL,
                    message=(
                        f"Unknown base_version {base_version} for session '{session_id}'. "
                        f"Known versions: {known}."
                    ),
                )
            )

        child = parent.with_overrides(
            version=history.next_version,
            param_overrides=param_overrides,
            code=code,
            spec=spec,
            note=note,
            timestamp=_now(),
        )

        result = build(child.code, child.params)
        if result.is_err():
            log.info(
                "revision.build_failed",
                session_id=session_id,
                base_version=base_version,
                kind=result.error.kind.value,  # type: ignore[union-attr]
            )
            return result  # type: ignore[return-value]

        artifact = result.unwrap()
        recorded = DesignVersion(
            version=child.version,
            spec=child.spec,
            code=child.code,
            params=child.params,
            parent_version=child.parent_version,
            artifact_id=artifact.artifact_id,
            note=child.note,
            timestamp=child.timestamp,
        )
        self._history.save_version(session_id, recorded)
        return Ok((recorded, diff_versions(parent, recorded)))

    def history(self, session_id: str) -> RevisionHistory:
        return self._history.load_history(session_id)

    def diff(
        self, *, session_id: str, from_version: int, to_version: int
    ) -> Result[VersionDiff, str]:
        """Diff two recorded versions. Errors (as text) if either version is unknown."""
        history = self._history.load_history(session_id)
        a = history.get(from_version)
        b = history.get(to_version)
        if a is None or b is None:
            missing = [
                str(v) for v, found in ((from_version, a), (to_version, b)) if found is None
            ]
            return Err(
                f"Unknown version(s) {', '.join(missing)} for session '{session_id}'."
            )
        return Ok(diff_versions(a, b))


def _now() -> str:
    return datetime.now(UTC).isoformat()
