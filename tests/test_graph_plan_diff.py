from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from urllib.request import urlopen

import pytest

from pfig.diff import NamespaceAlignment
from pfig.vis import build_graph_view_model_for_uri, serve_graph

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "graph_plan_diff"


def test_aligned_graph_preserves_paths_and_compares_nested_wiring(tmp_path):
    from textwrap import dedent

    (tmp_path / "inputs.pfig").write_text(
        "from collections import OrderedDict\ninput = OrderedDict()\n"
    )
    included = tmp_path / "shared.pfig"
    included.write_text(
        dedent("""\
        include "inputs.pfig" as data
        from collections import OrderedDict
        runner = OrderedDict(items=[@data/input(), @data/input], workers=2)
        """)
    )
    base, proposed = tmp_path / "wrapped.pfig", tmp_path / "flat.pfig"
    base.write_text('include "shared.pfig" as base\n__entrypoint__ = @base/runner.run\n')
    unchanged = included.read_text() + "__entrypoint__ = @runner.run\n"
    proposed.write_text(unchanged)
    strict = build_graph_view_model_for_uri(proposed, compare_to=base, alignment=None)
    assert strict["diff"]["counts"] == {"added": 2, "modified": 1, "removed": 2}
    assert "namespaceAlignment" not in strict["diff"]
    identity = build_graph_view_model_for_uri(proposed, compare_to=base)
    assert identity["diff"]["counts"] == {"added": 1, "modified": 1, "removed": 1}
    assert "data/input" not in identity["diff"]["nodesById"]
    alignment = NamespaceAlignment.parse("base=/")
    vm = build_graph_view_model_for_uri(proposed, compare_to=base, alignment=alignment)
    assert vm["diff"]["namespaceAlignment"] == "base → /"
    assert vm["diff"]["counts"] == {"added": 0, "modified": 0, "removed": 0}
    assert not vm["diff"]["baseOnlyEdges"]
    assert not any("diffStatus" in edge for edge in vm["edges"])
    assert {"data/input", "runner", "__entrypoint__"} <= {n["id"] for n in vm["nodes"]}

    proposed.write_text(unchanged.replace("workers=2", "workers=8"))
    vm = build_graph_view_model_for_uri(proposed, compare_to=base, alignment=alignment)
    assert vm["diff"]["counts"] == {"added": 0, "modified": 1, "removed": 0}
    assert [field["path"] for field in vm["diff"]["nodesById"]["runner"]["fields"]] == [
        (("kwarg", "workers"),),
    ]


def test_graph_identity_suppresses_alias_rewires_but_not_distinct_targets(tmp_path):
    shared = "from collections import OrderedDict\nsource = OrderedDict()\nrunner = OrderedDict(items=[@source(), @source])\n"
    for name in ("shared", "other"):
        (tmp_path / f"{name}.pfig").write_text(shared)
    includes = (
        'include "shared.pfig" as old\ninclude "shared.pfig" as new\n'
        'include "other.pfig" as other\n'
    )
    base, proposed = tmp_path / "base.pfig", tmp_path / "proposed.pfig"
    base.write_text(includes + "__entrypoint__ = @old/runner.run\n")
    proposed.write_text(includes + "__entrypoint__ = @new/runner.run\n")
    for mode in ("reachable", "all"):
        vm = build_graph_view_model_for_uri(proposed, compare_to=base, mode=mode)
        assert vm["diff"]["counts"] == {"added": 0, "modified": 0, "removed": 0}
        assert not vm["diff"]["baseOnlyNodes"]
        assert not vm["diff"]["baseOnlyEdges"]
        assert all("diffStatus" not in edge for edge in vm["edges"])

    proposed.write_text(includes + "__entrypoint__ = @other/runner.run\n")
    vm = build_graph_view_model_for_uri(proposed, compare_to=base)
    assert vm["diff"]["counts"] == {"added": 0, "modified": 1, "removed": 0}
    assert [(e["source"], e["target"]) for e in vm["diff"]["baseOnlyEdges"]] == [
        ("old/runner", "__entrypoint__"),
    ]
    assert [e["source"] for e in vm["edges"] if e.get("diffStatus") == "added"] == ["other/runner"]


