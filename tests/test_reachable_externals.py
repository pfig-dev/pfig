"""Execution requirements follow effective arguments, not declarations or recipes."""

import pytest

from pfig import instantiate, run
from pfig.execution import (
    Materializer,
    ProfileSpec,
    ValidationError,
    compile_pfig,
    execute_pfig_with_metadata,
    validate_pfig,
)
from pfig.parser import parse_config_file, parse_pfig_source

constructed = []


class Spy:
    def __init__(self, value):
        constructed.append(value)
        self.value = value

    def result(self):
        return self.value

    @property
    def path(self):
        constructed.append("property")
        return self.value


@pytest.fixture(autouse=True)
def reset_spy():
    constructed.clear()


@pytest.fixture
def graphs(tmp_path):
    full = tmp_path / "full.pfig"
    full.write_text("""from collections import OrderedDict
external nvidia_root
external render_root
inputs = [@nvidia_root, @render_root]
experiment = OrderedDict(inputs=@inputs)
__entrypoint__ = @experiment.copy
""")
    smoke = tmp_path / "smoke.pfig"
    smoke.write_text("""include "./full.pfig" as full
override full/inputs = [@full/nvidia_root]
__entrypoint__ = @full/__entrypoint__
""")
    return full, smoke


def test_derived_subset(graphs):
    full, smoke = graphs
    supplied = {"full/nvidia_root": "/data/nvidia"}
    metadata = validate_pfig(parse_config_file(smoke), filename=str(smoke), externals=supplied)
    assert metadata.compiled["declared_externals"] == {"full/nvidia_root", "full/render_root"}
    assert run(smoke, externals=supplied) == {"inputs": ["/data/nvidia"]}
    with pytest.raises(ValidationError, match="nvidia_root"):
        run(smoke)
    with pytest.raises(ValidationError, match="render_root"):
        run(full, externals={"nvidia_root": "/data/nvidia"})


def test_missing_fails_before_construction():
    source = """from tests.test_reachable_externals import Spy
external needed
first = Spy(value="first")
second = Spy(value=@needed)
result = [@first, @second]
"""
    with pytest.raises(ValidationError, match="needed"):
        instantiate("result", source=source)
    assert constructed == []


def test_fresh_effective_arguments_only():
    source = """from tests.test_reachable_externals import Spy
external unused
removed = Spy(value=@unused)
base = Spy(value=@removed)
middle = @base(value=@unused)
final = @middle(value="selected")
"""
    assert instantiate("final", source=source).value == "selected"
    assert constructed == ["selected"]


def test_instantiate_overlay():
    source = """from tests.test_reachable_externals import Spy
external unused
base = Spy(value=@unused)
"""
    assert instantiate("base", source=source, call_kwargs={"value": None}).value is None
    assert constructed == [None]


def test_shared_use_retains_base_requirements():
    source = """from tests.test_reachable_externals import Spy
external needed
base = Spy(value=@needed)
fresh = @base(value="fresh")
result = [@fresh, @base]
"""
    with pytest.raises(ValidationError, match="needed"):
        instantiate("result", source=source)
    assert constructed == []


def test_selected_external():
    with pytest.raises(ValidationError, match="Missing external value"):
        instantiate("needed", source="external needed")
    with pytest.raises(ValidationError, match="Missing external value"):
        validate_pfig(parse_pfig_source("external needed"), entrypoint="needed")


def test_fresh_callable_entrypoint(capsys):
    ast = parse_pfig_source("""from pfig.utils import Printer
external unused
base = Printer(object=@unused)
selected = @base(object="selected")
""")
    validate_pfig(ast, entrypoint="selected")
    assert capsys.readouterr().out == ""
    assert execute_pfig_with_metadata(ast, entrypoint="selected").result is None
    assert capsys.readouterr().out == "'selected'\n"


