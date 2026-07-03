"""The structured design intent the agent elicits from the user before modelling.

`DesignSpec` is the contract between the *conversation* phase and the *generation*
phase. Keeping it explicit means the geometry is always traceable to stated intent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Material(StrEnum):
    PLA = "pla"
    PETG = "petg"
    ABS = "abs"
    NYLON = "nylon"
    NYLON_CF = "nylon_cf"  # carbon-fibre reinforced nylon
    ALU_6061 = "alu_6061"
    STEEL_MILD = "steel_mild"


@dataclass(frozen=True, slots=True)
class MaterialProfile:
    """Physical properties used for mass and (later) stress estimation."""

    material: Material
    density_g_cm3: float
    tensile_mpa: float
    notes: str = ""


MATERIAL_LIBRARY: dict[Material, MaterialProfile] = {
    Material.PLA: MaterialProfile(Material.PLA, 1.24, 50.0, "Stiff, brittle, cheap"),
    Material.PETG: MaterialProfile(Material.PETG, 1.27, 50.0, "Tough, good layer adhesion"),
    Material.ABS: MaterialProfile(Material.ABS, 1.04, 40.0, "Heat resistant, warps"),
    Material.NYLON: MaterialProfile(Material.NYLON, 1.14, 70.0, "Tough, flexible, hygroscopic"),
    Material.NYLON_CF: MaterialProfile(
        Material.NYLON_CF, 1.20, 110.0, "Stiff and light — common for drone arms"
    ),
    Material.ALU_6061: MaterialProfile(Material.ALU_6061, 2.70, 310.0, "Machined metal parts"),
    Material.STEEL_MILD: MaterialProfile(Material.STEEL_MILD, 7.85, 400.0, "Heavy, very strong"),
}


@dataclass(frozen=True, slots=True)
class LoadCase:
    """A described mechanical load used to reason about (and later simulate) strength."""

    name: str
    description: str
    force_newtons: float | None = None


@dataclass(frozen=True, slots=True)
class DesignSpec:
    """Immutable snapshot of the agreed design intent."""

    title: str
    summary: str
    material: Material = Material.PETG
    requirements: tuple[str, ...] = field(default_factory=tuple)
    constraints: tuple[str, ...] = field(default_factory=tuple)
    load_cases: tuple[LoadCase, ...] = field(default_factory=tuple)
    interfaces: tuple[str, ...] = field(default_factory=tuple)
    """Mating features: hole patterns, bolt sizes, mounting standards (e.g. M3, 30.5mm)."""

    def material_profile(self) -> MaterialProfile:
        return MATERIAL_LIBRARY[self.material]
