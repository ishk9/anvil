# Anvil — Overview

Anvil is an **AI mechanical design agent** for parametric 3D parts (drone frames,
motor mounts, brackets, enclosures). You describe what you need in plain language; the
agent interviews you about the physics and constraints, writes **parametric CAD code**,
executes it to produce real solids, renders and critiques the result visually, validates
it (mass, printability, and — later — FEA stress), and exports manufacturable files
(STEP / STL).

## The core idea

LLMs cannot sculpt geometry directly, but they are excellent at writing code. So the
agent never emits meshes — it emits **`build123d` Python code**, which is executed in a
sandbox to produce a true B-rep solid. This gives us:

- Manufacturable output (STEP for machining, STL for printing).
- Measurable properties (volume, mass, center of mass, bounding box).
- A path to real simulation (FEA) because we have solids, not triangle soup.

## The closed loop

```
requirements conversation
        │  (structured DesignSpec)
        ▼
   generate CAD code  ──►  execute in sandbox  ──►  errors? feed back & retry
        ▲                        │
        │                        ▼
   revise design  ◄──  render (PNG) + vision critique
        ▲                        │
        │                        ▼
        └──────────  validate (mass / printability / FEA)
                                 │  (all pass + user approves)
                                 ▼
                          export STEP / STL
```

The two loops that make this work in practice:

1. **Error loop** — execution tracebacks are fed back so the model self-corrects.
2. **Vision loop** — renders are sent back to the model so it "sees" its own work.

## Honest scope

- **Strong:** brackets, mounts, standoffs, enclosures, simple structural arms.
- **Weak:** complex assemblies, tight tolerances/fits, aerodynamic surfaces.
- Physics is **reasoned by the LLM but verified by simulation and real testing**.
  Anything that flies is a first draft until bench-tested.

## Anti-"AI slop" rules (workspace convention)

- No hedge words ("seamless", "elevate", "transform").
- Docs and copy read like an engineer wrote them, not a marketer.
- Comments explain intent/trade-offs, never narrate the code.

See [`01-ARCHITECTURE.md`](01-ARCHITECTURE.md) for the layering and design patterns.
