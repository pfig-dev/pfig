"""Adapter for pfig LSP API (pfig >= 1.11).

Provides parse-and-index, load_index_for_uri, and node_at_position
using pfig's native API instead of the regex-based indexer.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from lsprotocol.types import Position, Range

from pfig.lsp.analysis.resolver import _path_to_uri, _uri_to_path
from pfig.lsp.state import DocumentIndex

logger = logging.getLogger(__name__)


def _dict_to_range(d: dict) -> Range:
    """Convert pfig dict range to lsprotocol Range."""
    start = d.get("start", d)
    end = d.get("end", start)
    return Range(
        start=Position(
            line=start.get("line", 0),
            character=start.get("character", 0),
        ),
        end=Position(
            line=end.get("line", 0),
            character=end.get("character", 0),
        ),
    )


def _ensure_lsp_index(pfig_index: object, uri: str) -> DocumentIndex:
    """Convert pfig index to our DocumentIndex with LSP types."""
    from pfig.lsp.state import (
        BindingDef,
        CallSite,
        ImportBinding,
        IncludeEdge,
        RefInfo,
    )

    idx = DocumentIndex()

    def to_range(obj: object) -> Range:
        if isinstance(obj, Range):
            return obj
        if hasattr(obj, "start") and hasattr(obj, "end"):
            range_obj = cast(Any, obj)
            return Range(start=range_obj.start, end=range_obj.end)
        if isinstance(obj, dict):
            return _dict_to_range(obj)
        return Range(Position(0, 0), Position(0, 0))

    # binding_defs
    for name, bdef in getattr(pfig_index, "binding_defs", {}).items():
        r = to_range(getattr(bdef, "range", bdef) if hasattr(bdef, "range") else bdef)
        idx.binding_defs[name] = BindingDef(name=name, uri=uri, range=r)

    # external_defs
    for name, bdef in getattr(pfig_index, "external_defs", {}).items():
        r = to_range(getattr(bdef, "range", bdef) if hasattr(bdef, "range") else bdef)
        idx.external_defs[name] = BindingDef(name=name, uri=uri, range=r)

    # refs
    for ref in getattr(pfig_index, "refs", []):
        kind = getattr(ref, "kind", "provider")
        if kind == "ref":
            kind = "provider"
        r = to_range(getattr(ref, "range", ref) if hasattr(ref, "range") else ref)
        idx.refs.append(RefInfo(name=getattr(ref, "name", ""), range=r, kind=kind))

    # imports
    for local_name, imp in getattr(pfig_index, "imports", {}).items():
        r = to_range(getattr(imp, "range", imp) if hasattr(imp, "range") else imp)
        mod_range = to_range(getattr(imp, "module_range", r) if hasattr(imp, "module_range") else r)
        idx.imports[local_name] = ImportBinding(
            local_name=local_name,
            module=getattr(imp, "module", ""),
            imported_name=getattr(imp, "imported_name", None),
            range=r,
            module_range=mod_range,
        )

    # includes
    for inc in getattr(pfig_index, "includes", []):
        r = to_range(getattr(inc, "range", inc) if hasattr(inc, "range") else inc)
        idx.includes.append(
            IncludeEdge(
                target_path=getattr(inc, "target_path", ""),
                alias=getattr(inc, "alias", None),
                range=r,
            )
        )

    # call_sites
    for cs in getattr(pfig_index, "call_sites", []):
        callee_r = to_range(getattr(cs, "callee_range", cs) if hasattr(cs, "callee_range") else cs)
        full_r = to_range(
            getattr(cs, "full_range", callee_r) if hasattr(cs, "full_range") else callee_r
        )
        kwargs = {}
        for k, v in getattr(cs, "kwargs", {}).items():
            kwargs[k] = to_range(v)
        idx.call_sites.append(
            CallSite(
                callee=getattr(cs, "callee", ""),
                callee_range=callee_r,
                kwargs=kwargs,
                full_range=full_r,
            )
        )

    # externals
    idx.externals = list(getattr(pfig_index, "externals", []))

    # overrides
    for target, override_val in getattr(pfig_index, "overrides", {}).items():
        idx.overrides[target] = to_range(override_val)

    idx.raw_index = pfig_index
    return idx


def index_document(text: str, uri: str) -> DocumentIndex:
    """Build DocumentIndex from source using pfig's parser (requires pfig >= 1.11)."""
    from pfig import parse_pfig_source

    filename = _uri_to_path(uri)
    if not filename:
        filename = uri

    result = parse_pfig_source(text, filename=filename, with_index=True)
    return _ensure_lsp_index(result.index, uri)


def load_index_for_uri(uri: str) -> DocumentIndex | None:
    """Load and index a file by URI. Returns None if file cannot be read."""
    path_str = _uri_to_path(uri)
    if not path_str:
        return None
    try:
        content = Path(path_str).read_text(encoding="utf-8", errors="replace")
        return index_document(content, uri)
    except OSError:
        return None


def node_at_position(index: DocumentIndex, pos: Position) -> _NodeInfo | None:
    """Return node at position for hover, GTD, completion (requires pfig >= 1.11)."""
    from pfig import node_at_position as pfig_node_at_position

    raw = index.raw_index
    if raw is None:
        return None
    pos_dict = {"line": pos.line, "character": pos.character}
    node = pfig_node_at_position(cast(Any, raw), pos_dict)
    if node is None:
        return None
    if isinstance(node, dict):
        r = node.get("range")
        if isinstance(r, dict):
            r = _dict_to_range(r)
        elif not isinstance(r, Range):
            r = Range(Position(0, 0), Position(0, 0))
        kind = node.get("kind", "")
        if kind == "ref":
            kind = "provider"
        if kind == "call_site":
            kind = "python_symbol"  # callee name for GTD/hover
        return _NodeInfo(kind=kind, name=node.get("name", ""), range=r)
    if hasattr(node, "kind") and hasattr(node, "name") and hasattr(node, "range"):
        kind = getattr(node, "kind", "")
        if kind == "ref":
            kind = "provider"
        if kind == "call_site":
            kind = "python_symbol"
        return _NodeInfo(
            kind=kind,
            name=getattr(node, "name", ""),
            range=getattr(node, "range", Range(Position(0, 0), Position(0, 0))),
        )
    return None


class _NodeInfo:
    """Minimal node info for server compatibility."""

    def __init__(self, kind: str, name: str, range: Range):
        self.kind = kind
        self.name = name
        self.range = range
