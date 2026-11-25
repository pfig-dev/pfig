"""Tests for LSP server helper functions."""

from __future__ import annotations

import sys
from pathlib import Path

from lsprotocol.types import Position, Range

from pfig.lsp.analysis.pfig_adapter import index_document
from pfig.lsp.analysis.python_resolver import PythonResolver
from pfig.lsp.server import (
    _apply_incremental_change,
    _complete_bindings,
    _complete_kwargs,
)
from pfig.lsp.state import DocumentState, WorkspaceState

FIXTURES_PY = Path(__file__).parent / "fixtures" / "py"


def setup_module():
    if str(FIXTURES_PY) not in sys.path:
        sys.path.insert(0, str(FIXTURES_PY))


class TestApplyIncrementalChange:
    def test_replace_mid_line(self):
        text = "line0\nline1\nline2\n"
        # Replace "ne1" (chars 2-5); range (1,2)-(1,5) excludes newline
        r = Range(start=Position(1, 2), end=Position(1, 5))
        result = _apply_incremental_change(text, r, "XXX")
        assert result == "line0\nliXXX\nline2\n"

    def test_replace_across_lines(self):
        text = "aa\nbb\ncc\n"
        # Replace "a\nb" with "X" -> "aX" + rest of line1 ("") + line2
        r = Range(start=Position(0, 1), end=Position(1, 2))
        result = _apply_incremental_change(text, r, "X")
        assert result == "aX\ncc\n"

    def test_replace_at_end_appends(self):
        text = "line0\n"
        r = Range(start=Position(0, 5), end=Position(0, 5))
        result = _apply_incremental_change(text, r, "X")
        assert result == "line0X\n"

    def test_start_line_beyond_text_appends(self):
        text = "line0\n"
        r = Range(start=Position(5, 0), end=Position(5, 0))
        result = _apply_incremental_change(text, r, "appended")
        assert result == "line0\nappended"

    def test_empty_range_inserts(self):
        text = "ab\n"
        r = Range(start=Position(0, 1), end=Position(0, 1))
        result = _apply_incremental_change(text, r, "X")
        assert result == "aXb\n"


class TestCompleteBindings:
    def test_completes_local_bindings(self):
        text = "epochs = 5\nbatch_size = 32\n"
        doc = DocumentState(uri="file:///test.pfig", text=text)
        doc.index = index_document(text, doc.uri)
        ws = WorkspaceState()
        ws.documents[doc.uri] = doc

        items = _complete_bindings(ws, doc.uri, "ep")
        labels = [i.label for i in items]
        assert "epochs" in labels

    def test_completes_included_bindings(self, tmp_path):
        """With real included file, completion includes its bindings."""
        consts = tmp_path / "consts.pfig"
        consts.write_text("batch_size = 32\n")
        main = tmp_path / "main.pfig"
        main.write_text('include "consts.pfig" as consts\nx = @consts/batch_size\n')
        uri = f"file://{main}"
        doc = DocumentState(uri=uri, text=main.read_text())
        doc.index = index_document(doc.text, uri)
        ws = WorkspaceState()
        ws.documents[uri] = doc

        items = _complete_bindings(ws, uri, "consts/")
        labels = [i.label for i in items]
        assert "consts/batch_size" in labels

    def test_empty_typed_returns_all_local(self):
        text = "a = 1\nb = 2\n"
        doc = DocumentState(uri="file:///test.pfig", text=text)
        doc.index = index_document(text, doc.uri)
        ws = WorkspaceState()
        ws.documents[doc.uri] = doc

        items = _complete_bindings(ws, doc.uri, "")
        labels = [i.label for i in items]
        assert "a" in labels
        assert "b" in labels


class TestCompleteKwargs:
    def test_completes_missing_params(self):
        text = "from sample_module import sample_function\nresult = sample_function(x=1, )\n"
        doc = DocumentState(uri="file:///test.pfig", text=text)
        doc.index = index_document(text, doc.uri)
        resolver = PythonResolver()
        # Position inside sample_function(...) - find the call_site's full_range
        for cs in doc.index.call_sites:
            if cs.full_range:
                # Use a position inside the call (e.g. after the comma)
                pos = Position(
                    line=cs.full_range.start.line,
                    character=cs.full_range.start.character + 5,
                )
                items = _complete_kwargs(doc, pos, resolver)
                labels = [i.label for i in items]
                # sample_function has x, y, z; x is already used
                assert "y=" in labels or "z=" in labels
                return
        raise AssertionError("No call site with full_range found")

    def test_no_duplicate_existing_kwargs(self):
        text = "from sample_module import sample_function\nresult = sample_function(x=1, y=2)\n"
        doc = DocumentState(uri="file:///test.pfig", text=text)
        doc.index = index_document(text, doc.uri)
        resolver = PythonResolver()
        pos = Position(line=1, character=35)

        items = _complete_kwargs(doc, pos, resolver)
        labels = [i.label for i in items]
        assert "x=" not in labels
        assert "y=" not in labels
