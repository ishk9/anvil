"""Observer interface so entry points can stream the agent's progress."""

from __future__ import annotations

from typing import Protocol

from domain.models.conversation import ToolCall, ToolResult


class AgentObserver(Protocol):
    """Callbacks fired during a turn. Keep implementations non-blocking."""

    def on_assistant_text(self, text: str) -> None: ...

    def on_tool_call(self, call: ToolCall) -> None: ...

    def on_tool_result(self, result: ToolResult) -> None: ...

    def on_step_limit(self, limit: int) -> None: ...


class NullObserver:
    """No-op observer (used by non-interactive callers such as the HTTP API)."""

    def on_assistant_text(self, text: str) -> None:
        return None

    def on_tool_call(self, call: ToolCall) -> None:
        return None

    def on_tool_result(self, result: ToolResult) -> None:
        return None

    def on_step_limit(self, limit: int) -> None:
        return None