def test_alternate_target_and_none():
    ast = parse_pfig_source("""from collections import OrderedDict
external first
external second
a = OrderedDict(value=@first)
b = OrderedDict(value=@second)
__entrypoint__ = @a.copy
""")
    metadata = validate_pfig(ast, entrypoint="b.copy", externals={"second": None})
    assert "first" not in metadata.reachable_names
    assert execute_pfig_with_metadata(
        ast, entrypoint="b.copy", externals={"second": None}
    ).result == {"value": None}
    with pytest.raises(ValidationError, match="first"):
        validate_pfig(ast, externals={"second": None})


def test_partial_fresh_chain_and_distinct_instances():
    source = """from tests.test_reachable_externals import Spy
external unused
base = Spy()
middle = @base(value=@unused)
a = @middle(value="a")
b = @middle(value="b")
result = [@a, @a, @b]
"""
    result = instantiate("result", source=source)
    assert result[0] is result[1]
    assert result[0] is not result[2]
    assert sorted(constructed) == ["a", "b"]
    constructed.clear()
    with pytest.raises(ValidationError, match="missing required kwargs"):
        instantiate("base", source=source)
    assert constructed == []
    assert instantiate("middle", source=source, call_kwargs={"value": "outer"}).value == "outer"


def test_override_changes_requirements_without_constructing_removed_branch():
    source = """from tests.test_reachable_externals import Spy
external old
external new
removed = Spy(value=@old)
selected = @removed
override selected = Spy(value=@new)
"""
    with pytest.raises(ValidationError) as exc:
        instantiate("selected", source=source)
    assert [issue.binding for issue in exc.value.issues] == ["new"]
    assert constructed == []
    assert instantiate("selected", source=source, externals={"new": 2}).value == 2
    assert constructed == [2]


@pytest.mark.parametrize(
    "expression",
    ["[@needed]", "(@needed,)", '{"key": @needed}', "1 + @needed", "-@needed", "@receiver.path"],
)
def test_expression_edges_without_evaluation(expression):
    source = f"""from tests.test_reachable_externals import Spy
external needed
receiver = Spy(value=@needed)
result = {expression}
"""
    with pytest.raises(ValidationError, match="needed"):
        instantiate("result", source=source)
    assert constructed == []


def test_attribute_validation_does_not_evaluate_properties():
    ast = parse_pfig_source("""from tests.test_reachable_externals import Spy
external needed
receiver = Spy(value=@needed)
__entrypoint__ = @receiver.path
""")
    validate_pfig(ast, externals={"needed": "value"})
    assert constructed == []


def test_diamond_aliases_and_rootless_diagnostics(tmp_path):
    (tmp_path / "base.pfig").write_text("external needed\nexternal unused\n")
    for name in ["left", "right"]:
        (tmp_path / f"{name}.pfig").write_text('include "./base.pfig" as base\n')
    path = tmp_path / "root.pfig"
    path.write_text("""include "./left.pfig" as left
include "./right.pfig" as right
result = [@left/base/needed, @right/base/needed]
""")
    assert instantiate("result", path=path, externals={"right/base/needed": None}) == [None, None]
    with pytest.raises(ValidationError) as exc:
        instantiate("result", path=path)
    assert len(exc.value.issues) == 1
    assert exc.value.issues[0].position is not None
    ast = parse_config_file(path)
    compiled, issues = compile_pfig(ast, filename=str(path))
    assert len(issues) == 2
    assert all(issue.code == "missing_external" for issue in issues)
    assert len(compiled["declared_externals"]) == 4
    assert compile_pfig(ast, filename=str(path), allow_missing_externals=True)[1] == []
    with pytest.raises(ValidationError, match="Conflicting runtime assignments"):
        instantiate("result", path=path, externals={"left/base/unused": 1, "right/base/unused": 2})


