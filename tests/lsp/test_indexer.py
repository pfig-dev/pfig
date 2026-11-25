"""Tests for the pfig document indexer."""

from __future__ import annotations

from pathlib import Path

from lsprotocol.types import Position

from pfig.lsp.analysis.pfig_adapter import index_document, node_at_position

FIXTURES = Path(__file__).parent / "fixtures" / "pfig"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class TestImportParsing:
    def test_bare_import(self):
        text = "import os\n"
        idx = index_document(text, "file:///test.pfig")
        assert "os" in idx.imports
        imp = idx.imports["os"]
        assert imp.module == "os"
        assert imp.imported_name is None

    def test_import_with_alias(self):
        text = "import numpy.random as rng\n"
        idx = index_document(text, "file:///test.pfig")
        assert "rng" in idx.imports
        assert idx.imports["rng"].module == "numpy.random"

    def test_from_import(self):
        text = "from collections import OrderedDict, defaultdict\n"
        idx = index_document(text, "file:///test.pfig")
        assert "OrderedDict" in idx.imports
        assert "defaultdict" in idx.imports
        assert idx.imports["OrderedDict"].module == "collections"
        assert idx.imports["OrderedDict"].imported_name == "OrderedDict"

    def test_from_import_with_alias(self):
        text = "from collections import OrderedDict as OD\n"
        idx = index_document(text, "file:///test.pfig")
        assert "OD" in idx.imports
        assert idx.imports["OD"].imported_name == "OrderedDict"


class TestIncludeParsing:
    def test_include_with_alias(self):
        text = 'include "base.pfig" as base\n'
        idx = index_document(text, "file:///test.pfig")
        assert len(idx.includes) == 1
        assert idx.includes[0].target_path == "base.pfig"
        assert idx.includes[0].alias == "base"

    def test_from_include(self):
        # pfig grammar: "from path include X" uses different syntax
        import pytest

        pytest.skip("pfig does not support 'from \"path\" include X' syntax")


class TestBindingParsing:
    def test_simple_binding(self):
        text = "model = torch.nn.Linear(in_features=10)\n"
        idx = index_document(text, "file:///test.pfig")
        assert "model" in idx.binding_defs
        assert idx.binding_defs["model"].name == "model"

    def test_namespaced_binding(self):
        # pfig grammar does not support namespaced bindings (name/with/slash)
        import pytest

        pytest.skip("pfig does not support namespaced bindings (train/model)")


class TestExternalParsing:
    def test_external(self):
        text = "external db_url\n"
        idx = index_document(text, "file:///test.pfig")
        assert "db_url" in idx.externals

    def test_namespaced_external(self):
        text = "external config/db_url\n"
        idx = index_document(text, "file:///test.pfig")
        assert "config/db_url" in idx.externals


class TestOverrideParsing:
    def test_override(self):
        text = "override model = torch.nn.Conv2d()\n"
        idx = index_document(text, "file:///test.pfig")
        assert "model" in idx.overrides


class TestRefExtraction:
    def test_provider_ref(self):
        text = "trainer = MyTrainer(model=@model)\n"
        idx = index_document(text, "file:///test.pfig")
        provider_refs = [r for r in idx.refs if r.kind == "provider"]
        assert any(r.name == "model" for r in provider_refs)

    def test_namespaced_provider_ref(self):
        text = "result = Foo(x=@base/model)\n"
        idx = index_document(text, "file:///test.pfig")
        provider_refs = [r for r in idx.refs if r.kind == "provider"]
        assert any(r.name == "base/model" for r in provider_refs)

    def test_dotted_symbol(self):
        text = "model = torch.nn.Linear(in_features=10)\n"
        idx = index_document(text, "file:///test.pfig")
        # pfig index may include as python_symbol or in call_sites
        sym_refs = [r for r in idx.refs if r.kind == "python_symbol"]
        call_callees = [cs.callee for cs in idx.call_sites]
        assert (
            any(r.name == "torch.nn.Linear" for r in sym_refs) or "torch.nn.Linear" in call_callees
        )


class TestCallSites:
    def test_call_site_kwargs(self):
        text = "model = MyClass(lr=0.001, epochs=10)\n"
        idx = index_document(text, "file:///test.pfig")
        assert len(idx.call_sites) >= 1
        cs = idx.call_sites[0]
        assert "lr" in cs.kwargs
        assert "epochs" in cs.kwargs


class TestBlockComments:
    def test_triple_single_quote_comment(self):
        text = "'''\nthis is a comment\n'''\nmodel = Foo()\n"
        idx = index_document(text, "file:///test.pfig")
        assert "model" in idx.binding_defs
        assert len(idx.binding_defs) == 1

    def test_triple_double_quote_comment(self):
        text = '"""\nthis is a comment\n"""\nmodel = Foo()\n'
        idx = index_document(text, "file:///test.pfig")
        assert "model" in idx.binding_defs
        assert len(idx.binding_defs) == 1


class TestNodeAtPosition:
    def test_finds_provider_ref(self):
        text = "result = Foo(x=@model)\n"
        idx = index_document(text, "file:///test.pfig")
        node = node_at_position(idx, Position(line=0, character=16))
        assert node is not None
        assert node.kind == "provider"
        assert node.name == "model"

    def test_returns_none_for_empty_position(self):
        text = "# just a comment\n"
        idx = index_document(text, "file:///test.pfig")
        node = node_at_position(idx, Position(line=0, character=0))
        assert node is None

    def test_finds_call_site_callee(self):
        text = "from foo import Trainer\ntrainer = Trainer(epochs=10)\n"
        idx = index_document(text, "file:///test.pfig")
        node = node_at_position(idx, Position(line=1, character=12))
        assert node is not None
        assert node.kind == "python_symbol"
        assert node.name == "Trainer"

    def test_finds_override(self):
        text = 'include "base.pfig" as base\noverride base/epochs = 10\n'
        idx = index_document(text, "file:///test.pfig")
        node = node_at_position(idx, Position(line=1, character=12))
        assert node is not None
        assert node.kind == "override"
        assert node.name == "base/epochs"

    def test_finds_import(self):
        text = "from torchvision import datasets, transforms\n"
        idx = index_document(text, "file:///test.pfig")
        # Position on "datasets"
        node = node_at_position(idx, Position(line=0, character=25))
        assert node is not None
        assert node.kind == "import"
        assert node.name == "datasets"


class TestBasicFixture:
    def test_basic_fixture_parses(self):
        text = _read_fixture("basic.pfig")
        idx = index_document(text, "file:///basic.pfig")
        assert "os" in idx.imports
        assert "OrderedDict" in idx.imports
        assert "db_url" in idx.externals
        assert "model" in idx.binding_defs
        assert "optimizer" in idx.binding_defs
        assert "trainer" in idx.binding_defs
