"""Value objects for the extended export surface (formats beyond STEP/STL + drawings).

Kept separate from ``geometry.py`` so the export request/format vocabulary can evolve
independently of the core ``GeometryArtifact``. A request is validated at construction:
callers pass user/model strings and get back a normalised, de-duplicated format set or a
``ValueError`` — the sandbox never sees an unknown format.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ExportFormat(StrEnum):
    """A downstream file format Anvil can produce from a built B-rep.

    STEP/STL are the always-on baseline (written by the main build runner). The rest are
    opt-in and produced by the export runner, which re-loads the STEP and re-derives the
    live shape — 3MF/DXF/SVG need the B-rep, not just the mesh.
    """

    STEP = "step"  # baseline B-rep (CAD interchange)
    STL = "stl"  # baseline mesh (3D print)
    THREE_MF = "3mf"  # mesh + colour/metadata (modern print/slicer)
    OBJ = "obj"  # mesh + material stub (DCC / rendering)
    GLTF = "gltf"  # mesh for web/graphics viewers
    DXF = "dxf"  # 2D projection for laser/CNC
    SVG = "svg"  # 2D projection (web/vector)
    DRAWING = "drawing"  # multi-view dimensioned drawing sheet (SVG)

    @property
    def suffix(self) -> str:
        """Filename suffix (with dot). DRAWING is a titled multi-view SVG sheet."""
        if self is ExportFormat.DRAWING:
            return ".drawing.svg"
        return f".{self.value}"


# Formats the main build runner already emits; requesting them never invokes the
# export runner (we just copy the existing files).
BASELINE_FORMATS: frozenset[ExportFormat] = frozenset({ExportFormat.STEP, ExportFormat.STL})

# Formats that require re-deriving the live shape from the STEP in the export runner.
DERIVED_FORMATS: frozenset[ExportFormat] = frozenset(ExportFormat) - BASELINE_FORMATS


@dataclass(frozen=True, slots=True)
class ExportRequest:
    """A validated set of formats to produce for one artifact, plus the export basename.

    ``formats`` is normalised at construction: STEP and STL are always included (they are
    the baseline deliverable), values are de-duplicated, and order is deterministic
    (enum-declaration order) so filenames and logs are stable across runs.
    """

    base_name: str
    formats: tuple[ExportFormat, ...]

    @classmethod
    def from_strings(cls, *, base_name: str, formats: list[str] | None) -> ExportRequest:
        """Build a request from loosely-typed input, raising on unknown formats.

        ``None``/empty ``formats`` yields the baseline (STEP + STL) — the historical
        behaviour. Casing and surrounding whitespace are ignored.
        """
        requested: set[ExportFormat] = set(BASELINE_FORMATS)
        for raw in formats or []:
            token = raw.strip().lower()
            if not token:
                continue
            try:
                requested.add(ExportFormat(token))
            except ValueError:
                allowed = ", ".join(f.value for f in ExportFormat)
                raise ValueError(
                    f"Unknown export format {raw!r}. Choose from: {allowed}."
                ) from None
        ordered = tuple(f for f in ExportFormat if f in requested)
        return cls(base_name=base_name, formats=ordered)

    @property
    def derived(self) -> tuple[ExportFormat, ...]:
        """The requested formats that need the export runner (not STEP/STL copies)."""
        return tuple(f for f in self.formats if f in DERIVED_FORMATS)

    def needs_export_runner(self) -> bool:
        """True when at least one requested format must be re-derived from the STEP."""
        return bool(self.derived)

    def filename_for(self, fmt: ExportFormat) -> str:
        """Derive the on-disk filename for one format from the request basename."""
        return f"{self.base_name}{fmt.suffix}"
