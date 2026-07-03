"""Unit tests for assemblies: BOM math, mate resolution, and interference detection.

No build123d is imported. Collision is exercised with trimesh box primitives, and the
generated assembly script is checked for syntactic validity with ``ast.parse``.
"""

from __future__ import annotations

import ast

import pytest
import trimesh

from application.assembly_toolkit import AssemblyToolkit
from domain.models.assembly import (
    Assembly,
    Mate,
    MateKind,
    PartInstance,
    build_bom,
    part_mass_g,
    resolve_locations,
)
from domain.models.design_spec import MATERIAL_LIBRARY, Material

# --- construction / invariants ------------------------------------------------


def test_instance_requires_exactly_one_source() -> None:
    with pytest.raises(ValueError, match="exactly one of artifact_id"):
        PartInstance(instance_id="a", name="p", material=Material.PLA)
    with pytest.raises(ValueError, match="exactly one of artifact_id"):
        PartInstance(
            instance_id="a", name="p", material=Material.PLA, artifact_id="x", code_ref="y"
        )


def test_instance_rejects_zero_quantity() -> None:
    with pytest.raises(ValueError, match="quantity must be >= 1"):
        PartInstance(instance_id="a", name="p", material=Material.PLA, code_ref="c", quantity=0)


# --- BOM math -----------------------------------------------------------------


def _inst(iid: str, name: str, material: Material, qty: int = 1) -> PartInstance:
    return PartInstance(
        instance_id=iid, name=name, material=material, code_ref=f"code_{iid}", quantity=qty
    )


def test_bom_multiplies_quantity_by_unit_mass_and_totals() -> None:
    asm = Assembly(
        assembly_id="asm1",
        title="bracket",
        instances=(
            _inst("i1", "plate", Material.PLA, qty=2),
            _inst("i2", "standoff", Material.ALU_6061, qty=4),
        ),
    )
    bom = build_bom(asm, {"i1": 10.0, "i2": 2.5})

    assert bom.total_mass_g == pytest.approx(2 * 10.0 + 4 * 2.5)
    by_name = {line.part_name: line for line in bom.lines}
    assert by_name["plate"].quantity == 2
    assert by_name["plate"].mass_g == pytest.approx(20.0)
    assert by_name["standoff"].quantity == 4
    assert by_name["standoff"].mass_g == pytest.approx(10.0)


def test_bom_groups_same_name_and_material() -> None:
    asm = Assembly(
        assembly_id="asm",
        title="t",
        instances=(
            _inst("a", "screw", Material.STEEL_MILD, qty=3),
            _inst("b", "screw", Material.STEEL_MILD, qty=5),
        ),
    )
    bom = build_bom(asm, {"a": 1.0, "b": 1.0})
    assert len(bom.lines) == 1
    assert bom.lines[0].quantity == 8
    assert bom.lines[0].mass_g == pytest.approx(8.0)


def test_bom_missing_mass_contributes_zero_but_keeps_line() -> None:
    asm = Assembly(assembly_id="a", title="t", instances=(_inst("x", "part", Material.PETG),))
    bom = build_bom(asm, {})
    assert len(bom.lines) == 1
    assert bom.lines[0].mass_g == 0.0


def test_part_mass_matches_density_formula() -> None:
    # 1000 mm^3 == 1 cm^3, so mass == density.
    assert part_mass_g(1000.0, Material.PLA) == pytest.approx(
        MATERIAL_LIBRARY[Material.PLA].density_g_cm3
    )


# --- mate resolution ----------------------------------------------------------


def test_offset_mate_places_b_relative_to_a() -> None:
    asm = Assembly(
        assembly_id="a",
        title="t",
        instances=(
            PartInstance("base", "base", Material.PLA, location=(10.0, 0.0, 0.0), code_ref="c"),
            PartInstance("arm", "arm", Material.PLA, code_ref="c"),
        ),
        mates=(
            Mate(MateKind.OFFSET, "base", "arm", params=(("dx", 5.0), ("dz", 2.0))),
        ),
    )
    placement = resolve_locations(asm)
    assert placement["arm"][0] == (15.0, 0.0, 2.0)


def test_rigid_mate_copies_anchor_frame() -> None:
    asm = Assembly(
        assembly_id="a",
        title="t",
        instances=(
            PartInstance(
                "a", "a", Material.PLA, location=(1.0, 2.0, 3.0), rotation=(0.0, 90.0, 0.0),
                code_ref="c",
            ),
            PartInstance("b", "b", Material.PLA, code_ref="c"),
        ),
        mates=(Mate(MateKind.RIGID, "a", "b"),),
    )
    placement = resolve_locations(asm)
    assert placement["b"] == ((1.0, 2.0, 3.0), (0.0, 90.0, 0.0))


