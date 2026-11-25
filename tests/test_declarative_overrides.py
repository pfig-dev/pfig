"""Canonical overrides must not depend on alias or construction order."""

from dataclasses import dataclass, replace

import pytest

from pfig import ValidationError, compile_pfig, instantiate, parse_pfig_source
from pfig.execution import ASTCompiler, get_materialized_bindings
from pfig.snapshot import build_ir_graph, compute_graph_digest


def diamond(tmp_path, overrides, reverse=False):
    (tmp_path / "values.pfig").write_text("width = 1\n")
    (tmp_path / "geometry.pfig").write_text(
        'include "./values.pfig" as values\nconfig = dict(width=@values/width)\n'
    )
    (tmp_path / "backbone.pfig").write_text(
        'include "./geometry.pfig" as geometry\nconfig = @geometry/config\n'
    )
    refs = ['"direct": @geometry/config', '"nested": @backbone/config']
    if reverse:
        refs.reverse()
    root = tmp_path / "train.pfig"
    root.write_text(
        'include "./geometry.pfig" as geometry\n'
        'include "./backbone.pfig" as backbone\n'
        + overrides
        + "\nresult = {"
        + ", ".join(refs)
        + ', "a": @geometry/values/width, "b": @backbone/geometry/values/width}\n'
    )
    return root


@pytest.mark.parametrize("reverse", [False, True])
def test_diamond_override(tmp_path, reverse):
    root = diamond(tmp_path, "override geometry/values/width = 2", reverse)
    result = instantiate("result", path=root)
    assert result["direct"] is result["nested"]
    assert result["direct"] == {"width": 2}
    assert result["a"] == result["b"] == 2


@pytest.mark.parametrize("target", ["geometry/values/width", "backbone/geometry/values/width"])
@pytest.mark.parametrize("second", [2, 4])
def test_duplicate_root_overrides(tmp_path, target, second):
    root = diamond(tmp_path, f"override geometry/values/width = 2\noverride {target} = {second}")
    with pytest.raises(ValidationError, match="Conflicting overrides") as error:
        instantiate("result", path=root)
    assert "geometry/values/width" in str(error.value)
    assert target in str(error.value)
    assert f"{root}:3:" in str(error.value)
    assert f"{root}:4:" in str(error.value)


@pytest.mark.parametrize("with_filename", [False, True])
def test_root_ancestor_precedence_with_optional_filename(tmp_path, monkeypatch, with_filename):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "base.pfig").write_text("value = 1\noverride value = 2\n")
    source = 'include "./base.pfig" as base\noverride base/value = 3\nresult = @base/value\n'
    filename = str(tmp_path / "root.pfig") if with_filename else None
    ast = parse_pfig_source(source)
    compiled, issues = compile_pfig(ast, filename=filename)
    assert not issues
    assert compiled["values"]["base/value"].value == 3
    assert get_materialized_bindings(ast, entrypoint="result", filename=filename)["result"] == 3


def test_parent_cannot_supply_ambient_reference(tmp_path):
    (tmp_path / "child.pfig").write_text("config = dict(width=@ambient)\n")
    root = tmp_path / "root.pfig"
    root.write_text('include "./child.pfig" as child\nambient = 3\nresult = @child/config\n')
    with pytest.raises(ValidationError, match="explicit include|Unknown binding"):
        instantiate("result", path=root)


@pytest.mark.parametrize("resolve", [False, True])
@pytest.mark.parametrize("second", [2, 3])
@pytest.mark.parametrize("reverse", [False, True])
def test_sibling_conflict_requires_ancestor(tmp_path, resolve, second, reverse):
    (tmp_path / "base.pfig").write_text("value = 1\n")
    for name, value in [("left", 2), ("right", second)]:
        (tmp_path / f"{name}.pfig").write_text(
            f'include "./base.pfig" as base\noverride base/value = {value}\n'
        )
    root = tmp_path / "root.pfig"
    includes = ['include "./left.pfig" as left', 'include "./right.pfig" as right']
    if reverse:
        includes.reverse()
    root.write_text(
        "\n".join(includes)
        + "\n"
        + ("override left/base/value = 4\n" if resolve else "")
        + "result = [@left/base/value, @right/base/value]\n"
    )
    if resolve:
        assert instantiate("result", path=root) == [4, 4]
    else:
        with pytest.raises(ValidationError, match="Conflicting overrides"):
            instantiate("result", path=root)


