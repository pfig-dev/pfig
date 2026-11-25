"""Tests for the LSP API (parse with index, resolve_include_path, resolve_provider_ref)."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from pfig import (
    DocumentIndex,
    ParseResult,
    compile_pfig,
    index_from_ast,
    node_at_position,
    parse_pfig_source,
    resolve_include_path,
    resolve_provider_ref,
)


class TestParseWithIndex:
    """Test parse_pfig_source with with_index=True."""

    def test_parse_with_index_returns_parse_result(self):
        source = "batch_size = 64"
        result = parse_pfig_source(source, with_index=True)
        assert isinstance(result, ParseResult)
        assert result.ast is not None
        assert result.index is not None

    def test_parse_without_index_returns_ast(self):
        source = "batch_size = 64"
        result = parse_pfig_source(source)
        assert not isinstance(result, ParseResult)
        assert hasattr(result, "statements")

    def test_index_has_binding_defs(self):
        source = "batch_size = 64\nepochs = 10"
        result = parse_pfig_source(source, with_index=True)
        assert "batch_size" in result.index.binding_defs
        assert "epochs" in result.index.binding_defs
        b = result.index.binding_defs["batch_size"]
        assert b.uri.startswith("file://")
        assert "start" in b.range and "end" in b.range

    def test_index_has_refs_and_call_sites(self):
        source = """
include "./consts.pfig" as consts
model = MLP(hidden_sizes=@consts/hidden_sizes, dropout=0.2)
"""
        result = parse_pfig_source(source, filename="main.pfig", with_index=True)
        assert len(result.index.refs) >= 1
        assert any(r.name == "consts/hidden_sizes" for r in result.index.refs)
        assert len(result.index.call_sites) >= 1
        assert any(c.callee == "MLP" for c in result.index.call_sites)

    def test_node_at_position(self):
        source = "batch_size = 64"
        result = parse_pfig_source(source, with_index=True)
        node = node_at_position(result.index, {"line": 0, "character": 0})
        assert node is not None
        assert node["kind"] == "binding"
        assert node["name"] == "batch_size"


class TestResolveIncludePath:
    """Test resolve_include_path LSP API."""

    def test_relative_path(self):
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "consts.pfig").write_text("x = 1")
            main = tmp_path / "main.pfig"
            main.write_text('include "./consts.pfig" as c')
            resolved = resolve_include_path("./consts.pfig", str(main))
            assert resolved is not None
            assert "consts.pfig" in resolved

    def test_nonexistent_returns_none(self):
        with TemporaryDirectory() as tmpdir:
            main = Path(tmpdir) / "main.pfig"
            main.write_text("x = 1")
            resolved = resolve_include_path("./nonexistent.pfig", str(main))
            assert resolved is None


class TestResolveProviderRef:
    """Test resolve_provider_ref."""

    def test_simple_binding(self):
        source = "epochs = 5\nx = @epochs"
        result = parse_pfig_source(source, filename="/tmp/test.pfig", with_index=True)
        res = resolve_provider_ref("epochs", "/tmp/test.pfig", result.index, {})
        assert res is not None
        uri, rng = res
        assert uri.startswith("file://")
        assert "start" in rng

    def test_included_binding(self):
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "consts.pfig").write_text("epochs = 5")
            main = tmp_path / "main.pfig"
            main.write_text('include "./consts.pfig" as consts\nx = @consts/epochs')
            result = parse_pfig_source(main.read_text(), filename=str(main), with_index=True)
            resolved = resolve_include_path("./consts.pfig", str(main))
            res = resolve_provider_ref(
                "consts/epochs",
                str(main),
                result.index,
                {"./consts.pfig": resolved},
            )
            assert res is not None
            uri, rng = res
            assert "consts.pfig" in uri


class TestCompileIncludeGraph:
    """Test include_graph in compile result."""

    def test_include_graph_present(self):
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "base.pfig").write_text("x = 1")
            main = tmp_path / "main.pfig"
            main.write_text('include "./base.pfig" as base')
            from pfig import parse_pfig_source

            ast = parse_pfig_source(main.read_text(), filename=str(main))
            compiled, issues = compile_pfig(ast, filename=str(main))
            assert "include_graph" in compiled
            graph = compiled["include_graph"]
            assert isinstance(graph, dict)
            main_resolved = str(main.resolve())
            if main_resolved in graph:
                assert len(graph[main_resolved]) >= 1
