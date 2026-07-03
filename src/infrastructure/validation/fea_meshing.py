"""Isolated FEA mesh + solve harness.

Runs as its OWN process:

    python -m infrastructure.validation.fea_meshing <request.json> <response.json>

It tetra-meshes a STEP/STL with gmsh, writes a CalculiX ``.inp`` linear-static deck,
shells out to ``ccx`` to solve, and parses the ``.frd`` result for peak von Mises stress
and displacement. It reports a strict JSON result. Never import the heavy work into the
app process: gmsh links OpenCASCADE and can crash hard on degenerate geometry, and a
solve can be slow — a subprocess contains crashes, hangs (parent wall-clock timeout), and
OOM, exactly like ``cad/sandbox_runner.py``.

The boundary-condition heuristic lives here (it needs the node coordinates), but its
*assumptions* are surfaced to the user by the adapter as report issues — see
``calculix_fea_validator.py``.

Request JSON:
    {"geometry_path", "work_dir", "job_name", "mesh_size_mm", "youngs_mpa", "poisson",
     "force_newtons", "cpu_seconds", "ccx_cmd"}
Response JSON:
    {"ok", "kind", "message", "traceback",
     "von_mises_max_mpa", "displacement_max_mm", "mesh_nodes", "frd_path"}

``kind`` on failure is one of: ``mesh`` (gmsh failed / empty mesh), ``solver`` (ccx
missing or non-zero exit / no result), ``internal``.
"""

from __future__ import annotations

import contextlib
import json
import math
import resource
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

# CalculiX linear tetra element. C3D4 is the 4-node linear tet; we deliberately mesh
# first order (Mesh.ElementOrder = 1) to keep the .inp small and the solve fast. Linear
# tets are stiff and under-report stress on coarse meshes — the adapter documents this.
_CCX_ELEMENT_TYPE = "C3D4"

# gmsh element type id for a 4-node tetrahedron (see gmsh docs / getElementProperties).
_GMSH_TET4 = 4

# Fraction of the total Z-height used as the thickness of the "base" (fixed) and "top"
# (loaded) node bands. A part rarely has a perfectly planar mounting face after import,
# so we grab a thin slab rather than a single Z-plane.
_FACE_BAND_FRACTION = 0.05


def _apply_limits(cpu_seconds: int) -> None:
    """CPU-time rlimit so a runaway mesh/solve can't burn the box. Mirrors the CAD sandbox.

    RLIMIT_AS is intentionally not set: gmsh/OCCT over-reserve virtual address space and a
    cap causes spurious MemoryError. Real memory containment is the container cgroup's job.
    """
    if cpu_seconds > 0:
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))


def _mesh(geometry_path: Path, mesh_size_mm: float) -> tuple[
    list[tuple[int, float, float, float]],
    list[tuple[int, tuple[int, ...]]],
]:
    """Tetra-mesh the geometry with gmsh and return (nodes, tets).

    ``nodes`` is a list of ``(tag, x, y, z)``; ``tets`` is ``(tag, (n0, n1, n2, n3))``.
    Raises on empty geometry / a mesh with no tets so the caller reports ``kind="mesh"``.
    """
    import gmsh

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("Mesh.ElementOrder", 1)
        if mesh_size_mm > 0:
            gmsh.option.setNumber("Mesh.MeshSizeMin", mesh_size_mm)
            gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size_mm)

        suffix = geometry_path.suffix.lower()
        if suffix in (".step", ".stp"):
            gmsh.model.occ.importShapes(str(geometry_path))
            gmsh.model.occ.synchronize()
        elif suffix == ".stl":
            # STL is a surface tessellation. Reconstruct a volume from it so we can
            # generate a solid tet mesh; classifySurfaces + createGeometry rebuild the
            # topology gmsh needs to bound a volume.
            gmsh.merge(str(geometry_path))
            gmsh.model.mesh.classifySurfaces(math.pi / 4, True, True, math.pi / 4)
            gmsh.model.mesh.createGeometry()
            surfaces = [s[1] for s in gmsh.model.getEntities(2)]
            loop = gmsh.model.geo.addSurfaceLoop(surfaces)
            gmsh.model.geo.addVolume([loop])
            gmsh.model.geo.synchronize()
        else:
            raise ValueError(f"Unsupported geometry for meshing: {geometry_path.suffix}")

        if not gmsh.model.getEntities(3):
            raise ValueError("No 3D volume to mesh (geometry imported as surfaces only).")

        gmsh.model.mesh.generate(3)

        node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
        nodes = [
            (int(tag), float(node_coords[3 * i]), float(node_coords[3 * i + 1]),
             float(node_coords[3 * i + 2]))
            for i, tag in enumerate(node_tags)
        ]

        elem_tags, elem_node_tags = gmsh.model.mesh.getElementsByType(_GMSH_TET4)
        tets = [
            (int(elem_tags[i]), tuple(int(elem_node_tags[4 * i + k]) for k in range(4)))
            for i in range(len(elem_tags))
        ]
    finally:
        gmsh.finalize()

    if not tets:
        raise ValueError("Mesh produced no tetrahedra (geometry may be a thin shell).")
    return nodes, tets


