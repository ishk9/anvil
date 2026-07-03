"""MechForge CLI — interactive design sessions and the API server launcher."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel

from config import __version__
from config.settings import get_settings
from container import Container
from interfaces.cli.observer import RichObserver
from logging_setup import configure_logging

app = typer.Typer(add_completion=False, help="AI mechanical design agent for 3D parts.")
console = Console()

_BANNER = (
    "[bold]Anvil[/bold] — describe a part (a drone motor mount, a bracket...). "
    "I'll discuss the physics, then design it.\n\n"
    "Commands: [cyan]/new[/cyan] reset · [cyan]/spec[/cyan] show spec · "
    "[cyan]/exports[/cyan] list files · [cyan]/exit[/cyan] quit"
)


@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"Anvil {__version__}")


@app.command()
def design(session_id: str | None = typer.Option(None, help="Resume/name a session id.")) -> None:
    """Start an interactive design session."""
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    if not settings.active_api_key():
        console.print(
            Panel(
                f"No API key set for vendor '{settings.llm_vendor.value}'. Set "
                f"[cyan]MECHFORGE_ANTHROPIC_API_KEY[/cyan] or "
                f"[cyan]MECHFORGE_OPENAI_API_KEY[/cyan] (and MECHFORGE_LLM_VENDOR).",
                title="missing configuration",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    container = Container()
    handle = container.coordinator().new_session(session_id)
    observer = RichObserver(console)

    console.print(Panel(_BANNER, border_style="cyan"))
    console.print(f"[dim]session: {handle.session_id}[/dim]\n")

    while True:
        try:
            user_text = console.input("[bold green]you[/bold green] > ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            return

        if not user_text:
            continue
        if user_text in {"/exit", "/quit"}:
            console.print("[dim]bye[/dim]")
            return
        if user_text == "/new":
            handle = container.coordinator().new_session()
            console.print(f"[dim]new session: {handle.session_id}[/dim]")
            continue
        if user_text == "/spec":
            _print_spec(handle)
            continue
        if user_text == "/exports":
            _print_exports(handle)
            continue

        try:
            handle.send(user_text, observer)
        except Exception as exc:
            console.print(Panel(str(exc), title="error", border_style="red"))


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host."),
    port: int = typer.Option(8000, help="Bind port."),
) -> None:
    """Run the HTTP API."""
    import uvicorn

    uvicorn.run("interfaces.api.app:create_app", host=host, port=port, factory=True)


@app.command()
def mcp(
    transport: str = typer.Option("stdio", help="Transport: stdio, http, or sse."),
    host: str = typer.Option("0.0.0.0", help="Bind host (http/sse)."),
    port: int = typer.Option(8000, help="Bind port (http/sse)."),
) -> None:
    """Run the MCP server (stdio for local clients; http/sse to expose via ngrok)."""
    from interfaces.mcp.server import main as mcp_main

    mcp_main(transport=transport, host=host, port=port)


@app.command()
def viewer(
    host: str = typer.Option("0.0.0.0", help="Bind host."),
    port: int = typer.Option(8000, help="Bind port."),
) -> None:
    """Run the live 3D viewer — auto-reloads the newest STL as builds land."""
    import uvicorn

    from interfaces.viewer.app import create_viewer_app

    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    app_instance = create_viewer_app(settings.workspace_dir)
    # Print a localhost URL Cursor's terminal port-detection can parse (like `next dev`),
    # so running this in Cursor's integrated terminal auto-opens the internal browser.
    console.print("\n[bold]Anvil Viewer[/bold]  ready")
    console.print(f"  Local:   {settings.viewer_url}/\n")
    uvicorn.run(app_instance, host=host, port=port)


@app.command()
def webui(
    host: str = typer.Option("0.0.0.0", help="Bind host."),
    port: int = typer.Option(8000, help="Bind port."),
) -> None:
    """Run the Web UI — drive design sessions from the browser (no MCP client needed)."""
    import uvicorn

    from interfaces.webui.app import create_webui_app

    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    if not settings.active_api_key():
        console.print(
            Panel(
                f"No API key set for vendor '{settings.llm_vendor.value}'. Set "
                f"[cyan]MECHFORGE_ANTHROPIC_API_KEY[/cyan] or "
                f"[cyan]MECHFORGE_OPENAI_API_KEY[/cyan].",
                title="missing configuration",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    container = Container()
    app_instance = create_webui_app(
        container.coordinator(),
        settings.workspace_dir,
        viewer_url=settings.viewer_url,
    )
    console.print("\n[bold]Anvil Web UI[/bold]  ready")
    console.print(f"  Local:   http://localhost:{port}/\n")
    uvicorn.run(app_instance, host=host, port=port)


def _print_spec(handle) -> None:  # type: ignore[no-untyped-def]
    spec = handle.session.spec
    if spec is None:
        console.print("[dim]no design spec recorded yet[/dim]")
        return
    console.print(
        Panel(
            f"[bold]{spec.title}[/bold] ({spec.material.value})\n{spec.summary}\n\n"
            f"requirements: {list(spec.requirements)}\n"
            f"constraints: {list(spec.constraints)}\n"
            f"interfaces: {list(spec.interfaces)}",
            title="design spec",
            border_style="cyan",
        )
    )


def _print_exports(handle) -> None:  # type: ignore[no-untyped-def]
    exports = handle.session.exports
    if not exports:
        console.print("[dim]nothing exported yet[/dim]")
        return
    for path in exports:
        console.print(f"[green]{path}[/green]")


if __name__ == "__main__":
    app()
