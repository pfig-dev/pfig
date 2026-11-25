"""Tests for graph view model serialization."""

from __future__ import annotations

from pathlib import Path

import pytest

from pfig.lsp.graph_view_model import (
    _kwarg_names_in_signature_order,
    _kwarg_vm_for_value,
    _strip_literals_for_display,
    build_graph_view_model,
)
from pfig.lsp.state import WorkspaceState

FIXTURES = Path(__file__).parent / "fixtures" / "pfig"


@pytest.fixture
def ws() -> WorkspaceState:
    return WorkspaceState(
        profiles=[],
        set_overrides={},
        allow_missing_externals=True,
        strict_signatures=False,
    )


def _index_for(text: str, uri: str):
    from pfig.lsp.analysis.pfig_adapter import index_document

    return index_document(text, uri)


def test_value_aliases_share_an_origin_and_keep_full_navigation_targets(
    ws: WorkspaceState, tmp_path: Path
):
    (tmp_path / "base.pfig").write_text(
        "from collections import OrderedDict\n"
        "entry = OrderedDict()\n"
        "entries = [@entry(), @entry(), @entry()]\n"
    )
    text = 'include "./base.pfig" as a\ninclude "./base.pfig" as b\n'
    uri = (tmp_path / "main.pfig").as_uri()
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=_index_for(text, uri), mode="all")
    assert vm["ok"]
    bindings = {node["id"]: node["binding"] for node in vm["nodes"]}
    a, b = bindings["a/entries"], bindings["b/entries"]
    assert a["origin"] == b["origin"]
    assert a["valuePreview"] == b["valuePreview"] == "[@entry(), @entry(), @entry()]"
    assert a["valueSequence"] == {
        "kind": "list",
        "count": 3,
        "items": [{"callee": "@entry", "targetId": "a/entry", "kwargs": []}] * 3,
    }
    assert b["valueSequence"]["items"][0]["targetId"] == "b/entry"
    for prefix in ("a", "b"):
        assert (
            sum(
                e["source"] == f"{prefix}/entry"
                and e["target"] == f"{prefix}/entries"
                and e["refKind"] == "fresh"
                for e in vm["edges"]
            )
            == 3
        )


def test_graph_reuses_source_indexes_and_refreshes_them(ws, tmp_path, monkeypatch):
    from collections import Counter

    import pfig
    from pfig.lsp import graph_view_model

    leaf = tmp_path / "leaf.pfig"
    leaf.write_text("x = 1\ny = 2\n")
    wrapper = tmp_path / "wrapper.pfig"
    wrapper.write_text('include "./leaf.pfig" as leaf\n')
    text = 'include "./wrapper.pfig" as a\ninclude "./wrapper.pfig" as b\noverride b/leaf/x = 3\n'
    uri = (tmp_path / "main.pfig").as_uri()
    index = _index_for(text, uri)
    parses = Counter()
    includes = Counter()
    parse = pfig.parse_pfig_source
    resolve = graph_view_model.resolve_includes

    def count_parse(*args, **kwargs):
        if kwargs.get("with_index"):
            parses[kwargs["filename"]] += 1
        return parse(*args, **kwargs)

    def count_include(edge, source, workspace):
        includes[source, edge.target_path] += 1
        return resolve(edge, source, workspace)

    monkeypatch.setattr(pfig, "parse_pfig_source", count_parse)
    monkeypatch.setattr(graph_view_model, "resolve_includes", count_include)

    def build():
        vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=index, mode="all")
        assert vm["ok"]
        return {n["id"]: n for n in vm["nodes"]}

    nodes = build()
    assert parses[str(wrapper)] == parses[str(leaf)] == 1
    assert includes[uri, "./wrapper.pfig"] == includes[wrapper.as_uri(), "./leaf.pfig"] == 1
    for alias in ("a", "b"):
        assert nodes[f"{alias}/leaf/x"]["activeDefinition"]["uri"] == uri
        assert nodes[f"{alias}/leaf/x"]["sourceFileUri"] == leaf.as_uri()
        assert nodes[f"{alias}/leaf/y"]["activeDefinition"]["range"]["start"]["line"] == 1

    leaf.write_text("\nx = 1\ny = 2\n")
    nodes = build()
    assert parses[str(wrapper)] == parses[str(leaf)] == 2
    assert nodes["a/leaf/y"]["activeDefinition"]["range"]["start"]["line"] == 2


