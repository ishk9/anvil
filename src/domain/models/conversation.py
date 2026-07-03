"""Provider-agnostic conversation primitives.

These decouple the agent loop from any specific LLM SDK. Adapters translate between
these types and vendor formats (Anthropic blocks / OpenAI messages).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class ContentImage:
    """A rendered image handed back to the model so it can *see* its own geometry."""

    path: Path
    media_type: str = "image/png"


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A model's request to invoke a tool."""

    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The outcome of running a tool, fed back to the model on the next turn."""

    tool_call_id: str
    ok: bool
    text: str
    images: tuple[ContentImage, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    tool_results: tuple[ToolResult, ...] = field(default_factory=tuple)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0
