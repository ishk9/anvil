# Anvil — Roadmap

Prioritized backlog. Each item lists **why**, **where** it plugs into the architecture,
effort (S/M/L), and risk. The ordering favors work that increases trust in the output and
makes iteration cheaper, since those are where the tool is currently weakest.

Legend — Effort: S (<1 day), M (a few days), L (a week+). Priority: P0 next, P1 soon,
P2 later.

## Guiding constraints

- Every new capability is a **port + adapter**, wired in `container.py`. No layer reaches
  around the domain.
- Anything the user can trigger should be reachable as an **MCP tool** (client is the
  brain), and mirrored on the CLI/API where it makes sense — all three share
  `application/design_toolkit.py`.
- New model-authored code still runs only inside the sandbox subprocess, inside Docker.
- Docs and copy stay engineer-toned (see `00-OVERVIEW.md` anti-slop rules).

---

## P0 — Close the trust loop

The headline claim is "reasoned by the LLM, verified by simulation." Today validation is
mass + printability heuristics; FEA is a stub. These two items make the output believable.

### 1. Real FEA — stress & displacement `[L, risk: M]`
- **Why:** replace `NullFeaValidator` so load cases produce actual von Mises stress,
  displacement, and safety factor instead of narrative.
- **Where:** implement `GeometryValidator` as `CalculiXFeaValidator` (or `sfepy`), swap it
  in `container.py`. Reuse the existing `LoadCase` on `DesignSpec`. Tetra-mesh the STEP/STL
  (gmsh) → solve → fold results into `ValidationReport`.
- **Notes:** mesh + solve is heavy; run it in the sandbox subprocess pattern with the same
  timeout/limits. Return a stress-map render for the vision loop.

### 2. Slicer-backed printability & cost `[M, risk: M]`
- **Why:** turn printability into ground truth — real print time, filament grams, support
  volume, and per-material cost.
- **Where:** a `SlicerValidator` (`GeometryValidator`) shelling out to PrusaSlicer/Cura CLI
  in the container; feed results into `ValidationReport`. Add `estimate_cost` to
  `DesignToolkit` + an MCP tool.
- **Notes:** bundle one slicer + a couple of print profiles in the image. Overhang/support
  analysis also enables the orientation optimizer (P2).

---

## P1 — Make iteration pleasant

### 3. Parametric revisions + history `[M, risk: S]`
- **Why:** "make the arms 10% thicker" should be a param edit + re-run, not a fresh build.
- **Where:** persist versioned `DesignSpec` + parameters via `ArtifactRepository`; add
  `revise_part` and `get_design_history` MCP tools; diff versions.
- **Payoff:** unlocks A/B compare in the viewer and the constraint solver (P2).

### 4. Fasteners, hardware & threads `[M, risk: S]`
- **Why:** most real parts mount to something. Standard M-series screws, heat-set inserts,
  bearings with correct clearance holes / counterbores; real printable/tapped threads
  (build123d supports thread generation).
- **Where:** a hardware catalog in `domain/`, helper generators in a new
  `application/` toolkit module; expose the catalog as an **MCP resource**.

### 5. Viewer upgrades `[M, risk: S]`
- **Why:** the viewer already hot-reloads STL; make it a real inspection tool.
- **Where:** `interfaces/viewer/` (three.js).
  - Mass/bbox HUD from existing metadata; inline `ValidationReport`.
  - Measure tool, section/clipping plane, dimensions overlay.
  - Download STEP/STL buttons; A/B compare two versions (needs #3); design gallery.

### 6. More export formats + 2D drawings `[M, risk: S]`
- **Why:** 3MF (colors/metadata), OBJ, and DXF for laser/2D; auto-dimensioned drawing
  sheets for handoff.
- **Where:** extend the exporter behind `ArtifactRepository`/executor output; add
  `export_part` format options.

---

## P2 — Depth & scale

### 7. Assemblies + BOM `[L, risk: L]`
- Multiple parts with mates/joints, a bill of materials, and interference/collision checks.
  Larger domain change (multi-part `DesignSpec`); do after revisions land.

### 8. Constraint solver `[L, risk: M]`
- Given targets (max mass, min wall, load with safety factor), search the parameter space
  automatically. Depends on P0 validators + P1 parametric revisions.

### 9. Drone-specific analysis `[S, risk: S]`
- Moment of inertia + CoM-vs-geometric-center balance report — cheap once mass properties
  exist, high value for the drone use case.

### 10. Build cache + design catalog `[M, risk: S]`
- Hash generated code → skip identical rebuilds. Searchable, tagged catalog of past designs
  via `ArtifactRepository`.

---

## Infra / DX (parallelizable)

- **CI:** GitHub Actions running `ruff` + `mypy` + `pytest` (the `checks` service) on push
  to `prod`. `[S]`
- **Publish image to GHCR** (multi-arch) so the MCP `docker run` config pulls a tagged
  image instead of building locally. `[S]`
- **Observability:** structured metrics on build time, failure rate, sandbox OOM/timeout
  counts. `[S]`
- **Web UI:** a front-end to drive designs without an MCP client (reuses the API + viewer).
  `[L]`

---

## Suggested sequence

1. FEA (#1) + slicer/cost (#2) — trust loop.
2. Parametric revisions (#3) — cheaper iteration; unblocks A/B + solver.
3. Fasteners/threads (#4) + viewer upgrades (#5).
4. Formats/drawings (#6), then assemblies (#7) and the constraint solver (#8).

CI + GHCR (infra) can be picked up any time and are near-free given the existing checks.
