"""Compose, cost, and collision-check multi-part assemblies.

`AssemblyToolkit` sits beside `DesignToolkit`: it turns an `Assembly` (declarative
instances + mates) into a single build123d script that the existing executor renders and
exports as one solid, rolls the instances up into a bill of materials, and reports
overlapping instance pairs via trimesh. It owns no CAD/mesh machinery of its own — the
heavy work is delegated to the injected `DesignToolkit.build` and to trimesh.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from application.design_toolkit import DesignToolkit
from application.session import DesignSession
from domain.models.assembly import (
    Assembly,
    BillOfMaterials,
    build_bom,
    resolve_locations,
)
from domain.models.errors import CadError
from domain.models.geometry import GeometryArtifact
from domain.models.result import Result

if TYPE_CHECKING:
    import trimesh

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class InstanceCollision:
    """One colliding pair of instances and the volume they share.

    `overlap_volume_mm3` is the boolean-intersection volume when it can be computed;
    it is `None` when only broad-phase collision detection succeeded (meshes touch but a
    robust boolean failed on degenerate input).
    """

    instance_a: str
    instance_b: str
    overlap_volume_mm3: float | None


@dataclass(frozen=True, slots=True)
class InterferenceReport:
    collisions: tuple[InstanceCollision, ...] = field(default_factory=tuple)
    skipped_instances: tuple[str, ...] = field(default_factory=tuple)

    @property
    def clear(self) -> bool:
        return not self.collisions


def _rigid_transform(
    location: tuple[float, float, float], rotation: tuple[float, float, float]
) -> Any:
    """Build a 4x4 homogeneous transform for a translation + extrinsic X-Y-Z rotation (deg)."""
    import numpy as np
    from trimesh.transformations import euler_matrix

    rx, ry, rz = (np.radians(a) for a in rotation)
    matrix = euler_matrix(rx, ry, rz, "sxyz")  # type: ignore[no-untyped-call]
    matrix[:3, 3] = location
    return matrix


class AssemblyToolkit:
    def __init__(self, *, toolkit: DesignToolkit) -> None:
        self._toolkit = toolkit

    # --- composition -----------------------------------------------------------

    def compose_code(self, assembly: Assembly, code_by_ref: dict[str, str]) -> str:
        """Generate a build123d script that places every instance and unions into `part`.

        `code_by_ref` maps an instance's `code_ref` to a build123d snippet that binds a
        local variable named `part`; each snippet runs in its own function scope, is moved
        to the instance's resolved location/rotation, and unioned into the assembly solid.
        Instances identified only by `artifact_id` cannot be composed from source here and
        raise — resolve them to a `code_ref` before composing.
        """
        placement = resolve_locations(assembly)
        blocks: list[str] = [
            "# Auto-generated assembly script — one solid per instance, unioned into `part`.",
            "_instances = []",
        ]
        for inst in assembly.instances:
            if inst.code_ref is None:
                raise ValueError(
                    f"instance '{inst.instance_id}' has no code_ref; cannot compose it from "
                    f"source (artifact_id-only instances must be resolved to code first)."
                )
            snippet = code_by_ref.get(inst.code_ref)
            if snippet is None:
                raise KeyError(
                    f"no code registered for code_ref '{inst.code_ref}' "
                    f"(instance '{inst.instance_id}')."
                )
            location, rotation = placement[inst.instance_id]
            blocks.append(
                self._instance_block(inst.instance_id, snippet, location, rotation)
            )

        blocks.append("part = _instances[0]")
        blocks.append("for _p in _instances[1:]:")
        blocks.append("    part = part + _p")
        return "\n".join(blocks)

    @staticmethod
    def _instance_block(
        instance_id: str,
        snippet: str,
        location: tuple[float, float, float],
        rotation: tuple[float, float, float],
    ) -> str:
        indented = "\n".join(f"    {line}" for line in snippet.splitlines())
        fn = f"_build_{_ident(instance_id)}"
        lx, ly, lz = location
        rx, ry, rz = rotation
        return (
            f"def {fn}():\n"
            f"{indented}\n"
            f"    return part\n"
            f"_p = {fn}()\n"
            f"_p = Rotation({rx}, {ry}, {rz}) * _p\n"
            f"_p = Pos({lx}, {ly}, {lz}) * _p\n"
            f"_instances.append(_p)"
        )

    def build(
        self,
        *,
        session: DesignSession,
        assembly: Assembly,
        code_by_ref: dict[str, str],
    ) -> Result[GeometryArtifact, CadError]:
        """Compose the assembly to a script and hand it to the existing build pipeline."""
        code = self.compose_code(assembly, code_by_ref)
        log.info(
            "assembly.build",
            assembly_id=assembly.assembly_id,
            instances=len(assembly.instances),
        )
        return self._toolkit.build(session=session, code=code)

    # --- bill of materials -----------------------------------------------------

    def bill_of_materials(
        self, assembly: Assembly, part_masses: dict[str, float]
    ) -> BillOfMaterials:
        """Roll the assembly up into a BOM. `part_masses` is per-instance single-copy mass (g)."""
        return build_bom(assembly, part_masses)

    # --- interference ----------------------------------------------------------

    def interference_report(
        self, assembly: Assembly, instance_meshes: dict[str, trimesh.Trimesh]
    ) -> InterferenceReport:
        """Detect overlapping instance pairs by placing each mesh at its resolved frame.

        `instance_meshes` maps `instance_id` -> an untransformed trimesh in the part's own
        frame. Instances without a mesh are reported as `skipped_instances` and excluded
        from collision checks (graceful degradation).

        Detection is two-phase: a cheap axis-aligned bounding-box (AABB) overlap prunes the
        pairs, then a boolean intersection confirms each candidate and measures the shared
        volume. A pair is reported only when the intersection volume is positive, or when
        the boolean fails (`overlap_volume_mm3=None`) — a failed boolean cannot rule the
        collision out, so it is surfaced rather than silently dropped. This avoids a hard
        dependency on python-fcl (trimesh's `CollisionManager`), which is optional.
        """
        placement = resolve_locations(assembly)
        placed: dict[str, trimesh.Trimesh] = {}
        skipped: list[str] = []

        for inst in assembly.instances:
            mesh = instance_meshes.get(inst.instance_id)
            if mesh is None:
                skipped.append(inst.instance_id)
                continue
            location, rotation = placement[inst.instance_id]
            moved = mesh.copy()
            moved.apply_transform(_rigid_transform(location, rotation))
            placed[inst.instance_id] = moved

        ids = sorted(placed)
        collisions: list[InstanceCollision] = []
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                if not _aabb_overlap(placed[a].bounds, placed[b].bounds):
                    continue
                overlap = self._overlap_volume(placed[a], placed[b])
                if overlap is None or overlap > 0.0:
                    collisions.append(InstanceCollision(a, b, overlap))

        return InterferenceReport(
            collisions=tuple(collisions), skipped_instances=tuple(skipped)
        )

    @staticmethod
    def _overlap_volume(a: trimesh.Trimesh, b: trimesh.Trimesh) -> float | None:
        """Shared volume of two placed meshes (mm^3), or None if it can't be measured.

        Prefers a precise mesh boolean intersection when a trimesh boolean backend
        (manifold3d / blender) is installed. Without one, falls back to the exact
        AABB-intersection volume — correct for axis-aligned boxes and a conservative
        over-estimate for arbitrary geometry (it ignores concavity/orientation).
        """
        try:
            inter = a.intersection(b)
        except Exception:  # no backend, or a degenerate boolean on this pair.
            return _aabb_overlap_volume(a.bounds, b.bounds)
        if inter is None or inter.is_empty or not hasattr(inter, "volume"):
            return 0.0
        return float(abs(inter.volume))


def _ident(text: str) -> str:
    """Turn an instance id into a safe Python identifier fragment."""
    cleaned = "".join(c if c.isalnum() else "_" for c in text)
    return cleaned if cleaned and not cleaned[0].isdigit() else f"i_{cleaned}"


def _aabb_overlap(a: Any, b: Any) -> bool:
    """True if two trimesh `bounds` arrays ([[minx,miny,minz],[maxx,maxy,maxz]]) overlap."""
    if a is None or b is None:
        return False
    return bool(all(a[0][k] <= b[1][k] and b[0][k] <= a[1][k] for k in range(3)))


def _aabb_overlap_volume(a: Any, b: Any) -> float:
    """Volume (mm^3) of the intersection of two AABBs; 0.0 if they do not overlap."""
    if a is None or b is None:
        return 0.0
    volume = 1.0
    for k in range(3):
        span = min(a[1][k], b[1][k]) - max(a[0][k], b[0][k])
        if span <= 0.0:
            return 0.0
        volume *= span
    return float(volume)
