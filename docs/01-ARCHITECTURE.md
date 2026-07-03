# Anvil — Architecture

## Style: Hexagonal (Ports & Adapters) + DDD layering

Dependencies point **inward**. The domain knows nothing about build123d, OpenAI, or
FastAPI. Everything external is reached through a **port** (a `typing.Protocol`) and
implemented by an **adapter** in `infrastructure/`.

```
             interfaces/           (CLI, HTTP — entry points)
                  │ depends on
                  ▼
             application/          (use cases, agent loop, tools)
                  │ depends on
                  ▼
                domain/            (models, ports, pure rules)  ← depends on NOTHING
                  ▲ implemented by
                  │
             infrastructure/       (build123d, LLM SDKs, renderer, validators, storage)
```

Wiring happens once in `container.py` (a `dependency-injector` container). Nothing else
constructs concrete adapters — they are injected.

## Package map (`src/`)

| Layer | Package | Responsibility |
|---|---|---|
| Domain | `domain/models` | Entities & value objects: `DesignSpec`, `GeometryArtifact`, `ValidationReport`, `Result`, conversation types |
| Domain | `domain/ports` | Protocols: `LLMProvider`, `CadExecutor`, `Renderer`, `GeometryValidator`, `ArtifactRepository` |
| Application | `application/design_toolkit.py` | `DesignToolkit` — the shared build/render/validate/export core used by **every** interface |
| Application | `application/tools` | Tool objects the embedded agent can call (thin wrappers over `DesignToolkit`) |
| Application | `application/agents` | `DesignAgent` — the tool-calling loop / state machine, and prompts |
| Application | `application/use_cases` | `RunDesignSession` — orchestrates a full interactive session |
| Infra | `infrastructure/cad` | `Build123dExecutor` + isolated subprocess `sandbox_runner` |
| Infra | `infrastructure/rendering` | `MatplotlibRenderer` (headless, CPU-only) |
| Infra | `infrastructure/validation` | mass-properties, printability, FEA (stub), composite |
| Infra | `infrastructure/llm` | `AnthropicProvider`, `OpenAIProvider` |
| Infra | `infrastructure/persistence` | `FilesystemArtifactRepository` |
| Interfaces | `interfaces/mcp` | **FastMCP stdio server** (primary) — client is the brain |
| Interfaces | `interfaces/cli` | Typer + Rich interactive session (embedded agent) |
| Interfaces | `interfaces/api` | FastAPI app (health + session endpoints) |
| Cross-cutting | `config`, `container.py`, `logging_setup.py` | settings, DI, structured logging |

## Design patterns used

- **Ports & Adapters / Dependency Inversion** — all I/O behind `Protocol`s.
- **Dependency Injection** — `dependency-injector` container; constructor injection.
- **Strategy** — swappable `LLMProvider` (Anthropic/OpenAI) and `GeometryValidator`s.
- **Composite** — `CompositeValidator` runs many validators, merges reports.
- **Command / Registry** — each agent capability is a `Tool` in a `ToolRegistry`.
- **Repository** — `ArtifactRepository` abstracts artifact storage.
- **Result type** — `Result[T, E]` for expected failures (bad CAD code, timeouts)
  instead of exceptions across boundaries. Unexpected bugs still raise.
- **Value objects** — immutable `@dataclass(frozen=True)` domain models.

## The CAD execution contract

LLM-authored code runs in a **separate process** (`sandbox_runner.py`) with a wall-clock
timeout and OS resource limits. The contract:

> The generated script must bind the final model to a variable named **`part`**
> (a build123d `Part` / `Compound` / `Solid`, or a build123d builder).

The runner exports STEP + STL, computes the bounding box, and returns JSON. Crashes,
infinite loops, and OOM are contained by process isolation + `setrlimit` + timeout.
The whole thing runs inside Docker, which is the real security boundary — executing
model-authored code is inherently unsafe and must never run on a trusted host.

## Units & materials

build123d works in **millimetres**. Volume is mm³. Material density is g/cm³, so
`mass_g = volume_mm3 / 1000 * density`. Materials live in `domain/models/design_spec.py`.

## Error & vision feedback

`ToolResult` carries text **and** optional image paths. LLM adapters encode images as
provider-native content blocks (base64) so the model literally sees each render on the
next turn. Execution errors are returned as `ToolResult(ok=False, text=<traceback>)`.

## Extensibility

- New solver? Implement `GeometryValidator` and register it in the container.
- New model vendor? Implement `LLMProvider`.
- Real FEA? Replace `NullFeaValidator` with a CalculiX-backed adapter — no other layer
  changes.
