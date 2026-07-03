"""Anvil MCP server (FastMCP, stdio transport).

This turns Anvil into an MCP tool provider: your MCP client (Cursor, Claude Desktop,
Claude Code) is the "brain" that discusses the physics with you and calls these tools.
There is no embedded LLM here — the client drives the conversation.

Tool-design pattern: one-tool-per-action (small surface). Transport: stdio, so the
client launches the process (we launch it inside Docker). Structured logs go to stderr;
stdout is reserved for the JSON-RPC protocol.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ContentBlock, TextContent

from config.settings import get_settings
from container import Container
from interfaces.mcp.service import McpDesignService
from logging_setup import configure_logging

_BUILD_DOC = """\
Build a 3D part by executing build123d Python code, then return the bounding box and
rendered preview images so you can inspect the result.

Requirements for `code`:
- Use build123d in algebra mode; all imports are implicit (write `Box`, `Cylinder`, ...).
- Work in MILLIMETRES.
- Bind the final solid to a variable named exactly `part`.

Example:
    plate = Box(30, 30, 4)
    plate = plate - Pos(0, 0, 0) * Cylinder(radius=1.7, height=4)
    part = plate

On failure you get the error text — fix the code and call again. On success you get an
`artifact_id` to pass to validate_part / export_part.
"""


def create_mcp(*, host: str = "0.0.0.0", port: int = 8000) -> FastMCP:
    settings = get_settings()
    # stdio uses stdout for protocol frames, so force logs to stderr (our default sink).
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    container = Container()
    service = McpDesignService(toolkit=container.toolkit())

    mcp = FastMCP("Anvil", host=host, port=port)

    @mcp.tool(description="List available materials with density and tensile strength.")
    def list_materials() -> str:
        return service.list_materials()

    @mcp.tool(description=_BUILD_DOC)
    def build_part(code: str, session_id: str = "default") -> list[ContentBlock]:
        outcome = service.build(session_id=session_id, code=code)
        blocks: list[ContentBlock] = [TextContent(type="text", text=outcome.message)]
        blocks.extend(Image(path=str(p)).to_image_content() for p in outcome.image_paths)
        return blocks

    @mcp.tool(
        description=(
            "Run mass, printability, and FEA-stub checks on a built part. "
            "material is one of the values from list_materials (default 'petg')."
        )
    )
    def validate_part(artifact_id: str, material: str = "petg", session_id: str = "default") -> str:
        return service.validate(session_id=session_id, artifact_id=artifact_id, material=material)

    @mcp.tool(
        description=(
            "Export a built part's STEP and STL to the session export folder. "
            "Call after validation passes and the user approves."
        )
    )
    def export_part(artifact_id: str, title: str = "", session_id: str = "default") -> str:
        return service.export(session_id=session_id, artifact_id=artifact_id, title=title or None)

    @mcp.tool(
        description=(
            "Open the live 3D viewer for a session in Cursor's internal browser. "
            "Returns a one-click command link (simpleBrowser.show) plus the raw URL. "
            "Call this after building so the user can watch the model live."
        )
    )
    def open_viewer(session_id: str = "default") -> str:
        base = settings.viewer_url.rstrip("/")
        url = f"{base}/?session={quote(session_id)}"
        # Cursor/VS Code command URI: runs simpleBrowser.show with the URL as its arg.
        cmd_uri = "command:simpleBrowser.show?" + quote(json.dumps([url]))
        return (
            f"Live viewer for session '{session_id}':\n"
            f"- Open in Cursor's internal browser: [Open viewer]({cmd_uri})\n"
            f"- Or press \u2325\u2318V, or paste this URL: {url}"
        )

    return mcp


_TRANSPORTS = {"stdio": "stdio", "http": "streamable-http", "sse": "sse"}


def main(*, transport: str = "stdio", host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the server. `transport` is one of: stdio (default), http, sse.

    http/sse serve on host:port (streamable-http path is /mcp) — used behind ngrok.
    """
    resolved = _TRANSPORTS.get(transport, transport)
    create_mcp(host=host, port=port).run(transport=resolved)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
