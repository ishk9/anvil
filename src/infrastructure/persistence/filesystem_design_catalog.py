"""`DesignCatalog` backed by a single JSON file at the workspace root.

Layout:
    <workspace>/catalog.json

The catalog is small (one record per exported design, metadata only) and read far more
often than written, so the whole document is loaded, mutated, and rewritten atomically on
each `add` — the same read-modify-write pattern as the history repository. Adding a design
whose id already exists replaces the earlier entry rather than duplicating it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import structlog

from domain.models.catalog import CatalogEntry
from domain.models.design_spec import Material
from domain.models.geometry import BoundingBox

log = structlog.get_logger(__name__)

_CATALOG_FILE = "catalog.json"
_SCHEMA_VERSION = 1


def _entry_to_dict(entry: CatalogEntry) -> dict[str, Any]:
    return {
        "design_id": entry.design_id,
        "title": entry.title,
        "material": entry.material.value,
        "bounding_box": {
            "x_mm": entry.bounding_box.x_mm,
            "y_mm": entry.bounding_box.y_mm,
            "z_mm": entry.bounding_box.z_mm,
        },
        "created_at": entry.created_at,
        "step_path": str(entry.step_path),
        "stl_path": str(entry.stl_path),
        "tags": list(entry.tags),
        "mass_g": entry.mass_g,
        "thumbnail_path": str(entry.thumbnail_path) if entry.thumbnail_path is not None else None,
    }


def _entry_from_dict(data: dict[str, Any]) -> CatalogEntry:
    bbox = data["bounding_box"]
    thumb = data.get("thumbnail_path")
    return CatalogEntry(
        design_id=data["design_id"],
        title=data["title"],
        material=Material(data["material"]),
        bounding_box=BoundingBox(x_mm=bbox["x_mm"], y_mm=bbox["y_mm"], z_mm=bbox["z_mm"]),
        created_at=data["created_at"],
        step_path=Path(data["step_path"]),
        stl_path=Path(data["stl_path"]),
        tags=tuple(data.get("tags", ())),
        mass_g=data.get("mass_g"),
        thumbnail_path=Path(thumb) if thumb is not None else None,
    )


class FilesystemDesignCatalog:
    def __init__(self, *, workspace_dir: Path) -> None:
        self._root = workspace_dir
        self._path = workspace_dir / _CATALOG_FILE

    def add(self, entry: CatalogEntry) -> None:
        entries = [e for e in self.list_all() if e.design_id != entry.design_id]
        entries.append(entry)
        payload = {
            "schema": _SCHEMA_VERSION,
            "entries": [_entry_to_dict(e) for e in entries],
        }
        self._root.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self._path, json.dumps(payload, indent=2))
        log.info("catalog.added", design_id=entry.design_id, tags=list(entry.tags))

    def search(self, query: str, tags: list[str]) -> list[CatalogEntry]:
        wanted = tuple(tags)
        return [e for e in self.list_all() if e.matches(query, wanted)]

    def list_all(self) -> list[CatalogEntry]:
        if not self._path.exists():
            return []
        payload = json.loads(self._path.read_text())
        return [_entry_from_dict(e) for e in payload.get("entries", ())]

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)
