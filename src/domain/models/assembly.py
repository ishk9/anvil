"""Value objects for multi-part assemblies: instances, mates, and the bill of materials.

An assembly is a graph of `PartInstance`s (each a placed reference to a buildable part)
related by `Mate` constraints. This module deliberately holds no CAD or mesh code — it is
pure domain data plus two pure functions (`resolve_locations`, `build_bom`) so the mate
solver and BOM math are trivially testable without build123d or trimesh.

Coordinate convention (shared with the rest of Anvil): millimetres, right-handed, with
rotations given as extrinsic X-Y-Z Euler angles in degrees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from domain.models.design_spec import MATERIAL_LIBRARY, Material

Vec3 = tuple[float, float, float]

_ORIGIN: Vec3 = (0.0, 0.0, 0.0)


class MateKind(StrEnum):
    """How a mate positions instance B relative to instance A.

    The solver is intentionally explicit rather than a general constraint system: each
    kind maps to a closed-form placement of B, resolved once against A's placement.
    """

    RIGID = "rigid"  # B inherits A's frame exactly (welded/co-located).
    COINCIDENT = "coincident"  # B's origin sits on A's origin; B keeps its own rotation.
    CONCENTRIC = "concentric"  # B shares A's axis; params.offset slides B along local +Z.
    OFFSET = "offset"  # B sits at A's origin plus params (dx, dy, dz), keeping its rotation.


@dataclass(frozen=True, slots=True)
class PartInstance:
    """A placed occurrence of a buildable part within an assembly.

    Exactly one of `artifact_id` / `code_ref` identifies the source geometry:
    `artifact_id` points at an already-built `GeometryArtifact`; `code_ref` names a
    reusable build123d snippet the assembly toolkit will compose. `quantity` is a BOM
    multiplier — it does not place extra copies in space (add explicit instances for that).
    """

    instance_id: str
    name: str
    material: Material
    location: Vec3 = _ORIGIN
    rotation: Vec3 = _ORIGIN
    quantity: int = 1
    artifact_id: str | None = None
    code_ref: str | None = None

    def __post_init__(self) -> None:
        if self.quantity < 1:
            raise ValueError(f"quantity must be >= 1, got {self.quantity} for '{self.instance_id}'")
        if (self.artifact_id is None) == (self.code_ref is None):
            raise ValueError(
                f"instance '{self.instance_id}' must set exactly one of artifact_id / code_ref"
            )


@dataclass(frozen=True, slots=True)
class Mate:
    """A declarative constraint relating two instances by id.

    `params` carries the numeric arguments a kind needs (e.g. OFFSET reads
    `dx`/`dy`/`dz`; CONCENTRIC reads `offset`). Unknown keys are ignored so callers may
    over-specify without error.
    """

    kind: MateKind
    instance_a: str
    instance_b: str
    params: tuple[tuple[str, float], ...] = field(default_factory=tuple)

    def param(self, key: str, default: float = 0.0) -> float:
        for name, value in self.params:
            if name == key:
                return value
        return default


# Joint is a semantic alias: some callers speak of "joints" (kinematic) rather than
# "mates" (positional). We resolve both to placements identically here.
Joint = Mate


@dataclass(frozen=True, slots=True)
class Assembly:
    """An immutable assembly: placed instances plus the mates that relate them."""

    assembly_id: str
    title: str
    instances: tuple[PartInstance, ...] = field(default_factory=tuple)
    mates: tuple[Mate, ...] = field(default_factory=tuple)

    def instance(self, instance_id: str) -> PartInstance:
        for inst in self.instances:
            if inst.instance_id == instance_id:
                return inst
        known = ", ".join(i.instance_id for i in self.instances) or "none"
        raise KeyError(f"Unknown instance_id '{instance_id}'. Known: {known}.")


@dataclass(frozen=True, slots=True)
class BomLine:
    """One rolled-up row of the bill of materials (parts grouped by name + material)."""

    part_name: str
    material: Material
    quantity: int
    mass_g: float


@dataclass(frozen=True, slots=True)
class BillOfMaterials:
    lines: tuple[BomLine, ...] = field(default_factory=tuple)
    total_mass_g: float = 0.0


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def resolve_locations(assembly: Assembly) -> dict[str, tuple[Vec3, Vec3]]:
    """Resolve each instance to a final (location, rotation) after applying mates.

    Each instance starts at its own declared placement; mates then override instance B's
    placement relative to instance A's *resolved* placement, in declaration order. This is
    a single forward pass — later mates win, and A must be resolved before it is used as an
    anchor (define mates parent-before-child). Cycles are not detected; keep chains acyclic.
    """
    placement: dict[str, tuple[Vec3, Vec3]] = {
        inst.instance_id: (inst.location, inst.rotation) for inst in assembly.instances
    }

    for mate in assembly.mates:
        anchor_loc, anchor_rot = placement[mate.instance_a]
        b = assembly.instance(mate.instance_b)

        if mate.kind is MateKind.RIGID:
            placement[b.instance_id] = (anchor_loc, anchor_rot)
        elif mate.kind is MateKind.COINCIDENT:
            placement[b.instance_id] = (anchor_loc, b.rotation)
        elif mate.kind is MateKind.CONCENTRIC:
            slide = mate.param("offset")
            placement[b.instance_id] = (_add(anchor_loc, (0.0, 0.0, slide)), anchor_rot)
        elif mate.kind is MateKind.OFFSET:
            delta = (mate.param("dx"), mate.param("dy"), mate.param("dz"))
            placement[b.instance_id] = (_add(anchor_loc, delta), b.rotation)

    return placement


def part_mass_g(volume_mm3: float, material: Material) -> float:
    """Mass of a solid of the given volume in the given material (1 cm^3 = 1000 mm^3)."""
    return volume_mm3 / 1000.0 * MATERIAL_LIBRARY[material].density_g_cm3


def build_bom(assembly: Assembly, mass_by_instance: dict[str, float]) -> BillOfMaterials:
    """Roll instances up into BOM lines, keyed by (part_name, material).

    `mass_by_instance` is the mass in grams of a *single* copy of each instance (by
    `instance_id`); `quantity` multiplies it. Instances absent from the map contribute
    zero mass — a missing measurement should not silently drop the line item. Lines are
    returned sorted by part name then material for stable output.
    """
    grouped: dict[tuple[str, Material], tuple[int, float]] = {}
    for inst in assembly.instances:
        unit_mass = mass_by_instance.get(inst.instance_id, 0.0)
        key = (inst.name, inst.material)
        qty, mass = grouped.get(key, (0, 0.0))
        grouped[key] = (qty + inst.quantity, mass + unit_mass * inst.quantity)

    lines = tuple(
        BomLine(part_name=name, material=material, quantity=qty, mass_g=mass)
        for (name, material), (qty, mass) in sorted(
            grouped.items(), key=lambda kv: (kv[0][0], kv[0][1].value)
        )
    )
    return BillOfMaterials(lines=lines, total_mass_g=sum(line.mass_g for line in lines))
