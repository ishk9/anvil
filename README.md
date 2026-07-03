# Anvil

AI mechanical design toolkit for parametric 3D parts (drone mounts, arms, brackets,
enclosures). You describe a part in plain language; the AI discusses the physics and
constraints, writes **parametric CAD code**, executes it into a real solid, renders and
critiques it, validates it, and exports manufacturable **STEP/STL** files.

> Runs entirely in Docker — nothing is installed on your host.

## Primary interface: MCP server

Anvil is primarily an **MCP server** — your AI client (Cursor, Claude Desktop, Claude
Code) is the brain that talks to you and calls Anvil's CAD tools (`build_part`,
`validate_part`, `export_part`, `list_materials`). `build_part` returns rendered images
inline, so the model sees its own geometry. **Setup and client config:
[`docs/02-MCP.md`](docs/02-MCP.md).**

```bash
cd anvil && docker compose build   # then register in ~/.cursor/mcp.json (see docs)
```

It also ships two standalone interfaces that share the same core: an embedded-agent CLI
(`anvil design`, needs an LLM key) and an HTTP API (`anvil serve`).

## How it works

LLMs can't sculpt geometry, but they're excellent at writing code. So the agent emits
[`build123d`](https://build123d.readthedocs.io) Python, which is executed in an isolated
sandbox to produce true B-rep solids. See [`docs/00-OVERVIEW.md`](docs/00-OVERVIEW.md) and
[`docs/01-ARCHITECTURE.md`](docs/01-ARCHITECTURE.md).

```
discuss physics ─► generate CAD code ─► execute (sandbox) ─► render + vision critique
       ▲                                        │                        │
       └──────────────── validate (mass / printability / FEA-stub) ◄─────┘
                                   │ (pass + approve)
                                   ▼
                            export STEP / STL
```

## Quick start

```bash
cp .env.example .env      # add your Anthropic or OpenAI API key
make cli                  # build the image and start the interactive design REPL
```

Then just talk to it, e.g. *"I need a motor mount for a 5-inch quad, 2306 motors, 30.5mm
stack."* Generated files land in `./data/<session>/exports/`.

### Other commands

```bash
make api       # run the HTTP API on http://localhost:8000 (docs at /docs)
make checks    # ruff + mypy + pytest inside the container
make shell     # a shell in the container
```

Or use Docker Compose directly:

```bash
docker compose run --rm cli design
docker compose up api
docker compose run --rm checks
```

## Live 3D viewer

A WebGL (three.js) viewer that renders the newest STL in your workspace and **hot-reloads
it the instant a new build lands** — so you watch the model update in real time as the MCP
tools iterate. It orbits (drag), zooms (trackpad/scroll, `+`/`−`, or buttons), and has
keyboard controls (`+`/`−` zoom, arrows orbit, `R` reset, `W` wireframe, Space auto-rotate).

```bash
docker compose up viewer     # then open http://localhost:8091
```

- Newest build across the workspace: `http://localhost:8091/`
- A specific session only: `http://localhost:8091/?session=drone8cm`

Server endpoints: `/` (viewer), `/model.stl` (newest STL, optional `?session=`),
`/api/latest` (metadata), `/events` (SSE stream that pushes on every model change). The
MCP server also exposes an `open_viewer` tool that returns a one-click link to open it.

### Auto-open in Cursor's internal browser (like `next dev`)

Cursor's "dev server → internal browser" is VS Code's terminal **port auto-forward +
Simple Browser**. It only detects processes/output in Cursor's **integrated terminal**, so
a detached container started elsewhere won't trigger it. To get the Next.js-style
auto-open:

1. Add to Cursor settings (`~/Library/Application Support/Cursor/User/settings.json`) so
   port `8091` opens a preview automatically:

   ```json
   "remote.autoForwardPorts": true,
   "remote.autoForwardPortsSource": "hybrid",
   "remote.portsAttributes": { "8091": { "label": "Anvil Viewer", "onAutoForward": "openPreview" } }
   ```

2. The `anvil viewer` command prints `Local:   http://localhost:8091/` at startup
   (like `next dev`) so Cursor's terminal parser catches the URL.

3. Run the viewer **in Cursor's integrated terminal** (foreground — not detached), e.g.
   `docker compose up viewer`, and make sure port `8091` isn't already taken by a detached
   container. Cursor then forwards the port and pops the internal browser automatically.

Fallbacks that always work: the **⌥⌘V** keybinding (bound to `simpleBrowser.show`), the
`open_viewer` MCP tool's link, or **⌘⇧P → "Simple Browser: Show"**. The viewer base URL is
configurable via `MECHFORGE_VIEWER_URL`.

## Configuration

All settings are environment variables prefixed `MECHFORGE_` (see `.env.example`).
Pick the model vendor with `MECHFORGE_LLM_VENDOR=anthropic|openai` and set the matching
API key.

## Scope & honesty

Strong at brackets, mounts, standoffs, enclosures, simple arms. Weak at complex
assemblies, tight tolerances, and aerodynamic surfaces. FEA is stubbed behind a port —
the agent reasons about stress but does not verify it. **Bench-test any load-bearing or
flying part.**

## Layout

```
src/
  domain/           # models, ports (protocols) — depends on nothing
  application/      # agent loop, tools, use cases
  infrastructure/   # build123d, LLM adapters, renderer, validators, storage
  interfaces/       # Typer CLI, MCP server, FastAPI API, live 3D viewer
  config/           # settings
  container.py      # dependency-injection composition root
  logging_setup.py  # structured logging
docker/             # Dockerfile
docs/               # architecture & overview
tests/              # unit + integration
```

## Security note

The agent executes model-authored Python. That is inherently unsafe and is contained by
process isolation + resource limits **inside Docker**. Do not run the sandbox on a trusted
host outside a container.
