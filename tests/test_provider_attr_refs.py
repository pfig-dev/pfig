"""Tests for provider attribute values and nested getattr chains."""

from __future__ import annotations

from types import SimpleNamespace

from pfig import parse_pfig_source
from pfig.ast_nodes import ProviderMethodRefExpr
from pfig.execution import get_materialized_bindings


def test_parse_nested_provider_attr_chain() -> None:
    ast = parse_pfig_source("result = @manager.topics.cmd_arms_left_tcp_pose")
    binding = ast.bindings()[0]
    assert isinstance(binding.value, ProviderMethodRefExpr)
    assert binding.value.path.parts == ["manager"]
    assert binding.value.attrs == ["topics", "cmd_arms_left_tcp_pose"]
    assert binding.value.method == "topics"


def test_noncallable_attribute_value() -> None:
    source = """
from types import SimpleNamespace

ns = SimpleNamespace(path="/cmd/arms/left/tcp_pose")
result = @ns.path
"""
    ast = parse_pfig_source(source)
    out = get_materialized_bindings(ast, entrypoint="result")
    assert out["result"] == "/cmd/arms/left/tcp_pose"


def test_nested_attribute_chain_materializes() -> None:
    source = """
from types import SimpleNamespace

manager = SimpleNamespace(
    topics=SimpleNamespace(
        cmd_arms_left_tcp_pose="/cmd/arms/left/tcp_pose",
        cmd_arms_right_tcp_pose="/cmd/arms/right/tcp_pose",
    )
)
topics = @manager.topics
left = @manager.topics.cmd_arms_left_tcp_pose
right = @topics.cmd_arms_right_tcp_pose
result = {"topics": @topics, "left": @left, "right": @right}
"""
    ast = parse_pfig_source(source)
    out = get_materialized_bindings(ast, entrypoint="result")["result"]
    assert isinstance(out["topics"], SimpleNamespace)
    assert out["left"] == "/cmd/arms/left/tcp_pose"
    assert out["right"] == "/cmd/arms/right/tcp_pose"


def test_reexport_namespace_then_leaf_attr() -> None:
    source = """
from types import SimpleNamespace

manager = SimpleNamespace(
    topics=SimpleNamespace(cmd_arms_left_tcp_pose="/cmd/arms/left/tcp_pose")
)
topics = @manager.topics
result = @topics.cmd_arms_left_tcp_pose
"""
    ast = parse_pfig_source(source)
    out = get_materialized_bindings(ast, entrypoint="result")
    assert out["result"] == "/cmd/arms/left/tcp_pose"


class _CountingChild:
    """Property that counts reads so attribute walks can be checked for double lookup."""

    def __init__(self, child: "_CountingChild | None" = None) -> None:
        self._child = child
        self.reads = 0

    @property
    def child(self) -> "_CountingChild | None":
        self.reads += 1
        return self._child


def test_attr_chain_reads_each_segment_once() -> None:
    source = f"""
from {__name__} import _CountingChild

a = _CountingChild()
b = _CountingChild(child=@a)
c = _CountingChild(child=@b)
a_obj = @a
b_obj = @b
c_obj = @c
result = {{"walked": @c.child.child.child, "a": @a_obj, "b": @b_obj, "c": @c_obj}}
"""
    ast = parse_pfig_source(source)
    result = get_materialized_bindings(ast, entrypoint="result")["result"]
    assert result["walked"] is None
    assert result["c"].reads == 1
    assert result["b"].reads == 1
    assert result["a"].reads == 1


def test_bound_methods_still_work() -> None:
    source = """
from collections import defaultdict

counter = defaultdict(default_factory=int)
clear_method = @counter.clear
result = @clear_method
"""
    ast = parse_pfig_source(source)
    out = get_materialized_bindings(ast, entrypoint="result")
    assert callable(out["result"])
