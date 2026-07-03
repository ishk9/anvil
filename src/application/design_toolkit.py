"""The single orchestration core for building, validating, and exporting parts.

Both entry points use this: the embedded agent's tools *and* the MCP server. Keeping the
build->render->validate->export pipeline here (not in either interface) is what keeps the
system DRY — a new front end is a thin adapter over this class.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import structlog

from application.session import DesignSession
from config.settings import Settings
from domain.models.design_spec import MATERIAL_LIBRARY, MaterialProfile
from domain.models.errors import CadError
from domain.models.export import ExportRequest
from domain.models.geometry import GeometryArtifact
from domain.models.result import Ok, Result
from domain.models.validation import ValidationReport
from domain.ports.artifact_repository import ArtifactRepository
from domain.ports.build_cache import BuildCache, build_cache_key
from domain.ports.cad_executor import CadExecutor
from domain.ports.metrics import MetricsSink
from domain.ports.renderer import Renderer
from domain.ports.validator import GeometryValidator
from infrastructure.observability import metric_names
from infrastructure.observability.structlog_metrics import NullMetricsSink

log = structlog.get_logger(__name__)


def _slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text.strip()]
    return "".join(keep).strip("-") or "part"


class DesignToolkit:
    def __init__(
        self,
        *,
        executor: CadExecutor,
        renderer: Renderer,
        validator: GeometryValidator,
        repository: ArtifactRepository,
        settings: Settings,
        cache: BuildCache | None = None,
        metrics: MetricsSink | None = None,
    ) -> None:
        self._executor = executor
        self._renderer = renderer
        self._validator = validator
        self._repository = repository
        self._settings = settings
        self._cache = cache
        self._metrics = metrics or NullMetricsSink()

    def build(
        self,
        *,
        session: DesignSession,
        code: str,
        params: Mapping[str, float] | None = None,
    ) -> Result[GeometryArtifact, CadError]:
        """Execute CAD code, render previews, and record the artifact in the session.

        Identical (code, params) pairs are served from the build cache when enabled, so
        re-running an unchanged design skips the expensive OCCT boolean/mesh pass.
        """
        with self._metrics.timer(metric_names.BUILD_SECONDS):
            cache_key = build_cache_key(code, params) if self._cache is not None else None
            if self._cache is not None and cache_key is not None:
                cached = self._cache.get(cache_key)
                if cached is not None:
                    session.add_artifact(cached)
                    self._metrics.incr(metric_names.BUILD_TOTAL, {"result": "ok"})
                    log.info("build.cache_hit", key=cache_key, artifact_id=cached.artifact_id)
                    return Ok(cached)

            artifact_id = session.next_artifact_id()
            out_dir = self._repository.artifact_dir(session.session_id, artifact_id)

            result = self._executor.execute(
                code=code, out_dir=out_dir, artifact_id=artifact_id, params=params
            )
            if result.is_err():
                self._metrics.incr(metric_names.BUILD_TOTAL, {"result": "error"})
                return result

            artifact = result.unwrap()
            renders = self._renderer.render(
                stl_path=artifact.stl_path,
                out_dir=out_dir,
                views=self._settings.render_views,
            )
            artifact = replace(artifact, render_paths=renders)
            session.add_artifact(artifact)
            if self._cache is not None and cache_key is not None:
                self._cache.put(cache_key, artifact)
            self._metrics.incr(metric_names.BUILD_TOTAL, {"result": "ok"})
            return Ok(artifact)

    def validate(self, *, session: DesignSession, artifact: GeometryArtifact) -> ValidationReport:
        with self._metrics.timer(metric_names.VALIDATE_SECONDS):
            return self._validator.validate(artifact=artifact, spec=session.effective_spec())

    def export(
        self,
        *,
        session: DesignSession,
        artifact: GeometryArtifact,
        title: str | None = None,
        formats: list[str] | None = None,
    ) -> list[Path]:
        """Copy STEP+STL and produce any requested extra formats into the export dir.

        ``formats`` are tokens (e.g. ["3mf", "dxf", "drawing"]); STEP/STL are always
        emitted. Unknown tokens raise ValueError. Extra formats are best-effort: if the
        export runner fails, STEP/STL still land and are returned.
        """
        export_dir = self._repository.export_dir(session.session_id)
        base = _slug(title or session.effective_spec().title)
        request = ExportRequest.from_strings(base_name=base, formats=formats)

        step_dest = export_dir / f"{base}.step"
        stl_dest = export_dir / f"{base}.stl"
        shutil.copy2(artifact.step_path, step_dest)
        shutil.copy2(artifact.stl_path, stl_dest)
        written: list[Path] = [step_dest, stl_dest]

        if request.needs_export_runner():
            produced = self._executor.export_extra(
                step_path=artifact.step_path,
                out_dir=export_dir,
                base_name=base,
                title=title or session.effective_spec().title,
                formats=list(request.derived),
            )
            written.extend(produced.values())

        session.exports.extend(written)
        self._metrics.incr(metric_names.EXPORT_TOTAL)
        return written

    def materials(self) -> list[MaterialProfile]:
        return list(MATERIAL_LIBRARY.values())
