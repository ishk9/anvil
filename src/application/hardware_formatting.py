"""UI-agnostic formatting of the hardware catalog into readable reference text.

Kept separate from ``application/formatting.py`` so the fastener reference (which is
large and reads like a datasheet) does not bloat the core design formatter. Used by
the MCP ``hardware`` resource and the ``list_hardware`` / ``hardware_spec`` tools.
"""

from __future__ import annotations

from domain.models.hardware import (
    BEARING_CATALOG,
    HEATSET_CATALOG,
    SCREW_CATALOG,
    BearingSpec,
    HeatsetInsert,
    ScrewSpec,
)


def format_screw(s: ScrewSpec) -> str:
    return (
        f"{s.designation}: major {s.major_diameter_mm} mm, pitch {s.pitch_mm} mm. "
        f"Clearance close/normal/coarse {s.clearance_close_mm}/{s.clearance_normal_mm}/"
        f"{s.clearance_coarse_mm} mm. Counterbore {s.counterbore_diameter_mm} x "
        f"{s.counterbore_depth_mm} mm. Countersink {s.countersink_diameter_mm} mm (90 deg). "
        f"Tap drill {s.tap_drill_mm} mm."
    )


def format_insert(i: HeatsetInsert) -> str:
    return (
        f"{i.thread} heat-set: L {i.length_mm} mm, OD {i.outer_diameter_mm} mm, "
        f"pocket {i.pocket_diameter_mm} x {i.pocket_depth_mm} mm."
    )


def format_bearing(b: BearingSpec) -> str:
    return (
        f"{b.name}: bore {b.bore_mm} mm, OD {b.outer_diameter_mm} mm, width {b.width_mm} mm, "
        f"press-fit interference {b.press_fit_clearance_mm} mm."
    )


def format_hardware_catalog() -> str:
    """Full datasheet-style dump of every catalog entry. All dimensions mm."""
    screws = "\n".join(f"- {format_screw(s)}" for s in SCREW_CATALOG.values())
    inserts = "\n".join(f"- {format_insert(i)}" for i in HEATSET_CATALOG.values())
    bearings = "\n".join(f"- {format_bearing(b)}" for b in BEARING_CATALOG.values())
    return (
        "Hardware catalog (ISO metric, all mm)\n\n"
        "ISO metric screws (ISO 261/273/4762/10642):\n"
        f"{screws}\n\n"
        "Heat-set inserts (brass, tapered):\n"
        f"{inserts}\n\n"
        "Deep-groove ball bearings:\n"
        f"{bearings}\n"
    )
