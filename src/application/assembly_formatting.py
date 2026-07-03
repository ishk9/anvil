"""UI-agnostic formatting of assembly outputs: the BOM and the interference report."""

from __future__ import annotations

from application.assembly_toolkit import InterferenceReport
from domain.models.assembly import BillOfMaterials


def format_bom(bom: BillOfMaterials) -> str:
    if not bom.lines:
        return "Bill of materials: (empty — no instances)."
    lines = ["Bill of materials:"]
    for line in bom.lines:
        lines.append(
            f"  {line.quantity} x {line.part_name} ({line.material.value}) "
            f"— {line.mass_g:.1f} g"
        )
    lines.append(f"Total mass: {bom.total_mass_g:.1f} g")
    return "\n".join(lines)


def format_interference_report(report: InterferenceReport) -> str:
    lines: list[str] = []
    if report.clear:
        lines.append("Interference: none detected.")
    else:
        lines.append(f"Interference: {len(report.collisions)} colliding pair(s):")
        for c in report.collisions:
            if c.overlap_volume_mm3 is None:
                overlap = "overlap volume unavailable (no boolean backend)"
            else:
                overlap = f"overlap ~{c.overlap_volume_mm3:.1f} mm^3"
            lines.append(f"  {c.instance_a} <-> {c.instance_b}: {overlap}")
    if report.skipped_instances:
        skipped = ", ".join(report.skipped_instances)
        lines.append(f"Skipped (no mesh available): {skipped}")
    return "\n".join(lines)
