"""`CadExecutor` adapter that runs build123d code in an isolated subprocess."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import structlog

from domain.models.errors import CadError, CadErrorKind
from domain.models.export import ExportFormat
from domain.models.geometry import BoundingBox, GeometryArtifact
from domain.models.result import Err, Ok, Result
from domain.ports.metrics import MetricsSink
from infrastructure.observability import metric_names
from infrastructure.observability.structlog_metrics import NullMetricsSink

log = structlog.get_logger(__name__)

_RUNNER_MODULE = "infrastructure.cad.sandbox_runner"
_EXPORT_RUNNER_MODULE = "infrastructure.cad.export_runner"


class Build123dExecutor:
    """Executes CAD code out-of-process with a wall-clock timeout and memory cap.

    Isolation rationale: model output is untrusted and OpenCASCADE can crash hard on
    degenerate input. A subprocess contains crashes, hangs (timeout), and OOM.
    """

    def __init__(
        self,
        *,
        timeout_seconds: int,
        memory_limit_mb: int,
        cpu_seconds: int = 600,
        metrics: MetricsSink | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._memory_limit_mb = memory_limit_mb
        self._cpu_seconds = cpu_seconds
        self._metrics = metrics or NullMetricsSink()

    def execute(
        self,
        *,
        code: str,
        out_dir: Path,
        artifact_id: str,
        params: Mapping[str, float] | None = None,
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
                    "params": dict(params or {}),
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
            self._metrics.incr(metric_names.CAD_TIMEOUT_TOTAL)
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
            self._metrics.incr(metric_names.CAD_OOM_TOTAL)
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

    def export_extra(
        self,
        *,
        step_path: Path,
        out_dir: Path,
        base_name: str,
        title: str,
        formats: list[ExportFormat],
    ) -> dict[str, Path]:
        """Produce derived formats (3mf/obj/gltf/dxf/svg/drawing) from an existing STEP.

        Runs the isolated export_runner so a bad projection/mesh can't crash the app or a
        build. Returns {format_value: output_path}; on failure logs and returns {} — extra
        formats are best-effort extras on top of the guaranteed STEP/STL.
        """
        if not formats:
            return {}
        out_dir.mkdir(parents=True, exist_ok=True)
        request_path = out_dir / f"{base_name}.export.request.json"
        response_path = out_dir / f"{base_name}.export.response.json"
        request_path.write_text(
            json.dumps(
                {
                    "step_path": str(step_path),
                    "out_dir": str(out_dir),
                    "base_name": base_name,
                    "title": title,
                    "formats": [f.value for f in formats],
                    "memory_limit_mb": self._memory_limit_mb,
                    "cpu_seconds": self._cpu_seconds,
                }
            )
        )
        try:
            subprocess.run(
                [sys.executable, "-m", _EXPORT_RUNNER_MODULE,
                 str(request_path), str(response_path)],
                timeout=self._timeout,
                capture_output=True,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log.warning("cad.export.timeout", base_name=base_name, timeout=self._timeout)
            return {}
        if not response_path.exists():
            log.warning("cad.export.no_response", base_name=base_name)
            return {}
        payload = json.loads(response_path.read_text())
        if not payload.get("ok"):
            log.warning("cad.export.failed", base_name=base_name, message=payload.get("message"))
            return {}
        return {fmt: Path(p) for fmt, p in payload.get("outputs", {}).items()}