def test_inline_alias_identity_uses_owner_and_expression_slot(ws: WorkspaceState, tmp_path: Path):
    base = tmp_path / "base.pfig"
    base.write_text(
        "from collections import OrderedDict\n"
        "entry = OrderedDict()\n"
        "consumer = OrderedDict(left=@entry(), right=@entry())\n"
    )
    text = 'include "./base.pfig" as a\ninclude "./base.pfig" as b\n'
    uri = (tmp_path / "main.pfig").as_uri()
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=_index_for(text, uri), mode="all")
    origins = sorted(n["syntheticOrigin"] for n in vm["nodes"] if n["kind"] == "fresh_instance")
    assert origins == [[str(base), "consumer", slot] for slot in ("left", "left", "right", "right")]


def test_sequence_preview_shows_fresh_arguments_without_evaluation(ws: WorkspaceState):
    text = """
from collections import OrderedDict
offset = -1000
entry = OrderedDict()
entries = [@entry(seconds=(@offset) / 1000, offset_ms=@offset), @entry(offset_ms=0)]
empty = ()
"""
    uri = "file:///main.pfig"
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=_index_for(text, uri), mode="all")
    assert vm["ok"]
    bindings = {node["id"]: node["binding"] for node in vm["nodes"]}
    assert bindings["entries"]["valueSequence"] == {
        "kind": "list",
        "count": 2,
        "items": [
            {
                "callee": "@entry",
                "targetId": "entry",
                "kwargs": [
                    {"name": "seconds", "text": "((@offset) / 1000)"},
                    {"name": "offset_ms", "text": "@offset"},
                ],
            },
            {
                "callee": "@entry",
                "targetId": "entry",
                "kwargs": [{"name": "offset_ms", "text": "0"}],
            },
        ],
    }
    assert bindings["empty"]["valueSequence"] == {"kind": "tuple", "count": 0, "items": []}


def test_recipe_context_is_not_effective_reachability(ws: WorkspaceState):
    text = """
from collections import OrderedDict
external replaced
external retained
base = OrderedDict(old=@replaced, keep=@retained)
variant = @base(old="selected")
__entrypoint__ = @variant.copy
"""
    uri = "file:///main.pfig"
    vm = build_graph_view_model(
        text=text, uri=uri, ws=ws, index=_index_for(text, uri), mode="reachable"
    )
    assert vm["ok"] is True
    nodes = {node["id"]: node for node in vm["nodes"]}
    assert nodes["variant"]["flags"]["isReachable"]
    assert nodes["retained"]["flags"]["isReachable"]
    assert not nodes["base"]["flags"]["isReachable"]
    assert not nodes["replaced"]["flags"]["isReachable"]
    assert nodes["replaced"]["binding"]["externalMissing"]
    assert any(edge["source"] == "base" and edge["target"] == "variant" for edge in vm["edges"])


def test_strip_literals_for_display_nested_collections():
    from pfig.execution import LiteralValue

    raw = (
        LiteralValue(value=0.4914),
        LiteralValue(value=LiteralValue(value=0.4822)),
    )
    stripped = _strip_literals_for_display(raw)
    assert stripped == (0.4914, 0.4822)
    assert "LiteralValue" not in str(stripped)
    nested = _strip_literals_for_display({"m": [LiteralValue(value=1), LiteralValue(value=2)]})
    assert nested == {"m": [1, 2]}