def test_aligned_graph_keeps_real_removal_when_base_path_collides(tmp_path):
    shared = tmp_path / "shared.pfig"
    shared.write_text("from collections import OrderedDict\nx = OrderedDict()\n")
    base, proposed = tmp_path / "wrapped.pfig", tmp_path / "flat.pfig"
    base.write_text(
        'include "shared.pfig" as base\n'
        "from collections import Counter\nx = Counter()\n__entrypoint__ = @x\n"
    )
    proposed.write_text(shared.read_text() + "__entrypoint__ = @x\n")
    vm = build_graph_view_model_for_uri(
        proposed,
        compare_to=base,
        alignment=NamespaceAlignment.parse("base=/"),
    )
    diff = vm["diff"]
    assert diff["counts"] == {"added": 0, "modified": 1, "removed": 1}
    assert diff["removals"][0]["label"] == "x"
    removed_id = diff["removals"][0]["id"]
    assert removed_id != "x"
    assert diff["nodesById"]["__entrypoint__"]["fields"][0]["before"]["targetId"] == removed_id
    assert diff["baseOnlyEdges"][0]["source"] == removed_id
    assert vm["edges"][0]["source"] == "x"


def test_inline_and_parent_deltas_link_to_same_removed_binding(tmp_path):
    shared = "from collections import OrderedDict\nx = OrderedDict()\n"
    (tmp_path / "shared.pfig").write_text(shared)
    consumer = "r = OrderedDict(inner=OrderedDict(source=@x))\n__entrypoint__ = @r\n"
    base, proposed = tmp_path / "base.pfig", tmp_path / "proposed.pfig"
    base.write_text(
        'include "shared.pfig" as base\nfrom collections import Counter, OrderedDict\n'
        "x = Counter()\n" + consumer
    )
    proposed.write_text(shared + consumer)
    vm = build_graph_view_model_for_uri(
        proposed, compare_to=base, alignment=NamespaceAlignment.parse("base=/")
    )
    (removal,) = vm["diff"]["removals"]
    (inline,) = (n for n in vm["nodes"] if n["kind"] == "inline_provider")
    (parent_delta,) = vm["diff"]["nodesById"]["r"]["fields"][0]["nested"]
    (inline_delta,) = inline["diff"]["fields"]
    assert removal["id"] != "x"
    for delta in (parent_delta, inline_delta):
        assert delta["before"]["targetId"] == removal["id"]
        assert delta["after"]["targetId"] == "x"


def test_class_method_recipes_compare_by_function_and_receiver(tmp_path):
    base, proposed = tmp_path / "base.pfig", tmp_path / "proposed.pfig"
    config = "from pathlib import Path, PosixPath\nfrom datetime import date\np = Path.cwd()\nd = date.today()\n"
    base.write_text(config)
    proposed.write_text(config)
    diff = build_graph_view_model_for_uri(proposed, compare_to=base)["diff"]
    assert diff["nodesById"] == {}

    proposed.write_text(config.replace("p = Path.cwd()", "p = PosixPath.cwd()"))
    diff = build_graph_view_model_for_uri(proposed, compare_to=base)["diff"]
    assert set(diff["nodesById"]) == {"p"}


def test_removed_cards_keep_authored_labels_but_navigate_to_aligned_targets(tmp_path):
    base, proposed = tmp_path / "base.pfig", tmp_path / "proposed.pfig"
    shared = "from collections import OrderedDict\nr = OrderedDict()\n"
    (tmp_path / "shared.pfig").write_text(shared)
    (tmp_path / "other.pfig").write_text(shared)
    base.write_text(
        'include "shared.pfig" as x\nfrom collections import OrderedDict\n'
        "old = OrderedDict(arg=@x/r)\nfresh = @x/r(arg=@x/r)\nitems = [@x/r()]\n"
    )
    proposed.write_text('include "shared.pfig" as y\ninclude "other.pfig" as x\n')
    diff = build_graph_view_model_for_uri(proposed, compare_to=base)["diff"]
    nodes = {n["id"]: n for n in diff["baseOnlyNodes"]}
    fresh = nodes["fresh"]["freshInstance"]
    for row in (nodes["old"]["provider"]["kwargs"][0], fresh["overlayKwargs"][0]):
        assert (row["refTargetId"], row["refQualPath"]) == ("y/r", "x/r")
    assert (fresh["baseProviderId"], fresh["baseProviderBindingName"]) == ("y/r", "x/r")
    item = nodes["items"]["binding"]["valueSequence"]["items"][0]
    assert (item["targetId"], item["callee"]) == ("y/r", "@x/r")
    assert {edge["source"] for edge in diff["baseOnlyEdges"]} == {"y/r"}


