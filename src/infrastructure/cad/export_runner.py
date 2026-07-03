"""Isolated export harness for derived formats (3MF/OBJ/glTF/DXF/SVG/drawing).

Runs as its OWN process, exactly like ``sandbox_runner``:

    python -m infrastructure.cad.export_runner <request.json> <response.json>

It re-loads a previously built STEP (``import_step``) and writes the requested extra
formats. Splitting this out of the main build runner keeps that hot path untouched: extra
formats are only paid for when a user actually asks to export them, and a crash while
producing (say) a DXF can never take down a build. The STEP is the single source of truth
for the B-rep, so re-deriving from it — rather than threading the live shape through IPC —
keeps the contract simple.

Request JSON:  {"step_path", "out_dir", "base_name", "title", "formats": ["3mf", ...]}
Response JSON: {"ok", "kind", "message", "traceback", "outputs": {"3mf": "/path", ...}}

``formats`` here are the DERIVED formats only (the parent filters out step/stl and copies
those directly). Unknown formats are the parent's responsibility to reject before calling.
"""

from __future__ import annotations

import contextlib
import json
import resource
import sys
import traceback
from pathlib import Path
from typing import Any


def _apply_limits(memory_limit_mb: int, cpu_seconds: int) -> None:
    """Same containment posture as the build sandbox: CPU cap always, RLIMIT_AS opt-in.

    RLIMIT_AS over-counts OCCT/numpy virtual reservations, so it stays opt-in; real memory
    containment is the container's cgroup limit, and the parent enforces a wall-clock kill.
    """
    if memory_limit_mb > 0:
        mem_bytes = memory_limit_mb * 1024 * 1024
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    if cpu_seconds > 0:
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))


def _load_shape(step_path: Path) -> Any:
    """Import the STEP and return a single exportable shape.

    ``import_step`` yields a Compound; when it holds exactly one solid we unwrap it so the
    downstream mesh/projection code sees the part directly, matching the build runner's
    ``_normalize_shape`` behaviour.
    """
    from build123d import import_step

    compound = import_step(str(step_path))
    solids = compound.solids()
    if len(solids) == 1:
        return solids[0]
    return compound


def _run(request: dict[str, Any]) -> dict[str, Any]:
    from domain.models.export import DERIVED_FORMATS, ExportFormat
    from infrastructure.cad.exporters import produce

    step_path = Path(request["step_path"])
    out_dir = Path(request["out_dir"])
    base_name: str = request["base_name"]
    title: str = request.get("title") or base_name
    format_tokens: list[str] = request.get("formats", [])

    if not step_path.exists():
        return {
            "ok": False,
            "kind": "internal",
            "message": f"STEP file not found for export: {step_path}",
        }

    try:
        formats = [ExportFormat(token) for token in format_tokens]
    except ValueError as exc:
        return {"ok": False, "kind": "internal", "message": f"Bad export format: {exc}"}

    unsupported = [f.value for f in formats if f not in DERIVED_FORMATS]
    if unsupported:
        return {
            "ok": False,
            "kind": "internal",
            "message": f"export_runner cannot produce baseline formats: {unsupported}",
        }

    try:
        shape = _load_shape(step_path)
    except Exception:
        return {
            "ok": False,
            "kind": "execution",
            "message": "Failed to re-load the STEP for export.",
            "traceback": traceback.format_exc(),
        }

    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    for fmt in formats:
        dest = out_dir / f"{base_name}{fmt.suffix}"
        try:
            produce(fmt, shape, dest, title=title)
        except Exception:
            return {
                "ok": False,
                "kind": "export",
                "message": f"Failed to produce {fmt.value} export.",
                "traceback": traceback.format_exc(),
            }
        outputs[fmt.value] = str(dest)

    return {"ok": True, "kind": "", "message": "", "traceback": "", "outputs": outputs}


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: export_runner <request.json> <response.json>\n")
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
            "message": "Export harness failure.",
            "traceback": traceback.format_exc(),
        }

    response_path.write_text(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
