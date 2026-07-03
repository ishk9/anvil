"""Observer that captures assistant text so the API can return a single reply."""

from __future__ import annotations

from domain.models.conversation import ToolCall, ToolResult


class CapturingObserver:
    def __init__(self) -> None:
        self._chunks: list[str] = []

    def on_assistant_text(self, text: str) -> None:
        self._chunks.append(text)

    def on_tool_call(self, call: ToolCall) -> None:
        return None

    def on_tool_result(self, result: ToolResult) -> None:
        return None

    def on_step_limit(self, limit: int) -> None:
        self._chunks.append(f"(reached the {limit}-step limit for this turn)")

    def reply(self) -> str:
        return "\n\n".join(c for c in self._chunks if c).strip()
