import copy

import pytest
import yaml

from kinetic_agents.evaluation.artifacts import inline_yaml, MechanismInputError, failure_diagnostic


@pytest.fixture
def mechanism(tmp_path):
    ct = pytest.importorskip("cantera")
    path = tmp_path / "mechanism.yaml"
    ct.Solution("h2o2.yaml").write_yaml(str(path))
    return path, yaml.safe_load(path.read_text())


@pytest.mark.parametrize("kinetics", ["gas", "bulk"])
def test_both_names_load_unchanged_with_native_cantera(mechanism, kinetics):
    import cantera as ct
    path, data = mechanism
    data["phases"][0]["kinetics"] = kinetics
    path.write_text(yaml.safe_dump(data))
    before = path.read_bytes()
    assert inline_yaml(path)["phases"][0]["kinetics"] == kinetics
    gas = ct.Solution(str(path))
    assert gas.n_species == 10 and gas.n_reactions == 29
    assert path.read_bytes() == before


@pytest.mark.parametrize("update,code", [
    ({"kinetics": "surface"}, "unsupported_kinetics"),
    ({"kinetics": "made-up"}, "unsupported_kinetics"),
    ({"thermo": "ideal-surface"}, "unsupported_thermo"),
    ({"species": [{"elsewhere.yaml/species": "all"}]}, "external_phase_reference"),
    ({"reactions": ["elsewhere.yaml/reactions"]}, "external_phase_reference"),
    ({"adjacent-phases": ["elsewhere.yaml/gas"]}, "external_phase_reference"),
])
def test_bulk_does_not_bypass_single_inline_gas_boundary(mechanism, update, code):
    path, data = mechanism
    data["phases"][0]["kinetics"] = "bulk"
    data["phases"][0].update(update)
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(MechanismInputError) as error:
        inline_yaml(path)
    diagnostic = failure_diagnostic(error.value, "yaml_ingress")
    assert diagnostic["reason_code"] == code
    assert diagnostic["failure_stage"] == "yaml_ingress"
    assert diagnostic["numerical_solves"] == 0


@pytest.mark.parametrize("kind,code", [("phases", "phase_count"),
    ("species", "external_or_noninline_data"), ("reactions", "external_or_noninline_data"),
    ("extension", "unsupported_metadata"), ("syntax", "malformed_yaml")])
def test_unsafe_or_malformed_inputs_remain_rejected(mechanism, kind, code):
    path, data = mechanism
    if kind == "phases":
        data["phases"].append(copy.deepcopy(data["phases"][0]))
    elif kind in ("species", "reactions"):
        data[kind] = ["external.yaml/" + kind]
    else:
        data["extensions"] = [{"type": "python", "name": "untrusted"}]
    path.write_text("phases: [" if kind == "syntax" else yaml.safe_dump(data))
    with pytest.raises(MechanismInputError) as error:
        inline_yaml(path)
    assert error.value.code == code


def test_native_chemistry_errors_are_not_renamed_as_ingress_errors(mechanism):
    import cantera as ct
    path, data = mechanism
    data["phases"][0]["kinetics"] = "bulk"
    data["reactions"][0]["equation"] = "H2 + O <=> OH"  # deliberately unbalanced
    path.write_text(yaml.safe_dump(data))
    inline_yaml(path)
    with pytest.raises(ct.CanteraError) as error:
        ct.Solution(str(path))
    detail = failure_diagnostic(error.value, "cantera_load")
    assert detail["reason_code"] == "native_load_failed"
    assert detail["failure_stage"] == "cantera_load"
    assert "H2 + O" not in detail["message"]


def test_symlink_not_permitted(mechanism):
    path, _ = mechanism
    link = path.with_name("link.yaml")
    link.symlink_to(path)
    with pytest.raises(PermissionError):
        inline_yaml(link)


def test_header_annotations_do_not_change_native_chemistry_or_open_paths(mechanism):
    import cantera as ct
    import numpy as np
    path, data = mechanism
    reference = ct.Solution(str(path))
    data.update(design="Subset without parameter fitting", parent="nonexistent/parent.yaml")
    data["phases"][0]["kinetics"] = "bulk"
    path.write_text(yaml.safe_dump(data))
    before = path.read_bytes()
    inline_yaml(path)
    actual = ct.Solution(str(path))
    assert actual.species_names == reference.species_names
    assert [s.input_data for s in actual.species()] == [s.input_data for s in reference.species()]
    assert [r.input_data for r in actual.reactions()] == [r.input_data for r in reference.reactions()]
    for temperature in (900, 1500, 2000):
        for gas in (actual, reference):
            gas.TPX = temperature, ct.one_atm, "H2:2,O2:1,N2:3"
        np.testing.assert_array_equal(actual.net_rates_of_progress, reference.net_rates_of_progress)
        np.testing.assert_array_equal(actual.net_production_rates, reference.net_production_rates)
    assert path.read_bytes() == before


@pytest.mark.parametrize("annotation", [{"path": "external.yaml"}, ["external.yaml"], None, "x" * 4097])
def test_annotations_cannot_be_structured_or_unbounded(mechanism, annotation):
    path, data = mechanism
    data["parent"] = annotation
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(MechanismInputError, match="bounded plain text"):
        inline_yaml(path)
