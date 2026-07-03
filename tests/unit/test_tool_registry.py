from typing import ClassVar

from application.tools.base import Tool, ToolContext
from application.tools.registry import ToolRegistry
from domain.models.conversation import ToolCall, ToolResult


class _EchoTool(Tool):
    name = "echo"
    description = "echo back"
    input_schema: ClassVar[dict[str, object]] = {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
    }

    def run(self, ctx: ToolContext, arguments: dict[str, object], call_id: str) -> ToolResult:
        return ToolResult(tool_call_id=call_id, ok=True, text=str(arguments.get("msg", "")))


class _BoomTool(Tool):
    name = "boom"
    description = "raises"
    input_schema: ClassVar[dict[str, object]] = {"type": "object", "properties": {}}

    def run(self, ctx: ToolContext, arguments: dict[str, object], call_id: str) -> ToolResult:
        raise RuntimeError("kaboom")


def test_registry_exposes_specs() -> None:
    registry = ToolRegistry([_EchoTool()])
    names = {spec.name for spec in registry.specs()}
    assert names == {"echo"}


def test_unknown_tool_returns_failure() -> None:
    registry = ToolRegistry([_EchoTool()])
    result = registry.execute(ctx=None, call=ToolCall(id="1", name="nope", arguments={}))  # type: ignore[arg-type]
    assert not result.ok
    assert "Unknown tool" in result.text


def test_tool_exception_is_contained() -> None:
    registry = ToolRegistry([_BoomTool()])
    result = registry.execute(ctx=None, call=ToolCall(id="2", name="boom", arguments={}))  # type: ignore[arg-type]
    assert not result.ok
    assert "kaboom" in result.text
