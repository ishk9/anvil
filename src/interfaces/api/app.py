"""FastAPI application factory.

Sessions are held in-process (fine for a single-worker MVP). For horizontal scaling,
back `SessionStore` with Redis and make the agent loop async — no other layer changes.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from application.use_cases.run_design_session import SessionHandle
from config.settings import get_settings
from container import Container
from interfaces.api.observer import CapturingObserver
from interfaces.api.schemas import (
    CreateSessionResponse,
    HealthResponse,
    MessageRequest,
    MessageResponse,
)
from logging_setup import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    container = Container()
    coordinator = container.coordinator()
    sessions: dict[str, SessionHandle] = {}

    app = FastAPI(title="MechForge", version="0.1.0")

    @app.get("/health/healthz", response_model=HealthResponse)
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/health/readyz", response_model=HealthResponse)
    def readyz() -> HealthResponse:
        ready = bool(settings.active_api_key())
        if not ready:
            raise HTTPException(status_code=503, detail="LLM API key not configured")
        return HealthResponse(status="ready")

    @app.post("/api/v1/sessions", response_model=CreateSessionResponse, status_code=201)
    def create_session() -> CreateSessionResponse:
        handle = coordinator.new_session()
        sessions[handle.session_id] = handle
        return CreateSessionResponse(session_id=handle.session_id)

    @app.post("/api/v1/sessions/{session_id}/messages", response_model=MessageResponse)
    def send_message(session_id: str, body: MessageRequest) -> MessageResponse:
        handle = sessions.get(session_id)
        if handle is None:
            raise HTTPException(status_code=404, detail="session not found")
        observer = CapturingObserver()
        handle.send(body.text, observer)
        return MessageResponse(
            session_id=session_id,
            reply=observer.reply(),
            artifact_count=len(handle.session.artifacts),
            exports=[str(p) for p in handle.session.exports],
        )

    return app