def test_removed_inline_aliases_share_a_card_and_keep_reference_multiplicity(tmp_path):
    base, proposed = tmp_path / "base.pfig", tmp_path / "proposed.pfig"
    (tmp_path / "shared.pfig").write_text(
        "from collections import OrderedDict\ns = OrderedDict()\n"
        "r = OrderedDict(arg=OrderedDict(input=[@s, @s]))\n"
    )
    includes = 'include "shared.pfig" as x\ninclude "shared.pfig" as y\n'
    base.write_text(includes)
    proposed.write_text(
        includes + "from collections import OrderedDict\noverride x/r = OrderedDict()\n"
    )
    diff = build_graph_view_model_for_uri(proposed, compare_to=base)["diff"]
    (inline,) = diff["baseOnlyNodes"]
    assert inline["kind"] == "inline_provider"
    inputs = [e for e in diff["baseOnlyEdges"] if e["target"] == inline["id"]]
    # Bundling counts occurrences per original consumer, not per display card.
    assert sorted(Counter(e["targetPath"] for e in inputs).values()) == [2, 2]


def test_compared_fixture_uses_qualified_references_and_base_file_ownership():
    vm = build_graph_view_model_for_uri(
        FIXTURE / "proposed/experiment.pfig", compare_to=FIXTURE / "base/experiment.pfig"
    )
    diff = vm["diff"]
    assert vm["mode"] == "reachable"
    assert diff["counts"] == {"added": 1, "modified": 3, "removed": 2}
    assert diff["byFile"] == {
        f"file:{(FIXTURE / 'proposed/transforms.pfig').as_uri()}": diff["counts"]
    }
    assert {"transforms/input_stats", "transforms/normalize"}.isdisjoint(
        n["id"] for n in vm["nodes"]
    )
    assert {n["id"] for n in diff["baseOnlyNodes"]} == {
        "transforms/input_stats",
        "transforms/normalize",
    }
    assert all(
        n["sourceFileUri"] == (FIXTURE / "base/transforms.pfig").as_uri()
        for n in diff["baseOnlyNodes"]
    )
    assert all(n["activeDefinition"] is None for n in diff["baseOnlyNodes"])
    assert {
        (e["source"], e["target"], e["targetPort"], e["diffStatus"]) for e in diff["baseOnlyEdges"]
    } == {
        ("transforms/input_stats", "transforms/normalize", "stats", "removed"),
        ("transforms/normalize", "transforms/preprocess", "transform", "removed"),
    }
    fields = diff["nodesById"]["transforms/preprocess"]["fields"]
    assert fields[0]["before"]["targetId"] == "transforms/normalize"
    assert fields[0]["after"]["targetId"] == "transforms/augment"
    assert [f["path"][0][0] for f in diff["nodesById"]["transforms/model"]["fields"]] == [
        "callable"
    ]
    json.dumps(vm, allow_nan=False)


def test_change_fields_accumulate_and_distinguish_missing_and_typed_values(tmp_path):
    old, new = tmp_path / "old.pfig", tmp_path / "new.pfig"
    old.write_text(
        "from collections import OrderedDict\n"
        "x = OrderedDict()\n"
        "p = OrderedDict(items=[@x, 2], edit=1, removed=@x, keep=[1, 2])\n"
    )
    new.write_text(
        "from collections import Counter, OrderedDict\n"
        "x = OrderedDict()\n"
        "p = Counter(items=[@x, True], edit=True, added=None, keep=[1, 2])\n"
    )
    vm = build_graph_view_model_for_uri(new, compare_to=old)
    diff = vm["diff"]
    assert vm["mode"] == "all"
    assert diff["counts"] == {"added": 0, "modified": 1, "removed": 0}
    fields = {f["path"][0]: f for f in diff["nodesById"]["p"]["fields"]}
    assert set(fields) == {
        ("callable", ""),
        ("kwarg", "items"),
        ("kwarg", "edit"),
        ("kwarg", "removed"),
        ("kwarg", "added"),
    }
    assert fields["kwarg", "removed"]["after"] == {"present": False}
    assert fields["kwarg", "added"]["before"] == {"present": False}
    assert fields["kwarg", "added"]["after"]["preview"] == "None"
    assert fields["kwarg", "items"]["nested"][0]["path"] == (("kwarg", "items"), ("item", 1))
    assert [e["basePortLabel"] for e in diff["baseOnlyEdges"]] == ["removed"]


