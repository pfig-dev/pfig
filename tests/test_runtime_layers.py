"""Canonical runtime resolution without alias expansion or input mutation."""

from collections import defaultdict

import pytest

from pfig import ExecutionError, ValidationError, compile_pfig, instantiate, parse_pfig_source
from pfig.execution import ASTCompiler, ProfileSpec, RuntimeInputLayer, resolve_runtime_layers

ORIGIN = ("base.pfig", "device")
ALIASES = {"a/device": ORIGIN, "b/device": ORIGIN}


def test_layers_resolve_before_paths_are_flattened():
    first = {"a/device": "cpu", "b/device": "cpu"}
    second = {"b/device": "cuda"}
    layers = [RuntimeInputLayer(first, "snapshot"), RuntimeInputLayer(second)]
    result = resolve_runtime_layers(ALIASES, set(ALIASES), layers)
    assert set(result) == {ORIGIN}
    assert result[ORIGIN].path == "b/device"
    assert result[ORIGIN].value == "cuda"
    assert result[ORIGIN].source == "cli"
    assert first == {"a/device": "cpu", "b/device": "cpu"}
    assert second == {"b/device": "cuda"}


@pytest.mark.parametrize(
    "values",
    [
        (1, True),
        (1, 1.0),
        ("cpu", "cuda"),
        ([1], [True]),
        ([1], (1,)),
        ({1: "cpu"}, {True: "cpu"}),
        ({(1,): "cpu"}, {(True,): "cpu"}),
    ],
)
@pytest.mark.parametrize("declared", [False, True])
def test_later_layer_cannot_hide_a_conflict(values, declared):
    with pytest.raises(ValidationError, match="Conflicting runtime assignments"):
        resolve_runtime_layers(
            ALIASES,
            set(ALIASES) if declared else set(),
            [
                RuntimeInputLayer(dict(zip(ALIASES, values))),
                RuntimeInputLayer({"a/device": "mps"}),
            ],
        )


def test_layers_validate_unknown_targets_and_profile_restrictions():
    with pytest.raises(ExecutionError, match="not found"):
        resolve_runtime_layers(ALIASES, set(ALIASES), [RuntimeInputLayer({"unknown": 1})])
    with pytest.raises(ExecutionError, match="Profiles may only override externals"):
        resolve_runtime_layers(
            ALIASES, set(), [RuntimeInputLayer({"a/device": "cpu"}, "profile:local.pfig")]
        )


@pytest.mark.parametrize("target", ["a/value", "b/value"])
@pytest.mark.parametrize("prefixed", [False, True])
def test_runtime_input_shadows_profile_through_either_alias(tmp_path, target, prefixed):
    (tmp_path / "base.pfig").write_text("value = 1\n")
    root = tmp_path / "root.pfig"
    root.write_text(
        'include "./base.pfig" as a\ninclude "./base.pfig" as b\nresult = [@a/value, @b/value]\n'
    )
    profile = tmp_path / "profile.pfig"
    profile.write_text("override value = 2\n" if prefixed else "override a/value = 2\n")
    spec = ProfileSpec(profile, prefix=("a",) if prefixed else ())

    assert instantiate("result", path=root, profile_paths=[spec], externals={target: 3}) == [3, 3]
    # The profile is invalid when its assignment remains the canonical winner.
    with pytest.raises(ExecutionError, match="Profiles may only override externals"):
        instantiate("result", path=root, profile_paths=[spec])


@pytest.mark.parametrize("source", ["cli", "profile:local.pfig", "profile-prefixed:local.pfig"])
def test_unknown_target_in_earlier_layer_is_not_dropped(source):
    with pytest.raises(ExecutionError, match="unknown"):
        resolve_runtime_layers(
            ALIASES,
            set(ALIASES),
            [
                RuntimeInputLayer({"unknown": 1}, source),
                RuntimeInputLayer({"b/device": "cpu"}),
            ],
        )


def test_winning_profile_cannot_override_an_ordinary_binding():
    with pytest.raises(ExecutionError, match="Profiles may only override externals"):
        resolve_runtime_layers(
            ALIASES,
            set(),
            [
                RuntimeInputLayer({"a/device": "cpu"}),
                RuntimeInputLayer({"b/device": "cuda"}, "profile:local.pfig"),
            ],
        )


def test_single_opaque_runtime_container_is_not_traversed():
    value = []
    value.append(value)
    result = resolve_runtime_layers(ALIASES, set(ALIASES), [RuntimeInputLayer({"a/device": value})])
    assert result[ORIGIN].value is value


def recursive_value(kind, leaf=1):
    if kind == "list":
        value = [leaf]
        value.append(value)
    elif kind == "dict":
        value = {"leaf": leaf}
        value["self"] = value
    else:
        items = [leaf]
        value = (items,)
        items.append(value)
    return value


