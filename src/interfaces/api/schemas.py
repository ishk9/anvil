"""Request/response models for the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateSessionResponse(BaseModel):
    session_id: str


class MessageRequest(BaseModel):
    text: str = Field(min_length=1)


class MessageResponse(BaseModel):
    session_id: str
    reply: str
    artifact_count: int
    exports: list[str]


class HealthResponse(BaseModel):
    status: str
