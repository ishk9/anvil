"""Generators that emit build123d code snippets for standard hardware features.

CAD runs in an isolated process (``infrastructure/cad/sandbox_runner.py``), so this
module never touches build123d itself. Instead each function is a pure string builder
that returns valid *algebra-mode* build123d Python operating on a variable named
``part`` (millimetres), matching the contract the sandbox binds.

The returned snippets:
- assume the sandbox namespace (all build123d names are imported implicitly, so we
  write ``Cylinder``, ``Pos``, ``Location`` bare, exactly like the ``build_part`` tool);
- subtract material from ``part`` by reassigning ``part = part - <feature>``;
- place features at a caller-supplied ``(x, y, z)`` on a face whose outward normal is
  +Z by default (the common "top of a plate" case), which the caller can rotate.

Threads use ``bd_warehouse.thread.IsoThread`` — the ISO 60-degree metric profile.
``bd_warehouse`` is a build123d extension and must be installed in the sandbox image
(see the integration spec). The generated import is explicit so a missing dependency
fails loudly with a clear ``ModuleNotFoundError`` rather than silently.
"""

from __future__ import annotations

from domain.models.hardware import Fit, bearing, counterbore, countersink, heatset_insert, screw


def _fmt(value: float) -> str:
    """Render a float without a trailing ``.0`` explosion but keep it a valid literal."""
    return repr(round(float(value), 4))


def _pos(x: float, y: float, z: float) -> str:
    return f"Pos({_fmt(x)}, {_fmt(y)}, {_fmt(z)})"


def clearance_hole(
    size: str,
    *,
    depth_mm: float,
    at: tuple[float, float, float] = (0.0, 0.0, 0.0),
    fit: Fit = Fit.NORMAL,
) -> str:
    """Snippet: drill a through/blind clearance hole so a screw of ``size`` passes.

    The hole is bored downward (-Z) from ``at``; give ``depth_mm`` >= wall thickness
    for a through hole.
    """
    s = screw(size)
    d = s.clearance(fit)
    return (
        f"# {size} clearance hole (ISO 273 {fit.value}, dia {_fmt(d)} mm)\n"
        f"part = part - {_pos(*at)} * Cylinder("
        f"radius={_fmt(d / 2)}, height={_fmt(depth_mm)}, "
        f"align=(Align.CENTER, Align.CENTER, Align.MAX))"
    )


def counterbored_hole(
    size: str,
    *,
    depth_mm: float,
    at: tuple[float, float, float] = (0.0, 0.0, 0.0),
    fit: Fit = Fit.NORMAL,
) -> str:
    """Snippet: clearance hole plus a flat counterbore that sinks a socket-cap head flush.

    The counterbore is cut from the top face at ``at`` (head recesses into -Z); the
    clearance shank continues for ``depth_mm``.
    """
    s = screw(size)
    d = s.clearance(fit)
    cb_dia, cb_depth = counterbore(size)
    return (
        f"# {size} counterbored hole (shank {_fmt(d)} mm, "
        f"counterbore {_fmt(cb_dia)} x {_fmt(cb_depth)} mm, ISO 4762 head)\n"
        f"part = part - {_pos(*at)} * Cylinder("
        f"radius={_fmt(d / 2)}, height={_fmt(depth_mm)}, "
        f"align=(Align.CENTER, Align.CENTER, Align.MAX))\n"
        f"part = part - {_pos(*at)} * Cylinder("
        f"radius={_fmt(cb_dia / 2)}, height={_fmt(cb_depth)}, "
        f"align=(Align.CENTER, Align.CENTER, Align.MAX))"
    )


def countersunk_hole(
    size: str,
    *,
    depth_mm: float,
    at: tuple[float, float, float] = (0.0, 0.0, 0.0),
    fit: Fit = Fit.NORMAL,
) -> str:
    """Snippet: clearance hole plus a 90-degree conical countersink for a flat head.

    Built as a truncated cone (``CounterSinkHole``'s recess) fused with the shank,
    cut from ``part``. The cone opens at the top face and narrows to the shank.
    """
    s = screw(size)
    shank = s.clearance(fit)
    cs_dia, angle = countersink(size)
    # Cone height so the flare reaches shank radius at the given 90 deg included angle:
    # half-angle = 45 deg -> height = (cs_radius - shank_radius) / tan(45) = radius delta.
    cone_h = (cs_dia - shank) / 2.0
    return (
        f"# {size} countersunk hole (flat head {_fmt(cs_dia)} mm, "
        f"{_fmt(angle)} deg incl., ISO 10642)\n"
        f"part = part - {_pos(*at)} * Cylinder("
        f"radius={_fmt(shank / 2)}, height={_fmt(depth_mm)}, "
        f"align=(Align.CENTER, Align.CENTER, Align.MAX))\n"
        f"part = part - {_pos(*at)} * Cone("
        f"bottom_radius={_fmt(shank / 2)}, top_radius={_fmt(cs_dia / 2)}, "
        f"height={_fmt(cone_h)}, align=(Align.CENTER, Align.CENTER, Align.MAX))"
    )