@pytest.mark.parametrize("replacement", ["2", "dict(width=2)"])
def test_replacement_shared_and_fresh(tmp_path, replacement):
    (tmp_path / "base.pfig").write_text("value = 1\ndep = dict()\n")
    root = tmp_path / "root.pfig"
    root.write_text(
        'include "./base.pfig" as a\ninclude "./base.pfig" as b\n'
        f"override a/value = {replacement}\n"
        "result = [@a/value, @b/value]\n"
    )
    result = instantiate("result", path=root)
    assert result[0] == result[1]
    if replacement.startswith("dict"):
        assert result[0] is result[1]
        root.write_text(
            root.read_text().replace(
                "result = [@a/value, @b/value]",
                "result = [@a/value(), @b/value()]",
            )
        )
        result = instantiate("result", path=root)
        assert result == [{"width": 2}, {"width": 2}]
        assert result[0] is not result[1]


@pytest.mark.parametrize("replacement", ['{"width": 2}', "[2]", "(2,)", "@template(width=2)"])
@pytest.mark.parametrize("reverse", [False, True])
def test_provider_to_value_override_shares_identity(tmp_path, replacement, reverse):
    (tmp_path / "base.pfig").write_text("obj = dict(width=1)\n")
    root = tmp_path / "root.pfig"
    references = ["@a/obj", "@b/obj"]
    if reverse:
        references.reverse()
    source = (
        'include "./base.pfig" as a\ninclude "./base.pfig" as b\n'
        f"template = dict()\noverride a/obj = {replacement}\n"
        f"result = [{', '.join(references)}]\n"
    )
    bindings = get_materialized_bindings(
        parse_pfig_source(source), entrypoint="result", filename=str(root)
    )
    first, second = bindings["result"]
    assert first is second is bindings["a/obj"] is bindings["b/obj"]


def test_fresh_value_override_constructs_once_per_shared_binding(tmp_path, monkeypatch):
    from types import SimpleNamespace

    calls = []

    def build(dep, width):
        calls.append(width)
        return SimpleNamespace(dep=dep, width=width)

    monkeypatch.setattr("pfig.helpers._test_value", build, raising=False)
    (tmp_path / "base.pfig").write_text("obj = dict()\n")
    root = tmp_path / "root.pfig"
    root.write_text(
        'include "./base.pfig" as a\ninclude "./base.pfig" as b\n'
        "from pfig.helpers import _test_value\n"
        "dep = dict()\ntemplate = _test_value(dep=@dep, width=1)\n"
        "override a/obj = @template(width=2)\n"
        "result = [@a/obj, @b/obj, @b/obj.dep, @a/obj(), @b/obj(width=3)]\n"
    )
    first, second, dep, fresh_a, fresh_b = instantiate("result", path=root)
    assert first is second
    assert fresh_a is not first and fresh_b is not first and fresh_a is not fresh_b
    assert first.dep is fresh_a.dep is fresh_b.dep is dep
    assert first.width == fresh_a.width == 2
    assert fresh_b.width == 3
    assert sorted(calls) == [2, 2, 3]


@pytest.mark.parametrize(
    "expression",
    [
        "(@unknown,)",
        "[@unknown]",
        '{"x": @unknown}',
        "@unknown + 1",
        "-@unknown",
        "@unknown.attr",
        "@unknown(x=1)",
    ],
)
def test_lexical_scope_in_nested_expressions(expression):
    _, issues = compile_pfig(parse_pfig_source(f"result = {expression}"))
    assert any(i.code == "unknown_binding" for i in issues)


