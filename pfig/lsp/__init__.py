"""
Public LSP API for pfig.

Provides parse_pfig_source(with_index), resolve_include_path, resolve_provider_ref,
and related types for editor/LSP integrations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..includes import resolve_include_path as _resolve_include_path
from ..lsp_index import (
    BindingDef,
    CallSite,
    DocumentIndex,
    ImportBinding,
    IncludeEdge,
    ParseResult,
    RefInfo,
    index_from_ast,
    node_at_position,
)
from ..parser import parse_pfig_source


def resolve_provider_ref(
    name: str,
    current_filename: str,
    index: DocumentIndex,
    resolved_includes: dict[str, str],
) -> Optional[tuple[str, dict]]:
    """Resolve a provider ref to its definition location.

    Args:
        name: e.g. "epochs" or "consts/epochs"
        current_filename: File containing the ref
        index: DocumentIndex of current file
        resolved_includes: Pre-resolved include paths (target_path -> resolved_abs_path)

    Returns:
        (uri, range) of the definition, or None.
        For overrides: returns the override location first.
    """
    from pathlib import Path
    from urllib.request import pathname2url

    def path_to_uri(p: str) -> str:
        return "file://" + pathname2url(str(Path(p).resolve()))

    current_uri = path_to_uri(current_filename)

    # Check override first (per spec: return override location first)
    if name in index.overrides:
        return (current_uri, index.overrides[name])

    # Simple name (no slash): look in current file
    if "/" not in name:
        if name in index.binding_defs:
            b = index.binding_defs[name]
            return (b.uri, b.range)
        if name in index.external_defs:
            b = index.external_defs[name]
            return (b.uri, b.range)
        return None

    # alias/name: look in included file
    alias, local_name = name.split("/", 1)
    for edge in index.includes:
        if edge.alias == alias:
            target_path = edge.target_path
            resolved = resolved_includes.get(target_path)
            if not resolved:
                resolved = resolve_include_path(target_path, current_filename)
            if not resolved:
                return None
            try:
                content = Path(resolved).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return None
            from ..parser import parse_pfig_source

            ast = parse_pfig_source(content, filename=resolved)
            inc_index = index_from_ast(ast, resolved, content)
            if local_name in inc_index.binding_defs:
                b = inc_index.binding_defs[local_name]
                return (b.uri, b.range)
            if local_name in inc_index.external_defs:
                b = inc_index.external_defs[local_name]
                return (b.uri, b.range)
            return None
    return None


def resolve_include_path(
    target_path: str,
    current_filename: str,
) -> Optional[str]:
    """Resolve an include path to an absolute file path (LSP-friendly API).

    Args:
        target_path: Explicit filesystem path from source, e.g. "./consts.pfig"
        current_filename: Absolute path of the file containing the include

    Returns:
        Absolute path to the resolved file, or None if not found.
    """
    base_path = Path(current_filename).resolve()
    try:
        resolution = _resolve_include_path(target_path, base_path=base_path)
        return str(resolution.path)
    except (ValueError, OSError):
        return None


__all__ = [
    "parse_pfig_source",
    "index_from_ast",
    "node_at_position",
    "resolve_include_path",
    "resolve_provider_ref",
    "ParseResult",
    "DocumentIndex",
    "BindingDef",
    "ImportBinding",
    "IncludeEdge",
    "RefInfo",
    "CallSite",
]
