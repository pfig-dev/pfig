"""
Document index for LSP integration.

Builds a structural index from a parsed pfig AST, with LSP-oriented ranges
(0-based line and character). Used by the pfig-vscode-syntax LSP for
go-to-definition, hover, completions, and diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.request import pathname2url

from .ast_nodes import (
    BinaryOpExpr,
    Binding,
    BuiltinCallExpr,
    CallExpr,
    DictExpr,
    DottedNameExpr,
    Expr,
    ExternalStmt,
    FromImportStmt,
    ImportStmt,
    IncludeStmt,
    ListExpr,
    OverrideStmt,
    PathExpr,
    PFigFile,
    ProviderMethodRefExpr,
    ProviderRefExpr,
    TupleExpr,
    UnaryOpExpr,
)
from .lexer import Position
from .lsp_utils import position_to_lsp, position_to_offset, range_from_position


def _path_to_uri(filename: str) -> str:
    """Convert filesystem path to file:// URI."""
    path = Path(filename).resolve()
    return "file://" + pathname2url(str(path))


@dataclass
class BindingDef:
    """A binding definition (name = expr)."""

    name: str
    uri: str
    range: dict  # LSP Range


@dataclass
class ImportBinding:
    """import x or from x import y."""

    local_name: str
    module: str
    imported_name: Optional[str]  # None for bare "import module"
    range: dict  # span of the imported name (for GTD on the name)
    module_range: dict  # span of the module path


@dataclass
class IncludeEdge:
    """include "path" or include "path" as alias."""

    target_path: str
    alias: Optional[str]  # None if no "as alias"
    range: dict  # span of the path string including quotes


@dataclass
class RefInfo:
    """A reference to @provider or dotted Python symbol."""

    name: str
    range: dict
    kind: str  # "provider" | "python_symbol"


@dataclass
class CallSite:
    """A call site: callee(...) or @provider(...)."""

    callee: str
    callee_range: dict
    kwargs: Dict[str, dict]  # kwarg name -> LSP Range of the kwarg name
    full_range: dict


@dataclass
class ParseResult:
    """Result of parsing with index. Returned by parse_pfig_source(with_index=True)."""

    ast: PFigFile
    index: DocumentIndex


@dataclass
class DocumentIndex:
    """Structural index of a pfig document for LSP consumption."""

    binding_defs: Dict[str, BindingDef] = field(default_factory=dict)
    external_defs: Dict[str, BindingDef] = field(default_factory=dict)
    refs: List[RefInfo] = field(default_factory=list)
    imports: Dict[str, ImportBinding] = field(default_factory=dict)
    includes: List[IncludeEdge] = field(default_factory=list)
    call_sites: List[CallSite] = field(default_factory=list)
    externals: List[str] = field(default_factory=list)
    overrides: Dict[str, dict] = field(default_factory=dict)


def _make_range(pos: Position, length: int, source: str) -> dict:
    """Build LSP Range from position and length."""
    return range_from_position(pos, length, source)


def _path_expr_length(path: PathExpr) -> int:
    """Length of path as written: a/b/c -> len('a/b/c')."""
    return len("/".join(path.parts))


def _dotted_expr_length(expr: DottedNameExpr) -> int:
    """Length of dotted name as written."""
    return len(".".join(expr.parts))


