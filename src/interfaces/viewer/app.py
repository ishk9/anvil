"""Live 3D viewer server.

Serves a WebGL (three.js) viewer that renders the newest exported/built STL in the
workspace and hot-reloads it whenever a new build lands — so you watch the model
update in real time as the agent iterates via the MCP tools.

Beyond passive viewing this is an inspection surface: it computes mass/bbox
properties on demand, runs the full validation stack inline, lists prior built
versions for A/B comparison, and exposes a gallery of every session's renders.

Endpoints:
- GET /                  the viewer page
- GET /model.stl         the newest .stl under the workspace (optionally ?session=)
- GET /api/latest        metadata about the newest .stl (name, mtime)
- GET /api/inspect       mass/volume/bbox/watertightness for the newest (or a named) STL
- GET /api/validation    full ValidationReport (issues + metrics) for the newest STL
- GET /api/versions      built artifacts under a session, for A/B compare
- GET /api/gallery       sessions/exports across the workspace, with thumbnails
- GET /artifact.stl      a specific built artifact's STL (by ?path=)
- GET /thumbnail         a render PNG (by ?path=)
- GET /download/stl      the newest STL as an attachment
- GET /download/step     the newest STEP as an attachment
- GET /events            Server-Sent Events stream; emits on every model change
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import structlog
import trimesh
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from container import Container
from domain.models.design_spec import MATERIAL_LIBRARY, DesignSpec, Material
from domain.models.geometry import BoundingBox, GeometryArtifact
from domain.models.validation import Severity, ValidationReport

log = structlog.get_logger(__name__)

_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")


def _newest_stl(workspace: Path, session: str | None) -> Path | None:
    """Return the most recently modified .stl under the workspace (or a session subdir)."""
    root = workspace / session if session else workspace
    if not root.exists():
        return None
    candidates = list(root.rglob("*.stl"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _newest_step(workspace: Path, session: str | None) -> Path | None:
    """Return the most recently modified .step under the workspace (or a session subdir)."""
    root = workspace / session if session else workspace
    if not root.exists():
        return None
    candidates = list(root.rglob("*.step"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _resolve_material(name: str | None) -> Material:
    """Map a query-param material name to a `Material`, defaulting to PETG on miss."""
    if not name:
        return Material.PETG
    try:
        return Material(name.lower())
    except ValueError:
        return Material.PETG


def _safe_under(workspace: Path, rel: str) -> Path:
    """Resolve ``rel`` under the workspace, refusing paths that escape it."""
    candidate = (workspace / rel).resolve()
    if not candidate.is_relative_to(workspace):
        raise HTTPException(status_code=400, detail="path escapes workspace")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="not found")
    return candidate


def inspect_stl(stl_path: Path, material: Material) -> dict[str, Any]:
    """Compute mass/geometry properties for an STL, keyed for the HUD.

    build123d works in millimetres, so mesh volume is mm^3 and density (g/cm^3)
    converts as ``mass_g = volume_mm3 / 1000 * density``.
    """
    mesh = trimesh.load(stl_path, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh):
        raise HTTPException(status_code=422, detail="STL did not load as a triangle mesh")

    profile = MATERIAL_LIBRARY[material]
    extents = mesh.extents
    bbox = BoundingBox(float(extents[0]), float(extents[1]), float(extents[2]))
    volume_mm3 = float(mesh.volume)
    mass_g = volume_mm3 / 1000.0 * profile.density_g_cm3
    com = [round(float(c), 3) for c in mesh.center_mass]

    return {
        "name": stl_path.name,
        "material": material.value,
        "density_g_cm3": profile.density_g_cm3,
        "bbox_mm": [bbox.x_mm, bbox.y_mm, bbox.z_mm],
        "volume_mm3": volume_mm3,
        "volume_cm3": volume_mm3 / 1000.0,
        "mass_g": mass_g,
        "center_of_mass_mm": com,
        "watertight": bool(mesh.is_watertight),
        "triangles": len(mesh.faces),
    }


def _report_to_json(report: ValidationReport) -> dict[str, Any]:
    """Flatten a `ValidationReport` into JSON the panel can colour by severity."""
    return {
        "passed": report.passed,
        "has_warnings": report.has_warnings,
        "issues": [
            {
                "check": issue.check,
                "severity": Severity(issue.severity).name,
                "message": issue.message,
                "value": issue.value,
                "limit": issue.limit,
            }
            for issue in report.issues
        ],
        "metrics": dict(report.metrics),
    }


def _artifact_from_stl(stl_path: Path, material: Material) -> GeometryArtifact:
    """Build a minimal `GeometryArtifact` from an STL so the validators can run.

    The composite fans out over mass/printability/drone/slicer/FEA; only the STL and
    a bounding box are load-bearing here. STEP path points alongside if present.
    """
    mesh = trimesh.load(stl_path, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh):
        raise HTTPException(status_code=422, detail="STL did not load as a triangle mesh")
    extents = mesh.extents
    bbox = BoundingBox(float(extents[0]), float(extents[1]), float(extents[2]))
    step_path = stl_path.with_suffix(".step")
    return GeometryArtifact(
        artifact_id=stl_path.stem,
        source_code="",
        step_path=step_path if step_path.exists() else stl_path,
        stl_path=stl_path,
        bounding_box=bbox,
    )


def _versions(workspace: Path, session: str) -> list[dict[str, Any]]:
    """List built artifacts for a session: ``<session>/artifacts/<id>/<id>.stl``."""
    artifacts_dir = workspace / session / "artifacts"
    if not artifacts_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for stl in sorted(artifacts_dir.glob("*/*.stl"), key=lambda p: p.stat().st_mtime):
        thumb = next(iter(sorted(stl.parent.glob("*.png"))), None)
        out.append(
            {
                "artifact_id": stl.parent.name,
                "stl": str(stl.relative_to(workspace)),
                "thumbnail": str(thumb.relative_to(workspace)) if thumb else None,
                "mtime": stl.stat().st_mtime,
            }
        )
    return out


def _gallery(workspace: Path) -> list[dict[str, Any]]:
    """List every session with its newest STL and a representative thumbnail."""
    if not workspace.exists():
        return []
    out: list[dict[str, Any]] = []
    for session_dir in sorted(p for p in workspace.iterdir() if p.is_dir()):
        stls = list(session_dir.rglob("*.stl"))
        if not stls:
            continue
        newest = max(stls, key=lambda p: p.stat().st_mtime)
        thumb = next(iter(sorted(session_dir.rglob("*.png"))), None)
        out.append(
            {
                "session": session_dir.name,
                "stl": str(newest.relative_to(workspace)),
                "thumbnail": str(thumb.relative_to(workspace)) if thumb else None,
                "count": len(stls),
                "mtime": newest.stat().st_mtime,
            }
        )
    return out


def create_viewer_app(workspace_dir: Path) -> FastAPI:
    app = FastAPI(title="Anvil Viewer", version="0.2.0")
    workspace = workspace_dir.resolve()

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(_HTML)

    @app.get("/api/latest")
    def latest(session: str | None = Query(default=None)) -> JSONResponse:
        newest = _newest_stl(workspace, session)
        if newest is None:
            return JSONResponse({"name": None, "mtime": 0.0})
        return JSONResponse(
            {
                "name": newest.name,
                "path": str(newest.relative_to(workspace)),
                "mtime": newest.stat().st_mtime,
            }
        )

    @app.get("/api/inspect")
    def inspect(
        session: str | None = Query(default=None),
        material: str | None = Query(default=None),
        path: str | None = Query(default=None),
    ) -> JSONResponse:
        stl = _safe_under(workspace, path) if path else _newest_stl(workspace, session)
        if stl is None:
            raise HTTPException(status_code=404, detail="no .stl found in workspace yet")
        return JSONResponse(inspect_stl(stl, _resolve_material(material)))

    @app.get("/api/validation")
    def validation(
        session: str | None = Query(default=None),
        material: str | None = Query(default=None),
        path: str | None = Query(default=None),
    ) -> JSONResponse:
        stl = _safe_under(workspace, path) if path else _newest_stl(workspace, session)
        if stl is None:
            raise HTTPException(status_code=404, detail="no .stl found in workspace yet")
        mat = _resolve_material(material)
        artifact = _artifact_from_stl(stl, mat)
        spec = DesignSpec(
            title=stl.stem,
            summary="Ad-hoc inspection of the newest build in the live viewer.",
            material=mat,
        )
        report = Container().validator().validate(artifact=artifact, spec=spec)
        return JSONResponse({"model": stl.name, **_report_to_json(report)})

    @app.get("/api/versions")
    def versions(session: str = Query()) -> JSONResponse:
        return JSONResponse({"session": session, "versions": _versions(workspace, session)})

    @app.get("/api/gallery")
    def gallery() -> JSONResponse:
        return JSONResponse({"sessions": _gallery(workspace)})

    @app.get("/model.stl")
    def model(session: str | None = Query(default=None)) -> FileResponse:
        newest = _newest_stl(workspace, session)
        if newest is None:
            raise HTTPException(status_code=404, detail="no .stl found in workspace yet")
        return FileResponse(
            newest,
            media_type="model/stl",
            headers={"Cache-Control": "no-store", "X-Model-Name": newest.name},
        )

    @app.get("/artifact.stl")
    def artifact_stl(path: str = Query()) -> FileResponse:
        stl = _safe_under(workspace, path)
        if stl.suffix.lower() != ".stl":
            raise HTTPException(status_code=400, detail="not an .stl")
        return FileResponse(
            stl,
            media_type="model/stl",
            headers={"Cache-Control": "no-store", "X-Model-Name": stl.name},
        )

    @app.get("/thumbnail")
    def thumbnail(path: str = Query()) -> FileResponse:
        png = _safe_under(workspace, path)
        if png.suffix.lower() != ".png":
            raise HTTPException(status_code=400, detail="not a .png")
        return FileResponse(png, media_type="image/png")

    @app.get("/download/stl")
    def download_stl(session: str | None = Query(default=None)) -> FileResponse:
        newest = _newest_stl(workspace, session)
        if newest is None:
            raise HTTPException(status_code=404, detail="no .stl found in workspace yet")
        return FileResponse(newest, media_type="model/stl", filename=newest.name)

    @app.get("/download/step")
    def download_step(session: str | None = Query(default=None)) -> FileResponse:
        newest = _newest_step(workspace, session)
        if newest is None:
            raise HTTPException(status_code=404, detail="no .step found in workspace yet")
        return FileResponse(
            newest, media_type="application/step", filename=newest.name
        )

    @app.get("/events")
    async def events(session: str | None = Query(default=None)) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            last: float | None = None
            # Emit an initial keepalive so the client wires up immediately.
            yield "retry: 2000\n\n"
            while True:
                newest = _newest_stl(workspace, session)
                mtime = newest.stat().st_mtime if newest else 0.0
                if mtime != last:
                    last = mtime
                    name = newest.name if newest else ""
                    yield f"event: model\ndata: {mtime}:{name}\n\n"
                else:
                    yield ": ping\n\n"
                await asyncio.sleep(1.0)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app