def test_concentric_mate_slides_b_along_z() -> None:
    asm = Assembly(
        assembly_id="a",
        title="t",
        instances=(
            PartInstance("hub", "hub", Material.PLA, location=(0.0, 0.0, 5.0), code_ref="c"),
            PartInstance("shaft", "shaft", Material.PLA, code_ref="c"),
        ),
        mates=(Mate(MateKind.CONCENTRIC, "hub", "shaft", params=(("offset", 4.0),)),),
    )
    placement = resolve_locations(asm)
    assert placement["shaft"][0] == (0.0, 0.0, 9.0)


# --- code generation ----------------------------------------------------------


def test_compose_code_is_valid_python_and_binds_part() -> None:
    # compose_code / interference_report never touch the DesignToolkit, so None is safe here.
    toolkit = AssemblyToolkit(toolkit=None)  # type: ignore[arg-type]
    asm = Assembly(
        assembly_id="a",
        title="t",
        instances=(
            PartInstance("plate", "plate", Material.PLA, code_ref="box", location=(0.0, 0.0, 0.0)),
            PartInstance("post", "post", Material.PLA, code_ref="cyl", location=(0.0, 0.0, 4.0)),
        ),
    )
    code = toolkit.compose_code(
        asm,
        {"box": "part = Box(10, 10, 4)", "cyl": "part = Cylinder(radius=2, height=8)"},
    )
    ast.parse(code)  # raises SyntaxError on malformed output
    assert "part = _instances[0]" in code
    assert "Pos(0.0, 0.0, 4.0)" in code


def test_compose_code_rejects_artifact_only_instance() -> None:
    toolkit = AssemblyToolkit(toolkit=None)  # type: ignore[arg-type]
    asm = Assembly(
        assembly_id="a",
        title="t",
        instances=(PartInstance("p", "p", Material.PLA, artifact_id="part_001"),),
    )
    with pytest.raises(ValueError, match="no code_ref"):
        toolkit.compose_code(asm, {})


# --- interference (trimesh) ---------------------------------------------------


def _box_instance(iid: str, location: tuple[float, float, float]) -> PartInstance:
    return PartInstance(iid, iid, Material.PLA, location=location, code_ref="box")


def test_overlapping_boxes_are_reported_with_positive_overlap() -> None:
    toolkit = AssemblyToolkit(toolkit=None)  # type: ignore[arg-type]
    asm = Assembly(
        assembly_id="a",
        title="t",
        instances=(
            _box_instance("a", (0.0, 0.0, 0.0)),
            _box_instance("b", (5.0, 0.0, 0.0)),  # 10mm box centred at 5 -> overlaps 0-centred box
        ),
    )
    meshes = {
        "a": trimesh.creation.box(extents=(10, 10, 10)),
        "b": trimesh.creation.box(extents=(10, 10, 10)),
    }
    report = toolkit.interference_report(asm, meshes)
    assert not report.clear
    assert len(report.collisions) == 1
    col = report.collisions[0]
    assert (col.instance_a, col.instance_b) == ("a", "b")
    assert col.overlap_volume_mm3 is not None
    assert col.overlap_volume_mm3 == pytest.approx(5 * 10 * 10, rel=0.05)


def test_separated_boxes_do_not_collide() -> None:
    toolkit = AssemblyToolkit(toolkit=None)  # type: ignore[arg-type]
    asm = Assembly(
        assembly_id="a",
        title="t",
        instances=(
            _box_instance("a", (0.0, 0.0, 0.0)),
            _box_instance("b", (50.0, 0.0, 0.0)),
        ),
    )
    meshes = {
        "a": trimesh.creation.box(extents=(10, 10, 10)),
        "b": trimesh.creation.box(extents=(10, 10, 10)),
    }
    report = toolkit.interference_report(asm, meshes)
    assert report.clear
    assert report.collisions == ()


def test_missing_mesh_is_skipped_not_crashed() -> None:
    toolkit = AssemblyToolkit(toolkit=None)  # type: ignore[arg-type]
    asm = Assembly(
        assembly_id="a",
        title="t",
        instances=(_box_instance("a", (0.0, 0.0, 0.0)), _box_instance("b", (0.0, 0.0, 0.0))),
    )
    report = toolkit.interference_report(asm, {"a": trimesh.creation.box(extents=(10, 10, 10))})
    assert report.skipped_instances == ("b",)
    assert report.clear