def _collect_refs_and_calls(
    expr: Expr,
    source: str,
    uri: str,
    refs: List[RefInfo],
    call_sites: List[CallSite],
) -> None:
    """Recursively walk expression tree to collect refs and call sites."""
    if isinstance(expr, ProviderRefExpr):
        name = "/".join(expr.path.parts)
        length = 1 + _path_expr_length(expr.path)  # @ + path
        rng = _make_range(expr.position, length, source)
        refs.append(RefInfo(name=name, range=rng, kind="provider"))

    elif isinstance(expr, ProviderMethodRefExpr):
        attr_suffix = "." + ".".join(expr.attrs)
        name = "/".join(expr.path.parts) + attr_suffix
        length = 1 + _path_expr_length(expr.path) + len(attr_suffix)
        rng = _make_range(expr.position, length, source)
        refs.append(RefInfo(name=name, range=rng, kind="provider"))

    elif isinstance(expr, DottedNameExpr):
        name = ".".join(expr.parts)
        length = _dotted_expr_length(expr)
        rng = _make_range(expr.position, length, source)
        refs.append(RefInfo(name=name, range=rng, kind="python_symbol"))

    elif isinstance(expr, CallExpr):
        if isinstance(expr.callable, ProviderRefExpr):
            callee = "@" + "/".join(expr.callable.path.parts)
            callee_len = 1 + _path_expr_length(expr.callable.path)
        elif isinstance(expr.callable, DottedNameExpr):
            callee = ".".join(expr.callable.parts)
            callee_len = _dotted_expr_length(expr.callable)
        else:
            callee = "?"
            callee_len = 0

        callee_range = _make_range(expr.callable.position, callee_len, source)

        kwargs_ranges: Dict[str, dict] = {}
        kwarg_positions = getattr(expr, "kwarg_positions", None) or {}
        for kwarg_name, kwarg_value in expr.kwargs.items():
            pos = kwarg_positions.get(kwarg_name) or kwarg_value.position
            kwargs_ranges[kwarg_name] = _make_range(pos, len(kwarg_name), source)
            _collect_refs_and_calls(kwarg_value, source, uri, refs, call_sites)

        full_len = _estimate_call_length(expr, source)
        full_range = _make_range(expr.position, full_len, source)

        call_sites.append(
            CallSite(
                callee=callee,
                callee_range=callee_range,
                kwargs=kwargs_ranges,
                full_range=full_range,
            )
        )

        if not isinstance(expr.callable, (ProviderRefExpr, DottedNameExpr)):
            _collect_refs_and_calls(expr.callable, source, uri, refs, call_sites)

    elif isinstance(expr, ListExpr):
        for e in expr.elements:
            _collect_refs_and_calls(e, source, uri, refs, call_sites)
    elif isinstance(expr, TupleExpr):
        for e in expr.elements:
            _collect_refs_and_calls(e, source, uri, refs, call_sites)
    elif isinstance(expr, DictExpr):
        for k, v in expr.pairs:
            _collect_refs_and_calls(k, source, uri, refs, call_sites)
            _collect_refs_and_calls(v, source, uri, refs, call_sites)
    elif isinstance(expr, BinaryOpExpr):
        _collect_refs_and_calls(expr.left, source, uri, refs, call_sites)
        _collect_refs_and_calls(expr.right, source, uri, refs, call_sites)
    elif isinstance(expr, UnaryOpExpr):
        _collect_refs_and_calls(expr.operand, source, uri, refs, call_sites)
    elif isinstance(expr, BuiltinCallExpr):
        for arg in expr.args:
            _collect_refs_and_calls(arg, source, uri, refs, call_sites)


def _estimate_call_length(expr: CallExpr, source: str) -> int:
    """Estimate length of call from position to end. Use heuristics."""
    start_offset = position_to_offset(source, expr.position.line, expr.position.column)
    rest = source[start_offset:]
    depth = 0
    for i, c in enumerate(rest):
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return min(len(rest), 200)


