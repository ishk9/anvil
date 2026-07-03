"""Web UI server tests.

The coordinator and agent are faked so no LLM is contacted; the fake agent drives the
observer with a scripted sequence of assistant text, a tool call and a tool result, which
is what the SSE endpoint re-emits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from application.agents.events import AgentObserver
from application.session import DesignSession
from application.tools.base import ToolContext
from application.use_cases.run_design_session import DesignSessionCoordinator, SessionHandle
from domain.models.conversation import ToolCall, ToolResult
from domain.models.design_spec import DesignSpec, Material
from domain.models.geometry import BoundingBox, GeometryArtifact
from interfaces.webui.app import create_webui_app


class _FakeAgent:
    """Stands in for `DesignAgent`: scripts a turn's observer callbacks and mutates state."""

    def run_turn(
        self,
        *,
        ctx: ToolContext,
        history: list[object],
        user_text: str,
        observer: AgentObserver,
    ) -> list[object]:
        observer.on_assistant_text("Let me model that.")
        observer.on_tool_call(ToolCall(id="c1", name="build_part", arguments={"code": "..."}))
        observer.on_tool_result(ToolResult(tool_call_id="c1", ok=True, text="built"))
        # Record spec + an artifact so the state snapshot has something to show.
        ctx.session.spec = DesignSpec(title="bracket", summary="a bracket", material=Material.PETG)
        stl = ctx.settings.workspace_dir / ctx.session.session_id / "part.stl"
        ctx.session.add_artifact(
            GeometryArtifact(
                artifact_id="part_001",
                source_code="...",
                step_path=stl.with_suffix(".step"),
                stl_path=stl,
                bounding_box=BoundingBox(10.0, 20.0, 30.0),
            )
        )
        return history


class _FakeCoordinator(DesignSessionCoordinator):
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    def new_session(self, session_id: str | None = None) -> SessionHandle:
        from config.settings import Settings

        settings = Settings(workspace_dir=self._workspace)
        session = DesignSession(session_id=session_id or "sess_test")
        ctx = ToolContext(session=session, toolkit=None, settings=settings)  # type: ignore[arg-type]
        return SessionHandle(agent=_FakeAgent(), ctx=ctx)  # type: ignore[arg-type]


def _client(workspace: Path, viewer_url: str = "") -> TestClient:
    app = create_webui_app(_FakeCoordinator(workspace), workspace, viewer_url=viewer_url)
    return TestClient(app)


def test_index_serves_html(tmp_path: Path) -> None:
    client = _client(tmp_path)
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "Anvil" in res.text


def test_index_injects_viewer_url(tmp_path: Path) -> None:
    client = _client(tmp_path, viewer_url="http://localhost:8091")
    res = client.get("/")
    assert "http://localhost:8091" in res.text
    assert "{{VIEWER_URL}}" not in res.text


def test_create_session_returns_id(tmp_path: Path) -> None:
    client = _client(tmp_path)
    res = client.post("/api/v1/sessions")
    assert res.status_code == 201
    assert res.json()["session_id"]


def test_state_unknown_session_is_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/v1/sessions/nope/state").status_code == 404


def test_stream_emits_agent_activity_and_final_state(tmp_path: Path) -> None:
    client = _client(tmp_path)
    sid = client.post("/api/v1/sessions").json()["session_id"]

    with client.stream("GET", f"/api/v1/sessions/{sid}/stream?text=make%20a%20bracket") as res:
        assert res.status_code == 200
        body = "".join(res.iter_text())

    events = _parse_sse(body)
    names = [e for e, _ in events]
    assert "start" in names
    assert "assistant" in names
    assert "tool_call" in names
    assert "tool_result" in names
    assert names[-1] == "done"

    assistant = next(d for e, d in events if e == "assistant")
    assert assistant["text"] == "Let me model that."
    state = next(d for e, d in events if e == "state")
    assert state["spec"]["title"] == "bracket"
    assert state["artifacts"][0]["id"] == "part_001"


def test_state_snapshot_after_turn(tmp_path: Path) -> None:
    client = _client(tmp_path)
    sid = client.post("/api/v1/sessions").json()["session_id"]
    with client.stream("GET", f"/api/v1/sessions/{sid}/stream?text=go") as res:
        "".join(res.iter_text())

    state = client.get(f"/api/v1/sessions/{sid}/state").json()
    assert state["artifacts"][0]["bbox_mm"] == [10.0, 20.0, 30.0]


def test_files_serves_workspace_file(tmp_path: Path) -> None:
    (tmp_path / "sess_test").mkdir()
    stl = tmp_path / "sess_test" / "part.stl"
    stl.write_text("solid x\nendsolid x\n")
    client = _client(tmp_path)
    res = client.get("/files/sess_test/part.stl")
    assert res.status_code == 200
    assert res.headers["content-type"] == "model/stl"


def test_files_rejects_path_traversal(tmp_path: Path) -> None:
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("nope")
    client = _client(tmp_path)
    res = client.get("/files/../secret.txt")
    assert res.status_code == 404


def test_files_download_sets_attachment(tmp_path: Path) -> None:
    f = tmp_path / "out.step"
    f.write_text("ISO-10303-21;")
    client = _client(tmp_path)
    res = client.get("/files/out.step?download=true")
    assert res.status_code == 200
    assert "attachment" in res.headers["content-disposition"]


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for frame in body.split("\n\n"):
        event = ""
        data = ""
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = line[len("data:") :].strip()
        if event:
            out.append((event, json.loads(data) if data else {}))
    return out
