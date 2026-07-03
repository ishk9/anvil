"""`CadExecutor` adapter that runs build123d code in an isolated subprocess."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import structlog

from domain.models.errors import CadError, CadErrorKind
from domain.models.geometry import BoundingBox, GeometryArtifact
from domain.models.result import Err, Ok, Result

log = structlog.get_logger(__name__)

_RUNNER_MODULE = "infrastructure.cad.sandbox_runner"


class Build123dExecutor:
    """Executes CAD code out-of-process with a wall-clock timeout and memory cap.

    Isolation rationale: model output is untrusted and OpenCASCADE can crash hard on
    degenerate input. A subprocess contains crashes, hangs (timeout), and OOM.
    """

    def __init__(
        self, *, timeout_seconds: int, memory_limit_mb: int, cpu_seconds: int = 600
    ) -> None:
        self._timeout = timeout_seconds
        self._memory_limit_mb = memory_limit_mb
        self._cpu_seconds = cpu_seconds

    def execute(
        self,
        *,
        code: str,
        out_dir: Path,
        artifact_id: str,
    ) -> Result[GeometryArtifact, CadError]:
        out_dir.mkdir(parents=True, exist_ok=True)
        step_path = out_dir / f"{artifact_id}.step"
        stl_path = out_dir / f"{artifact_id}.stl"
        request_path = out_dir / f"{artifact_id}.request.json"
        response_path = out_dir / f"{artifact_id}.response.json"

        request_path.write_text(
            json.dumps(
                {
                    "code": code,
                    "step_path": str(step_path),
                    "stl_path": str(stl_path),
                    "memory_limit_mb": self._memory_limit_mb,
                    "cpu_seconds": self._cpu_seconds,
                }
            )
        )

        try:
            subprocess.run(
                [sys.executable, "-m", _RUNNER_MODULE, str(request_path), str(response_path)],
                timeout=self._timeout,
                capture_output=True,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log.warning("cad.timeout", artifact_id=artifact_id, timeout=self._timeout)
            return Err(
                CadError(
                    kind=CadErrorKind.TIMEOUT,
                    message=(
                        f"CAD build exceeded {self._timeout}s. Simplify the model or avoid "
                        f"expensive boolean/fillet operations."
                    ),
                )
            )

        if not response_path.exists():
            return Err(
                CadError(
                    kind=CadErrorKind.INTERNAL,
                    message=(
                        "Sandbox produced no response (OCCT crash or the container hit its "
                        "cgroup memory limit). Simplify the model or raise the container's "
                        "--memory / MECHFORGE_CAD_* limits."
                    ),
                )
            )

        payload = json.loads(response_path.read_text())
        if not payload.get("ok"):
            return Err(
                CadError(
                    kind=CadErrorKind(payload.get("kind", "internal")),
                    message=payload.get("message", "Unknown CAD failure."),
                    traceback=payload.get("traceback", ""),
                )
            )

        dims = payload["bbox"]
        artifact = GeometryArtifact(
            artifact_id=artifact_id,
            source_code=code,
            step_path=step_path,
            stl_path=stl_path,
            bounding_box=BoundingBox(x_mm=dims["x"], y_mm=dims["y"], z_mm=dims["z"]),
        )
        log.info("cad.built", artifact_id=artifact_id, bbox=dims)
        return Ok(artifact)
