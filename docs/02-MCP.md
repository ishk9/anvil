# Anvil as an MCP server

Anvil's primary interface is an **MCP (Model Context Protocol) server**. Instead of
embedding its own LLM, it exposes the CAD pipeline as tools that *your* AI client
(Cursor, Claude Desktop, Claude Code) calls. The client is the "brain" — it discusses the
physics and constraints with you and decides when to build, validate, and export. You get
the vision loop for free: `build_part` returns rendered images inline, so the client model
literally sees each result.

## Design decisions (per the build-mcp-server skill)

- **Deployment:** local **stdio** — this is a personal/local tool that runs pure
  computation (build123d) and writes local files. The client launches it, inside Docker.
- **Tool pattern:** one-tool-per-action (small surface, < 15 tools).
- **Framework:** FastMCP (Python), since we wrap a Python library.
- **Auth:** none. No LLM key is needed for the MCP path.

## Tools

| Tool | Purpose |
|---|---|
| `list_materials` | Materials with density and tensile strength, to inform trade-offs. |
| `build_part(code, session_id="default")` | Execute build123d code (must bind `part`, mm). Returns bounding box + rendered images and an `artifact_id`. |
| `validate_part(artifact_id, material="petg", session_id="default")` | Mass, printability, FEA-stub checks. |
| `export_part(artifact_id, title="", session_id="default")` | Copy STEP + STL to `data/<session>/exports/`. |

State is keyed by `session_id`, so one client conversation maps to one session.

## Setup

1. Build the image once:

   ```bash
   cd anvil
   docker compose build
   ```

   Or pull a published image instead of building locally — tagged releases are
   pushed to GHCR:

   ```bash
   docker pull ghcr.io/ishk9/anvil:latest   # or a specific tag, e.g. :v0.1.0
   docker tag ghcr.io/ishk9/anvil:latest anvil:latest
   ```

   The client config below references `anvil:latest`; the `docker tag` step lets a
   pulled image satisfy it without changing the config. Alternatively, use the full
   `ghcr.io/ishk9/anvil:<tag>` reference directly in the client `args`.

2. Register the server with your client. It's launched via `docker run -i` over stdio.

### Cursor (`~/.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "anvil": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "/ABSOLUTE/PATH/TO/anvil/data:/data",
        "anvil:latest",
        "anvil", "mcp"
      ]
    }
  }
}
```

Replace `/ABSOLUTE/PATH/TO/anvil` with the real path. The `-v` mount is where your
exported STEP/STL files land.

### Claude Desktop (`claude_desktop_config.json`)

Same `mcpServers` block as above.

## Try it

In your client, say: *"Use anvil to design a 5-inch quad motor mount for 2306 motors
with a 30.5mm mounting pattern, in nylon-CF."* The client will call `list_materials`,
`build_part` (and iterate on the renders), `validate_part`, then `export_part`.

## Notes

- Logs go to **stderr**; **stdout** carries the JSON-RPC protocol (don't print to stdout).
- Executing model-authored code is isolated in a subprocess with resource limits, inside
  Docker. Keep it that way.
- The embedded agent (`anvil design`) and HTTP API still exist for standalone use;
  all three share the same `DesignToolkit` core.