def test_kwarg_vm_literal_preview_strips_nested_literal_value():
    from pfig.execution import LiteralValue

    kvm = _kwarg_vm_for_value(
        (LiteralValue(value=0.1), LiteralValue(value=0.2)),
        None,
    )
    assert kvm["valueKind"] == "literal"
    assert kvm["inlinePreview"] is not None
    assert "LiteralValue" not in kvm["inlinePreview"]


def test_graph_arithmetic_kwarg_preview_uses_pfig_syntax(ws: WorkspaceState, tmp_path: Path):
    (tmp_path / "base.pfig").write_text(
        "from collections import OrderedDict\n"
        "width = 96\n"
        "head_dim = 32\n"
        "attention = OrderedDict(num_attention_heads=@width // @head_dim, offset=-@width)\n"
    )
    text = 'include "./base.pfig" as head\n'
    uri = (tmp_path / "main.pfig").as_uri()
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=_index_for(text, uri), mode="all")
    assert vm["ok"]
    attention = next(n for n in vm["nodes"] if n["id"] == "head/attention")
    kwarg = attention["provider"]["kwargs"][0]
    assert kwarg["name"] == "num_attention_heads"
    assert kwarg["inlinePreview"] == "(@width // @head_dim)"
    assert kwarg["evaluatedPreview"] == "3"
    assert sorted(
        (edge["source"], edge["targetPort"], edge["refKind"])
        for edge in vm["edges"]
        if edge["target"] == "head/attention"
    ) == [
        ("head/head_dim", "num_attention_heads", "shared"),
        ("head/width", "num_attention_heads", "shared"),
        ("head/width", "offset", "shared"),
    ]


def test_named_arithmetic_value_previews(ws: WorkspaceState):
    text = "width = 96\ndoubled = @width * 2\nnegative = -@doubled\ninvalid = (@width) / 0\n"
    uri = "file:///main.pfig"
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=_index_for(text, uri), mode="all")
    previews = {node["id"]: node["binding"]["valuePreview"] for node in vm["nodes"]}
    assert previews["doubled"] == "(@width * 2) = 192"
    assert previews["negative"] == "(-@doubled) = -192"
    assert previews["invalid"] == "((@width) / 0)"


def test_arithmetic_kwarg_preview_does_not_materialize_providers(ws: WorkspaceState):
    text = """
from collections import OrderedDict
zero = 0
source = OrderedDict()
example = OrderedDict(provider=@source + 1, invalid=(1 / @zero))
"""
    uri = "file:///main.pfig"
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=_index_for(text, uri), mode="all")
    assert vm["ok"]
    kwargs = next(n["provider"]["kwargs"] for n in vm["nodes"] if n["id"] == "example")
    assert {kw["name"] for kw in kwargs} == {"provider", "invalid"}
    assert all("evaluatedPreview" not in kw for kw in kwargs)


def test_graph_tuple_kwarg_preview_omits_literal_value_repr(ws: WorkspaceState):
    text = """
from collections import OrderedDict
n = OrderedDict(
    mean=(0.4914, 0.4822, 0.4465),
    std=(0.247, 0.2435, 0.2616),
)
__entrypoint__ = @n
"""
    uri = "file:///main.pfig"
    idx = _index_for(text, uri)
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=idx, mode="all")
    assert vm["ok"] is True
    nnode = next(n for n in vm["nodes"] if n["id"] == "n")
    for name in ("mean", "std"):
        kw = next(k for k in nnode["provider"]["kwargs"] if k["name"] == name)
        assert kw["valueKind"] == "literal"
        preview = kw.get("inlinePreview") or ""
        assert "LiteralValue" not in preview


def test_entrypoint_node_binding_name_shows_ref(ws: WorkspaceState):
    """__entrypoint__ header shows @binding / @binding.method, not the binding id."""
    text = """
from collections import OrderedDict
trainer = OrderedDict()
__entrypoint__ = @trainer.fit
"""
    uri = "file:///main.pfig"
    idx = _index_for(text, uri)
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=idx, mode="all")
    assert vm["ok"] is True
    ep = next(n for n in vm["nodes"] if n["id"] == "__entrypoint__")
    assert ep["bindingName"] == "@trainer.fit"


