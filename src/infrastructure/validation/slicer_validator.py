"""Slicer-backed print time / filament / cost estimation.

Shells out to a slicer CLI (PrusaSlicer by default) to slice ``artifact.stl_path`` into
G-code, then parses the summary comments the slicer writes for the *real* estimated print
time and filament mass. Cost is derived from a per-material filament price table plus a
machine hourly rate.

If the slicer binary is not on PATH (the common case in CI / the python-slim container),
the adapter does **not** raise: it falls back to a purely geometric estimate from the mesh
volume and material density, and flags that with a WARNING so the numbers are never
mistaken for a true slice.

What we parse (PrusaSlicer, verified against libslic3r source)
-------------------------------------------------------------
PrusaSlicer writes summary comments near the end of the G-code::

    ; estimated printing time (normal mode) = 1h 5m 30s
    ; filament used [mm] = 1234.5
    ; filament used [cm3] = 2.97
    ; filament used [g] = 3.68

Time uses ``%dd %dh %dm %ds`` with leading zero components omitted. There is no
support-only filament comment, so support volume is estimated by integrating extrusion
volume over the G-code regions PrusaSlicer tags with ``;TYPE:Support material`` (and
``Support material interface``). ``filament used [cm3]`` gives total extruded volume,
which lets us convert the summed support extrusion length into a mm^3 volume.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import structlog
import trimesh

from domain.models.design_spec import DesignSpec, Material
from domain.models.geometry import GeometryArtifact
from domain.models.validation import Severity, ValidationIssue, ValidationReport

log = structlog.get_logger(__name__)

# --- Cost model defaults --------------------------------------------------------------

# Indicative retail filament / stock prices in USD per kg. FDM polymers are spool prices;
# the metals are here so the cost method degrades sensibly for machined-material specs
# (they are not printed — treat the number as a rough stock-mass cost, not a print cost).
DEFAULT_MATERIAL_PRICES_USD_PER_KG: dict[Material, float] = {
    Material.PLA: 22.0,
    Material.PETG: 25.0,
    Material.ABS: 24.0,
    Material.NYLON: 45.0,
    Material.NYLON_CF: 85.0,
    Material.ALU_6061: 12.0,
    Material.STEEL_MILD: 4.0,
}

# Machine + operator amortisation charged per print hour.
DEFAULT_MACHINE_RATE_USD_PER_HOUR = 1.50

# Fallback-estimate tuning. Solid mesh volume is scaled by this to approximate the
# deposited material of a typical part printed at moderate infill + walls.
_FALLBACK_INFILL_FACTOR = 0.55

# Volumetric deposition rate (mm^3/s) used to turn deposited volume into a print time when
# no slicer is available. ~8 mm^3/s is a realistic sustained rate for a 0.4mm nozzle.
_FALLBACK_FLOW_MM3_PER_S = 8.0

# Thresholds for advisory warnings.
_LONG_PRINT_WARN_MIN = 8 * 60.0  # 8 hours
_SUPPORT_HEAVY_WARN_FRACTION = 0.25  # support > 25% of deposited volume

_MATERIALS_PRINTED_ON_FDM = frozenset(
    {Material.PLA, Material.PETG, Material.ABS, Material.NYLON, Material.NYLON_CF}
)

# --- G-code parsing -------------------------------------------------------------------

_RE_PRINT_TIME = re.compile(
    r"estimated printing time \(normal mode\) = "
    r"(?:(\d+)d )?(?:(\d+)h )?(?:(\d+)m )?(?:(\d+)s)?"
)
_RE_FILAMENT_G = re.compile(r"filament used \[g\] = ([\d.]+)")
_RE_FILAMENT_CM3 = re.compile(r"filament used \[cm3\] = ([\d.]+)")
_RE_FILAMENT_MM = re.compile(r"filament used \[mm\] = ([\d.]+)")
_RE_EXTRUDE = re.compile(r"^G[01] .*E([-\d.]+)", re.IGNORECASE)
_RE_TYPE = re.compile(r"^;\s*TYPE:(.+)$")


@dataclass(frozen=True, slots=True)
class _SlicerSummary:
    """Parsed figures from one slicer run (all optional; ``None`` if the slicer omitted it)."""

    print_time_min: float | None
    filament_g: float | None
    filament_cm3: float | None
    support_volume_mm3: float | None


class SlicerValidator:
    """Estimates print time, filament, support, and cost by slicing the exported STL.

    The heavy lifting is delegated to a slicer CLI so the numbers reflect the same
    toolpath planning a user would get on their own machine. When no slicer is installed
    the adapter still returns a report, using a geometric estimate flagged as such.
    """

    def __init__(
        self,
        *,
        slicer_cmd: str = "prusa-slicer",
        slicer_config_path: Path | None = None,
        machine_rate_usd_per_hour: float = DEFAULT_MACHINE_RATE_USD_PER_HOUR,
        material_prices_usd_per_kg: Mapping[Material, float] | None = None,
        timeout_seconds: int = 120,
    ) -> None:
        self._slicer_cmd = slicer_cmd
        self._config_path = slicer_config_path
        self._machine_rate = machine_rate_usd_per_hour
        self._prices = dict(material_prices_usd_per_kg or DEFAULT_MATERIAL_PRICES_USD_PER_KG)
        self._timeout = timeout_seconds

    @property
    def name(self) -> str:
        return "slicer"

    def validate(self, *, artifact: GeometryArtifact, spec: DesignSpec) -> ValidationReport:
        profile = spec.material_profile()
        summary, slicer_used = self._run_slicer(artifact.stl_path)

        if summary is None:
            summary = self._geometric_estimate(artifact.stl_path, profile.density_g_cm3)

        cost = self._cost(
            filament_g=summary.filament_g,
            print_time_min=summary.print_time_min,
            material=profile.material,
        )
        return self._build_report(
            summary=summary,
            cost_usd=cost,
            material=profile.material,
            slicer_used=slicer_used,
        )

    # --- slicer invocation ------------------------------------------------------------

    def _run_slicer(self, stl_path: Path) -> tuple[_SlicerSummary | None, bool]:
        """Slice the STL and parse the result. Returns (summary, slicer_was_used).

        ``(None, False)`` means the slicer is unavailable or failed and the caller should
        fall back to a geometric estimate.
        """
        binary = shutil.which(self._slicer_cmd)
        if binary is None:
            log.info("slicer.not_installed", cmd=self._slicer_cmd)
            return None, False

        with tempfile.TemporaryDirectory(prefix="anvil-slice-") as tmp:
            gcode_path = Path(tmp) / "out.gcode"
            cmd = [binary, "--export-gcode", "-o", str(gcode_path)]
            if self._config_path is not None:
                cmd += ["--load", str(self._config_path)]
            cmd.append(str(stl_path))

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                log.warning("slicer.invocation_failed", cmd=self._slicer_cmd, error=str(exc))
                return None, False

            if proc.returncode != 0 or not gcode_path.exists():
                log.warning(
                    "slicer.export_failed",
                    cmd=self._slicer_cmd,
                    returncode=proc.returncode,
                    stderr=proc.stderr[-500:] if proc.stderr else "",
                )
                return None, False

            gcode = gcode_path.read_text(errors="ignore")

        return self._parse_gcode(gcode), True

    def _parse_gcode(self, gcode: str) -> _SlicerSummary:
        """Extract print time, filament mass/volume, and support volume from G-code."""
        print_time_min = _first_time_minutes(gcode)
        filament_g = _first_float(_RE_FILAMENT_G, gcode)
        filament_cm3 = _first_float(_RE_FILAMENT_CM3, gcode)
        filament_mm = _first_float(_RE_FILAMENT_MM, gcode)

        support_volume_mm3 = _support_volume_mm3(
            gcode, filament_mm=filament_mm, filament_cm3=filament_cm3
        )
        return _SlicerSummary(
            print_time_min=print_time_min,
            filament_g=filament_g,
            filament_cm3=filament_cm3,
            support_volume_mm3=support_volume_mm3,
        )

    # --- geometric fallback -----------------------------------------------------------

    def _geometric_estimate(self, stl_path: Path, density_g_cm3: float) -> _SlicerSummary:
        """Approximate filament and time from mesh volume when no slicer is available.

        Deposited volume is the solid mesh volume scaled by an infill factor (walls + a
        moderate infill are far less than 100% solid). Mass follows from material density;
        time from a constant volumetric flow-rate heuristic. Support is not modelled here.
        """
        mesh = trimesh.load(stl_path, force="mesh")
        solid_volume_mm3 = float(mesh.volume) if isinstance(mesh, trimesh.Trimesh) else 0.0

        deposited_mm3 = solid_volume_mm3 * _FALLBACK_INFILL_FACTOR
        filament_g = deposited_mm3 / 1000.0 * density_g_cm3
        print_time_min = (deposited_mm3 / _FALLBACK_FLOW_MM3_PER_S) / 60.0 if deposited_mm3 else 0.0
        return _SlicerSummary(
            print_time_min=print_time_min,
            filament_g=filament_g,
            filament_cm3=deposited_mm3 / 1000.0,
            support_volume_mm3=None,
        )

    # --- cost -------------------------------------------------------------------------

    def _cost(
        self,
        *,
        filament_g: float | None,
        print_time_min: float | None,
        material: Material,
    ) -> float:
        """cost = filament_g * price_per_kg/1000 + print_time_h * machine_rate."""
        price_per_kg = self._prices.get(material, 0.0)
        material_cost = (filament_g or 0.0) * price_per_kg / 1000.0
        machine_cost = (print_time_min or 0.0) / 60.0 * self._machine_rate
        return material_cost + machine_cost

    # --- report assembly --------------------------------------------------------------

    def _build_report(
        self,
        *,
        summary: _SlicerSummary,
        cost_usd: float,
        material: Material,
        slicer_used: bool,
    ) -> ValidationReport:
        issues: list[ValidationIssue] = []
        metrics: dict[str, float] = {}

        if not slicer_used:
            issues.append(
                ValidationIssue(
                    self.name,
                    Severity.WARNING,
                    "slicer not installed — cost/time estimated from geometry",
                )
            )
            if material not in _MATERIALS_PRINTED_ON_FDM:
                issues.append(
                    ValidationIssue(
                        self.name,
                        Severity.INFO,
                        f"{material.value} is a machined material, not FDM-printed; "
                        "the estimate treats it as deposited mass and is indicative only.",
                    )
                )

        if summary.print_time_min is not None:
            metrics["slicer.print_time_min"] = round(summary.print_time_min, 1)
            issues.append(
                ValidationIssue(
                    self.name,
                    Severity.INFO,
                    f"Estimated print time: {_fmt_duration(summary.print_time_min)}",
                    value=round(summary.print_time_min, 1),
                )
            )
            if summary.print_time_min > _LONG_PRINT_WARN_MIN:
                issues.append(
                    ValidationIssue(
                        self.name,
                        Severity.WARNING,
                        f"Long print (~{summary.print_time_min / 60.0:.1f} h). Consider "
                        "splitting the part or reducing infill.",
                        value=round(summary.print_time_min, 1),
                        limit=_LONG_PRINT_WARN_MIN,
                    )
                )

        if summary.filament_g is not None:
            metrics["slicer.filament_g"] = round(summary.filament_g, 2)
            issues.append(
                ValidationIssue(
                    self.name,
                    Severity.INFO,
                    f"Filament used: {summary.filament_g:.1f} g",
                    value=round(summary.filament_g, 2),
                )
            )

        if summary.support_volume_mm3 is not None:
            metrics["slicer.support_volume_mm3"] = round(summary.support_volume_mm3, 1)
            issues.append(
                ValidationIssue(
                    self.name,
                    Severity.INFO,
                    f"Support material volume: {summary.support_volume_mm3 / 1000.0:.2f} cm^3",
                    value=round(summary.support_volume_mm3, 1),
                )
            )
            deposited_mm3 = (summary.filament_cm3 or 0.0) * 1000.0
            if (
                deposited_mm3 > 0.0
                and summary.support_volume_mm3 / deposited_mm3 > _SUPPORT_HEAVY_WARN_FRACTION
            ):
                fraction = summary.support_volume_mm3 / deposited_mm3
                issues.append(
                    ValidationIssue(
                        self.name,
                        Severity.WARNING,
                        f"Support-heavy: ~{fraction * 100:.0f}% of filament is support. "
                        "Reorient the part or add chamfers to reduce overhangs.",
                        value=round(fraction, 3),
                        limit=_SUPPORT_HEAVY_WARN_FRACTION,
                    )
                )

        metrics["slicer.cost_usd"] = round(cost_usd, 2)
        issues.append(
            ValidationIssue(
                self.name,
                Severity.INFO,
                f"Estimated cost: ${cost_usd:.2f} "
                f"({material.value} @ ${self._prices.get(material, 0.0):.0f}/kg + "
                f"${self._machine_rate:.2f}/h machine)",
                value=round(cost_usd, 2),
            )
        )

        return ValidationReport(issues=tuple(issues), metrics=metrics)


# --- module-level parse helpers -------------------------------------------------------


def _first_float(pattern: re.Pattern[str], text: str) -> float | None:
    match = pattern.search(text)
    return float(match.group(1)) if match else None


def _first_time_minutes(gcode: str) -> float | None:
    match = _RE_PRINT_TIME.search(gcode)
    if match is None:
        return None
    days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    total_s = days * 86400 + hours * 3600 + minutes * 60 + seconds
    return total_s / 60.0 if total_s else 0.0


def _support_volume_mm3(
    gcode: str, *, filament_mm: float | None, filament_cm3: float | None
) -> float | None:
    """Integrate extruded filament over ``;TYPE:Support material`` regions.

    G-code E values are cumulative filament *length* (mm). We sum the positive deltas that
    fall under a support type header, then convert that length share into a mm^3 volume
    using the total ``filament used [cm3]`` / ``filament used [mm]`` ratio the slicer
    already reported. Returns ``None`` if we can't tie the two together.
    """
    if not filament_mm or not filament_cm3:
        return None

    support_len_mm = 0.0
    in_support = False
    last_e: float | None = None
    saw_e = False

    for line in gcode.splitlines():
        type_match = _RE_TYPE.match(line)
        if type_match is not None:
            in_support = "support" in type_match.group(1).strip().lower()
            continue

        e_match = _RE_EXTRUDE.match(line)
        if e_match is None:
            continue
        saw_e = True
        e_val = float(e_match.group(1))
        if last_e is not None:
            delta = e_val - last_e
            if delta > 0.0 and in_support:
                support_len_mm += delta
        last_e = e_val

    if not saw_e:
        return None
    if support_len_mm <= 0.0:
        return 0.0

    volume_per_mm = (filament_cm3 * 1000.0) / filament_mm  # mm^3 per mm of filament
    return support_len_mm * volume_per_mm


def _fmt_duration(minutes: float) -> str:
    total_min = round(minutes)
    hours, mins = divmod(total_min, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"