@pytest.mark.parametrize("target", ["a/value", "b/value"])
@pytest.mark.parametrize("reverse", [False, True])
def test_equivalent_reference_overrides_are_duplicates(tmp_path, target, reverse):
    (tmp_path / "base.pfig").write_text("value = 1\nother = 3\n")
    overrides = ["override a/value = @a/other", f"override {target} = @b/other"]
    if reverse:
        overrides.reverse()
    root = tmp_path / "root.pfig"
    root.write_text(
        'include "./base.pfig" as a\ninclude "./base.pfig" as b\n'
        + "\n".join(overrides)
        + "\nresult = [@a/value, @b/value]\n"
    )
    with pytest.raises(ValidationError, match="Conflicting overrides"):
        instantiate("result", path=root)


@pytest.mark.parametrize("second", ["OrderedDict(a=1, b=2)", "OrderedDict(b=2, a=1)"])
@pytest.mark.parametrize("reverse", [False, True])
def test_ordered_dictionary_overrides_are_duplicates(second, reverse):
    overrides = [
        "override value = OrderedDict(a=1, b=2)",
        f"override value = {second}",
    ]
    if reverse:
        overrides.reverse()
    source = "from collections import OrderedDict\nvalue = 0\n"

    # A single recipe must retain keyword order; competing declarations must
    # never be accepted by comparing unordered keyword dictionaries.
    result = instantiate("value", source=source + overrides[0])
    expected_keys = ["b", "a"] if overrides[0].endswith("(b=2, a=1)") else ["a", "b"]
    assert list(result) == expected_keys
    with pytest.raises(ValidationError, match="Conflicting overrides"):
        instantiate("value", source=source + "\n".join(overrides))


def test_diamond_reuses_one_authored_override(tmp_path):
    (tmp_path / "base.pfig").write_text("value = 1\n")
    (tmp_path / "middle.pfig").write_text(
        'include "./base.pfig" as base\ntemplate = dict(width=2)\noverride base/value = @template\n'
    )
    root = tmp_path / "root.pfig"
    includes = [
        'include "./middle.pfig" as a',
        'include "./middle.pfig" as b',
        'include "./base.pfig" as direct',
    ]
    cache = {}
    digests = set()
    for statements in [includes, includes[::-1], includes]:
        source = (
            "\n".join(statements) + "\nresult = [@a/base/value, @b/base/value, @direct/value]\n"
        )
        ast = parse_pfig_source(source, filename=str(root))
        compiler = ASTCompiler(base_path=str(root), loaded_files=cache)
        compiled = compiler.compile_file(ast)
        assert not compiler.validation_issues
        digests.add(compute_graph_digest(build_ir_graph(compiled)))
        result = get_materialized_bindings(ast, entrypoint="result", filename=str(root))["result"]
        assert result[0] is result[1] is result[2]
        assert result[0] == {"width": 2}
    assert len(digests) == 1


@pytest.mark.parametrize("second", ["True", "1.0"])
def test_literal_type_conflicts(second):
    with pytest.raises(ValidationError, match="Conflicting overrides"):
        instantiate("value", source=f"value = 0\noverride value = 1\noverride value = {second}")


@pytest.mark.parametrize("second", [2, 3])
def test_ancestor_cannot_hide_same_file_conflict(tmp_path, second):
    (tmp_path / "base.pfig").write_text(
        f"value = 1\noverride value = 2\noverride value = {second}\n"
    )
    root = tmp_path / "root.pfig"
    root.write_text('include "./base.pfig" as base\noverride base/value = 4\nresult = 0\n')
    with pytest.raises(ValidationError, match="Conflicting overrides"):
        instantiate("result", path=root)