def _write_inp(
    inp_path: Path,
    nodes: list[tuple[int, float, float, float]],
    tets: list[tuple[int, tuple[int, ...]]],
    *,
    youngs_mpa: float,
    poisson: float,
    force_newtons: float,
) -> None:
    """Write a CalculiX linear-static ``.inp`` deck.

    Boundary conditions (documented heuristic, mirrored in the adapter's report):
      - Fix the lowest-Z band of nodes fully (encastre: DOF 1-3 = 0) — the mounting base.
      - Distribute ``force_newtons`` as equal nodal CLOADs over the highest-Z band, in -Z.
    """
    zs = [z for _, _, _, z in nodes]
    z_min, z_max = min(zs), max(zs)
    band = max((z_max - z_min) * _FACE_BAND_FRACTION, 1e-9)

    fixed = [tag for tag, _, _, z in nodes if z <= z_min + band]
    loaded = [tag for tag, _, _, z in nodes if z >= z_max - band]

    if not fixed:
        raise ValueError("No nodes found on the base face to fix.")
    if not loaded:
        raise ValueError("No nodes found on the top face to load.")

    lines: list[str] = ["*NODE, NSET=NALL"]
    lines.extend(f"{tag}, {x:.6f}, {y:.6f}, {z:.6f}" for tag, x, y, z in nodes)

    lines.append(f"*ELEMENT, TYPE={_CCX_ELEMENT_TYPE}, ELSET=EALL")
    lines.extend(
        f"{tag}, {n[0]}, {n[1]}, {n[2]}, {n[3]}" for tag, n in tets
    )

    lines.append("*NSET, NSET=NFIX")
    lines.extend(_chunked_csv(fixed))
    lines.append("*NSET, NSET=NLOAD")
    lines.extend(_chunked_csv(loaded))

    lines.append("*MATERIAL, NAME=MAT")
    lines.append("*ELASTIC")
    lines.append(f"{youngs_mpa:.6f}, {poisson:.6f}")
    lines.append("*SOLID SECTION, ELSET=EALL, MATERIAL=MAT")

    lines.append("*STEP")
    lines.append("*STATIC")
    lines.append("*BOUNDARY")
    lines.append("NFIX, 1, 3, 0.0")

    # Force is split equally across the loaded band, applied in -Z (DOF 3).
    per_node = -abs(force_newtons) / len(loaded)
    lines.append("*CLOAD")
    lines.append(f"NLOAD, 3, {per_node:.8f}")

    # Ask for nodal displacement (U) and stress (S) in the .frd result.
    lines.append("*NODE FILE")
    lines.append("U")
    lines.append("*EL FILE")
    lines.append("S")
    lines.append("*END STEP")

    inp_path.write_text("\n".join(lines) + "\n")


def _chunked_csv(tags: list[int], per_line: int = 16) -> list[str]:
    """Emit node tags as comma-separated lines (CalculiX dislikes very long lines)."""
    out: list[str] = []
    for start in range(0, len(tags), per_line):
        chunk = tags[start : start + per_line]
        out.append(", ".join(str(t) for t in chunk))
    return out


def _solve(inp_path: Path, ccx_cmd: str, cpu_seconds: int) -> Path:
    """Run ``ccx`` on the deck (job name = inp stem, no extension) and return the .frd path.

    Raises FileNotFoundError if ``ccx`` is not on PATH (caller reports ``kind="solver"``).
    """
    if shutil.which(ccx_cmd) is None:
        raise FileNotFoundError(f"CalculiX solver '{ccx_cmd}' not found on PATH.")

    job = inp_path.with_suffix("")  # ccx wants the job name without .inp
    proc = subprocess.run(
        [ccx_cmd, job.name],
        cwd=str(inp_path.parent),
        capture_output=True,
        text=True,
        timeout=max(cpu_seconds, 1),
        check=False,
    )
    frd_path = job.with_suffix(".frd")
    if not frd_path.exists():
        tail = (proc.stdout or proc.stderr or "")[-500:]
        raise RuntimeError(f"ccx produced no .frd (exit {proc.returncode}). Output tail: {tail}")
    return frd_path


