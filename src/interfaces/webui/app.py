"""Web UI server.

A single-page front end for driving design sessions from a browser — no MCP client
required. It reuses the same `DesignSessionCoordinator` the CLI and API drive, so the
agent loop lives in exactly one place.

A turn runs on a worker thread while its observer callbacks are pushed onto a queue and
drained as Server-Sent Events, so the browser watches tool calls, results and assistant
text arrive live rather than waiting for the whole turn to finish.

Endpoints:
- GET  /                              the single-page app
- POST /api/v1/sessions               create a session, returns its id
- GET  /api/v1/sessions/{id}/stream   run a turn (?text=...) and stream progress as SSE
- GET  /api/v1/sessions/{id}/state    current spec / artifacts / exports snapshot
- GET  /files/{path}                  serve a workspace file (STL, STEP, render PNG)
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from application.use_cases.run_design_session import DesignSessionCoordinator, SessionHandle
from domain.models.conversation import ToolCall, ToolResult

log = structlog.get_logger(__name__)

_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")

# Sentinel pushed onto the event queue when a turn's worker thread has finished.
_DONE = object()


def _sse(event: str, data: dict[str, Any]) -> str:
    """Format one Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class _QueueObserver:
    """Agent observer that funnels every callback onto a thread-safe queue.

    The worker thread running the turn calls these; the request coroutine drains the
    queue and re-emits each item as an SSE frame.
    """

    def __init__(self, sink: queue.Queue[tuple[str, dict[str, Any]]]) -> None:
        self._sink = sink

    def on_assistant_text(self, text: str) -> None:
        if text.strip():
            self._sink.put(("assistant", {"text": text}))

    def on_tool_call(self, call: ToolCall) -> None:
        self._sink.put(("tool_call", {"name": call.name, "arguments": call.arguments}))

    def on_tool_result(self, result: ToolResult) -> None:
        self._sink.put(
            (
                "tool_result",
                {"ok": result.ok, "text": _truncate(result.text)},
            )
        )

    def on_step_limit(self, limit: int) -> None:
        self._sink.put(("step_limit", {"limit": limit}))


def _truncate(text: str, limit: int = 4000) -> str:
    """Keep tool-result payloads sane for the browser; full text lives in the workspace."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… ({len(text) - limit} more chars)"


@dataclass(frozen=True, slots=True)
class _SpecView:
    title: str
    material: str
    summary: str
    requirements: list[str]
    constraints: list[str]
    interfaces: list[str]


def _spec_view(handle: SessionHandle) -> _SpecView | None:
    spec = handle.session.spec
    if spec is None:
        return None
    return _SpecView(
        title=spec.title,
        material=spec.material.value,
        summary=spec.summary,
        requirements=list(spec.requirements),
        constraints=list(spec.constraints),
        interfaces=list(spec.interfaces),
    )


def _state_payload(handle: SessionHandle, workspace: Path) -> dict[str, Any]:
    """Snapshot of what the UI shows in its side panel: spec, artifacts, exports."""
    session = handle.session
    spec = _spec_view(handle)
    artifacts = [
        {
            "id": a.artifact_id,
            "bbox_mm": list(a.bounding_box.as_tuple()),
            "stl": _rel(a.stl_path, workspace),
            "step": _rel(a.step_path, workspace),
            "renders": [r for r in (_rel(p, workspace) for p in a.render_paths) if r],
        }
        for a in session.artifacts
    ]
    return {
        "session_id": session.session_id,
        "spec": None if spec is None else asdict(spec),
        "artifacts": artifacts,
        "exports": [e for e in (_rel(p, workspace) for p in session.exports) if e],
    }


def _rel(path: Path, workspace: Path) -> str | None:
    """Path relative to the workspace, or None if it escapes it (never served)."""
    try:
        return str(path.resolve().relative_to(workspace))
    except ValueError:
        return None


def create_webui_app(
    coordinator: DesignSessionCoordinator,
    workspace_dir: Path,
    *,
    viewer_url: str = "",
) -> FastAPI:
    """Build the Web UI app.

    `coordinator` and `workspace_dir` come from the composition root (`Container`); this
    layer never constructs adapters itself. `viewer_url` is the base URL of the live 3D
    viewer, embedded as an iframe when set.
    """
    app = FastAPI(title="Anvil Web UI", version="0.1.0")
    workspace = workspace_dir.resolve()
    sessions: dict[str, SessionHandle] = {}

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html = _HTML.replace("{{VIEWER_URL}}", viewer_url)
        return HTMLResponse(html)

    @app.post("/api/v1/sessions")
    def create_session() -> JSONResponse:
        handle = coordinator.new_session()
        sessions[handle.session_id] = handle
        return JSONResponse({"session_id": handle.session_id}, status_code=201)

    @app.get("/api/v1/sessions/{session_id}/state")
    def state(session_id: str) -> JSONResponse:
        handle = sessions.get(session_id)
        if handle is None:
            raise HTTPException(status_code=404, detail="session not found")
        return JSONResponse(_state_payload(handle, workspace))

    @app.get("/api/v1/sessions/{session_id}/stream")
    async def stream(session_id: str, text: str = Query(min_length=1)) -> StreamingResponse:
        handle = sessions.get(session_id)
        if handle is None:
            raise HTTPException(status_code=404, detail="session not found")

        async def event_stream() -> AsyncIterator[str]:
            events: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
            error: dict[str, str] = {}

            def worker() -> None:
                try:
                    handle.send(text, _QueueObserver(events))
                except Exception as exc:  # surface failures to the browser, don't hang it
                    log.exception("webui.turn_failed", session_id=session_id)
                    error["message"] = str(exc)
                finally:
                    events.put(("__done__", {}))

            thread = threading.Thread(target=worker, daemon=True)
            thread.start()

            yield _sse("start", {"session_id": session_id})
            for event, data in _drain(events):
                yield _sse(event, data)
            if error:
                yield _sse("error", {"message": error["message"]})
            # Hand the final state back so the panel refreshes without a second request.
            state_payload = await run_in_threadpool(_state_payload, handle, workspace)
            yield _sse("state", state_payload)
            yield _sse("done", {})

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/files/{path:path}")
    def files(path: str, download: bool = Query(default=False)) -> FileResponse:
        target = (workspace / path).resolve()
        if not _within(target, workspace) or not target.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        media = _MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream")
        headers = {"Cache-Control": "no-store"}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="{target.name}"'
        return FileResponse(target, media_type=media, headers=headers)

    return app


_MEDIA_TYPES = {
    ".stl": "model/stl",
    ".step": "application/step",
    ".stp": "application/step",
    ".png": "image/png",
}


def _within(target: Path, root: Path) -> bool:
    """Guard against path traversal out of the workspace."""
    return root == target or root in target.parents


def _drain(events: queue.Queue[tuple[str, dict[str, Any]]]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield queued events until the worker signals completion."""
    while True:
        event, data = events.get()
        if event == "__done__":
            return
        yield event, data
