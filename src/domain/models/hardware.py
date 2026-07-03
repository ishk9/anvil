"""Catalog of standard mechanical hardware as frozen value objects.

Anvil-designed parts have to bolt onto real things, so this module encodes the
dimensions a designer needs to cut the *mating* features: clearance holes for
screws to pass through, counterbore/countersink recesses for heads, tap-drill
holes for cutting threads, heat-set-insert pockets, and bearing seats.

All dimensions are millimetres. Sources are cited per table:

- ISO 261    — general-purpose metric screw threads (major dia, coarse pitch).
- ISO 273    — clearance holes for bolts and screws (fine/medium/coarse).
- ISO 4762   — hexagon socket head cap screws (head dia/height for counterbore).
- ISO 10642  — countersunk (flat) head screws (head dia, 90° included angle).
- Tap drill  — ~ major_diameter - pitch (the standard 100%-ish rule for ISO
               coarse threads; e.g. M3x0.5 -> 2.5 mm), rounded to the nearest
               real drill (so M8x1.25 is the standard 6.8 mm, not 6.75 mm).

The numbers are the widely published nominal values; they are conservative
enough for FDM/SLA prints and light metal work, not a substitute for a machinist's
handbook on a tight-tolerance job.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class HeadType(StrEnum):
    """Screw head geometries Anvil can recess for."""

    SOCKET_CAP = "socket_cap"  # ISO 4762 cylindrical head, hex socket drive
    COUNTERSUNK = "countersunk"  # ISO 10642 flat head, 90 deg included angle


class Fit(StrEnum):
    """ISO 273 clearance-hole fit classes (looser -> more slop for alignment)."""

    CLOSE = "close"
    NORMAL = "normal"
    COARSE = "coarse"


@dataclass(frozen=True, slots=True)
class ScrewSpec:
    """One ISO metric screw size and the features needed to mount it.

    ``clearance_*`` are through-hole diameters (ISO 273). ``counterbore_*`` size
    the flat-bottomed recess for a socket-cap head (ISO 4762). ``countersink_*``
    size the conical recess for a flat head (ISO 10642). ``tap_drill_mm`` is the
    hole to drill *before* cutting an internal thread.
    """

    designation: str  # e.g. "M3"
    major_diameter_mm: float  # nominal thread OD
    pitch_mm: float  # ISO coarse pitch
    clearance_close_mm: float
    clearance_normal_mm: float
    clearance_coarse_mm: float
    counterbore_diameter_mm: float  # socket-cap head OD + a touch (ISO 4762 nom head dia)
    counterbore_depth_mm: float  # socket-cap head height (sink the head flush)
    countersink_diameter_mm: float  # flat-head major OD at the surface (ISO 10642)
    tap_drill_mm: float

    def clearance(self, fit: Fit) -> float:
        return {
            Fit.CLOSE: self.clearance_close_mm,
            Fit.NORMAL: self.clearance_normal_mm,
            Fit.COARSE: self.clearance_coarse_mm,
        }[fit]


@dataclass(frozen=True, slots=True)
class HeatsetInsert:
    """A brass heat-set threaded insert (e.g. the common McMaster/CNC-Kitchen style).

    Pressed into a moulded/printed boss with a soldering iron; the plastic reflows
    around the knurl. ``pocket_diameter_mm`` is the hole to model for the melt to
    fill; it is deliberately a hair under the insert OD so material displaces
    inward rather than the insert falling through. Values are typical for the
    standard "short" tapered inserts and vary by brand — treat as a starting point.
    """

    thread: str  # thread it provides, e.g. "M3"
    length_mm: float
    outer_diameter_mm: float  # max knurl diameter
    pocket_diameter_mm: float  # printed pocket bore
    pocket_depth_mm: float  # slightly deeper than the insert to hold excess plastic


@dataclass(frozen=True, slots=True)
class BearingSpec:
    """A deep-groove ball bearing by its trade number.

    ``press_fit_clearance_mm`` is subtracted from ``outer_diameter_mm`` when
    modelling the housing bore for an interference (press) fit; a negative-to-zero
    value here means the pocket is cut *smaller* than the bearing OD by that
    amount so the bearing is retained. For a slip fit, add clearance instead.
    """

    name: str  # trade designation, e.g. "608"
    bore_mm: float  # inner diameter (shaft)
    outer_diameter_mm: float
    width_mm: float
    press_fit_clearance_mm: float  # OD interference for a light press fit


# --- ISO metric screw catalog -------------------------------------------------
# Columns per size (all mm):
#   major dia | coarse pitch (ISO 261) | clearance close/normal/coarse (ISO 273)
#   | socket-cap head dia & height (ISO 4762) | flat-head dia (ISO 10642)
#   | tap drill (= major - pitch)
_SCREWS: tuple[ScrewSpec, ...] = (
    ScrewSpec("M2", 2.0, 0.40, 2.2, 2.4, 2.6, 3.8, 2.0, 4.0, 1.6),
    ScrewSpec("M2.5", 2.5, 0.45, 2.7, 2.9, 3.1, 4.5, 2.5, 5.0, 2.05),
    ScrewSpec("M3", 3.0, 0.50, 3.2, 3.4, 3.6, 5.5, 3.0, 6.0, 2.5),
    ScrewSpec("M4", 4.0, 0.70, 4.3, 4.5, 4.8, 7.0, 4.0, 8.0, 3.3),
    ScrewSpec("M5", 5.0, 0.80, 5.3, 5.5, 5.8, 8.5, 5.0, 10.0, 4.2),
    ScrewSpec("M6", 6.0, 1.00, 6.4, 6.6, 7.0, 10.0, 6.0, 12.0, 5.0),
    ScrewSpec("M8", 8.0, 1.25, 8.4, 9.0, 10.0, 13.0, 8.0, 16.0, 6.8),
)

SCREW_CATALOG: dict[str, ScrewSpec] = {s.designation: s for s in _SCREWS}


# --- Heat-set inserts (typical short/tapered brass, e.g. CNC Kitchen) ---------
_INSERTS: tuple[HeatsetInsert, ...] = (
    HeatsetInsert("M2", 4.0, 3.2, 3.2, 4.4),
    HeatsetInsert("M2.5", 5.7, 4.0, 4.0, 6.1),
    HeatsetInsert("M3", 5.7, 4.6, 4.0, 6.1),
    HeatsetInsert("M4", 8.1, 5.6, 5.6, 8.5),
    HeatsetInsert("M5", 9.5, 6.4, 6.4, 10.0),
    HeatsetInsert("M6", 12.7, 8.0, 8.0, 13.2),
)

HEATSET_CATALOG: dict[str, HeatsetInsert] = {i.thread: i for i in _INSERTS}


# --- Deep-groove ball bearings (ISO 15 / common trade sizes) ------------------
# bore | OD | width. Press-fit interference is a light 0.01-0.02 mm; for printed
# plastic housings a designer often prefers a slip fit + retainer instead.
_BEARINGS: tuple[BearingSpec, ...] = (
    BearingSpec("623", 3.0, 10.0, 4.0, 0.01),
    BearingSpec("626", 6.0, 19.0, 6.0, 0.012),
    BearingSpec("608", 8.0, 22.0, 7.0, 0.012),  # the "skateboard" bearing
    BearingSpec("6800", 10.0, 19.0, 5.0, 0.012),
    BearingSpec("6801", 12.0, 21.0, 5.0, 0.012),
    BearingSpec("6802", 15.0, 24.0, 5.0, 0.014),
)

BEARING_CATALOG: dict[str, BearingSpec] = {b.name: b for b in _BEARINGS}


# --- Lookup helpers -----------------------------------------------------------


def screw(size: str) -> ScrewSpec:
    """Return the screw spec for a designation like ``"M3"``.

    Raises ``KeyError`` with the known sizes if the designation is unknown.
    """
    try:
        return SCREW_CATALOG[size]
    except KeyError:
        raise KeyError(
            f"Unknown screw size {size!r}. Known: {', '.join(SCREW_CATALOG)}."
        ) from None


def clearance_hole_mm(size: str, fit: Fit = Fit.NORMAL) -> float:
    """Through-hole diameter (ISO 273) for a screw of ``size`` to pass freely."""
    return screw(size).clearance(fit)


def tap_drill_mm(size: str) -> float:
    """Pilot-hole diameter to drill before cutting an internal thread (major - pitch)."""
    return screw(size).tap_drill_mm


def counterbore(size: str) -> tuple[float, float]:
    """Socket-cap counterbore as ``(diameter_mm, depth_mm)`` (ISO 4762 head)."""
    s = screw(size)
    return s.counterbore_diameter_mm, s.counterbore_depth_mm


def countersink(size: str) -> tuple[float, float]:
    """Flat-head countersink as ``(surface_diameter_mm, included_angle_deg)`` (ISO 10642)."""
    return screw(size).countersink_diameter_mm, 90.0


def heatset_insert(size: str) -> HeatsetInsert:
    """Return the heat-set insert spec that provides internal thread ``size``."""
    try:
        return HEATSET_CATALOG[size]
    except KeyError:
        raise KeyError(
            f"No heat-set insert for {size!r}. Known: {', '.join(HEATSET_CATALOG)}."
        ) from None


def bearing(name: str) -> BearingSpec:
    """Return the bearing spec for a trade number like ``"608"``."""
    try:
        return BEARING_CATALOG[name]
    except KeyError:
        raise KeyError(
            f"Unknown bearing {name!r}. Known: {', '.join(BEARING_CATALOG)}."
        ) from None


def screw_sizes() -> tuple[str, ...]:
    return tuple(SCREW_CATALOG)


def bearing_names() -> tuple[str, ...]:
    return tuple(BEARING_CATALOG)
