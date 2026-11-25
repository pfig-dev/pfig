"""Include path resolution for pfig files.

Uses pfig's resolve_include_path for explicit filesystem paths.
Also provides cycle detection.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import pathname2url, url2pathname

from pfig.lsp.state import DocumentIndex, IncludeEdge, WorkspaceState


def resolve_includes(
    edge: IncludeEdge,
    current_uri: str,
    ws: WorkspaceState,
) -> str | None:
    """Resolve an include edge to an absolute file URI (requires pfig >= 1.11)."""
    from pfig import resolve_include_path

    current_path = _uri_to_path(current_uri)
    if not current_path:
        return None
    resolved = resolve_include_path(
        edge.target_path,
        current_path,
    )
    return _path_to_uri(resolved) if resolved else None


def detect_include_cycles(uri: str, ws: WorkspaceState) -> list[list[str]] | None:
    """Return a list of cycle witness chains, or ``None`` if no cycles."""
    cycles: list[list[str]] = []
    visited: set[str] = set()
    path: list[str] = []

    def dfs(node: str) -> None:
        if node in visited:
            return
        if node in path:
            cycle_start = path.index(node)
            cycles.append(path[cycle_start:] + [node])
            return
        path.append(node)
        for child in ws.include_graph.get(node, set()):
            dfs(child)
        path.pop()
        visited.add(node)

    dfs(uri)
    return cycles if cycles else None


# ---------------------------------------------------------------------------
# URI <-> path helpers
# ---------------------------------------------------------------------------


def _uri_to_path(uri: str) -> str | None:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return url2pathname(parsed.path)
    return None


def _path_to_uri(path: str) -> str:
    abs_path = os.path.abspath(path)
    return "file://" + pathname2url(abs_path)


def load_index_for_uri(uri: str) -> DocumentIndex | None:
    """Load and index a file by URI. Returns None if file cannot be read."""
    from pfig.lsp.analysis.pfig_adapter import load_index_for_uri as _load

    return _load(uri)
