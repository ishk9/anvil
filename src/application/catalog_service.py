"""Application service that records exported designs in the catalog and queries it.

The toolkit builds/validates/exports; this service turns a finished export into a durable,
searchable `CatalogEntry`. Kept separate from `DesignToolkit` so the catalog is an opt-in
concern layered on top of the core pipeline rather than baked into every build.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import structlog

from domain.models.catalog import CatalogEntry
from domain.models.design_spec import Material
from domain.models.geometry import GeometryArtifact
from domain.ports.design_catalog import DesignCatalog

log = structlog.get_logger(__name__)


class CatalogService:
    def __init__(self, *, catalog: DesignCatalog) -> None:
        self._catalog = catalog

    def add_design(
        self,
        *,
        design_id: str,
        title: str,
        material: Material,
        artifact: GeometryArtifact,
        step_path: Path,
        stl_path: Path,
        tags: list[str] | None = None,
        mass_g: float | None = None,
        thumbnail_path: Path | None = None,
    ) -> CatalogEntry:
        """Record a design after it has been exported. Returns the stored entry."""
        entry = CatalogEntry(
            design_id=design_id,
            title=title,
            material=material,
            bounding_box=artifact.bounding_box,
            created_at=datetime.now(UTC).isoformat(),
            step_path=step_path,
            stl_path=stl_path,
            tags=tuple(tags or ()),
            mass_g=mass_g,
            thumbnail_path=thumbnail_path,
        )
        self._catalog.add(entry)
        return entry

    def search(self, *, query: str = "", tags: list[str] | None = None) -> list[CatalogEntry]:
        return self._catalog.search(query, tags or [])

    def list_all(self) -> list[CatalogEntry]:
        return self._catalog.list_all()