def test_profiles_and_supplied_unused_inputs(graphs, tmp_path):
    _, smoke = graphs
    profile = tmp_path / "profile.pfig"
    profile.write_text('override nvidia_root = "profile"\noverride render_root = "unused"\n')
    metadata = validate_pfig(
        parse_config_file(smoke),
        filename=str(smoke),
        profile_paths=[ProfileSpec(profile, prefix=("full",))],
        externals={"full/nvidia_root": "api"},
    )
    assert metadata.compiled["runtime_inputs"] == {
        "full/nvidia_root": "api",
        "full/render_root": "unused",
    }
    assert "full/render_root" not in metadata.reachable_names
    assert metadata.compiled["runtime_sources"]["full/render_root"].startswith("profile-prefixed:")


def test_incomplete_validation_cannot_materialize():
    ast = parse_pfig_source("""from tests.test_reachable_externals import Spy
external needed
obj = Spy(value=@needed)
__entrypoint__ = @obj.result
""")
    metadata = validate_pfig(ast, allow_missing_externals=True)
    materialized, issues = Materializer().materialize_reachable(
        metadata.compiled,
        metadata.reachable_names,
        target_name=metadata.entrypoint_name,
    )
    assert materialized == {}
    assert [issue.code for issue in issues] == ["missing_external"]
    with pytest.raises(ValidationError, match="needed"):
        execute_pfig_with_metadata(ast, allow_missing_externals=True)
    assert constructed == []


@pytest.mark.parametrize("missing", ["entry", "snapshot"])
@pytest.mark.parametrize("runner", ["api", "cli"])
def test_snapshot_and_entrypoint_validate_before_either_root(tmp_path, missing, runner):
    path = tmp_path / "root.pfig"
    path.write_text("""from tests.test_reachable_externals import Spy
external entry
external snapshot
obj = Spy(value=@entry)
output = Spy(value=@snapshot)
__snapshot__ = @output.path
__entrypoint__ = @obj.result
""")
    supplied = {"entry": "value", "snapshot": str(tmp_path / "snapshots")}
    del supplied[missing]
    if runner == "api":
        with pytest.raises(ValidationError, match=missing):
            run(path, externals=supplied)
    else:
        from pfig.cli import create_parser

        args = create_parser().parse_args(["run", str(path)])
        args.set_values = supplied
        assert args.func(args) == 1
    assert constructed == []


@pytest.mark.parametrize("bypass", ["disabled", "explicit"])
def test_bypassed_snapshot_inputs_not_required(tmp_path, monkeypatch, bypass):
    path = tmp_path / "root.pfig"
    path.write_text("""from tests.test_reachable_externals import Spy
external unused
__snapshot__ = @unused
obj = Spy(value="entry")
__entrypoint__ = @obj.result
""")
    monkeypatch.setattr("pfig.snapshot.create_snapshot", lambda **kwargs: None)
    kwargs = {"no_snapshot": True} if bypass == "disabled" else {"snapshot_dir": tmp_path / "out"}
    assert run(path, **kwargs) == "entry"
    assert constructed == ["entry"]


