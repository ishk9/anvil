"""Rich-based observer that streams the agent's work to the terminal."""

from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from domain.models.conversation import ToolCall, ToolResult

_MAX_RESULT_CHARS = 600


class RichObserver:
    def __init__(self, console: Console) -> None:
        self._console = console

    def on_assistant_text(self, text: str) -> None:
        self._console.print(Panel(Markdown(text), title="engineer", border_style="cyan"))

    def on_tool_call(self, call: ToolCall) -> None:
        preview = ", ".join(f"{k}={_short(v)}" for k, v in call.arguments.items())
        self._console.print(f"[dim]-> {call.name}({preview})[/dim]")

    def on_tool_result(self, result: ToolResult) -> None:
        status = "[green]ok[/green]" if result.ok else "[red]fail[/red]"
        body = result.text
        if len(body) > _MAX_RESULT_CHARS:
            body = body[:_MAX_RESULT_CHARS] + " ..."
        self._console.print(f"[dim]  {status}: {body}[/dim]")
        if result.images:
            paths = ", ".join(str(img.path) for img in result.images)
            self._console.print(f"[dim]  rendered {len(result.images)} view(s): {paths}[/dim]")

    def on_step_limit(self, limit: int) -> None:
        self._console.print(f"[yellow]Reached the {limit}-step limit for this turn.[/yellow]")


def _short(value: object, limit: int = 40) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "..."