def test_simple_provider_ref_edges(ws: WorkspaceState):
    text = """
from collections import OrderedDict
model = OrderedDict()
result = OrderedDict(x=@model)
__entrypoint__ = @result
"""
    uri = "file:///main.pfig"
    idx = _index_for(text, uri)
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=idx, mode="all")
    assert vm["ok"] is True
    ids = {n["id"] for n in vm["nodes"]}
    assert "model" in ids
    assert "result" in ids
    assert "__entrypoint__" in ids
    ports = {(e["source"], e["target"], e["targetPort"]) for e in vm["edges"]}
    assert ("model", "result", "x") in ports


def test_reachable_mode_subgraph(ws: WorkspaceState):
    text = """
from collections import OrderedDict
a = OrderedDict()
b = OrderedDict(x=@a)
__entrypoint__ = @b
unused = OrderedDict()
"""
    uri = "file:///main.pfig"
    idx = _index_for(text, uri)
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=idx, mode="reachable")
    assert vm["ok"] is True
    assert vm["reachableUnavailable"] is False
    visible = {n["id"] for n in vm["nodes"]}
    assert "unused" not in visible
    assert {"a", "b", "__entrypoint__"} <= visible


def test_no_entrypoint_falls_back_to_all(ws: WorkspaceState):
    text = "from collections import OrderedDict\na = OrderedDict()\nb = OrderedDict(x=@a)\n"
    uri = "file:///main.pfig"
    idx = _index_for(text, uri)
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=idx, mode="reachable")
    assert vm["ok"] is True
    assert vm["reachableUnavailable"] is True
    assert vm["mode"] == "all"
    visible = {n["id"] for n in vm["nodes"]}
    assert "a" in visible and "b" in visible


def test_all_mode_preserves_entrypoint_reachability_flags(ws: WorkspaceState):
    """In mode=all, every binding is visible but isReachable reflects the entrypoint closure."""
    text = """
from collections import OrderedDict
a = OrderedDict()
b = OrderedDict(x=@a)
__entrypoint__ = @b
unused = OrderedDict()
"""
    uri = "file:///main.pfig"
    idx = _index_for(text, uri)
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=idx, mode="all")
    assert vm["ok"] is True
    assert vm["mode"] == "all"
    assert vm["reachableUnavailable"] is False
    ids = {n["id"] for n in vm["nodes"]}
    assert "unused" in ids
    assert {"a", "b", "__entrypoint__"} <= ids
    by_id = {n["id"]: n for n in vm["nodes"]}
    assert by_id["unused"]["flags"]["isReachable"] is False
    assert by_id["b"]["flags"]["isReachable"] is True
    assert by_id["a"]["flags"]["isReachable"] is True


def test_graph_view_model_node_ids_are_unique(ws: WorkspaceState):
    """Guards against duplicate React Flow node ids (would cause ghost / doubled cards)."""
    text = """
from collections import OrderedDict
a = OrderedDict()
b = OrderedDict(x=@a)
__entrypoint__ = @b
unused = OrderedDict()
orphan_num = 42
"""
    uri = "file:///main.pfig"
    idx = _index_for(text, uri)
    for mode in ("all", "reachable"):
        vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=idx, mode=mode)
        assert vm["ok"] is True
        raw = [n["id"] for n in vm["nodes"]]
        assert len(raw) == len(set(raw)), (mode, raw)


