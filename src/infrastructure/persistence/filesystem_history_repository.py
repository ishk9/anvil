"""`DesignHistoryRepository` backed by a single JSON file per session.

Layout (parallels `FilesystemArtifactRepository`, same workspace root):
    <workspace>/<session_id>/history.json

The whole lineage lives in one file rewritten on each append. History is small (tens of
versions of structured metadata, no geometry), so a read-modify-write of one JSON doc is
simpler and safer than an append-log and avoids partial-record corruption. Writes are
atomic via a temp-file + os.replace so a crash mid-write can't truncate the history.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import structlog

from domain.models.design_spec import DesignSpec, LoadCase, Material
from domain.models.revision import DesignVersion, RevisionHistory

log = structlog.get_logger(__name__)

_HISTORY_FILE = "history.json"
_SCHEMA_VERSION = 1


def _load_case_to_dict(lc: LoadCase) -> dict[str, Any]:
    return {"name": lc.name, "description": lc.description, "force_newtons": lc.force_newtons}


def _load_case_from_dict(data: dict[str, Any]) -> LoadCase:
    return LoadCase(
        name=data["name"],
        description=data["description"],
        force_newtons=data.get("force_newtons"),
    )


def _spec_to_dict(spec: DesignSpec) -> dict[str, Any]:
    return {
        "title": spec.title,
        "summary": spec.summary,
        "material": spec.material.value,
        "requirements": list(spec.requirements),
        "constraints": list(spec.constraints),
        "load_cases": [_load_case_to_dict(lc) for lc in spec.load_cases],
        "interfaces": list(spec.interfaces),
    }


def _spec_from_dict(data: dict[str, Any]) -> DesignSpec:
    return DesignSpec(
        title=data["title"],
        summary=data["summary"],
        material=Material(data["material"]),
        requirements=tuple(data.get("requirements", ())),
        constraints=tuple(data.get("constraints", ())),
        load_cases=tuple(_load_case_from_dict(lc) for lc in data.get("load_cases", ())),
        interfaces=tuple(data.get("interfaces", ())),
    )


def _version_to_dict(v: DesignVersion) -> dict[str, Any]:
    return {
        "version": v.version,
        "parent_version": v.parent_version,
        "spec": _spec_to_dict(v.spec),
        "code": v.code,
        "params": dict(v.params),
        "artifact_id": v.artifact_id,
        "note": v.note,
        "timestamp": v.timestamp,
    }


def _version_from_dict(data: dict[str, Any]) -> DesignVersion:
    return DesignVersion(
        version=data["version"],
        spec=_spec_from_dict(data["spec"]),
        code=data["code"],
        # JSON numbers may decode as int; params are semantically float dimensions.
        params={k: float(val) for k, val in data.get("params", {}).items()},
        parent_version=data.get("parent_version"),
        artifact_id=data.get("artifact_id"),
        note=data.get("note", ""),
        timestamp=data.get("timestamp", ""),
    )


class FilesystemHistoryRepository:
    def __init__(self, *, workspace_dir: Path) -> None:
        self._root = workspace_dir

    def _session_dir(self, session_id: str) -> Path:
        path = self._root / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _history_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / _HISTORY_FILE

    def load_history(self, session_id: str) -> RevisionHistory:
        path = self._history_path(session_id)
        if not path.exists():
            return RevisionHistory()
        payload = json.loads(path.read_text())
        versions = tuple(_version_from_dict(v) for v in payload.get("versions", ()))
        return RevisionHistory(versions=versions)

    def get_version(self, session_id: str, version: int) -> DesignVersion | None:
        return self.load_history(session_id).get(version)

    def save_version(self, session_id: str, version: DesignVersion) -> None:
        history = self.load_history(session_id).append(version)
        payload = {
            "schema": _SCHEMA_VERSION,
            "versions": [_version_to_dict(v) for v in history.versions],
        }
        self._atomic_write(self._history_path(session_id), json.dumps(payload, indent=2))
        log.info(
            "history.saved",
            session_id=session_id,
            version=version.version,
            parent=version.parent_version,
        )

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)
