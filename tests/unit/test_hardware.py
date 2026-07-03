"""Unit tests for the hardware catalog and code-snippet generators.

No CAD stack is imported: the generators are pure string builders, so we assert on
their *syntactic* validity with ``ast.parse`` and on the catalog math directly.
"""

from __future__ import annotations

import ast

import pytest

from application.hardware_formatting import format_hardware_catalog
from application.hardware_toolkit import (
    bearing_pocket,
    clearance_hole,
    counterbored_hole,
    countersunk_hole,
    heatset_pocket,
    tapped_thread,
)
from domain.models.hardware import (
    BEARING_CATALOG,
    SCREW_CATALOG,
    Fit,
    bearing,
    clearance_hole_mm,
    counterbore,
    countersink,
    heatset_insert,
    screw,
    tap_drill_mm,
)

# --- catalog lookups ----------------------------------------------------------


def test_screw_lookup_returns_expected_geometry() -> None:
    m3 = screw("M3")
    assert m3.major_diameter_mm == 3.0
    assert m3.pitch_mm == 0.5


def test_unknown_screw_raises_with_known_sizes_listed() -> None:
    with pytest.raises(KeyError, match="M3"):
        screw("M99")


def test_every_screw_clearance_is_larger_than_its_major_diameter() -> None:
    for s in SCREW_CATALOG.values():
        for fit in Fit:
            assert s.clearance(fit) > s.major_diameter_mm


def test_clearance_widens_from_close_to_coarse() -> None:
    for s in SCREW_CATALOG.values():
        assert s.clearance_close_mm < s.clearance_normal_mm < s.clearance_coarse_mm


def test_clearance_helper_matches_fit() -> None:
    assert clearance_hole_mm("M3", Fit.CLOSE) == SCREW_CATALOG["M3"].clearance_close_mm
    assert clearance_hole_mm("M3") == SCREW_CATALOG["M3"].clearance_normal_mm


# --- clearance / tap math -----------------------------------------------------


def test_tap_drill_tracks_major_minus_pitch() -> None:
    # Tap drill ~= major - pitch (ISO coarse). Catalog values are the nearest
    # standard drill, so allow rounding up to the next common size (M8 -> 6.8 not 6.75).
    for s in SCREW_CATALOG.values():
        nominal = s.major_diameter_mm - s.pitch_mm
        assert nominal <= tap_drill_mm(s.designation) <= nominal + 0.1


def test_tap_drill_is_smaller_than_clearance() -> None:
    for size in SCREW_CATALOG:
        assert tap_drill_mm(size) < clearance_hole_mm(size)


def test_counterbore_fits_a_socket_cap_head() -> None:
    dia, depth = counterbore("M3")
    assert dia > SCREW_CATALOG["M3"].major_diameter_mm
    assert depth > 0


def test_countersink_is_ninety_degrees() -> None:
    _, angle = countersink("M4")
    assert angle == 90.0


def test_heatset_pocket_is_near_insert_outer_diameter() -> None:
    ins = heatset_insert("M3")
    assert ins.pocket_diameter_mm <= ins.outer_diameter_mm
    assert ins.pocket_depth_mm >= ins.length_mm


def test_bearing_lookup() -> None:
    b608 = bearing("608")
    assert (b608.bore_mm, b608.outer_diameter_mm, b608.width_mm) == (8.0, 22.0, 7.0)


def test_bearing_outer_diameter_exceeds_bore() -> None:
    for b in BEARING_CATALOG.values():
        assert b.outer_diameter_mm > b.bore_mm > 0
        assert b.width_mm > 0


# --- generator output is valid python -----------------------------------------

_AT = (5.0, -3.0, 2.0)


def _assert_parses(code: str) -> None:
    ast.parse(code)  # raises SyntaxError on malformed output


def test_all_generators_emit_parseable_code() -> None:
    snippets = [
        clearance_hole("M3", depth_mm=4.0, at=_AT),
        clearance_hole("M5", depth_mm=6.0, fit=Fit.COARSE),
        counterbored_hole("M4", depth_mm=10.0, at=_AT),
        countersunk_hole("M3", depth_mm=5.0, at=_AT),
        heatset_pocket("M3", at=_AT),
        bearing_pocket("608", at=_AT),
        bearing_pocket("623", press_fit=False),
        tapped_thread("M6", length_mm=12.0, at=_AT),
        tapped_thread("M8", length_mm=20.0, external=True),
    ]
    for snippet in snippets:
        _assert_parses(snippet)


def test_generators_operate_on_the_part_contract_variable() -> None:
    # Every generator must reassign `part` so it slots into the sandbox contract.
    for snippet in (
        clearance_hole("M2", depth_mm=3.0),
        counterbored_hole("M3", depth_mm=5.0),
        countersunk_hole("M4", depth_mm=6.0),
        heatset_pocket("M5"),
        bearing_pocket("626"),
        tapped_thread("M3", length_mm=6.0),
    ):
        assert "part =" in snippet


def test_internal_thread_bores_tap_drill_and_imports_isothread() -> None:
    code = tapped_thread("M6", length_mm=12.0)
    assert "IsoThread" in code
    assert "bd_warehouse.thread" in code
    # Internal thread subtracts the tap-drill hole and adds the thread solid.
    assert "part = part -" in code
    assert "part = part +" in code


def test_external_thread_adds_material() -> None:
    code = tapped_thread("M8", length_mm=20.0, external=True)
    assert "external=True" in code
    assert "part = part +" in code


def test_snippet_dimensions_track_the_catalog() -> None:
    # A clearance hole's radius must be half the ISO 273 normal clearance.
    code = clearance_hole("M3", depth_mm=4.0)
    expected_radius = SCREW_CATALOG["M3"].clearance_normal_mm / 2
    assert repr(round(expected_radius, 4)) in code


# --- formatting ---------------------------------------------------------------


def test_catalog_formatting_mentions_every_family() -> None:
    text = format_hardware_catalog()
    assert "M3" in text
    assert "608" in text
    assert "heat-set" in text
    assert "Tap drill" in text