@pytest.mark.parametrize("kind", ["list", "dict", "tuple"])
@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_recursive_runtime_aliases_materialize_once(tmp_path, kind, shared, reverse):
    first = recursive_value(kind)
    second = first if shared else recursive_value(kind)
    (tmp_path / "base.pfig").write_text("external device\n")
    root = tmp_path / "root.pfig"
    root.write_text(
        'include "./base.pfig" as a\ninclude "./base.pfig" as b\nresult = [@a/device, @b/device]\n'
    )
    entries = [("a/device", first), ("b/device", second)]
    if reverse:
        entries.reverse()
    result = instantiate("result", path=root, externals=dict(entries))
    assert result[0] is result[1] is first
    if kind == "list":
        assert first[1] is first
    elif kind == "dict":
        assert first["self"] is first
    else:
        assert first[0][1] is first


@pytest.mark.parametrize("kind", ["list", "dict", "tuple"])
@pytest.mark.parametrize("leaf", [2, True])
def test_recursive_runtime_conflicts_are_not_hidden_by_later_layers(kind, leaf):
    with pytest.raises(ValidationError, match="Conflicting runtime assignments"):
        resolve_runtime_layers(
            ALIASES,
            set(ALIASES),
            [
                RuntimeInputLayer(
                    {"a/device": recursive_value(kind), "b/device": recursive_value(kind, leaf)}
                ),
                RuntimeInputLayer({"a/device": "cpu"}),
            ],
        )


def test_runtime_comparison_does_not_invoke_opaque_equality():
    class Device:
        def __eq__(self, other):
            pytest.fail("Runtime comparison invoked user equality")

    first, second = Device(), Device()
    shared = {"device": [first]}
    result = resolve_runtime_layers(
        ALIASES,
        set(ALIASES),
        [RuntimeInputLayer({"a/device": shared, "b/device": {"device": [first]}})],
    )
    assert result[ORIGIN].value is shared
    with pytest.raises(ValidationError, match="Conflicting runtime assignments"):
        resolve_runtime_layers(
            ALIASES,
            set(ALIASES),
            [RuntimeInputLayer({"a/device": shared, "b/device": {"device": [second]}})],
        )


@pytest.mark.parametrize("second_factory", [list, int])
@pytest.mark.parametrize("reverse", [False, True])
def test_runtime_defaultdict_aliases_require_identity(tmp_path, second_factory, reverse):
    (tmp_path / "base.pfig").write_text("external device\n")
    root = tmp_path / "root.pfig"
    root.write_text(
        'include "./base.pfig" as a\ninclude "./base.pfig" as b\nresult = [@a/device, @b/device]\n'
    )
    first = defaultdict(list)
    second = defaultdict(second_factory)
    entries = [("a/device", first), ("b/device", second)]
    if reverse:
        entries.reverse()
    # Equal items do not imply equivalent objects: default_factory is meaningful
    # state, and even equal factories do not make distinct opaque objects identical.
    with pytest.raises(ValidationError, match="Conflicting runtime assignments"):
        instantiate("result", path=root, externals=dict(entries))

    result = instantiate("result", path=root, externals={"a/device": first, "b/device": first})
    assert result[0] is result[1] is first
    assert first.default_factory is list
    assert not first and not second


def test_runtime_dictionary_comparison_preserves_unordered_keys():
    first = {(1, "device"): ["cpu"], "rate": 2}
    second = {"rate": 2, (1, "device"): ["cpu"]}
    result = resolve_runtime_layers(
        ALIASES, set(ALIASES), [RuntimeInputLayer({"a/device": first, "b/device": second})]
    )
    assert result[ORIGIN].value is first


def test_cached_modules_never_contain_root_runtime_inputs(tmp_path):
    base = tmp_path / "base.pfig"
    base.write_text("external device\nvalue = 1\n")
    source = 'include "./base.pfig" as a\ninclude "./base.pfig" as b\nresult = @a/device\n'
    cache = {}
    for alias, value in [("a", "cpu"), ("b", "cuda")]:
        path = tmp_path / f"{alias}.pfig"
        inputs = {f"{alias}/device": value, f"{alias}/value": 2}
        compiler = ASTCompiler(base_path=str(path), loaded_files=cache, externals=inputs)
        compiled = compiler.compile_file(parse_pfig_source(source))
        assert compiled["runtime_inputs"] == inputs
        assert compiled["resolved_externals"] == {
            "a/device": value,
            "b/device": value,
            "a/value": 2,
            "b/value": 2,
        }
        assert compiled["values"]["a/device"] is compiled["values"]["b/device"]
        assert compiled["values"]["a/value"] is compiled["values"]["b/value"]
        assert cache[str(base)]["runtime_inputs"] == {}
        assert cache[str(base)]["values"]["value"].value == 1
        assert "device" not in cache[str(base)]["values"]


def test_runtime_resolution_does_not_hide_duplicate_external_binding():
    _, issues = compile_pfig(
        parse_pfig_source("external device\ndevice = 1\n"), externals={"device": 2}
    )
    assert any(issue.code == "duplicate_binding" for issue in issues)
