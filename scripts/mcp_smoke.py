"""Manual end-to-end smoke test of the MCP server over stdio.

Spawns `anvil mcp`, initializes an MCP session, lists tools, and calls build_part on
a trivial box — verifying the full protocol path including image content. Run inside the
container:

    docker compose run --rm -T mcp python -m scripts.mcp_smoke   # (or see below)
    docker run --rm anvil:latest python /app/scripts/mcp_smoke.py
"""

from __future__ import annotations

import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    params = StdioServerParameters(command="anvil", args=["mcp"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        tools = await session.list_tools()
        print("tools:", sorted(t.name for t in tools.tools))

        result = await session.call_tool("build_part", {"code": "part = Box(20, 20, 4)"})
        kinds = [block.type for block in result.content]
        print("build_part content kinds:", kinds)
        assert "image" in kinds, "expected at least one rendered image in the result"

        validated = await session.call_tool(
            "validate_part", {"artifact_id": "part_001", "material": "pla"}
        )
        text = "".join(b.text for b in validated.content if b.type == "text")
        print("validate_part:", text.splitlines()[0] if text else "(no text)")
        print("OK")


if __name__ == "__main__":
    asyncio.run(main())