def test_chained_includes_pol_c_x_shares_active_definition_with_c_x(ws: WorkspaceState, tmp_path):
    """pol/c/hyper must resolve through policy → consts (same site as c/hyper) for compact graph."""
    consts = tmp_path / "consts.pfig"
    consts.write_text("from collections import OrderedDict\nhyper = OrderedDict()\n")
    policy = tmp_path / "policy.pfig"
    policy.write_text(
        'include "./consts.pfig" as c\nfrom collections import OrderedDict\nmid = OrderedDict()\n'
    )
    train = tmp_path / "train.pfig"
    train.write_text(
        'include "./policy.pfig" as pol\n'
        'include "./consts.pfig" as c\n'
        "from collections import OrderedDict\n"
        "a = OrderedDict(h=@c/hyper, g=@pol/c/hyper)\n"
        "__entrypoint__ = @a\n"
    )
    train_uri = train.resolve().as_uri()
    text = train.read_text()
    idx = _index_for(text, train_uri)
    vm = build_graph_view_model(text=text, uri=train_uri, ws=ws, index=idx, mode="all")
    assert vm["ok"] is True
    by_id = {n["id"]: n for n in vm["nodes"]}
    assert "c/hyper" in by_id
    assert "pol/c/hyper" in by_id
    ad_direct = by_id["c/hyper"].get("activeDefinition")
    ad_nested = by_id["pol/c/hyper"].get("activeDefinition")
    assert ad_direct is not None, "c/hyper should have activeDefinition"
    assert ad_nested is not None, "pol/c/hyper should resolve through chained includes"
    assert ad_direct == ad_nested


def test_overview_source_file_keeps_original_location_of_override(ws: WorkspaceState, tmp_path):
    base = tmp_path / "base.pfig"
    base.write_text(
        "from collections import OrderedDict\n"
        "value = 1\n"
        "wrapped = OrderedDict(inner=OrderedDict(x=@value))\n"
    )
    text = (
        'include "./base.pfig" as base\noverride base/value = 2\n__entrypoint__ = @base/wrapped\n'
    )
    train = tmp_path / "train.pfig"
    train.write_text(text)
    uri = train.as_uri()
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=_index_for(text, uri), mode="all")
    assert vm["ok"] is True
    assert vm["entryFileUri"] == uri
    by_id = {node["id"]: node for node in vm["nodes"]}
    overridden = by_id["base/value"]
    assert overridden["flags"]["isOverridden"] is True
    assert overridden["activeDefinition"]["uri"] == uri
    assert overridden["sourceFileUri"] == base.as_uri()
    inline = next(node for node in vm["nodes"] if node["kind"] == "inline_provider")
    assert inline["sourceFileUri"] == base.as_uri()


def test_aliases_navigate_to_same_winning_override(ws: WorkspaceState, tmp_path: Path):
    base = tmp_path / "base.pfig"
    base.write_text("x = 1\n")
    (tmp_path / "middle.pfig").write_text('include "./base.pfig" as base\noverride base/x = 2\n')
    text = (
        'include "./middle.pfig" as middle\n'
        'include "./base.pfig" as direct\n'
        "override direct/x = 3\n"
    )
    uri = (tmp_path / "main.pfig").as_uri()
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=_index_for(text, uri), mode="all")
    assert vm["ok"]
    nodes = {n["id"]: n for n in vm["nodes"]}
    for name in ("direct/x", "middle/base/x"):
        node = nodes[name]
        assert node["binding"]["valuePreview"] == "3"
        assert node["sourceFileUri"] == base.as_uri()
        assert node["flags"]["isOverridden"]
        assert node["activeDefinition"] == nodes["direct/x"]["activeDefinition"]
        assert node["activeDefinition"]["uri"] == uri
        assert node["activeDefinition"]["range"]["start"]["line"] == 2


def test_unused_literal_value_has_no_outgoing_edges_in_all_mode(ws: WorkspaceState):
    """Orphan value bindings (mode=all) are what the webview fold step removes when hiding value nodes."""
    text = """
from collections import OrderedDict
used = OrderedDict()
consumer = OrderedDict(x=@used)
__entrypoint__ = @consumer
orphan_num = 42
"""
    uri = "file:///main.pfig"
    idx = _index_for(text, uri)
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=idx, mode="all")
    assert vm["ok"] is True
    assert any(n["id"] == "orphan_num" and n["kind"] == "value" for n in vm["nodes"])
    assert not any(e["source"] == "orphan_num" for e in vm["edges"])


