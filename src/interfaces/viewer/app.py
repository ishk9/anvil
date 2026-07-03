"""Live 3D viewer server.

Serves a WebGL (three.js) viewer that renders the newest exported/built STL in the
workspace and hot-reloads it whenever a new build lands — so you watch the model
update in real time as the agent iterates via the MCP tools.

Endpoints:
- GET /                 the viewer page
- GET /model.stl        the newest .stl under the workspace (optionally ?session=)
- GET /api/latest       metadata about the newest .stl (name, mtime)
- GET /events           Server-Sent Events stream; emits on every model change
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

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


def create_viewer_app(workspace_dir: Path) -> FastAPI:
    app = FastAPI(title="MechForge Viewer", version="0.1.0")
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

    @app.get("/model.stl")
    def model(session: str | None = Query(default=None)) -> FileResponse:
        newest = _newest_stl(workspace, session)
        if newest is None:
            raise HTTPException(status_code=404, detail="no .stl found in workspace yet")
        return FileResponse(
            newest,
            media_type="model/stl",
            headers={
                "Cache-Control": "no-store",
                "X-Model-Name": newest.name,
            },
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
