"""Unit tests for the live viewer server: pure helpers + endpoints via TestClient.

A tiny watertight STL is written with trimesh into a tmp workspace laid out like the
real one (``<session>/artifacts/<id>/<id>.stl`` + a render PNG), so the helpers and
FastAPI routes can be exercised without build123d.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import trimesh
from fastapi.testclient import TestClient

from domain.models.design_spec import Material
from interfaces.viewer.app import (
    _gallery,
    _newest_step,
    _newest_stl,
    _resolve_material,
    _versions,
    create_viewer_app,
    inspect_stl,
)


def _write_box(path: Path, extents: tuple[float, float, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    trimesh.creation.box(extents=extents).export(path)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A workspace with one session, one built artifact (stl+step+png), and an export."""
    session = tmp_path / "sess-1"
    art = session / "artifacts" / "part-000"
    _write_box(art / "part-000.stl", (10.0, 20.0, 30.0))
    (art / "part-000.step").write_text("ISO-10303-21;", encoding="utf-8")
    (art / "part-000_view0.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    exp = session / "exports"
    _write_box(exp / "part.stl", (10.0, 20.0, 30.0))
    (exp / "part.step").write_text("ISO-10303-21;", encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(workspace: Path) -> TestClient:
    return TestClient(create_viewer_app(workspace))


# ── Pure helpers ───────────────────────────────────────────────────────────


def test_newest_stl_finds_file(workspace: Path) -> None:
    assert _newest_stl(workspace, None) is not None
    assert _newest_stl(workspace, "sess-1") is not None
    assert _newest_stl(workspace, "missing") is None


def test_newest_step_finds_file(workspace: Path) -> None:
    step = _newest_step(workspace, "sess-1")
    assert step is not None and step.suffix == ".step"


def test_resolve_material_defaults_petg() -> None:
    assert _resolve_material(None) is Material.PETG
    assert _resolve_material("bogus") is Material.PETG
    assert _resolve_material("ALU_6061") is Material.ALU_6061


def test_inspect_stl_measures_box(workspace: Path) -> None:
    stl = workspace / "sess-1" / "artifacts" / "part-000" / "part-000.stl"
    data = inspect_stl(stl, Material.PETG)
    assert data["bbox_mm"] == pytest.approx([10.0, 20.0, 30.0])
    assert data["volume_mm3"] == pytest.approx(6000.0)
    # mass = volume_mm3 / 1000 * density (petg 1.27) = 7.62 g
    assert data["mass_g"] == pytest.approx(6000.0 / 1000.0 * 1.27)
    assert data["watertight"] is True
    assert data["triangles"] == 12


def test_versions_lists_built_artifacts(workspace: Path) -> None:
    versions = _versions(workspace, "sess-1")
    assert len(versions) == 1
    assert versions[0]["artifact_id"] == "part-000"
    assert versions[0]["thumbnail"] is not None
    assert _versions(workspace, "missing") == []


def test_gallery_lists_sessions(workspace: Path) -> None:
    gallery = _gallery(workspace)
    assert len(gallery) == 1
    assert gallery[0]["session"] == "sess-1"
    assert gallery[0]["count"] >= 1
    assert gallery[0]["thumbnail"] is not None


# ── Endpoints ──────────────────────────────────────────────────────────────


def test_index_serves_html(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "Anvil" in r.text


def test_latest_reports_newest(client: TestClient) -> None:
    r = client.get("/api/latest", params={"session": "sess-1"})
    assert r.status_code == 200
    assert r.json()["name"] is not None


def test_inspect_endpoint(client: TestClient) -> None:
    r = client.get("/api/inspect", params={"session": "sess-1", "material": "pla"})
    assert r.status_code == 200
    body = r.json()
    assert body["material"] == "pla"
    assert body["bbox_mm"] == pytest.approx([10.0, 20.0, 30.0])


def test_validation_endpoint(client: TestClient) -> None:
    r = client.get("/api/validation", params={"session": "sess-1"})
    assert r.status_code == 200
    body = r.json()
    assert "issues" in body and "metrics" in body
    assert "passed" in body
    # The mass validator always runs and emits a mass metric.
    assert any(i["check"] == "mass_properties" for i in body["issues"])
    assert body["metrics"].get("mass.mass_g") is not None


def test_versions_endpoint(client: TestClient) -> None:
    r = client.get("/api/versions", params={"session": "sess-1"})
    assert r.status_code == 200
    assert r.json()["versions"][0]["artifact_id"] == "part-000"


def test_gallery_endpoint(client: TestClient) -> None:
    r = client.get("/api/gallery")
    assert r.status_code == 200
    assert r.json()["sessions"][0]["session"] == "sess-1"


def test_model_stl_endpoint(client: TestClient) -> None:
    r = client.get("/model.stl", params={"session": "sess-1"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "model/stl"


def test_artifact_stl_by_path(client: TestClient) -> None:
    rel = "sess-1/artifacts/part-000/part-000.stl"
    r = client.get("/artifact.stl", params={"path": rel})
    assert r.status_code == 200
    assert r.headers["x-model-name"] == "part-000.stl"


def test_artifact_stl_rejects_non_stl(client: TestClient) -> None:
    rel = "sess-1/artifacts/part-000/part-000.step"
    r = client.get("/artifact.stl", params={"path": rel})
    assert r.status_code == 400


def test_thumbnail_endpoint(client: TestClient) -> None:
    rel = "sess-1/artifacts/part-000/part-000_view0.png"
    r = client.get("/thumbnail", params={"path": rel})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


def test_path_traversal_rejected(client: TestClient) -> None:
    r = client.get("/artifact.stl", params={"path": "../../etc/passwd"})
    assert r.status_code in (400, 404)


def test_download_stl_attachment(client: TestClient) -> None:
    r = client.get("/download/stl", params={"session": "sess-1"})
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]


def test_download_step_attachment(client: TestClient) -> None:
    r = client.get("/download/step", params={"session": "sess-1"})
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]


def test_inspect_404_when_empty(tmp_path: Path) -> None:
    empty = TestClient(create_viewer_app(tmp_path))
    assert empty.get("/api/inspect").status_code == 404