def test_inline_provider_kwarg_emits_anonymous_node(ws: WorkspaceState):
    text = """
from collections import OrderedDict
outer = OrderedDict(inner=OrderedDict(x=1))
__entrypoint__ = @outer
"""
    uri = "file:///main.pfig"
    idx = _index_for(text, uri)
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=idx, mode="all")
    assert vm["ok"] is True
    inline_nodes = [n for n in vm["nodes"] if n.get("kind") == "inline_provider"]
    assert len(inline_nodes) == 1
    anon = inline_nodes[0]
    assert anon["bindingName"] == "<inline>"
    assert anon["flags"].get("isAnonymous") is True
    ad = anon.get("activeDefinition")
    assert ad is not None
    assert ad["uri"].startswith("file://")
    assert "start" in ad["range"] and "end" in ad["range"]
    assert any(
        e["source"] == anon["id"] and e["target"] == "outer" and e["targetPort"] == "inner"
        for e in vm["edges"]
    )


def test_list_of_inline_providers_uses_collection_node(ws: WorkspaceState):
    """Kwargs like ``transforms=[…]`` use a list node; elements wire into slot ports."""
    text = """
from collections import OrderedDict
preprocess = OrderedDict(
    transforms=[
        OrderedDict(a=1),
        OrderedDict(b=OrderedDict(c=2)),
    ],
)
__entrypoint__ = @preprocess
"""
    uri = "file:///main.pfig"
    idx = _index_for(text, uri)
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=idx, mode="all")
    assert vm["ok"] is True
    pnode = next(n for n in vm["nodes"] if n["id"] == "preprocess")
    kws = pnode["provider"]["kwargs"]
    assert len(kws) == 1
    assert kws[0]["name"] == "transforms"
    assert kws[0]["valueKind"] == "collectionNested"
    coll_id = kws[0]["syntheticCollectionId"]
    assert coll_id
    coll = next(n for n in vm["nodes"] if n["id"] == coll_id)
    assert coll["kind"] == "collection"
    assert coll["collection"]["shape"] == "list"
    cad = coll.get("activeDefinition")
    assert cad is not None
    assert cad["uri"].startswith("file://")
    assert "start" in cad["range"] and "end" in cad["range"]
    assert [s["label"] for s in coll["collection"]["slots"]] == ["0", "1"]
    assert any(
        e["source"] == coll_id and e["target"] == "preprocess" and e["targetPort"] == "transforms"
        for e in vm["edges"]
    )
    inline_nodes = [n for n in vm["nodes"] if n.get("kind") == "inline_provider"]
    assert len(inline_nodes) == 3
    i1 = next(n for n in inline_nodes if any(k["name"] == "a" for k in n["provider"]["kwargs"]))
    i2 = next(n for n in inline_nodes if any(k["name"] == "b" for k in n["provider"]["kwargs"]))
    assert i1.get("activeDefinition") is not None
    assert i2.get("activeDefinition") is not None
    assert any(
        e["source"] == i1["id"] and e["target"] == coll_id and e["targetPort"] == "__slot0__"
        for e in vm["edges"]
    )
    assert any(
        e["source"] == i2["id"] and e["target"] == coll_id and e["targetPort"] == "__slot1__"
        for e in vm["edges"]
    )
    b_kw = next(k for k in i2["provider"]["kwargs"] if k["name"] == "b")
    assert b_kw["valueKind"] == "inlineNested"
    nested = next(n for n in inline_nodes if n["id"] == b_kw["syntheticInlineId"])
    c_kw = next(k for k in nested["provider"]["kwargs"] if k["name"] == "c")
    assert c_kw["inlinePreview"] == "2"