@pytest.mark.parametrize("left_value", [3, 4])
def test_deeper_sibling_has_no_extra_precedence(tmp_path, left_value):
    (tmp_path / "base.pfig").write_text("value = 1\n")
    (tmp_path / "deep.pfig").write_text('include "./base.pfig" as base\noverride base/value = 3\n')
    (tmp_path / "right.pfig").write_text('include "./deep.pfig" as deep\n')
    root = tmp_path / "root.pfig"
    root.write_text(
        'include "./base.pfig" as base\ninclude "./right.pfig" as right\n'
        "override base/value = 4\nresult = [@base/value, @right/deep/base/value]\n"
    )
    assert instantiate("result", path=root) == [4, 4]
    # Move the root override into a sibling: it no longer dominates deep.pfig.
    (tmp_path / "left.pfig").write_text(
        f'include "./base.pfig" as base\noverride base/value = {left_value}\n'
    )
    root.write_text('include "./left.pfig" as left\ninclude "./right.pfig" as right\nresult = 0\n')
    with pytest.raises(ValidationError, match="Conflicting overrides"):
        instantiate("result", path=root)


def test_unreachable_override_cycle_is_invalid():
    with pytest.raises(ValidationError, match="Dependency cycle"):
        instantiate("result", source="value = 1\noverride value = @value + 1\nresult = 0\n")


def test_external_supplied_once_and_runtime_canonical_override(tmp_path):
    (tmp_path / "base.pfig").write_text(
        "external device\nvalue = 1\nobj = dict(device=@device, value=@value)\n"
    )
    root = tmp_path / "root.pfig"
    root.write_text(
        'include "./base.pfig" as a\ninclude "./base.pfig" as b\nresult = [@a/obj, @b/obj]\n'
    )
    inputs = {"b/device": "cpu", "a/value": 2}
    result = instantiate("result", path=root, externals=inputs)
    assert result == [{"device": "cpu", "value": 2}] * 2
    assert result[0] is result[1]
    assert inputs == {"b/device": "cpu", "a/value": 2}


@pytest.mark.parametrize("field", ["name", "position", "canonical_key"])
@pytest.mark.parametrize("reverse", [False, True])
def test_runtime_dataclass_alias_conflicts(tmp_path, field, reverse):
    @dataclass
    class Device:
        name: str = "cpu"
        position: str = "first"
        canonical_key: str = "default"

    (tmp_path / "base.pfig").write_text("external device\n")
    root = tmp_path / "root.pfig"
    root.write_text(
        'include "./base.pfig" as a\ninclude "./base.pfig" as b\nresult = [@a/device, @b/device]\n'
    )
    device = Device()
    # Runtime dataclasses are opaque objects, even when their fields are equal.
    for other in [replace(device, **{field: "different"}), replace(device)]:
        entries = [("a/device", device), ("b/device", other)]
        if reverse:
            entries.reverse()
        with pytest.raises(ValidationError, match="Conflicting runtime assignments"):
            instantiate("result", path=root, externals=dict(entries))

    result = instantiate("result", path=root, externals={"a/device": device, "b/device": device})
    assert result[0] is result[1] is device


def test_profile_alias_conflict_not_hidden_by_later_profile(tmp_path):
    (tmp_path / "base.pfig").write_text("external device\n")
    root = tmp_path / "root.pfig"
    root.write_text('include "./base.pfig" as a\ninclude "./base.pfig" as b\nresult = @a/device\n')
    bad = tmp_path / "bad.pfig"
    bad.write_text('override a/device = "cpu"\noverride b/device = "cuda"\n')
    later = tmp_path / "later.pfig"
    later.write_text('override a/device = "cpu"\n')
    with pytest.raises(ValidationError, match="Conflicting runtime assignments"):
        instantiate("result", path=root, profile_paths=[bad, later])


def test_fresh_variants_share_dependencies_and_effective_attributes(tmp_path):
    (tmp_path / "base.pfig").write_text(
        "from types import SimpleNamespace\n"
        "dep = dict()\nobj = SimpleNamespace(dep=@dep, width=1)\n"
    )
    root = tmp_path / "root.pfig"
    # An override recipe may refer to root-owned dependencies in its lexical scope.
    root.write_text(
        'include "./base.pfig" as a\ninclude "./base.pfig" as b\n'
        "from types import SimpleNamespace\n"
        "override a/obj = SimpleNamespace(dep=@a/dep, width=2)\n"
        "result = [@a/obj(), @b/obj(), @b/obj.width]\n"
    )
    first, second, width = instantiate("result", path=root)
    assert first is not second
    assert first.dep is second.dep
    assert first.width == second.width == width == 2


