"""`BuildCache` backed by the local filesystem (the same mounted workspace volume).

Layout:
    <workspace>/.cache/<key>/
        manifest.json   # bbox, source, relative filenames
        <artifact>.step
        <artifact>.stl
        <render>.png ...

The cache is content-addressed by `build_cache_key`. `put` copies the artifact's files
into the entry directory so the cache stays valid even after the originating session's
scratch dir is cleaned up. `get` re-reads the manifest and verifies every referenced file
still exists on disk — a half-deleted entry is treated as a miss, never a broken artifact.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import structlog

from domain.models.geometry import BoundingBox, GeometryArtifact

log = structlog.get_logger(__name__)

_MANIFEST_FILE = "manifest.json"
_SCHEMA_VERSION = 1


class FilesystemBuildCache:
    def __init__(self, *, workspace_dir: Path) -> None:
        self._root = workspace_dir / ".cache"

    def _entry_dir(self, key: str) -> Path:
        return self._root / key

    def get(self, key: str) -> GeometryArtifact | None:
        entry_dir = self._entry_dir(key)
        manifest_path = entry_dir / _MANIFEST_FILE
        if not manifest_path.exists():
            return None
        try:
            data: dict[str, Any] = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            log.warning("build_cache.manifest_unreadable", key=key)
            return None

        step_path = entry_dir / data["step"]
        stl_path = entry_dir / data["stl"]
        render_paths = tuple(entry_dir / name for name in data.get("renders", ()))
        referenced = (step_path, stl_path, *render_paths)
        if not all(p.exists() for p in referenced):
            log.warning("build_cache.entry_incomplete", key=key)
            return None

        bbox = data["bounding_box"]
        return GeometryArtifact(
            artifact_id=data["artifact_id"],
            source_code=data["source_code"],
            step_path=step_path,
            stl_path=stl_path,
            bounding_box=BoundingBox(x_mm=bbox["x_mm"], y_mm=bbox["y_mm"], z_mm=bbox["z_mm"]),
            render_paths=render_paths,
        )

    def put(self, key: str, artifact: GeometryArtifact) -> None:
        entry_dir = self._entry_dir(key)
        entry_dir.mkdir(parents=True, exist_ok=True)

        step_name = self._copy_in(entry_dir, artifact.step_path)
        stl_name = self._copy_in(entry_dir, artifact.stl_path)
        render_names = [self._copy_in(entry_dir, p) for p in artifact.render_paths]

        manifest = {
            "schema": _SCHEMA_VERSION,
            "artifact_id": artifact.artifact_id,
            "source_code": artifact.source_code,
            "bounding_box": {
                "x_mm": artifact.bounding_box.x_mm,
                "y_mm": artifact.bounding_box.y_mm,
                "z_mm": artifact.bounding_box.z_mm,
            },
            "step": step_name,
            "stl": stl_name,
            "renders": render_names,
        }
        self._atomic_write(entry_dir / _MANIFEST_FILE, json.dumps(manifest, indent=2))
        log.info("build_cache.stored", key=key, artifact_id=artifact.artifact_id)

    @staticmethod
    def _copy_in(entry_dir: Path, source: Path) -> str:
        """Copy `source` into `entry_dir`, returning the stored filename."""
        dest = entry_dir / source.name
        shutil.copy2(source, dest)
        return source.name

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)
