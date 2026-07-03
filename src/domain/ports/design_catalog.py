"""Port for a searchable, tagged catalog of past designs."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from domain.models.catalog import CatalogEntry


@runtime_checkable
class DesignCatalog(Protocol):
    """Persistent index of exported designs, queryable by text and tags."""

    def add(self, entry: CatalogEntry) -> None: ...

    def search(self, query: str, tags: list[str]) -> list[CatalogEntry]: ...

    def list_all(self) -> list[CatalogEntry]: ...
