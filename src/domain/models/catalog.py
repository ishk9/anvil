"""Value object for a design recorded in the searchable catalog.

A `CatalogEntry` is a lightweight index record of a design the user exported — enough to
find it again (title, tags, material) and to preview it (bbox, thumbnail, export paths)
without loading any geometry. It is not the geometry itself; the export paths point at it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from domain.models.design_spec import Material
from domain.models.geometry import BoundingBox


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    design_id: str
    title: str
    material: Material
    bounding_box: BoundingBox
    created_at: str
    """ISO-8601 UTC timestamp of when the design was cataloged."""

    step_path: Path
    stl_path: Path
    tags: tuple[str, ...] = field(default_factory=tuple)
    mass_g: float | None = None
    thumbnail_path: Path | None = None

    def matches(self, query: str, tags: tuple[str, ...]) -> bool:
        """True if the free-text `query` and every requested tag are satisfied.

        Query matches substrings of title or tags (case-insensitive); an empty query
        matches everything. Tag filtering is conjunctive (all requested tags must be
        present), also case-insensitive.
        """
        own_tags = {t.lower() for t in self.tags}
        if not all(t.lower() in own_tags for t in tags):
            return False
        needle = query.strip().lower()
        if not needle:
            return True
        haystack = " ".join((self.title.lower(), *own_tags))
        return needle in haystack
