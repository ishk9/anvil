"""Isolated CAD execution harness.

Runs as its OWN process:

    python -m infrastructure.cad.sandbox_runner <request.json> <response.json>

It executes untrusted, model-authored build123d code, enforces OS resource limits,
exports STEP + STL, and reports a strict JSON result. Never import this into the app
process — the whole point is isolation. Wall-clock timeout is enforced by the parent.

Request JSON:  {"code", "step_path", "stl_path", "memory_limit_mb", "cpu_seconds"}
Response JSON: {"ok", "kind", "message", "traceback", "bbox": {"x","y","z"}}
"""

from __future__ import annotations

import contextlib
import json
import resource
import sys
import traceback
from pathlib import Path
from typing import Any

# The variable the generated script must bind.
_CONTRACT_VAR = "part"


def _apply_limits(memory_limit_mb: int, cpu_seconds: int) -> None:
    """Guard against runaway code via CPU time (and optionally address space).

    RLIMIT_AS is only applied when ``memory_limit_mb > 0``. It caps *virtual* address
    space, which OCCT/numpy/matplotlib over-reserve well beyond real memory use, so a
    low value causes spurious OOM crashes. Real memory containment is delegated to the
    container's cgroup limit (docker --memory); the wall-clock timeout handles hangs.
    """
    if memory_limit_mb > 0:
        mem_bytes = memory_limit_mb * 1024 * 1024
        # Some platforms (e.g. macOS) reject RLIMIT_AS; Docker/Linux is the deployment target.
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    if cpu_seconds > 0:
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))


def _normalize_shape(obj: Any) -> Any:
    """Accept a build123d builder or a raw shape and return an exportable shape."""
    from build123d import BuildPart  # local import: only needed in the sandbox

    if isinstance(obj, BuildPart):
        return obj.part
    if getattr(obj, "part", None) is not None:
        return obj.part
    return obj


def _run(request: dict[str, Any]) -> dict[str, Any]:
    code: str = request["code"]
    step_path = Path(request["step_path"])
    stl_path = Path(request["stl_path"])

    import build123d as b3d
    from build123d import export_step, export_stl

    namespace: dict[str, Any] = {"__name__": "__anvil_sandbox__"}
    namespace.update({name: getattr(b3d, name) for name in dir(b3d) if not name.startswith("_")})

    # Parametric revisions: expose overridable dimensions the code may read, e.g.
    #   w = params.get("arm_width_mm", 8.0)
    namespace["params"] = dict(request.get("params", {}))

    # --- execute model code ---
    try:
        exec(compile(code, "<generated_cad>", "exec"), namespace)
    except Exception:
        return {
            "ok": False,
            "kind": "execution",
            "message": "Generated code raised while building the model.",
            "traceback": traceback.format_exc(),
        }

    if _CONTRACT_VAR not in namespace:
        return {
            "ok": False,
            "kind": "contract",
            "message": (
                f"The script must bind the final model to a variable named "
                f"'{_CONTRACT_VAR}'. It was not found in the namespace."
            ),
        }

    shape = _normalize_shape(namespace[_CONTRACT_VAR])

    # --- measure ---
    try:
        volume = float(shape.volume)
        bbox = shape.bounding_box()
        size = bbox.size
        dims = {"x": float(size.X), "y": float(size.Y), "z": float(size.Z)}
    except Exception:
        return {
            "ok": False,
            "kind": "empty",
            "message": "Could not measure the result; it may not be a valid solid.",
            "traceback": traceback.format_exc(),
        }

    if volume <= 1e-6:
        return {
            "ok": False,
            "kind": "empty",
            "message": f"The model has ~zero volume ({volume:.6f} mm^3). No solid produced.",
        }

    # --- export ---
    try:
        step_path.parent.mkdir(parents=True, exist_ok=True)
        export_step(shape, str(step_path))
        export_stl(shape, str(stl_path))
    except Exception:
        return {
            "ok": False,
            "kind": "export",
            "message": "Failed to export STEP/STL from the built shape.",
            "traceback": traceback.format_exc(),
        }

    return {"ok": True, "kind": "", "message": "", "traceback": "", "bbox": dims}


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: sandbox_runner <request.json> <response.json>\n")
        return 2

    request_path, response_path = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        request = json.loads(request_path.read_text())
        _apply_limits(
            int(request.get("memory_limit_mb", 0)),
            int(request.get("cpu_seconds", 600)),
        )
        result = _run(request)
    except Exception:
        result = {
            "ok": False,
            "kind": "internal",
            "message": "Sandbox harness failure.",
            "traceback": traceback.format_exc(),
        }

    response_path.write_text(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
