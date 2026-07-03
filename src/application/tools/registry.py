"""Registry that resolves and dispatches tool calls by name."""

from __future__ import annotations

from collections.abc import Sequence

import structlog

from application.tools.base import Tool, ToolContext
from domain.models.conversation import ToolCall, ToolResult
from domain.ports.llm import ToolSpec

log = structlog.get_logger(__name__)


class ToolRegistry:
    def __init__(self, tools: Sequence[Tool]) -> None:
        self._tools: dict[str, Tool] = {tool.name: tool for tool in tools}

    def specs(self) -> list[ToolSpec]:
        return [tool.spec() for tool in self._tools.values()]

    def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(
                tool_call_id=call.id,
                ok=False,
                text=f"Unknown tool '{call.name}'.",
            )
        try:
            log.info("tool.execute", tool=call.name)
            return tool.run(ctx, call.arguments, call.id)
        except Exception as exc:
            log.exception("tool.error", tool=call.name)
            return ToolResult(
                tool_call_id=call.id,
                ok=False,
                text=f"Tool '{call.name}' failed unexpectedly: {exc}",
            )
