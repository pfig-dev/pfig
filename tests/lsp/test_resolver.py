"""Tests for include path resolution."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from lsprotocol.types import Position, Range

from pfig.lsp.analysis.resolver import (
    _uri_to_path,
    detect_include_cycles,
    load_index_for_uri,
    resolve_includes,
)
from pfig.lsp.state import IncludeEdge, WorkspaceState

_DUMMY_RANGE = Range(
    start=Position(line=0, character=0),
    end=Position(line=0, character=0),
)


class TestRelativeIncludes:
    def test_resolves_existing_file(self, tmp_path: Path):
        target = tmp_path / "base.pfig"
        target.write_text("x = 1\n")
        current_uri = f"file://{tmp_path / 'main.pfig'}"
        edge = IncludeEdge(target_path="base.pfig", alias="base", range=_DUMMY_RANGE)
        ws = WorkspaceState()
        result = resolve_includes(edge, current_uri, ws)
        assert result is not None
        assert "base.pfig" in result

    def test_returns_none_for_missing_file(self, tmp_path: Path):
        current_uri = f"file://{tmp_path / 'main.pfig'}"
        edge = IncludeEdge(target_path="nonexistent.pfig", alias="x", range=_DUMMY_RANGE)
        ws = WorkspaceState()
        result = resolve_includes(edge, current_uri, ws)
        assert result is None


def test_protocol_includes_are_not_resolved():
    for path in ("data://config.pfig", "package://my_lib/base.pfig"):
        edge = IncludeEdge(target_path=path, alias="d", range=_DUMMY_RANGE)
        assert resolve_includes(edge, "file:///dummy.pfig", WorkspaceState()) is None


class TestCycleDetection:
    def test_no_cycle(self):
        ws = WorkspaceState()
        ws.include_graph = {
            "file:///a.pfig": {"file:///b.pfig"},
            "file:///b.pfig": {"file:///c.pfig"},
            "file:///c.pfig": set(),
        }
        result = detect_include_cycles("file:///a.pfig", ws)
        assert result is None

    def test_detects_direct_cycle(self):
        ws = WorkspaceState()
        ws.include_graph = {
            "file:///a.pfig": {"file:///b.pfig"},
            "file:///b.pfig": {"file:///a.pfig"},
        }
        result = detect_include_cycles("file:///a.pfig", ws)
        assert result is not None
        assert len(result) >= 1

    def test_detects_indirect_cycle(self):
        ws = WorkspaceState()
        ws.include_graph = {
            "file:///a.pfig": {"file:///b.pfig"},
            "file:///b.pfig": {"file:///c.pfig"},
            "file:///c.pfig": {"file:///a.pfig"},
        }
        result = detect_include_cycles("file:///a.pfig", ws)
        assert result is not None


class TestUriHelpers:
    def test_non_file_uri_returns_none_for_path(self):
        assert _uri_to_path("untitled:///foo.pfig") is None
        assert _uri_to_path("https://example.com/file.pfig") is None

    def test_file_uri_converts_to_path(self):
        assert _uri_to_path("file:///tmp/test.pfig") is not None


class TestNonFileUriResolve:
    """resolve_includes returns None when current_uri is not a file:// URI."""

    def test_untitled_uri_returns_none(self, tmp_path: Path):
        target = tmp_path / "base.pfig"
        target.write_text("x = 1\n")
        edge = IncludeEdge(target_path="base.pfig", alias="base", range=_DUMMY_RANGE)
        ws = WorkspaceState()
        result = resolve_includes(edge, "untitled:///foo.pfig", ws)
        assert result is None

    def test_relative_path_with_current_in_subdir(self, tmp_path: Path):
        """Relative include from file in subdir resolves correctly."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        target = tmp_path / "base.pfig"
        target.write_text("x = 1\n")
        current_uri = f"file://{subdir / 'main.pfig'}"
        edge = IncludeEdge(target_path="../base.pfig", alias="base", range=_DUMMY_RANGE)
        ws = WorkspaceState()
        result = resolve_includes(edge, current_uri, ws)
        assert result is not None
        assert "base.pfig" in result


class TestLoadIndexForUri:
    def test_returns_index_for_existing_file(self, tmp_path: Path):
        p = tmp_path / "test.pfig"
        p.write_text("model = Foo()\n")
        uri = f"file://{p}"
        idx = load_index_for_uri(uri)
        assert idx is not None
        assert "model" in idx.binding_defs

    def test_returns_none_for_missing_file(self, tmp_path: Path):
        uri = f"file://{tmp_path / 'nonexistent.pfig'}"
        idx = load_index_for_uri(uri)
        assert idx is None

    def test_returns_none_for_non_file_uri(self):
        idx = load_index_for_uri("untitled:///foo.pfig")
        assert idx is None
