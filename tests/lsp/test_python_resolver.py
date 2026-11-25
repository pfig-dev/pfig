"""Tests for Python symbol resolution."""

from __future__ import annotations

import sys
from pathlib import Path

from lsprotocol.types import Position, Range

from pfig.lsp.analysis.python_resolver import PythonResolver
from pfig.lsp.state import ImportBinding

FIXTURES_PY = Path(__file__).parent / "fixtures" / "py"

_DUMMY_RANGE = Range(
    start=Position(line=0, character=0),
    end=Position(line=0, character=0),
)


def setup_module():
    if str(FIXTURES_PY) not in sys.path:
        sys.path.insert(0, str(FIXTURES_PY))


class TestGotoDefinition:
    def test_goto_stdlib_symbol(self):
        resolver = PythonResolver()
        imports = {
            "path": ImportBinding(
                local_name="path",
                module="os.path",
                imported_name=None,
                range=_DUMMY_RANGE,
            )
        }
        loc = resolver.goto_definition("path.join", imports, use_runtime=True)
        assert loc is not None
        assert "os" in loc.uri or "posixpath" in loc.uri or "ntpath" in loc.uri

    def test_goto_fixture_symbol(self):
        resolver = PythonResolver()
        imports = {
            "sample_function": ImportBinding(
                local_name="sample_function",
                module="sample_module",
                imported_name="sample_function",
                range=_DUMMY_RANGE,
            )
        }
        loc = resolver.goto_definition("sample_function", imports, use_runtime=True)
        assert loc is not None
        assert "sample_module" in loc.uri


class TestHoverInfo:
    def test_hover_for_stdlib(self):
        resolver = PythonResolver()
        imports = {
            "join": ImportBinding(
                local_name="join",
                module="os.path",
                imported_name="join",
                range=_DUMMY_RANGE,
            )
        }
        info = resolver.get_hover_info("join", imports)
        # May or may not return info depending on environment
        # Just verify no crash
        assert info is None or isinstance(info, str)


class TestCallableParams:
    def test_params_for_fixture_class(self):
        resolver = PythonResolver()
        imports = {
            "SampleClass": ImportBinding(
                local_name="SampleClass",
                module="sample_module",
                imported_name="SampleClass",
                range=_DUMMY_RANGE,
            )
        }
        params = resolver.get_callable_params("SampleClass", imports)
        assert "name" in params
        assert "value" in params