def index_from_ast(
    ast: PFigFile,
    filename: str,
    source: str,
    uri: Optional[str] = None,
) -> DocumentIndex:
    """Build a DocumentIndex from a parsed AST.

    Args:
        ast: Parsed PFigFile AST
        filename: Path to source file (for resolving relative includes)
        source: Full source text (needed for computing ranges)
        uri: Optional file:// URI. If None, constructed from filename.

    Returns:
        DocumentIndex with all structural info and LSP ranges (0-based)
    """
    file_uri = uri or _path_to_uri(filename)
    index = DocumentIndex()

    for stmt in ast.statements:
        if isinstance(stmt, Binding):
            rng = _make_range(stmt.position, len(stmt.name), source)
            index.binding_defs[stmt.name] = BindingDef(name=stmt.name, uri=file_uri, range=rng)

        elif isinstance(stmt, ExternalStmt):
            name = str(stmt.path)
            length = _path_expr_length(stmt.path)
            rng = _make_range(stmt.path.position, length, source)
            index.external_defs[name] = BindingDef(name=name, uri=file_uri, range=rng)
            index.externals.append(name)

        elif isinstance(stmt, ImportStmt):
            local_name = stmt.alias or stmt.module.split(".")[0]
            module_pos = stmt.module_position or stmt.position
            module_range = _make_range(module_pos, len(stmt.module), source)
            if stmt.alias and stmt.alias_position:
                name_range = _make_range(stmt.alias_position, len(stmt.alias), source)
            else:
                name_range = module_range
            index.imports[local_name] = ImportBinding(
                local_name=local_name,
                module=stmt.module,
                imported_name=None,
                range=name_range,
                module_range=module_range,
            )

        elif isinstance(stmt, FromImportStmt):
            module_pos = stmt.module_position or stmt.position
            module_range = _make_range(module_pos, len(stmt.module), source)
            for item in stmt.names:
                local_name = item.alias or item.name
                pos = (
                    item.alias_position
                    if (item.alias and item.alias_position)
                    else item.name_position
                )
                if pos:
                    name_len = len(item.alias or item.name)
                    name_range = _make_range(pos, name_len, source)
                else:
                    name_range = module_range
                index.imports[local_name] = ImportBinding(
                    local_name=local_name,
                    module=stmt.module,
                    imported_name=item.name,
                    range=name_range,
                    module_range=module_range,
                )

        elif isinstance(stmt, IncludeStmt):
            path_pos = stmt.path_position or stmt.position
            path_len = len(stmt.path) + 2
            rng = _make_range(path_pos, path_len, source)
            index.includes.append(
                IncludeEdge(
                    target_path=stmt.path,
                    alias=stmt.alias,
                    range=rng,
                )
            )

        elif isinstance(stmt, OverrideStmt):
            target = str(stmt.path)
            length = _path_expr_length(stmt.path)
            rng = _make_range(stmt.path.position, length, source)
            index.overrides[target] = rng

    for binding in ast.bindings():
        _collect_refs_and_calls(binding.value, source, file_uri, index.refs, index.call_sites)

    for override in ast.overrides():
        _collect_refs_and_calls(override.value, source, file_uri, index.refs, index.call_sites)

    return index


def node_at_position(index: DocumentIndex, pos: dict) -> Optional[dict]:
    """Return {kind, name, range} for the node at LSP position pos, or None.

    Args:
        index: DocumentIndex to search
        pos: LSP Position {"line": int, "character": int} (0-based)

    Returns:
        dict with kind, name, range, or None if nothing at position
    """
    line, char = pos.get("line", 0), pos.get("character", 0)

    def contains(rng: dict) -> bool:
        start = rng["start"]
        end = rng["end"]
        if line < start["line"] or line > end["line"]:
            return False
        if line == start["line"] and char < start["character"]:
            return False
        if line == end["line"] and char > end["character"]:
            return False
        return True

    for name, binding in index.binding_defs.items():
        if contains(binding.range):
            return {"kind": "binding", "name": name, "range": binding.range}

    for name, binding in index.external_defs.items():
        if contains(binding.range):
            return {"kind": "external", "name": name, "range": binding.range}

    for local_name, imp in index.imports.items():
        if contains(imp.range):
            return {"kind": "import", "name": local_name, "range": imp.range}

    for ref in index.refs:
        if contains(ref.range):
            return {"kind": "ref", "name": ref.name, "range": ref.range}

    for edge in index.includes:
        if contains(edge.range):
            return {
                "kind": "include",
                "name": edge.target_path,
                "range": edge.range,
            }

    for target, rng in index.overrides.items():
        if contains(rng):
            return {"kind": "override", "name": target, "range": rng}

    for call in index.call_sites:
        if contains(call.full_range):
            return {"kind": "call_site", "name": call.callee, "range": call.full_range}
        for kwarg_name, kwarg_range in call.kwargs.items():
            if contains(kwarg_range):
                return {
                    "kind": "kwarg",
                    "name": kwarg_name,
                    "range": kwarg_range,
                }

    return None