@pytest.mark.parametrize("mode", ["missing", "supplied", "disabled", "explicit"])
@pytest.mark.parametrize("runner", ["api", "cli"])
def test_external_snapshot_directive(tmp_path, monkeypatch, mode, runner):
    from pfig.cli import _resolve_snapshot_dir, create_parser

    path = tmp_path / "root.pfig"
    path.write_text("""from tests.test_reachable_externals import Spy
external __snapshot__
obj = Spy(value="entry")
__entrypoint__ = @obj.result
""")
    destination = tmp_path / "snapshots"
    supplied = {"__snapshot__": str(destination)} if mode == "supplied" else {}
    snapshots = []
    if runner == "api":
        monkeypatch.setattr(
            "pfig.snapshot.create_snapshot", lambda **kwargs: snapshots.append(kwargs["output_dir"])
        )
        kwargs = {"externals": supplied, "no_snapshot": mode == "disabled"}
        if mode == "explicit":
            kwargs["snapshot_dir"] = destination
        if mode == "missing":
            with pytest.raises(ValidationError, match="Missing external value: '__snapshot__'"):
                run(path, **kwargs)
        else:
            assert run(path, **kwargs) == "entry"
    else:
        options = []
        if mode == "disabled":
            options = ["--no-snapshot"]
        elif mode == "explicit":
            options = ["--snapshot", str(destination)]
        args = create_parser().parse_args(["run", str(path), *options])
        args.set_values = supplied

        def snapshot(**kwargs):
            if kwargs["snapshot_enabled"]:
                snapshots.append(_resolve_snapshot_dir(kwargs["args"], kwargs["metadata"]))

        monkeypatch.setattr("pfig.cli._maybe_create_snapshot", snapshot)
        assert args.func(args) == (1 if mode == "missing" else 0)
    assert constructed == ([] if mode == "missing" else ["entry"])
    assert snapshots == ([destination] if mode in {"supplied", "explicit"} else [])


def test_cli_computed_snapshot_keeps_shared_identity(tmp_path, monkeypatch):
    from pfig.cli import create_parser

    path = tmp_path / "root.pfig"
    path.write_text(f'''from tests.test_reachable_externals import Spy
obj = Spy(value="{tmp_path}")
__snapshot__ = @obj.path
__entrypoint__ = @obj.result
''')
    order = []

    def snapshot(**kwargs):
        assert constructed == [str(tmp_path), "property"]
        order.append("snapshot")

    monkeypatch.setattr("pfig.cli._maybe_create_snapshot", snapshot)
    args = create_parser().parse_args(["run", str(path)])
    args.set_values = {}
    assert args.func(args) == 0
    assert order == ["snapshot"]
    assert constructed == [str(tmp_path), "property"]


@pytest.mark.parametrize("supplied", [{}, {"unused": "preserved"}])
def test_bundle_and_replay_preserve_omitted_and_supplied_inputs(tmp_path, monkeypatch, supplied):
    import sys

    from pfig.bundle import load_bundle
    from pfig.replay.runner import replay_run
    from pfig.replay.snapshot_loader import load_and_validate_snapshot
    from pfig.replay.validate import replay_validate
    from pfig.replay.workspace import ReplayWorkspace

    monkeypatch.chdir(tmp_path)
    path = tmp_path / "root.pfig"
    path.write_text("""from collections import OrderedDict
external unused
base = OrderedDict(value=@unused, inherited=7)
middle = @base(value="middle")
selected = @middle(value="selected")
__entrypoint__ = @selected.copy
""")
    assert run(path, externals=supplied, snapshot_dir=tmp_path / "snapshots") == {
        "value": "selected",
        "inherited": 7,
    }
    snapshot = load_and_validate_snapshot(next((tmp_path / "snapshots").iterdir()))
    assert snapshot.externals == supplied
    assert "base" not in snapshot.graph
    assert "middle" not in snapshot.graph
    assert snapshot.graph["selected"]["recipe"]["kwargs"] == {"value": "selected", "inherited": 7}
    ws = ReplayWorkspace(tmp_path / "relocated", snapshot, prepare_ok=True)
    ws.create_dirs()
    ws.venv_python = sys.executable
    _, manifest = load_bundle(snapshot.bundle_tarball, extract_dir=ws.bundle_dir)
    bundled = ws.bundle_dir / manifest.entry
    assert "external unused" in bundled.read_text()
    assert replay_validate(ws)
    assert replay_run(ws) == 0
    ws.snapshot.entrypoint = "base.copy"
    if not supplied:
        with pytest.raises(ValidationError, match="unused"):
            replay_validate(ws)
        with pytest.raises(ValidationError, match="unused"):
            run(bundled, entrypoint="base.copy", no_snapshot=True)
    else:
        assert run(bundled, entrypoint="base.copy", externals=supplied, no_snapshot=True) == {
            "value": "preserved",
            "inherited": 7,
        }


