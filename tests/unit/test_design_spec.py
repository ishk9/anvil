from domain.models.design_spec import MATERIAL_LIBRARY, DesignSpec, Material


def test_every_material_has_a_profile() -> None:
    for material in Material:
        assert material in MATERIAL_LIBRARY
        assert MATERIAL_LIBRARY[material].density_g_cm3 > 0


def test_spec_resolves_its_material_profile() -> None:
    spec = DesignSpec(title="arm", summary="drone arm", material=Material.NYLON_CF)
    assert spec.material_profile().material is Material.NYLON_CF
    assert spec.material_profile().tensile_mpa > 0


def test_spec_defaults_are_empty_tuples() -> None:
    spec = DesignSpec(title="x", summary="y")
    assert spec.requirements == ()
    assert spec.load_cases == ()
    assert spec.material is Material.PETG
