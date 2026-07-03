"""MCP-facing facade over `DesignToolkit`.

Holds per-`session_id` state and returns plain data (text + image paths), so it is fully
testable without importing any MCP framework types. The FastMCP layer (`server.py`) is a
thin adapter over this.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from application.assembly_formatting import format_bom, format_interference_report
from application.assembly_toolkit import AssemblyToolkit
from application.catalog_service import CatalogService
from application.constraint_formatting import format_solve_result
from application.constraint_solver import ConstraintSolver
from application.design_toolkit import DesignToolkit
from application.formatting import (
    format_diff,
    format_history,
    format_materials,
    format_validation_report,
    format_version_diff,
)
from application.revision_service import RevisionService
from application.session import DesignSession
from domain.models.assembly import Assembly, Mate, MateKind, PartInstance
from domain.models.constraints import Objective, ParamRange, SolveRequest, Target
from domain.models.design_spec import DesignSpec, Material
from domain.models.errors import CadError
from domain.models.geometry import GeometryArtifact
from domain.models.result import Result
from domain.models.validation import ValidationReport


@dataclass(frozen=True, slots=True)
class BuildOutcome:
    ok: bool
    message: str
    image_paths: list[Path] = field(default_factory=list)


class McpDesignService:
    def __init__(
        self,
        *,
        toolkit: DesignToolkit,
        revisions: RevisionService,
        catalog: CatalogService,
        assemblies: AssemblyToolkit,
    ) -> None:
        self._toolkit = toolkit
        self._revisions = revisions
        self._catalog = catalog
        self._assemblies = assemblies
        self._sessions: dict[str, DesignSession] = {}

    def list_materials(self) -> str:
        return format_materials(self._toolkit.materials())

    def build(
        self, *, session_id: str, code: str, params: dict[str, float] | None = None
    ) -> BuildOutcome:
        session = self._session(session_id)
        result = self._toolkit.build(session=session, code=code, params=params)
        if result.is_err():
            return BuildOutcome(
                ok=False,
                message="Build failed. Fix the code and call build_part again.\n\n"
                + result.error.as_feedback(),  # type: ignore[union-attr]
            )
        artifact = result.unwrap()
        # Record the build as a version so it can be revised / diffed later.
        version = self._revisions.record_initial(
            session_id=session_id,
            spec=session.effective_spec(),
            code=code,
            artifact_id=artifact.artifact_id,
            params=params,
            note="build",
        )
        b = artifact.bounding_box
        message = (
            f"Built '{artifact.artifact_id}' (recorded as v{version.version}). Bounding box: "
            f"{b.x_mm:.1f} x {b.y_mm:.1f} x {b.z_mm:.1f} mm. "
            f"Inspect the attached renders. Use artifact_id='{artifact.artifact_id}' "
            f"to validate_part or export_part, or revise_part with base_version={version.version}."
        )
        return BuildOutcome(ok=True, message=message, image_paths=list(artifact.render_paths))

    def validate(self, *, session_id: str, artifact_id: str, material: str) -> str:
        session, artifact = self._require_artifact(session_id, artifact_id)
        session.spec = self._spec_with_material(session, material)
        report = self._toolkit.validate(session=session, artifact=artifact)
        return format_validation_report(report)

    def estimate_cost(self, *, session_id: str, artifact_id: str, material: str) -> str:
        session, artifact = self._require_artifact(session_id, artifact_id)
        session.spec = self._spec_with_material(session, material)
        report = self._toolkit.validate(session=session, artifact=artifact)
        cost = report.metrics.get("slicer.cost_usd")
        if cost is None:
            return "Cost could not be estimated (no slicer output)."
        minutes = report.metrics.get("slicer.print_time_min", 0.0)
        grams = report.metrics.get("slicer.filament_g", 0.0)
        return (
            f"Estimated cost in {material}: ${cost:.2f}\n"
            f"- print time: {minutes:.0f} min\n"
            f"- filament: {grams:.1f} g"
        )

    def export(
        self,
        *,
        session_id: str,
        artifact_id: str,
        title: str | None,
        formats: list[str] | None = None,
    ) -> str:
        session, artifact = self._require_artifact(session_id, artifact_id)
        paths = self._toolkit.export(
            session=session, artifact=artifact, title=title, formats=formats
        )
        listed = "\n".join(f"- {p}" for p in paths)
        return f"Exported {len(paths)} file(s):\n{listed}"

    # --- constraint solver ---

    def solve_design(
        self,
        *,
        session_id: str,
        code: str,
        ranges: list[dict[str, float | str | None]],
        targets: list[dict[str, float | str]],
        objective: dict[str, str],
        max_evaluations: int = 32,
    ) -> str:
        session = self._session(session_id)

        def evaluate(params: Mapping[str, float]) -> ValidationReport | None:
            result = self._toolkit.build(session=session, code=code, params=dict(params))
            if result.is_err():
                return None  # build failed -> solver marks candidate infeasible
            artifact = result.unwrap()
            return self._toolkit.validate(session=session, artifact=artifact)

        request = SolveRequest(
            code=code,
            ranges=tuple(
                ParamRange(
                    name=str(r["name"]),
                    lo=float(r["lo"]),  # type: ignore[arg-type]
                    hi=float(r["hi"]),  # type: ignore[arg-type]
                    step=(float(r["step"]) if r.get("step") is not None else None),  # type: ignore[arg-type]
                )
                for r in ranges
            ),
            targets=tuple(
                Target(metric=str(t["metric"]), op=str(t["op"]), value=float(t["value"]))  # type: ignore[arg-type]
                for t in targets
            ),
            objective=Objective(
                metric=str(objective["metric"]),
                direction=str(objective["direction"]),  # type: ignore[arg-type]
            ),
            max_evaluations=max_evaluations,
        )
        solver = ConstraintSolver(evaluate=evaluate)
        return format_solve_result(solver.solve(request), request.objective)

    # --- revisions & history ---

    def revise(
        self,
        *,
        session_id: str,
        base_version: int,
        param_overrides: dict[str, float],
        note: str = "",
    ) -> str:
        session = self._session(session_id)

        def build(code: str, params: dict[str, float]) -> Result[GeometryArtifact, CadError]:
            return self._toolkit.build(session=session, code=code, params=params)

        result = self._revisions.revise(
            session_id=session_id,
            base_version=base_version,
            param_overrides=param_overrides,
            build=build,
            note=note,
        )
        if result.is_err():
            return "Revision failed.\n\n" + result.error.as_feedback()  # type: ignore[union-attr]
        version, diff = result.unwrap()
        return format_version_diff(version, diff)

    def design_history(self, *, session_id: str) -> str:
        return format_history(self._revisions.history(session_id))

    def diff_versions(self, *, session_id: str, from_version: int, to_version: int) -> str:
        result = self._revisions.diff(
            session_id=session_id, from_version=from_version, to_version=to_version
        )
        if result.is_err():
            return result.error  # type: ignore[union-attr]
        return format_diff(result.unwrap())

    # --- catalog ---

    def add_to_catalog(
        self, *, session_id: str, artifact_id: str, title: str, tags: list[str] | None
    ) -> str:
        session, artifact = self._require_artifact(session_id, artifact_id)
        spec = session.effective_spec()
        step_dest = artifact.step_path
        stl_dest = artifact.stl_path
        entry = self._catalog.add_design(
            design_id=f"{session_id}:{artifact_id}",
            title=title or spec.title,
            material=spec.material,
            artifact=artifact,
            step_path=step_dest,
            stl_path=stl_dest,
            tags=tags,
        )
        return f"Catalogued '{entry.title}' [{entry.design_id}] with tags {list(entry.tags)}."

    def search_catalog(self, *, query: str = "", tags: list[str] | None = None) -> str:
        entries = self._catalog.search(query=query, tags=tags)
        if not entries:
            return "No matching designs in the catalog."
        return "Catalog matches:\n" + "\n".join(
            f"- {e.design_id}: {e.title} ({e.material.value}) tags={list(e.tags)}" for e in entries
        )

    def list_catalog(self) -> str:
        entries = self._catalog.list_all()
        if not entries:
            return "The design catalog is empty."
        return "Design catalog:\n" + "\n".join(
            f"- {e.design_id}: {e.title} ({e.material.value})" for e in entries
        )

    # --- assemblies ---

    def build_assembly(
        self,
        *,
        session_id: str,
        title: str,
        instances: list[dict[str, object]],
        mates: list[dict[str, object]] | None,
        code_by_ref: dict[str, str],
    ) -> BuildOutcome:
        assembly = _assembly_from_dicts(title, instances, mates)
        session = self._session(session_id)
        result = self._assemblies.build(
            session=session, assembly=assembly, code_by_ref=code_by_ref
        )
        if result.is_err():
            return BuildOutcome(
                ok=False,
                message="Assembly build failed.\n\n"
                + result.error.as_feedback(),  # type: ignore[union-attr]
            )
        artifact = result.unwrap()
        b = artifact.bounding_box
        return BuildOutcome(
            ok=True,
            message=(
                f"Built assembly '{artifact.artifact_id}'. Bounding box "
                f"{b.x_mm:.1f} x {b.y_mm:.1f} x {b.z_mm:.1f} mm."
            ),
            image_paths=list(artifact.render_paths),
        )

    def assembly_bom(
        self, *, title: str, instances: list[dict[str, object]], part_masses: dict[str, float]
    ) -> str:
        assembly = _assembly_from_dicts(title, instances, None)
        return format_bom(self._assemblies.bill_of_materials(assembly, part_masses))

    def check_interference(
        self,
        *,
        title: str,
        instances: list[dict[str, object]],
        mates: list[dict[str, object]] | None,
        instance_stl_paths: dict[str, str],
    ) -> str:
        import trimesh

        assembly = _assembly_from_dicts(title, instances, mates)
        meshes: dict[str, trimesh.Trimesh] = {}
        for iid, path in instance_stl_paths.items():
            loaded = trimesh.load(path, force="mesh")
            if isinstance(loaded, trimesh.Trimesh):
                meshes[iid] = loaded
        return format_interference_report(
            self._assemblies.interference_report(assembly, meshes)
        )

    # --- internals ---

    def _session(self, session_id: str) -> DesignSession:
        return self._sessions.setdefault(session_id, DesignSession(session_id=session_id))

    def _require_artifact(
        self, session_id: str, artifact_id: str
    ) -> tuple[DesignSession, GeometryArtifact]:
        session = self._session(session_id)
        for artifact in session.artifacts:
            if artifact.artifact_id == artifact_id:
                return session, artifact
        known = ", ".join(a.artifact_id for a in session.artifacts) or "none"
        raise ValueError(
            f"Unknown artifact_id '{artifact_id}' in session '{session_id}'. Known: {known}."
        )

    def _spec_with_material(self, session: DesignSession, material: str) -> DesignSpec:
        mat = Material(material)
        if session.spec is not None:
            return replace(session.spec, material=mat)
        return DesignSpec(title="untitled", summary="", material=mat)


def _vec3(value: object) -> tuple[float, float, float]:
    if isinstance(value, list | tuple) and len(value) == 3:
        x, y, z = value
        return (float(x), float(y), float(z))
    return (0.0, 0.0, 0.0)


def _mate_params(value: object) -> tuple[tuple[str, float], ...]:
    if isinstance(value, dict):
        return tuple((str(k), float(v)) for k, v in value.items())
    return ()


def _assembly_from_dicts(
    title: str, instances: list[dict[str, object]], mates: list[dict[str, object]] | None
) -> Assembly:
    """Build an ``Assembly`` from the flat JSON dicts MCP tools receive."""
    parts = tuple(
        PartInstance(
            instance_id=str(i["instance_id"]),
            name=str(i["name"]),
            material=Material(str(i.get("material", "petg"))),
            location=_vec3(i.get("location")),
            rotation=_vec3(i.get("rotation")),
            quantity=int(str(i.get("quantity", 1))),
            artifact_id=(str(i["artifact_id"]) if i.get("artifact_id") is not None else None),
            code_ref=(str(i["code_ref"]) if i.get("code_ref") is not None else None),
        )
        for i in instances
    )
    resolved_mates = tuple(
        Mate(
            kind=MateKind(str(m["kind"])),
            instance_a=str(m["instance_a"]),
            instance_b=str(m["instance_b"]),
            params=_mate_params(m.get("params")),
        )
        for m in (mates or [])
    )
    return Assembly(assembly_id=title, title=title, instances=parts, mates=resolved_mates)