def test_scoped_snapshot_digest_captures_nested_fresh_recipes():
    from pfig.snapshot import build_ir_graph, compute_graph_digest

    def graph(inherited):
        ast = parse_pfig_source(f"""from collections import OrderedDict
external unused
base = OrderedDict(value=@unused, inherited={inherited})
obj = OrderedDict(items=[@base(value="selected")])
__entrypoint__ = @obj.copy
""")
        metadata = validate_pfig(ast)
        return build_ir_graph(metadata.compiled, metadata.reachable_names)

    assert compute_graph_digest(graph(1)) != compute_graph_digest(graph(2))


@pytest.mark.parametrize("base", ["dict(a=1)", "json.JSONEncoder()"])
@pytest.mark.parametrize(
    "expression",
    ["@base()", "[@base(), @base()]", "(@base(),)", '{"nested": [@base()]}', "len([@base()])"],
)
def test_scoped_snapshot_warns_for_nested_fresh_introspection(base, expression):
    from pfig.snapshot import build_ir_graph, check_introspection_warnings

    ast = parse_pfig_source(f"""import json
base = {base}
result = {expression}
__entrypoint__ = @result.__repr__
""")
    metadata = validate_pfig(ast)
    graph = build_ir_graph(metadata.compiled, metadata.reachable_names)
    assert "base" not in graph
    warnings = check_introspection_warnings(graph)
    if base == "dict(a=1)":
        assert len(warnings) == 1
        assert warnings[0]["code"] == "incomplete_introspection"
        assert warnings[0]["providers"] == ["result"]
    else:
        assert warnings == []


def test_scoped_fresh_snapshot_retains_defaults_and_positional_dependencies():
    from pfig.snapshot import build_ir_graph, check_introspection_warnings

    ast = parse_pfig_source("""from collections import defaultdict
from tests.test_fresh_chaining import MockLayer
base = MockLayer(size=1)
variant = @base(size=2)
dictionary = defaultdict(default_factory=int)
fresh_dictionary = @dictionary()
__entrypoint__ = @variant.__repr__
""")
    metadata = validate_pfig(ast)
    graph = build_ir_graph(metadata.compiled, metadata.reachable_names)
    assert graph["variant"]["recipe"]["kwargs"] == {"name": "base", "size": 2, "activation": "relu"}
    assert check_introspection_warnings(graph) == []
    metadata = validate_pfig(ast, entrypoint="fresh_dictionary.copy")
    graph = build_ir_graph(metadata.compiled, metadata.reachable_names)
    assert graph["fresh_dictionary"]["recipe"]["args"] == [{"$import": "int"}]
    assert check_introspection_warnings(graph)[0]["providers"] == ["fresh_dictionary"]


@pytest.mark.parametrize("expression", ["-@base()", "@base() + 1", "len([@base()])"])
def test_scoped_snapshot_retains_recipes_inside_expressions(expression):
    from pfig.snapshot import build_ir_graph, compute_graph_digest

    def graph(value):
        ast = parse_pfig_source(f'''from decimal import Decimal
base = Decimal(value="{value}")
result = {expression}
obj = dict(result=@result)
__entrypoint__ = @obj.copy
''')
        metadata = validate_pfig(ast)
        assert "base" not in metadata.reachable_names
        return build_ir_graph(metadata.compiled, metadata.reachable_names)

    assert compute_graph_digest(graph(1)) != compute_graph_digest(graph(2))


@pytest.mark.parametrize("unused", ["bad = @unknown", "bad = Spy(typo=1)", "bad = @bad"])
def test_unreachable_structural_errors_still_precede_construction(unused):
    source = f"""from tests.test_reachable_externals import Spy
external unused
{unused}
selected = Spy(value="selected")
"""
    with pytest.raises(ValidationError):
        instantiate("selected", source=source)
    assert constructed == []