def heatset_pocket(
    size: str, *, at: tuple[float, float, float] = (0.0, 0.0, 0.0)
) -> str:
    """Snippet: a blind pocket for a brass heat-set insert of thread ``size``.

    Bored downward from ``at``; sized to the insert's printed pocket bore/depth.
    """
    ins = heatset_insert(size)
    return (
        f"# {size} heat-set insert pocket "
        f"(bore {_fmt(ins.pocket_diameter_mm)} x {_fmt(ins.pocket_depth_mm)} mm)\n"
        f"part = part - {_pos(*at)} * Cylinder("
        f"radius={_fmt(ins.pocket_diameter_mm / 2)}, height={_fmt(ins.pocket_depth_mm)}, "
        f"align=(Align.CENTER, Align.CENTER, Align.MAX))"
    )


def bearing_pocket(
    name: str, *, at: tuple[float, float, float] = (0.0, 0.0, 0.0), press_fit: bool = True
) -> str:
    """Snippet: a seat for a deep-groove ball bearing ``name``.

    With ``press_fit`` the bore is cut ``press_fit_clearance_mm`` *under* the bearing
    OD for a light interference fit; otherwise it is cut at nominal OD (slip fit).
    The seat is the bearing's full width, bored downward from ``at``.
    """
    b = bearing(name)
    bore = b.outer_diameter_mm - (b.press_fit_clearance_mm if press_fit else 0.0)
    fit_note = "press fit" if press_fit else "slip fit"
    return (
        f"# {name} bearing seat (OD {_fmt(b.outer_diameter_mm)} mm, "
        f"bore {_fmt(bore)} mm {fit_note}, width {_fmt(b.width_mm)} mm)\n"
        f"part = part - {_pos(*at)} * Cylinder("
        f"radius={_fmt(bore / 2)}, height={_fmt(b.width_mm)}, "
        f"align=(Align.CENTER, Align.CENTER, Align.MAX))"
    )


def tapped_thread(
    size: str,
    *,
    length_mm: float,
    at: tuple[float, float, float] = (0.0, 0.0, 0.0),
    external: bool = False,
) -> str:
    """Snippet: a real, printable ISO metric thread via ``bd_warehouse.thread.IsoThread``.

    ``external=False`` (default) cuts an *internal* thread: a tap-drill hole is bored,
    then the internal thread solid is fused in — the standard way to model a tapped
    hole or a printed nut. ``external=True`` builds an external thread and fuses it
    onto ``part`` (a threaded stud/bolt shank of radius = major/2).

    ``IsoThread`` derives the profile from ``major_diameter`` and ``pitch``; we pull
    both from the catalog so the thread matches the same ``size`` used elsewhere.
    ``interference`` (its default 0.2 mm) lets the thread fuse cleanly with the core.
    """
    s = screw(size)
    header = (
        f"# {size} {'external' if external else 'internal'} ISO thread "
        f"(major {_fmt(s.major_diameter_mm)} mm, pitch {_fmt(s.pitch_mm)} mm, "
        f"len {_fmt(length_mm)} mm)\n"
        "from bd_warehouse.thread import IsoThread  # requires bd_warehouse in the sandbox\n"
    )
    thread = (
        "IsoThread(\n"
        f"    major_diameter={_fmt(s.major_diameter_mm)},\n"
        f"    pitch={_fmt(s.pitch_mm)},\n"
        f"    length={_fmt(length_mm)},\n"
        f"    external={external!r},\n"
        '    end_finishes=("fade", "fade"),\n'
        ")"
    )
    if external:
        # Stud core at minor-ish radius, then the external thread fused on top.
        core_r = s.major_diameter_mm / 2 - s.pitch_mm * (5 / 8) * 0.866
        return (
            header
            + f"part = part + {_pos(*at)} * Cylinder("
            f"radius={_fmt(core_r)}, height={_fmt(length_mm)}, "
            "align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
            + f"part = part + {_pos(*at)} * {thread}"
        )
    # Internal: bore the tap-drill clearance, then fuse the internal thread in.
    return (
        header
        + f"part = part - {_pos(*at)} * Cylinder("
        f"radius={_fmt(s.tap_drill_mm / 2)}, height={_fmt(length_mm)}, "
        "align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
        + f"part = part + {_pos(*at)} * {thread}"
    )