def test_conflicts_construct_nothing(tmp_path, monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("Validation constructed a provider")

    monkeypatch.setattr("pfig.helpers._test_forbidden", forbidden, raising=False)
    root = diamond(
        tmp_path, "override geometry/values/width = 2\noverride geometry/values/width = 3"
    )
    root.write_text(
        root.read_text().replace(
            "result = {",
            "from pfig.helpers import _test_forbidden\nunused = _test_forbidden()\nresult = {",
        )
    )
    with pytest.raises(ValidationError, match="Conflicting overrides"):
        instantiate("result", path=root)


@pytest.mark.parametrize("scope", ["same_path", "different_aliases", "siblings"])
@pytest.mark.parametrize("reachable", [False, True])
def test_identical_provider_overrides_fail_before_construction(
    tmp_path, monkeypatch, scope, reachable
):
    calls = []

    def build():
        calls.append("constructed")
        return {}

    monkeypatch.setattr("pfig.helpers._test_override", build, raising=False)
    (tmp_path / "base.pfig").write_text("value = 0\n")
    import_line = "from pfig.helpers import _test_override\n"
    if scope == "siblings":
        # Distinct declarations with identical text and source positions must not
        # be mistaken for one declaration encountered through a diamond.
        for name in ["left", "right"]:
            (tmp_path / f"{name}.pfig").write_text(
                import_line
                + 'include "./base.pfig" as base\n'
                + "override base/value = _test_override()\n"
            )
        source = 'include "./left.pfig" as left\ninclude "./right.pfig" as right\n'
        target = "left/base/value"
    else:
        second_target = "a/value" if scope == "same_path" else "b/value"
        source = (
            import_line
            + 'include "./base.pfig" as a\ninclude "./base.pfig" as b\n'
            + "override a/value = _test_override()\n"
            + f"override {second_target} = _test_override()\n"
        )
        target = "a/value"
    source += f"result = @{target}\n" if reachable else "result = 0\n"
    root = tmp_path / "root.pfig"
    root.write_text(source)

    # Both the editor-facing compiler and execution must reject the declarations,
    # without evaluating their recipes to decide whether the results are equal.
    _, issues = compile_pfig(parse_pfig_source(source, filename=str(root)), filename=str(root))
    assert [issue.code for issue in issues] == ["override_conflict"]
    assert calls == []
    with pytest.raises(ValidationError) as error:
        instantiate("result", path=root)
    assert [issue.code for issue in error.value.issues] == ["override_conflict"]
    assert calls == []


def test_unresolved_cache_and_context_isolation(tmp_path):
    (tmp_path / "base.pfig").write_text("value = 1\n")
    cache = {}
    for override, expected in [("override a/value = 2", 2), ("", 1)]:
        root = tmp_path / "root.pfig"
        source = f'include "./base.pfig" as a\n{override}\nresult = @a/value\n'
        compiler = ASTCompiler(base_path=str(root), loaded_files=cache)
        compiled = compiler.compile_file(parse_pfig_source(source, filename=str(root)))
        assert compiled["values"]["a/value"].value == expected
        root.write_text(source)
        assert instantiate("result", path=root) == expected


def test_cached_nested_overrides_preserve_ancestry_and_context_isolation(tmp_path):
    (tmp_path / "leaf.pfig").write_text("value = 1\noverride value = 2\n")
    (tmp_path / "base.pfig").write_text('include "./leaf.pfig" as leaf\noverride leaf/value = 3\n')
    cache = {}
    roots = []
    for override, expected in [("", 3), ("", 3), ("override a/leaf/value = 4", 4), ("", 3)]:
        root = tmp_path / f"root{len(roots)}.pfig"
        source = f'include "./base.pfig" as a\n{override}\nresult = @a/leaf/value\n'
        compiler = ASTCompiler(base_path=str(root), loaded_files=cache)
        compiled = compiler.compile_file(parse_pfig_source(source, filename=str(root)))
        assert not compiler.validation_issues
        assert compiled["values"]["a/leaf/value"].value == expected
        assert compiler._include_graph[str(tmp_path / "base.pfig")] == {str(tmp_path / "leaf.pfig")}
        # Cached metadata must not bring another root's inclusion context with it.
        assert not set(roots).intersection(compiler._include_graph)
        assert cache[str(tmp_path / "base.pfig")]["values"]["leaf/value"].value == 1
        roots.append(str(root))
    assert len(cache) == 2


def test_many_aliases_compile_source_once_and_share_object(tmp_path):
    (tmp_path / "base.pfig").write_text("obj = dict(width=1)\n")
    root = tmp_path / "root.pfig"
    source = "\n".join(f'include "./base.pfig" as a{i}' for i in range(64))
    source += "\nresult = [" + ", ".join(f"@a{i}/obj" for i in range(64)) + "]\n"
    root.write_text(source)
    compiler = ASTCompiler(base_path=str(root))
    compiler.compile_file(parse_pfig_source(source, filename=str(root)))
    assert len(compiler.loaded_files) == 1
    result = instantiate("result", path=root)
    assert all(item is result[0] for item in result)


def test_diamond_bundle_relocation(tmp_path):
    from pfig.bundle import create_bundle, load_bundle

    source = tmp_path / "source"
    source.mkdir()
    root = diamond(source, "override geometry/values/width = 2")
    compiler = ASTCompiler(base_path=str(root))
    compiler.compile_file(parse_pfig_source(root.read_text(), filename=str(root)))
    bundle = create_bundle(compiler.file_origins, root, tmp_path / "bundle", as_tarball=True)
    extracted, manifest = load_bundle(bundle, extract_dir=tmp_path / "extracted")
    result = instantiate("result", path=extracted / manifest.entry)
    assert result["direct"] is result["nested"]
    assert result["direct"] == {"width": 2}


def test_lsp_effective_recipe_and_conflicts(tmp_path):
    pytest.importorskip("lsprotocol")
    from pfig.lsp.analysis.pfig_validator import validate_with_pfig

    root = diamond(tmp_path, "override geometry/values/width = 2")
    diagnostics, compiled = validate_with_pfig(root.read_text(), root.as_uri())
    assert not diagnostics
    assert compiled["values"]["backbone/geometry/values/width"].value == 2
    root = diamond(
        tmp_path, "override geometry/values/width = 2\noverride backbone/geometry/values/width = 3"
    )
    diagnostics, _ = validate_with_pfig(root.read_text(), root.as_uri())
    assert any("Conflicting overrides" in d.message for d in diagnostics)


def test_cli_conflict_matches_api(tmp_path):
    import subprocess
    import sys

    root = diamond(
        tmp_path, "override geometry/values/width = 2\noverride backbone/geometry/values/width = 3"
    )
    result = subprocess.run(
        [sys.executable, "-m", "pfig", "validate", str(root)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "Conflicting overrides" in result.stderr


def test_statement_order_and_checkout_portable_digest(tmp_path):
    digests = set()
    for directory in [tmp_path / "one", tmp_path / "two"]:
        directory.mkdir()
        root = diamond(directory, "override geometry/values/width = 2")
        for source in [root.read_text(), "\n".join(reversed(root.read_text().splitlines()))]:
            # The result dictionary occupies one line; all statements are independent.
            compiled, issues = compile_pfig(
                parse_pfig_source(source, filename=str(root)), filename=str(root)
            )
            assert not issues
            digests.add(compute_graph_digest(build_ir_graph(compiled)))
    assert len(digests) == 1


def test_runtime_alias_conflicts(tmp_path):
    root = diamond(tmp_path, "")
    with pytest.raises(ValidationError, match="Conflicting"):
        instantiate(
            "result",
            path=root,
            externals={
                "geometry/values/width": 2,
                "backbone/geometry/values/width": 3,
            },
        )
