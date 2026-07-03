"""Value objects describing produced geometry and its measurable properties."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BoundingBox:
    x_mm: float
    y_mm: float
    z_mm: float

    @property
    def volume_mm3(self) -> float:
        return self.x_mm * self.y_mm * self.z_mm

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x_mm, self.y_mm, self.z_mm)


@dataclass(frozen=True, slots=True)
class MassProperties:
    volume_mm3: float
    mass_g: float
    center_of_mass_mm: tuple[float, float, float]
    bounding_box: BoundingBox
    watertight: bool


@dataclass(frozen=True, slots=True)
class GeometryArtifact:
    """A successfully built part: the source that made it and where it lives on disk."""

    artifact_id: str
    source_code: str
    step_path: Path
    stl_path: Path
    bounding_box: BoundingBox
    render_paths: tuple[Path, ...] = field(default_factory=tuple)
