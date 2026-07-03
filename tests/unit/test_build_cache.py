from __future__ import annotations

from pathlib import Path

from domain.models.geometry import BoundingBox, GeometryArtifact
from domain.ports.build_cache import build_cache_key
from infrastructure.persistence.filesystem_build_cache import FilesystemBuildCache


def _make_artifact(tmp_path: Path, *, artifact_id: str = "part_001") -> GeometryArtifact:
    src = tmp_path / "src"
    src.mkdir()
    step = src / f"{artifact_id}.step"
    stl = src / f"{artifact_id}.stl"
    render = src / f"{artifact_id}_iso.png"
    step.write_text("STEP")
    stl.write_text("STL")
    render.write_bytes(b"\x89PNG")
    return GeometryArtifact(
        artifact_id=artifact_id,
        source_code="part = Box(1, 2, 3)",
        step_path=step,
        stl_path=stl,
        bounding_box=BoundingBox(x_mm=1.0, y_mm=2.0, z_mm=3.0),
        render_paths=(render,),
    )


def test_key_is_stable_and_order_independent() -> None:
    a = build_cache_key("code", {"a": 1.0, "b": 2.0})
    b = build_cache_key("code", {"b": 2.0, "a": 1.0})
    assert a == b
    assert len(a) == 64


def test_key_changes_with_code_and_params() -> None:
    base = build_cache_key("code", {"a": 1.0})
    assert build_cache_key("other", {"a": 1.0}) != base
    assert build_cache_key("code", {"a": 2.0}) != base
    assert build_cache_key("code") != base


def test_get_miss_returns_none(tmp_path: Path) -> None:
    cache = FilesystemBuildCache(workspace_dir=tmp_path)
    assert cache.get("deadbeef") is None


def test_put_then_get_round_trips(tmp_path: Path) -> None:
    cache = FilesystemBuildCache(workspace_dir=tmp_path)
    artifact = _make_artifact(tmp_path)
    key = build_cache_key(artifact.source_code)

    cache.put(key, artifact)
    restored = cache.get(key)

    assert restored is not None
    assert restored.artifact_id == artifact.artifact_id
    assert restored.source_code == artifact.source_code
    assert restored.bounding_box == artifact.bounding_box
    assert restored.step_path.exists()
    assert restored.stl_path.exists()
    assert len(restored.render_paths) == 1
    assert restored.render_paths[0].exists()


def test_put_copies_files_so_entry_survives_source_deletion(tmp_path: Path) -> None:
    cache = FilesystemBuildCache(workspace_dir=tmp_path)
    artifact = _make_artifact(tmp_path)
    key = build_cache_key(artifact.source_code)
    cache.put(key, artifact)

    # Wipe the originating scratch dir; the cache entry must remain valid.
    artifact.step_path.unlink()
    artifact.stl_path.unlink()

    restored = cache.get(key)
    assert restored is not None
    assert restored.step_path.read_text() == "STEP"


def test_get_treats_incomplete_entry_as_miss(tmp_path: Path) -> None:
    cache = FilesystemBuildCache(workspace_dir=tmp_path)
    artifact = _make_artifact(tmp_path)
    key = build_cache_key(artifact.source_code)
    cache.put(key, artifact)

    restored = cache.get(key)
    assert restored is not None
    restored.stl_path.unlink()  # corrupt the entry

    assert cache.get(key) is None