def test_callable_swap_distinguishes_rejected_and_removed_kwargs(tmp_path):
    from pfig.execution import ProviderSpec
    from pfig.lsp.graph_plan_diff import _row_changes

    def old_callable(positional, *, optional=None, obsolete=None):
        pass

    def new_callable(positional, /, *, optional=None):
        pass

    def accepts_kwargs(**kwargs):
        pass

    before = ProviderSpec(old_callable, [], {"positional": 1, "optional": 2, "obsolete": 3})

    def rejected(callable_obj):
        fields = _row_changes(before, ProviderSpec(callable_obj, [], {}))
        assert sum(field["path"][0][0] == "kwarg" for field in fields) == 3
        return {field["path"][0][1] for field in fields if field.get("notAccepted")}

    assert rejected(new_callable) == {"positional", "obsolete"}
    assert rejected(old_callable) == set()
    assert rejected(accepts_kwargs) == set()
    assert rejected(dict) == set()  # Signature unavailable on CPython.

    old, new = tmp_path / "old.pfig", tmp_path / "new.pfig"
    old.write_text(
        "from collections import OrderedDict\np = OrderedDict(obsolete=2, rel_tol=0.1)\n"
    )
    new.write_text("from math import isclose\np = isclose(a=1, b=2)\n")
    vm = build_graph_view_model_for_uri(new, compare_to=old)
    assert vm["ok"]
    fields = {f["path"][0]: f for f in vm["diff"]["nodesById"]["p"]["fields"]}
    assert fields["kwarg", "obsolete"]["notAccepted"]
    assert fields["kwarg", "obsolete"]["before"]["preview"] == "2"
    assert not fields["kwarg", "rel_tol"].get("notAccepted")
    assert not fields["kwarg", "a"].get("notAccepted")


def test_comparison_classifies_before_scoping(tmp_path):
    base, proposed = tmp_path / "base.pfig", tmp_path / "proposed.pfig"
    common = "from collections import OrderedDict\na = OrderedDict()\nb = OrderedDict()\n"
    base.write_text(common + "edit = 1\nold = OrderedDict(input=@edit)\n__entrypoint__ = @old\n")
    proposed.write_text(common + "edit=2\nold=OrderedDict(input=@edit)\n__entrypoint__ = @b\n")
    vm = build_graph_view_model_for_uri(proposed, compare_to=base)
    assert {n["id"] for n in vm["nodes"]} == {"b", "__entrypoint__"}
    assert vm["diff"]["counts"] == {"added": 0, "modified": 2, "removed": 0}
    assert set(vm["diff"]["nodesById"]) == {"edit", "__entrypoint__"}
    assert {n["id"] for n in vm["diff"]["contextNodes"]} == {"old"}


def test_comparison_matches_synthetic_paths_and_edge_multiplicity(tmp_path):
    base, proposed = tmp_path / "base.pfig", tmp_path / "proposed.pfig"
    common = "from collections import OrderedDict\nx = OrderedDict()\n"
    base.write_text(common + 'p = OrderedDict(mapping={"item": @x()}, refs=[@x, @x])\n')
    proposed.write_text(
        common
        + 'p = OrderedDict(extra=OrderedDict(), mapping={"new": 0, "item": @x()}, refs=[@x])\n'
    )
    diff = build_graph_view_model_for_uri(proposed, compare_to=base)["diff"]
    assert diff["counts"] == {"added": 0, "modified": 1, "removed": 0}
    assert diff["baseOnlyNodes"] == []
    assert [(e["source"], e["target"], e["targetPort"]) for e in diff["baseOnlyEdges"]] == [
        ("x", "p", "refs")
    ]