def _parse_frd(frd_path: Path) -> tuple[float, float]:
    """Parse a CalculiX ASCII ``.frd`` for peak displacement magnitude and von Mises stress.

    FRD nodal-result layout (the columns we need):
      - A block header ``-4  DISP`` / ``-4  STRESS`` names the dataset.
      - Each value line starts with ``-1`` then the node number, then the components.
        DISP → D1 D2 D3 (mm). STRESS → SXX SYY SZZ SXY SYZ SZX (MPa, since we fed MPa/mm/N).
      - ``-3`` terminates a block.

    We compute von Mises per node from the 6 stress components and track the maxima. We do
    the arithmetic by hand rather than depend on ccx2paraview/VTK — the format is stable and
    this keeps the dependency surface to gmsh only.
    """
    disp_max = 0.0
    vm_max = 0.0
    mode: str | None = None  # "disp" | "stress" | None

    with frd_path.open("r") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if stripped.startswith("-4"):
                name = stripped.split()[1] if len(stripped.split()) > 1 else ""
                if name == "DISP":
                    mode = "disp"
                elif name == "STRESS":
                    mode = "stress"
                else:
                    mode = None
                continue
            if stripped.startswith("-3"):
                mode = None
                continue
            if mode is None or not stripped.startswith("-1"):
                continue

            values = _split_frd_record(line)
            if mode == "disp" and len(values) >= 3:
                mag = math.sqrt(values[0] ** 2 + values[1] ** 2 + values[2] ** 2)
                disp_max = max(disp_max, mag)
            elif mode == "stress" and len(values) >= 6:
                vm = _von_mises(values[:6])
                vm_max = max(vm_max, vm)

    return vm_max, disp_max


def _split_frd_record(line: str) -> list[float]:
    """Extract the numeric components from a ``-1`` FRD value line.

    FRD is fixed-width (each value 12 chars) but ccx also emits E-notation that survives a
    whitespace split. We drop the ``-1`` marker and the node id, then read floats. To stay
    robust to values that touch (no space), fall back to fixed 12-char columns.
    """
    parts = line.split()
    if len(parts) >= 3 and parts[0] == "-1":
        try:
            return [float(p) for p in parts[2:]]
        except ValueError:
            pass
    # Fixed-width fallback: marker(3) + node(10) then 12-char columns.
    body = line[13:]
    out: list[float] = []
    for start in range(0, len(body), 12):
        chunk = body[start : start + 12].strip()
        if chunk:
            with contextlib.suppress(ValueError):
                out.append(float(chunk))
    return out


def _von_mises(s: list[float]) -> float:
    """von Mises equivalent stress from [SXX, SYY, SZZ, SXY, SYZ, SZX]."""
    sxx, syy, szz, sxy, syz, szx = s
    return math.sqrt(
        0.5
        * (
            (sxx - syy) ** 2
            + (syy - szz) ** 2
            + (szz - sxx) ** 2
            + 6.0 * (sxy**2 + syz**2 + szx**2)
        )
    )


def _run(request: dict[str, Any]) -> dict[str, Any]:
    geometry_path = Path(request["geometry_path"])
    work_dir = Path(request["work_dir"])
    work_dir.mkdir(parents=True, exist_ok=True)
    job_name = request.get("job_name", "fea_job")

    # --- mesh ---
    try:
        nodes, tets = _mesh(geometry_path, float(request["mesh_size_mm"]))
    except Exception:
        return {
            "ok": False,
            "kind": "mesh",
            "message": "gmsh failed to tetra-mesh the geometry.",
            "traceback": traceback.format_exc(),
        }

    # --- write deck ---
    inp_path = work_dir / f"{job_name}.inp"
    try:
        _write_inp(
            inp_path,
            nodes,
            tets,
            youngs_mpa=float(request["youngs_mpa"]),
            poisson=float(request["poisson"]),
            force_newtons=float(request["force_newtons"]),
        )
    except Exception:
        return {
            "ok": False,
            "kind": "mesh",
            "message": "Could not derive boundary conditions from the mesh.",
            "traceback": traceback.format_exc(),
        }

    # --- solve ---
    try:
        frd_path = _solve(inp_path, request.get("ccx_cmd", "ccx"), int(request["cpu_seconds"]))
    except FileNotFoundError as exc:
        return {"ok": False, "kind": "solver", "message": str(exc), "traceback": ""}
    except Exception:
        return {
            "ok": False,
            "kind": "solver",
            "message": "CalculiX solve failed.",
            "traceback": traceback.format_exc(),
        }

    # --- parse ---
    try:
        vm_max, disp_max = _parse_frd(frd_path)
    except Exception:
        return {
            "ok": False,
            "kind": "solver",
            "message": "Solved, but the .frd result could not be parsed.",
            "traceback": traceback.format_exc(),
        }

    return {
        "ok": True,
        "kind": "",
        "message": "",
        "traceback": "",
        "von_mises_max_mpa": vm_max,
        "displacement_max_mm": disp_max,
        "mesh_nodes": len(nodes),
        "frd_path": str(frd_path),
    }


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: fea_meshing <request.json> <response.json>\n")
        return 2

    request_path, response_path = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        request = json.loads(request_path.read_text())
        _apply_limits(int(request.get("cpu_seconds", 600)))
        result = _run(request)
    except Exception:
        result = {
            "ok": False,
            "kind": "internal",
            "message": "FEA harness failure.",
            "traceback": traceback.format_exc(),
        }

    response_path.write_text(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
