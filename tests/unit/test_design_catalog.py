from __future__ import annotations

from pathlib import Path

from application.catalog_service import CatalogService
from domain.models.catalog import CatalogEntry
from domain.models.design_spec import Material
from domain.models.geometry import BoundingBox, GeometryArtifact
from infrastructure.persistence.filesystem_design_catalog import FilesystemDesignCatalog


def _entry(
    design_id: str,
    *,
    title: str = "Drone arm",
    material: Material = Material.NYLON_CF,
    tags: tuple[str, ...] = (),
) -> CatalogEntry:
    return CatalogEntry(
        design_id=design_id,
        title=title,
        material=material,
        bounding_box=BoundingBox(x_mm=10.0, y_mm=20.0, z_mm=5.0),
        created_at="2026-07-03T00:00:00+00:00",
        step_path=Path(f"/exports/{design_id}.step"),
        stl_path=Path(f"/exports/{design_id}.stl"),
        tags=tags,
        mass_g=12.5,
    )


def test_add_then_list_round_trips(tmp_path: Path) -> None:
    catalog = FilesystemDesignCatalog(workspace_dir=tmp_path)
    catalog.add(_entry("d1", tags=("drone", "structural")))

    entries = catalog.list_all()
    assert len(entries) == 1
    got = entries[0]
    assert got.design_id == "d1"
    assert got.material is Material.NYLON_CF
    assert got.tags == ("drone", "structural")
    assert got.mass_g == 12.5
    assert got.bounding_box.x_mm == 10.0


def test_list_all_empty_when_no_file(tmp_path: Path) -> None:
    catalog = FilesystemDesignCatalog(workspace_dir=tmp_path)
    assert catalog.list_all() == []


def test_add_same_id_replaces_not_duplicates(tmp_path: Path) -> None:
    catalog = FilesystemDesignCatalog(workspace_dir=tmp_path)
    catalog.add(_entry("d1", title="First"))
    catalog.add(_entry("d1", title="Second"))

    entries = catalog.list_all()
    assert len(entries) == 1
    assert entries[0].title == "Second"


def test_search_by_text_matches_title_and_tags(tmp_path: Path) -> None:
    catalog = FilesystemDesignCatalog(workspace_dir=tmp_path)
    catalog.add(_entry("d1", title="Drone arm", tags=("aerospace",)))
    catalog.add(_entry("d2", title="Bracket", tags=("mount",)))

    assert {e.design_id for e in catalog.search("drone", [])} == {"d1"}
    assert {e.design_id for e in catalog.search("mount", [])} == {"d2"}
    assert catalog.search("nonexistent", []) == []


def test_search_by_tags_is_conjunctive(tmp_path: Path) -> None:
    catalog = FilesystemDesignCatalog(workspace_dir=tmp_path)
    catalog.add(_entry("d1", tags=("drone", "structural")))
    catalog.add(_entry("d2", tags=("drone",)))

    assert {e.design_id for e in catalog.search("", ["drone"])} == {"d1", "d2"}
    assert {e.design_id for e in catalog.search("", ["drone", "structural"])} == {"d1"}


def test_search_tags_are_case_insensitive(tmp_path: Path) -> None:
    catalog = FilesystemDesignCatalog(workspace_dir=tmp_path)
    catalog.add(_entry("d1", tags=("Drone",)))
    assert {e.design_id for e in catalog.search("", ["DRONE"])} == {"d1"}


def test_empty_query_and_no_tags_returns_all(tmp_path: Path) -> None:
    catalog = FilesystemDesignCatalog(workspace_dir=tmp_path)
    catalog.add(_entry("d1"))
    catalog.add(_entry("d2"))
    assert len(catalog.search("", [])) == 2


def test_service_add_design_persists_entry(tmp_path: Path) -> None:
    catalog = FilesystemDesignCatalog(workspace_dir=tmp_path)
    service = CatalogService(catalog=catalog)
    artifact = GeometryArtifact(
        artifact_id="part_001",
        source_code="part = Box(1, 1, 1)",
        step_path=tmp_path / "part.step",
        stl_path=tmp_path / "part.stl",
        bounding_box=BoundingBox(x_mm=1.0, y_mm=1.0, z_mm=1.0),
    )

    entry = service.add_design(
        design_id="d1",
        title="Cube",
        material=Material.PLA,
        artifact=artifact,
        step_path=tmp_path / "exports" / "cube.step",
        stl_path=tmp_path / "exports" / "cube.stl",
        tags=["test", "cube"],
        mass_g=1.24,
    )

    assert entry.created_at  # populated with a timestamp
    found = service.search(query="cube", tags=["test"])
    assert [e.design_id for e in found] == ["d1"]
    assert service.list_all()[0].mass_g == 1.24