def test_dict_of_inline_providers_uses_collection_node(ws: WorkspaceState):
    text = """
from collections import OrderedDict
preprocess = OrderedDict(
    layers={"a": OrderedDict(x=1), "b": OrderedDict(y=2)},
)
__entrypoint__ = @preprocess
"""
    uri = "file:///main.pfig"
    idx = _index_for(text, uri)
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=idx, mode="all")
    assert vm["ok"] is True
    pnode = next(n for n in vm["nodes"] if n["id"] == "preprocess")
    tw = next(k for k in pnode["provider"]["kwargs"] if k["name"] == "layers")
    assert tw["valueKind"] == "collectionNested"
    coll = next(n for n in vm["nodes"] if n["id"] == tw["syntheticCollectionId"])
    assert coll["collection"]["shape"] == "dict"
    assert coll.get("activeDefinition") is not None
    labels = {s["label"] for s in coll["collection"]["slots"]}
    assert "'a'" in labels and "'b'" in labels


def test_fresh_call_edge_kind(ws: WorkspaceState):
    text = """
from collections import OrderedDict
base = OrderedDict(x=1)
wrapped = OrderedDict(inner=@base(y=2))
__entrypoint__ = @wrapped
"""
    uri = "file:///main.pfig"
    idx = _index_for(text, uri)
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=idx, mode="all")
    assert vm["ok"] is True
    fresh_edges = [e for e in vm["edges"] if e["refKind"] == "fresh"]
    assert any(e["source"] == "base" for e in fresh_edges)
    assert any(e["source"] == "base" and e["targetPort"] == "__fresh_base__" for e in fresh_edges)
    assert any(
        e["target"] == "wrapped" and e["targetPort"] == "inner" and e["refKind"] == "shared"
        for e in vm["edges"]
    )
    wnode = next(n for n in vm["nodes"] if n["id"] == "wrapped")
    inner_kw = next(k for k in wnode["provider"]["kwargs"] if k["name"] == "inner")
    assert inner_kw["valueKind"] == "freshNested"
    assert inner_kw["syntheticFreshId"]
    fin = next(n for n in vm["nodes"] if n["id"] == inner_kw["syntheticFreshId"])
    assert fin["kind"] == "fresh_instance"
    fad = fin.get("activeDefinition")
    assert fad is not None
    assert fad["uri"].startswith("file://")
    assert "start" in fad["range"] and "end" in fad["range"]
    assert fin["freshInstance"]["baseProviderId"] == "base"
    oy = next(x for x in fin["freshInstance"]["overlayKwargs"] if x["name"] == "y")
    assert oy["valueKind"] == "literal"
    assert oy["inlinePreview"] == "2"


def test_fresh_overlay_inline_provider_gets_own_node(ws: WorkspaceState):
    """Inline ProviderSpec in a fresh() overlay uses a separate graph node like kwargs do."""
    text = """
from collections import OrderedDict
base = OrderedDict(x=1)
wrapped = OrderedDict(inner=@base(nested=OrderedDict(z=3)))
__entrypoint__ = @wrapped
"""
    uri = "file:///main.pfig"
    idx = _index_for(text, uri)
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=idx, mode="all")
    assert vm["ok"] is True
    wnode = next(n for n in vm["nodes"] if n["id"] == "wrapped")
    inner_kw = next(k for k in wnode["provider"]["kwargs"] if k["name"] == "inner")
    fin = next(n for n in vm["nodes"] if n["id"] == inner_kw["syntheticFreshId"])
    assert fin.get("activeDefinition") is not None
    nested_ov = next(x for x in fin["freshInstance"]["overlayKwargs"] if x["name"] == "nested")
    assert nested_ov["valueKind"] == "inlineNested"
    assert nested_ov["syntheticInlineId"]
    inline = next(n for n in vm["nodes"] if n["id"] == nested_ov["syntheticInlineId"])
    assert inline["kind"] == "inline_provider"
    assert any(
        e["source"] == inline["id"] and e["target"] == fin["id"] and e["targetPort"] == "nested"
        for e in vm["edges"]
    )
    z_kw = next(k for k in inline["provider"]["kwargs"] if k["name"] == "z")
    assert z_kw["valueKind"] == "literal"
    assert z_kw["inlinePreview"] == "3"