def test_removed_aliases_count_once_and_keep_removal_only_file(tmp_path):
    (tmp_path / "old.pfig").write_text("x = 1\n")
    base, proposed = tmp_path / "base.pfig", tmp_path / "proposed.pfig"
    base.write_text('include "old.pfig" as first\ninclude "old.pfig" as second\ny = 0\n')
    proposed.write_text("y=0\n")
    vm = build_graph_view_model_for_uri(proposed, compare_to=base)
    assert vm["diff"]["counts"] == {"added": 0, "modified": 0, "removed": 1}
    assert vm["diff"]["removals"][0]["aliases"] == ["first/x", "second/x"]
    assert vm["diff"]["removals"][0]["fileUri"] == (tmp_path / "old.pfig").as_uri()
    assert (tmp_path / "old.pfig").as_uri() not in {n["sourceFileUri"] for n in vm["nodes"]}


@pytest.mark.parametrize("bad_side", ["Base", "Proposed"])
def test_compare_reports_compile_failure_by_side(tmp_path, bad_side):
    base, proposed = tmp_path / "base.pfig", tmp_path / "proposed.pfig"
    base.write_text("x=1\n")
    proposed.write_text("x=1\n")
    invalid = base if bad_side == "Base" else proposed
    invalid.write_text("x = (\n")
    vm = build_graph_view_model_for_uri(proposed, compare_to=base)
    assert not vm["ok"]
    assert vm["error"].startswith(f"{bad_side} ({invalid}):")
    assert "diff" not in vm


def test_shared_profile_and_set_inputs_and_missing_external_policy(tmp_path):
    base, proposed = tmp_path / "base.pfig", tmp_path / "proposed.pfig"
    config = "external device\nexternal workers\n"
    base.write_text(config)
    proposed.write_text(config)
    profile = tmp_path / "profile.pfig"
    profile.write_text('override device = "cpu"\n')

    missing = build_graph_view_model_for_uri(
        proposed, compare_to=base, allow_missing_externals=False
    )
    assert missing["error"].startswith("Proposed (")
    assert "Missing external value" in missing["error"]
    assert build_graph_view_model_for_uri(proposed, compare_to=base)["ok"]

    configured = build_graph_view_model_for_uri(
        proposed,
        compare_to=base,
        profiles=[str(profile)],
        set_overrides={"workers": 8},
        allow_missing_externals=False,
    )
    assert configured["ok"]
    assert configured["diff"]["counts"] == {"added": 0, "modified": 0, "removed": 0}
    assert not any(n["binding"]["externalMissing"] for n in configured["nodes"])


def test_positional_deltas_do_not_mask_other_ir_fields():
    from collections import Counter, OrderedDict

    from pfig.execution import ProviderSpec
    from pfig.lsp.graph_plan_diff import _row_changes

    old = ProviderSpec(OrderedDict, [1], {"changed": 2, "same": 3})
    new = ProviderSpec(Counter, [4], {"changed": 5, "same": 3})
    assert [f["path"][0] for f in _row_changes(old, new)] == [
        ("callable", ""),
        ("arg", 0),
        ("kwarg", "changed"),
    ]


def test_compare_refresh_compiles_both_plans_once_and_ignores_request_paths(tmp_path, monkeypatch):
    import pfig.vis.server as server_module

    base, proposed = tmp_path / "base.pfig", tmp_path / "proposed.pfig"
    base.write_text("# a comment\nx = [1, 2]\n")
    proposed.write_text("x=[1,2]\n")
    compile_graph_plan = server_module.compile_graph_plan
    calls = []

    def count_compile(**kw):
        calls.append(kw["uri"])
        return compile_graph_plan(**kw)

    monkeypatch.setattr(server_module, "compile_graph_plan", count_compile)
    srv = serve_graph(proposed, compare_to=base, open_browser=False)
    try:
        url = f"{srv.url}api/graph?mode=all&compare_to=not-the-base.pfig"
        with urlopen(url, timeout=5) as response:
            vm = json.load(response)
        assert vm["diff"]["counts"] == {"added": 0, "modified": 0, "removed": 0}
        assert [n["id"] for n in vm["nodes"]] == ["x"]
        base.write_text("x=[2,2]\n")
        with urlopen(url, timeout=5) as response:
            vm = json.load(response)
        assert vm["diff"]["counts"]["modified"] == 1
        assert calls == [proposed.as_uri(), base.as_uri()] * 2
    finally:
        srv.shutdown()