def test_nested_inline_fresh_calls_have_active_definition(ws: WorkspaceState):
    """Each synthetic fresh_instance node reveals its own @call(...) site."""
    text = """
from collections import OrderedDict
base = OrderedDict(x=1)
nested = OrderedDict(a=1)
wrapped = OrderedDict(inner=@base(sub=@nested(b=2)))
__entrypoint__ = @wrapped
"""
    uri = "file:///main.pfig"
    idx = _index_for(text, uri)
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=idx, mode="all")
    assert vm["ok"] is True
    anon_fresh = [
        n
        for n in vm["nodes"]
        if n.get("kind") == "fresh_instance" and n.get("flags", {}).get("isAnonymous")
    ]
    assert len(anon_fresh) == 2
    bases = {n["freshInstance"]["baseProviderId"] for n in anon_fresh}
    assert bases == {"base", "nested"}
    for n in anon_fresh:
        ad = n.get("activeDefinition")
        assert ad is not None
        assert ad["uri"].startswith("file://")
        assert "start" in ad["range"] and "end" in ad["range"]


def test_include_namespaced_ref(tmp_path, ws: WorkspaceState):
    consts = tmp_path / "consts.pfig"
    consts.write_text("rate = 0.01\n")
    main = tmp_path / "main.pfig"
    main.write_text(
        'include "./consts.pfig" as consts\n'
        "from collections import OrderedDict\n"
        "cfg = OrderedDict(lr=@consts/rate)\n"
        "__entrypoint__ = @cfg\n"
    )
    uri = f"file://{main}"
    text = main.read_text()
    idx = _index_for(text, uri)
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=idx, mode="all")
    assert vm["ok"] is True
    # composed graph includes namespaced binding
    ids = {n["id"] for n in vm["nodes"]}
    assert "consts/rate" in ids or "rate" in ids
    if "consts/rate" in ids:
        rate_vm = next(n for n in vm["nodes"] if n["id"] == "consts/rate")
        assert rate_vm["bindingName"] == "rate"
    # at least one edge from rate-like node into cfg
    assert len(vm["edges"]) >= 1


def test_external_only_nodes_and_edges(ws: WorkspaceState):
    """Declared but unprovided externals appear as nodes; edges from refs resolve."""
    text = """
external api_key
from collections import OrderedDict
cfg = OrderedDict(token=@api_key)
__entrypoint__ = @cfg
"""
    uri = "file:///main.pfig"
    idx = _index_for(text, uri)
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=idx, mode="all")
    assert vm["ok"] is True
    assert "api_key" in {n["id"] for n in vm["nodes"]}
    ext = next(n for n in vm["nodes"] if n["id"] == "api_key")
    assert ext["kind"] == "external"
    assert ext["flags"]["isExternal"] is True
    assert ext["binding"]["isExternal"] is True
    assert ext["binding"]["externalMissing"] is True
    ports = {(e["source"], e["target"], e["targetPort"]) for e in vm["edges"]}
    assert ("api_key", "cfg", "token") in ports


def test_kwarg_names_in_signature_order():
    """Visualization orders kwargs like the callable signature, not alphabetically."""

    def f(zebra=1, apple=2, banana=3):
        return None

    # Source/dict order is apple, zebra; alphabetical would be apple, zebra; signature is zebra, apple
    assert _kwarg_names_in_signature_order(f, {"apple": 0, "zebra": 1}) == [
        "zebra",
        "apple",
    ]

    # Names not on the signature follow, in dict insertion order
    assert _kwarg_names_in_signature_order(f, {"apple": 0, "extra": 1, "zebra": 2}) == [
        "zebra",
        "apple",
        "extra",
    ]


def test_profile_only_skipped(ws: WorkspaceState):
    text = "override foo = 1\n"
    uri = "file:///profile.pfig"
    idx = _index_for(text, uri)
    vm = build_graph_view_model(text=text, uri=uri, ws=ws, index=idx, mode="all")
    assert vm["ok"] is False
    assert "profile" in (vm.get("error") or "").lower()
