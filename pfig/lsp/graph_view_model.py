"""Serialize a binding-level graph view model for the VS Code webview (pfig >= 1.11)."""

from __future__ import annotations

import inspect
import logging
import math
import operator
import re
from inspect import Parameter
from pathlib import Path
from typing import Any, cast

from lsprotocol.types import DiagnosticSeverity

from pfig.lsp.analysis.pfig_validator import _is_profile_only, validate_with_pfig
from pfig.lsp.analysis.resolver import _path_to_uri, _uri_to_path, resolve_includes
from pfig.lsp.state import DocumentIndex, WorkspaceState

logger = logging.getLogger(__name__)


def _range_to_dict(r: Any) -> dict[str, Any] | None:
    """Normalize lsprotocol Range or dict to JSON-friendly range."""
    if r is None:
        return None
    if isinstance(r, dict):
        start = r.get("start", r)
        end = r.get("end", start)
        return {
            "start": {
                "line": int(start.get("line", 0)),
                "character": int(start.get("character", 0)),
            },
            "end": {
                "line": int(end.get("line", 0)),
                "character": int(end.get("character", 0)),
            },
        }
    if hasattr(r, "start") and hasattr(r, "end"):
        return {
            "start": {
                "line": int(r.start.line),
                "character": int(r.start.character),
            },
            "end": {
                "line": int(r.end.line),
                "character": int(r.end.character),
            },
        }
    return None


def _pfig_position_to_range_dict(pos: Any) -> dict[str, Any] | None:
    if pos is None:
        return None
    try:
        from pfig.lsp_utils import position_to_lsp

        p = position_to_lsp(pos)
        e = {"line": p["line"], "character": p["character"] + 1}
        return {"start": dict(p), "end": e}
    except Exception:
        return None


_SLOT_PORT_RE = re.compile(r"^__slot(\d+)__$")


def _is_slot_port(name: str) -> bool:
    return _SLOT_PORT_RE.match(name) is not None


def _binding_leaf_name(binding_id: str) -> str:
    """Last segment of a qualified binding id (card title); full id stays on `id` and outgoing edges."""
    if "/" in binding_id:
        return binding_id.rsplit("/", 1)[-1]
    return binding_id


def _normalize_graph_uri(uri: str) -> str:
    if uri.startswith("file://"):
        return uri
    p = _uri_to_path(uri)
    if p:
        return _path_to_uri(p)
    return uri


def _expr_span_lsp_range(expr: Any, source: str) -> dict[str, Any] | None:
    """Full LSP range for a bracketed expression (list / dict / tuple)."""
    from pfig.lsp_utils import offset_to_lsp, position_to_offset

    pos = getattr(expr, "position", None)
    if pos is None:
        return None
    start_off = position_to_offset(source, pos.line, pos.column)
    if start_off >= len(source):
        return None
    opener = source[start_off]
    closers = {"[": "]", "{": "}", "(": ")"}
    if opener not in closers:
        from pfig.lsp_utils import range_from_position

        return range_from_position(pos, 1, source)
    closer = closers[opener]
    depth = 0
    i = start_off
    while i < len(source):
        c = source[i]
        if c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                end = i + 1
                return {
                    "start": offset_to_lsp(source, start_off),
                    "end": offset_to_lsp(source, end),
                }
        i += 1
    return None


def _binding_stmt_by_name(ast: Any, name: str) -> Any | None:
    from pfig.ast_nodes import Binding

    for stmt in getattr(ast, "statements", []):
        if isinstance(stmt, Binding) and stmt.name == name:
            return stmt
    return None


def _navigate_ast_path(expr: Any, steps: list[tuple[str, str | int]]) -> Any | None:
    from pfig.ast_nodes import CallExpr, DictExpr, ListExpr, TupleExpr

    cur: Any = expr
    for kind, val in steps:
        if kind == "k":
            if not isinstance(cur, CallExpr):
                return None
            if not isinstance(val, str):
                return None
            cur = cur.kwargs.get(val)
        elif kind == "s":
            idx = int(val)
            if isinstance(cur, (ListExpr, TupleExpr)):
                if idx >= len(cur.elements):
                    return None
                cur = cur.elements[idx]
            elif isinstance(cur, DictExpr):
                if idx >= len(cur.pairs):
                    return None
                cur = cur.pairs[idx][1]
            else:
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def _call_expr_span_lsp_range(expr: Any, source: str) -> dict[str, Any] | None:
    """LSP range covering a CallExpr from its start (@ or callee) through closing ``)``."""
    from pfig.ast_nodes import CallExpr
    from pfig.lsp_utils import offset_to_lsp, position_to_offset

    if not isinstance(expr, CallExpr):
        return None
    pos = getattr(expr, "position", None)
    if pos is None:
        return None
    start_off = position_to_offset(source, pos.line, pos.column)
    rest = source[start_off:]
    depth = 0
    for i, c in enumerate(rest):
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                end_off = start_off + i + 1
                return {
                    "start": offset_to_lsp(source, start_off),
                    "end": offset_to_lsp(source, end_off),
                }
    return None


def _fresh_call_active_definition(
    text: str,
    uri: str,
    root_binding: str,
    ast_path: list[tuple[str, str | int]],
    pfig_ast: Any | None | bool = None,
) -> dict[str, Any] | None:
    """activeDefinition for inline ``@provider(...)`` (compiled as FreshProviderCall)."""
    if not root_binding or not text.strip():
        return None
    try:
        from pfig.ast_nodes import CallExpr
    except Exception:
        return None
    if pfig_ast is False:
        return None
    if pfig_ast is None:
        try:
            from pfig import parse_pfig_source

            filename = _uri_to_path(uri) or uri or "<string>"
            pfig_ast = parse_pfig_source(text, filename=filename, with_index=False)
        except Exception:
            return None
    bstmt = _binding_stmt_by_name(pfig_ast, root_binding)
    if bstmt is None:
        return None
    target = _navigate_ast_path(bstmt.value, ast_path)
    if not isinstance(target, CallExpr):
        return None
    span = _call_expr_span_lsp_range(target, text)
    if span is None:
        return None
    return {"uri": _normalize_graph_uri(uri), "range": span}


def _collection_active_definition(
    text: str,
    uri: str,
    root_binding: str,
    ast_path: list[tuple[str, str | int]],
    pfig_ast: Any | None | bool = None,
) -> dict[str, Any] | None:
    if not ast_path or not root_binding or not text.strip():
        return None
    try:
        from pfig.ast_nodes import DictExpr, ListExpr, TupleExpr
    except Exception:
        return None
    if pfig_ast is False:
        return None
    if pfig_ast is None:
        try:
            from pfig import parse_pfig_source

            filename = _uri_to_path(uri) or uri or "<string>"
            pfig_ast = parse_pfig_source(text, filename=filename, with_index=False)
        except Exception:
            return None
    bstmt = _binding_stmt_by_name(pfig_ast, root_binding)
    if bstmt is None:
        return None
    target = _navigate_ast_path(bstmt.value, ast_path)
    if not isinstance(target, (ListExpr, DictExpr, TupleExpr)):
        return None
    span = _expr_span_lsp_range(target, text)
    if span is None:
        return None
    return {"uri": _normalize_graph_uri(uri), "range": span}


class _GraphSources:
    """Resolve authored locations with one index and include lookup per source file."""

    def __init__(self, entry_path: str, entry_index: DocumentIndex, ws: WorkspaceState):
        self.entry_path = str(Path(entry_path).resolve()) if entry_path else ""
        self.ws = ws
        self.indexes: dict[str, DocumentIndex | None] = {self.entry_path: entry_index}
        self.includes: dict[str, dict[str, str]] = {}

    def _index(self, path: str) -> DocumentIndex | None:
        from pfig import parse_pfig_source
        from pfig.lsp.analysis.pfig_adapter import _ensure_lsp_index

        if path not in self.indexes:
            try:
                text = Path(path).read_text(encoding="utf-8")
            except OSError:
                self.indexes[path] = None
            else:
                parsed = parse_pfig_source(text, filename=path, with_index=True)
                self.indexes[path] = _ensure_lsp_index(parsed.index, _path_to_uri(path))
        return self.indexes[path]

    def _included_files(self, path: str, index: DocumentIndex) -> dict[str, str]:
        if path not in self.includes:
            uri = _path_to_uri(path)
            paths: dict[str, str | None] = {}
            aliases: dict[str, str] = {}
            for edge in index.includes:
                if edge.alias is None:
                    continue
                if edge.target_path not in paths:
                    target = resolve_includes(edge, uri, self.ws)
                    paths[edge.target_path] = _uri_to_path(target) if target else None
                if (child := paths[edge.target_path]) is not None:
                    aliases.setdefault(edge.alias, child)
            self.includes[path] = aliases
        return self.includes[path]

    def binding_location(
        self,
        binding_id: str,
        *,
        include_overrides: bool = True,
    ) -> tuple[str, dict[str, Any], bool] | None:
        path = self.entry_path
        while path:
            index = self._index(path)
            if index is None:
                return None
            if include_overrides and binding_id in index.overrides:
                rd = _range_to_dict(index.overrides[binding_id])
                return (_path_to_uri(path), rd, True) if rd is not None else None
            alias, sep, remainder = binding_id.partition("/")
            if not sep:
                binding = index.binding_defs.get(binding_id) or index.external_defs.get(binding_id)
                rd = _range_to_dict(binding.range) if binding else None
                return (
                    (_normalize_graph_uri(binding.uri), rd, False)
                    if binding and rd is not None
                    else None
                )
            path = self._included_files(path, index).get(alias, "")
            binding_id = remainder
        return None


def _callable_fqn(obj: Any) -> str:
    try:
        from pfig.snapshot import _get_callable_fqn

        return _get_callable_fqn(obj)
    except Exception:
        return str(obj)


def _unwrap_literal_value(val: Any) -> Any:
    from pfig.execution import LiteralValue

    v = val
    while isinstance(v, LiteralValue):
        v = v.value
    return v


def _strip_literals_for_display(val: Any) -> Any:
    """
    Recursively unwrap ``LiteralValue`` so list/tuple/dict previews show plain values
    (e.g. ``(0.49, 0.48)`` instead of ``LiteralValue(value=0.49), …``).
    """
    from pfig.execution import LiteralValue

    v = val
    while isinstance(v, LiteralValue):
        v = v.value
    if isinstance(v, list):
        return [_strip_literals_for_display(x) for x in v]
    if isinstance(v, tuple):
        return tuple(_strip_literals_for_display(x) for x in v)
    if isinstance(v, dict):
        return {
            _strip_literals_for_display(k): _strip_literals_for_display(x) for k, x in v.items()
        }
    if isinstance(v, (set, frozenset)):
        ctor = frozenset if isinstance(v, frozenset) else set
        return ctor(_strip_literals_for_display(x) for x in v)
    return v


# Bound list previews so config data cannot dominate card layout.
_VALUE_SEQUENCE_LIMIT = 8


def _preview_reference(path: str, namespace: str) -> str:
    return "@" + (path.removeprefix(namespace + "/") if namespace else path)


def _clip_preview(text: str, limit: int = 120) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _compiled_expr_preview(item: Any, namespace: str, depth: int = 0) -> str:
    from collections.abc import Iterator
    from itertools import islice

    from pfig.ast_nodes import BinaryOpExpr, UnaryOpExpr
    from pfig.execution import FreshProviderCall, ProviderMethodRef, ProviderRef, ProviderSpec

    def limited(parts: Iterator[str], count: int) -> str:
        preview = list(islice(parts, _VALUE_SEQUENCE_LIMIT))
        if count > _VALUE_SEQUENCE_LIMIT:
            preview.append("…")
        return ", ".join(preview)

    if depth >= 6:
        return "…"
    item = _unwrap_literal_value(item)

    def child(v: Any) -> str:
        return _compiled_expr_preview(v, namespace, depth + 1)

    if isinstance(item, ProviderRef):
        return _preview_reference(item.name, namespace)
    if isinstance(item, ProviderMethodRef):
        return f"{_preview_reference(item.name, namespace)}.{'.'.join(item.attrs)}"
    if isinstance(item, FreshProviderCall):
        args = limited(
            (f"{key}={child(v)}" for key, v in item.overlay_kwargs.items()),
            len(item.overlay_kwargs),
        )
        return f"{_preview_reference(item.provider_name, namespace)}({args})"
    if isinstance(item, ProviderSpec):
        args = (f"{key}={child(v)}" for key, v in item.kwargs.items())
        pos = [child(v) for v in item.args[:_VALUE_SEQUENCE_LIMIT]]
        tail = limited(args, len(item.kwargs))
        return f"{_callable_fqn(item.callable_obj)}({', '.join([*pos, *([tail] if tail else [])])})"
    if isinstance(item, BinaryOpExpr):
        left = child(item.left)
        if item.operator == "/" and isinstance(
            _unwrap_literal_value(item.left), (ProviderRef, ProviderMethodRef)
        ):
            left = f"({left})"  # Distinguish division from a namespace separator.
        return f"({left} {item.operator} {child(item.right)})"
    if isinstance(item, UnaryOpExpr):
        return f"({item.operator}{child(item.operand)})"
    if isinstance(item, (list, tuple)):
        text = limited((child(v) for v in item), len(item))
        if isinstance(item, list):
            return f"[{text}]"
        return f"({text}{',' if len(item) == 1 else ''})"
    if isinstance(item, dict):
        text = limited((f"{child(k)}: {child(v)}" for k, v in item.items()), len(item))
        return "{" + text + "}"
    if item is None or isinstance(item, (str, bool, int, float)):
        return _clip_preview(repr(item))
    return "…"


def _value_sequence_preview(
    value: list[Any] | tuple[Any, ...], binding: str
) -> tuple[str, dict[str, Any]]:
    """Describe the compiled sequence without evaluating references or providers."""
    from pfig.execution import FreshProviderCall

    namespace = binding.rpartition("/")[0]
    items: list[dict[str, Any]] = []
    for raw in value[:_VALUE_SEQUENCE_LIMIT]:
        item = _unwrap_literal_value(raw)
        if isinstance(item, FreshProviderCall):
            items.append(
                {
                    "callee": _preview_reference(item.provider_name, namespace),
                    "targetId": item.provider_name,
                    "kwargs": [
                        {"name": key, "text": _clip_preview(_compiled_expr_preview(v, namespace))}
                        for key, v in item.overlay_kwargs.items()
                    ],
                }
            )
        else:
            items.append({"text": _clip_preview(_compiled_expr_preview(item, namespace))})
    return _clip_preview(_compiled_expr_preview(value, namespace)), {
        "kind": "list" if isinstance(value, list) else "tuple",
        "count": len(value),
        "items": items,
    }


def _seq_needs_inline_expand(seq: list[Any] | tuple[Any, ...]) -> bool:
    """
    True when a list/tuple should be visualized as a collection node (not a single literal).
    We expand when any element (recursively) is an inline provider, fresh call, or nested
    collection that itself needs expansion.
    """
    from pfig.execution import FreshProviderCall, ProviderSpec

    for item in seq:
        u = _unwrap_literal_value(item)
        if isinstance(u, (list, tuple)):
            if _seq_needs_inline_expand(u):
                return True
        elif isinstance(u, dict):
            if _dict_needs_inline_expand(u):
                return True
        elif isinstance(u, (ProviderSpec, FreshProviderCall)):
            return True
    return False


def _dict_needs_inline_expand(d: dict[Any, Any]) -> bool:
    """True when a dict should be a dict collection node (values contain graphable constructs)."""
    from pfig.execution import FreshProviderCall, ProviderSpec

    for v in d.values():
        u = _unwrap_literal_value(v)
        if isinstance(u, (list, tuple)):
            if _seq_needs_inline_expand(u):
                return True
        elif isinstance(u, dict):
            if _dict_needs_inline_expand(u):
                return True
        elif isinstance(u, (ProviderSpec, FreshProviderCall)):
            return True
    return False


def _use_attach_for_kwarg(val: Any) -> bool:
    """Whether ``attach_kw_to_consumer`` should handle this kwarg value."""
    from pfig.execution import FreshProviderCall, ProviderSpec

    u = _unwrap_literal_value(val)
    if isinstance(u, (FreshProviderCall, ProviderSpec)):
        return True
    if isinstance(u, (list, tuple)) and _seq_needs_inline_expand(u):
        return True
    return isinstance(u, dict) and _dict_needs_inline_expand(u)


def _iter_value_refs(value: Any) -> list[tuple[str, str]]:
    """Collect (target_binding_id, ref_kind) from a compiled value expression."""
    from pfig.ast_nodes import BinaryOpExpr, UnaryOpExpr
    from pfig.execution import (
        FreshProviderCall,
        LiteralValue,
        ProviderMethodRef,
        ProviderRef,
        ProviderSpec,
    )

    out: list[tuple[str, str]] = []

    def visit(v: Any) -> None:
        if isinstance(v, LiteralValue):
            visit(v.value)
            return
        if isinstance(v, BinaryOpExpr):
            visit(v.left)
            visit(v.right)
            return
        if isinstance(v, UnaryOpExpr):
            visit(v.operand)
            return
        if isinstance(v, ProviderRef):
            out.append((v.name, "shared"))
            return
        if isinstance(v, ProviderMethodRef):
            out.append((v.name, "method"))
            return
        if isinstance(v, FreshProviderCall):
            out.append((v.provider_name, "fresh"))
            for ov in v.overlay_kwargs.values():
                visit(ov)
            return
        if isinstance(v, ProviderSpec):
            for a in v.args:
                visit(a)
            for x in v.kwargs.values():
                visit(x)
            return
        if isinstance(v, (list, tuple)):
            for item in v:
                visit(item)
            return
        if isinstance(v, dict):
            for x in v.values():
                visit(x)

    visit(value)
    return out


def _kwarg_names_in_signature_order(callable_obj: Any, kwargs: dict[str, Any]) -> list[str]:
    """Order kwarg names as in ``inspect.signature(callable_obj)``, then any extras."""
    if not kwargs:
        return []
    try:
        sig = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return list(kwargs.keys())

    ordered: list[str] = []
    seen: set[str] = set()
    for name, param in sig.parameters.items():
        if param.kind in (Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY):
            if name in kwargs:
                ordered.append(name)
                seen.add(name)
    for name in kwargs:
        if name not in seen:
            ordered.append(name)
    return ordered


FRESH_BASE_PORT = "__fresh_base__"


def _alloc_synthetic_id(counter: list[int], prefix: str) -> str:
    counter[0] += 1
    return f"{prefix}/{counter[0]}"


def _overlay_kwarg_order_names(
    fresh: Any,
    providers: dict[str, Any],
) -> list[str]:
    from pfig.execution import FreshProviderCall, ProviderSpec

    if not isinstance(fresh, FreshProviderCall):
        return []
    base_spec = providers.get(fresh.provider_name)
    if base_spec is not None and isinstance(base_spec, ProviderSpec):
        return _kwarg_names_in_signature_order(base_spec.callable_obj, fresh.overlay_kwargs)
    return list(fresh.overlay_kwargs.keys())


def _fresh_overlay_slot_vm_entries(
    node_id: str,
    slot_kw: str,
    ov: Any,
    providers: dict[str, Any],
    nested_fresh_ids: dict[tuple[str, str], str],
    nested_inline_ids: dict[tuple[str, str], str],
    nested_collection_ids: dict[tuple[str, str], str],
    values: dict[str, Any],
) -> list[dict[str, Any]]:
    """View-model rows for one fresh-overlay slot (possibly a collection node)."""
    from pfig.execution import FreshProviderCall, ProviderSpec

    ov_u = _unwrap_literal_value(ov)
    if isinstance(ov_u, (list, tuple)) and _seq_needs_inline_expand(ov_u):
        cid = nested_collection_ids.get((node_id, slot_kw), "")
        return [
            {
                "name": slot_kw,
                "valueKind": "collectionNested",
                "syntheticCollectionId": cid,
                "refTargetId": None,
                "inlinePreview": None,
                "overlayKwargs": None,
            }
        ]
    if isinstance(ov_u, dict) and _dict_needs_inline_expand(ov_u):
        cid = nested_collection_ids.get((node_id, slot_kw), "")
        return [
            {
                "name": slot_kw,
                "valueKind": "collectionNested",
                "syntheticCollectionId": cid,
                "refTargetId": None,
                "inlinePreview": None,
                "overlayKwargs": None,
            }
        ]
    if isinstance(ov_u, FreshProviderCall):
        cid = nested_fresh_ids.get((node_id, slot_kw), "")
        return [
            {
                "name": slot_kw,
                "valueKind": "nestedFresh",
                "syntheticFreshId": cid,
                "refTargetId": None,
                "inlinePreview": None,
                "overlayKwargs": None,
            }
        ]
    if isinstance(ov_u, ProviderSpec):
        iid = nested_inline_ids.get((node_id, slot_kw), "")
        return [
            {
                "name": slot_kw,
                "valueKind": "inlineNested",
                "syntheticInlineId": iid,
                "refTargetId": None,
                "inlinePreview": None,
                "overlayKwargs": None,
            }
        ]
    kvm = _kwarg_vm_for_value(ov, providers, binding=node_id, values=values)
    kvm["name"] = slot_kw
    return [kvm]


def _fresh_overlay_kw_display_entries(
    node_id: str,
    call: Any,
    providers: dict[str, Any],
    nested_fresh_ids: dict[tuple[str, str], str],
    nested_inline_ids: dict[tuple[str, str], str],
    nested_collection_ids: dict[tuple[str, str], str],
    values: dict[str, Any],
) -> list[dict[str, Any]]:
    from pfig.execution import FreshProviderCall

    if not isinstance(call, FreshProviderCall):
        return []
    out: list[dict[str, Any]] = []
    for kw_name in _overlay_kwarg_order_names(call, providers):
        ov = call.overlay_kwargs[kw_name]
        out.extend(
            _fresh_overlay_slot_vm_entries(
                node_id,
                kw_name,
                ov,
                providers,
                nested_fresh_ids,
                nested_inline_ids,
                nested_collection_ids,
                values,
            )
        )
    return out


def _ref_kind_to_edge_kind(rkind: str) -> str:
    if rkind == "fresh":
        return "fresh"
    if rkind == "method":
        return "shared"
    return "shared"


def _entrypoint_display_binding_name(compiled_expr: Any) -> str:
    """
    Graph header for ``__entrypoint__``: show linked @provider / @provider.method /
    callable short name instead of the binding id.
    """
    from pfig.execution import (
        FreshProviderCall,
        LiteralValue,
        ProviderMethodRef,
        ProviderRef,
        ProviderSpec,
    )

    v = compiled_expr
    while isinstance(v, LiteralValue):
        v = v.value
    if isinstance(v, ProviderMethodRef):
        return f"@{v.name}.{'.'.join(v.attrs)}"
    if isinstance(v, ProviderRef):
        return f"@{v.name}"
    if isinstance(v, FreshProviderCall):
        return f"@{v.provider_name}(…)"
    if isinstance(v, ProviderSpec):
        return _callable_fqn(v.callable_obj).split(".")[-1]
    return "__entrypoint__"


_PREVIEW_ARITHMETIC = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
    "//": operator.floordiv,
    "**": operator.pow,
}


def _arithmetic_preview_result(expr: Any, values: dict[str, Any]) -> str | None:
    """Evaluate bounded scalar arithmetic without executing providers or accessing attributes."""
    from pfig.ast_nodes import BinaryOpExpr, UnaryOpExpr
    from pfig.execution import LiteralValue, ProviderRef

    budget = 128

    def evaluate(item: Any) -> int | float | None:
        nonlocal budget
        budget -= 1
        if budget < 0:
            return None
        if isinstance(item, LiteralValue):
            return evaluate(item.value)
        if isinstance(item, ProviderRef):
            return evaluate(values.get(item.name))
        if isinstance(item, BinaryOpExpr) and item.operator in _PREVIEW_ARITHMETIC:
            left, right = evaluate(item.left), evaluate(item.right)
            if left is None or right is None or (item.operator == "**" and abs(right) > 256):
                return None
            item = _PREVIEW_ARITHMETIC[item.operator](left, right)
        elif isinstance(item, UnaryOpExpr) and item.operator == "-":
            value = evaluate(item.operand)
            return -value if value is not None else None
        if type(item) is int and item.bit_length() <= 256:
            return item
        if type(item) is float and math.isfinite(item):
            return item
        return None

    try:
        result = evaluate(expr)
    except ArithmeticError:
        return None
    return repr(result) if result is not None else None


def _kwarg_vm_for_value(
    val: Any,
    providers: dict[str, Any] | None = None,
    *,
    binding: str = "",
    values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from pfig.ast_nodes import BinaryOpExpr, UnaryOpExpr
    from pfig.execution import (
        FreshProviderCall,
        LiteralValue,
        ProviderMethodRef,
        ProviderRef,
        ProviderSpec,
    )

    if isinstance(val, LiteralValue):
        return _kwarg_vm_for_value(val.value, providers, binding=binding, values=values)

    if isinstance(val, ProviderRef):
        return {
            "name": "",
            "valueKind": "ref",
            "refTargetId": val.name,
            "inlinePreview": None,
            "overlayKwargs": None,
        }
    if isinstance(val, ProviderMethodRef):
        return {
            "name": "",
            "valueKind": "ref",
            "refTargetId": val.name,
            "inlinePreview": f".{'.'.join(val.attrs)}",
            "overlayKwargs": None,
        }
    if isinstance(val, FreshProviderCall):
        return {
            "name": "",
            "valueKind": "freshRef",
            "refTargetId": val.provider_name,
            "inlinePreview": None,
            "overlayKwargs": None,
            "syntheticFreshId": None,
        }
    if isinstance(val, ProviderSpec):
        return {
            "name": "",
            "valueKind": "expr",
            "refTargetId": None,
            "inlinePreview": _callable_fqn(val.callable_obj),
            "overlayKwargs": None,
        }
    arithmetic = isinstance(val, (BinaryOpExpr, UnaryOpExpr))
    if arithmetic:
        preview = _compiled_expr_preview(val, binding.rpartition("/")[0])
        result = _arithmetic_preview_result(val, values or {})
    else:
        preview = _clip_preview(str(_strip_literals_for_display(val)), 80)
        result = None
    return {
        "name": "",
        "valueKind": "expr" if arithmetic else "literal",
        "refTargetId": None,
        "inlinePreview": preview,
        "overlayKwargs": None,
        **({"evaluatedPreview": result} if result is not None else {}),
    }


def build_graph_view_model(
    *,
    text: str,
    uri: str,
    ws: WorkspaceState,
    index: DocumentIndex | None,
    mode: str = "reachable",
) -> dict[str, Any]:
    """
    Build JSON-serializable graph view model for the active pfig document.

    mode: \"reachable\" (from __entrypoint__ when present) or \"all\".
    """
    index = index if index is not None else DocumentIndex()
    compiled, error, diagnostic_count = compile_graph_plan(text=text, uri=uri, ws=ws, index=index)
    if compiled is None:
        failure = graph_error(
            mode, error or "Compile failed or produced no data; fix diagnostics and retry."
        )
        if diagnostic_count is not None:
            failure["diagnostics"] = diagnostic_count
        return failure
    return render_graph_view_model(
        text=text, uri=uri, ws=ws, index=index, mode=mode, compiled=compiled
    )


def graph_error(mode: str, error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": error,
        "nodes": [],
        "edges": [],
        "mode": mode,
        "reachableUnavailable": True,
        "entrypoint": None,
    }


def compile_graph_plan(
    *,
    text: str,
    uri: str,
    ws: WorkspaceState,
    index: DocumentIndex,
    comparison: bool = False,
) -> tuple[dict[str, Any] | None, str | None, int | None]:
    if _is_profile_only(index):
        return None, "Graph is not available for profile-only files.", None

    diags, compiled = validate_with_pfig(
        text,
        uri,
        profiles=ws.profiles,
        set_overrides=ws.set_overrides,
        allow_missing_externals=ws.allow_missing_externals,
        strict=ws.strict_signatures,
        index=index,
    )

    if comparison:
        # Editor graphs can preview partially compiled files; a comparison must
        # not interpret partial compilation as removed wiring.
        problem = next(
            (
                d
                for d in diags
                if d.severity == DiagnosticSeverity.Error
                or (d.code == "missing_external" and not ws.allow_missing_externals)
            ),
            None,
        )
        if problem:
            return None, problem.message, len(diags)
    if not compiled:
        return None, None, len(diags)
    return compiled, None, None


def render_graph_view_model(
    *,
    text: str,
    uri: str,
    ws: WorkspaceState,
    index: DocumentIndex,
    mode: str,
    compiled: dict[str, Any],
    comparison: bool = False,
) -> dict[str, Any]:
    """Render a validated plan. Comparison adds positional ports and synthetic identities."""
    from pfig.ast_nodes import BinaryOpExpr, UnaryOpExpr
    from pfig.execution import (
        ExecutionError,
        FreshProviderCall,
        LiteralValue,
        ProviderMethodRef,
        ProviderRef,
        ProviderSpec,
        find_reachable_bindings,
        get_dependencies,
        resolve_entrypoint,
    )

    entry_path = _uri_to_path(uri) or ""
    sources = _GraphSources(entry_path, index, ws)
    declared_externals = compiled.get("declared_externals", set()) or set()
    provided_externals = compiled.get("provided_externals", set()) or set()

    providers: dict[str, Any] = compiled.get("providers", {}) or {}
    values: dict[str, Any] = compiled.get("values", {}) or {}
    binding_origins = compiled.get("binding_origins", {})
    authored_override_paths = compiled.get("authored_override_paths", {})

    entrypoint_name: str | None = None
    reachability: set[str] | None = None
    reachable_unavailable = False
    requested_mode = mode

    try:
        entrypoint_name = resolve_entrypoint(compiled, None)
        reachability = find_reachable_bindings(compiled, entrypoint_name)
    except ExecutionError:
        reachable_unavailable = True
        entrypoint_name = None
        reachability = None
        if requested_mode == "reachable":
            mode = "all"

    reachable_filter: set[str] | None = None
    if mode == "reachable" and reachability is not None:
        # Keep authored recipe context for navigation and fresh-base edges.
        # isReachable below still reports effective evaluation dependencies;
        # recipe-only nodes (including replaced arguments) are not required.
        reachable_filter = set(reachability)
        pending = list(reachability)
        bindings = {**providers, **values}
        while pending:
            name = pending.pop()
            for dep in get_dependencies(bindings.get(name)):
                if dep not in reachable_filter:
                    reachable_filter.add(dep)
                    pending.append(dep)

    graph_binding_ids = set(providers.keys()) | set(values.keys()) | set(declared_externals)
    all_ids: set[str] = set(graph_binding_ids)
    synthetic_parent: dict[str, str] = {}
    synthetic_input: dict[str, str] = {}
    pending_fresh_synthetic: dict[str, FreshProviderCall] = {}
    pending_fresh_nav: dict[str, tuple[str, list[tuple[str, str | int]]]] = {}
    nested_fresh_ids: dict[tuple[str, str], str] = {}
    nested_inline_ids: dict[tuple[str, str], str] = {}
    nested_collection_ids: dict[tuple[str, str], str] = {}
    pending_inline: dict[str, tuple[Any, list[dict[str, Any]]]] = {}
    pending_collection: dict[str, dict[str, Any]] = {}
    synth_counter: list[int] = [0]

    def anchor_binding(nid: str) -> str:
        cur = nid
        seen: set[str] = set()
        while cur in synthetic_parent and cur not in seen:
            seen.add(cur)
            cur = synthetic_parent[cur]
        return cur

    def visible(nid: str) -> bool:
        if reachable_filter is None:
            return True
        if nid in reachable_filter:
            return True
        return anchor_binding(nid) in reachable_filter

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    edge_i = 0

    def add_edge(
        src: str,
        tgt: str,
        target_port: str,
        ref_kind: str,
    ) -> None:
        nonlocal edge_i
        if synthetic_parent.get(src) == tgt:
            synthetic_input[src] = target_port
        if not visible(src) or not visible(tgt):
            return
        if src not in all_ids or tgt not in all_ids:
            return
        eid = f"e{edge_i}"
        edge_i += 1
        edges.append(
            {
                "id": eid,
                "source": src,
                "target": tgt,
                "targetPort": target_port,
                "refKind": ref_kind,
            }
        )

    def provider_inputs(spec: ProviderSpec) -> list[tuple[str, Any]]:
        positional = [(f"#{i + 1}", v) for i, v in enumerate(spec.args)] if comparison else []
        return positional + [
            (k, spec.kwargs[k])
            for k in _kwarg_names_in_signature_order(spec.callable_obj, spec.kwargs)
        ]

    def emit_fresh_subgraph(
        node_id: str,
        call: FreshProviderCall,
        consumer_id: str | None,
        consumer_kw: str | None,
        *,
        is_named_binding: bool,
        overlay_ast_path: list[tuple[str, str | int]] | None = None,
    ) -> None:
        all_ids.add(node_id)
        if consumer_id is not None:
            synthetic_parent[node_id] = consumer_id
        if not is_named_binding:
            pending_fresh_synthetic[node_id] = call
            pending_fresh_nav[node_id] = (
                anchor_binding(node_id),
                list(overlay_ast_path or []),
            )

        overlay_prefix: list[tuple[str, str | int]] = list(overlay_ast_path or [])

        def emit_fresh_overlay_at(
            consumer_inner: str,
            sink_port: str,
            ov: Any,
            coll_path: list[tuple[str, str | int]] | None = None,
            path_root: str | None = None,
        ) -> None:
            base_nav = list(coll_path if coll_path is not None else overlay_prefix)
            path_steps = base_nav + [("k", sink_port)]
            r = path_root or anchor_binding(consumer_inner)
            ov_u = _unwrap_literal_value(ov)
            if isinstance(ov_u, (list, tuple)) and _seq_needs_inline_expand(ov_u):
                coll_id = _alloc_synthetic_id(synth_counter, "__pfig_coll")
                all_ids.add(coll_id)
                synthetic_parent[coll_id] = consumer_inner
                nested_collection_ids[(consumer_inner, sink_port)] = coll_id
                add_edge(coll_id, consumer_inner, sink_port, "shared")
                shape = "tuple" if isinstance(ov_u, tuple) else "list"
                slots_meta: list[dict[str, str]] = []
                for i, elem in enumerate(ov_u):
                    port = f"__slot{i}__"
                    slots_meta.append({"port": port, "label": str(i)})
                    emit_fresh_overlay_at(
                        coll_id,
                        port,
                        elem,
                        coll_path=path_steps + [("s", i)],
                        path_root=r,
                    )
                pending_collection[coll_id] = {
                    "shape": shape,
                    "slots": slots_meta,
                    "ast_path": path_steps,
                    "path_root": r,
                }
                return
            if isinstance(ov_u, dict) and _dict_needs_inline_expand(ov_u):
                coll_id = _alloc_synthetic_id(synth_counter, "__pfig_coll")
                all_ids.add(coll_id)
                synthetic_parent[coll_id] = consumer_inner
                nested_collection_ids[(consumer_inner, sink_port)] = coll_id
                add_edge(coll_id, consumer_inner, sink_port, "shared")
                slots_meta = []
                for ki, (dkey, elem) in enumerate(ov_u.items()):
                    port = f"__slot{ki}__"
                    label = repr(dkey) if isinstance(dkey, str) else str(dkey)
                    slots_meta.append({"port": port, "label": label})
                    emit_fresh_overlay_at(
                        coll_id,
                        port,
                        elem,
                        coll_path=path_steps + [("s", ki)],
                        path_root=r,
                    )
                pending_collection[coll_id] = {
                    "shape": "dict",
                    "slots": slots_meta,
                    "ast_path": path_steps,
                    "path_root": r,
                }
                return
            if isinstance(ov_u, FreshProviderCall):
                fid = _alloc_synthetic_id(synth_counter, "__pfig_fresh")
                nested_fresh_ids[(consumer_inner, sink_port)] = fid
                emit_fresh_subgraph(
                    fid,
                    ov_u,
                    consumer_inner,
                    sink_port,
                    is_named_binding=False,
                    overlay_ast_path=path_steps,
                )
            elif isinstance(ov_u, ProviderSpec):
                iid = _alloc_synthetic_id(synth_counter, "__pfig_inline")
                nested_inline_ids[(consumer_inner, sink_port)] = iid
                all_ids.add(iid)
                synthetic_parent[iid] = consumer_inner
                add_edge(iid, consumer_inner, sink_port, "shared")
                kw_entries_il: list[dict[str, Any]] = []
                for ikw, iv in provider_inputs(ov_u):
                    kw_entries_il.extend(
                        attach_kw_to_consumer(iid, ikw, iv, coll_path=path_steps, path_root=r)
                    )
                pending_inline[iid] = (ov_u, kw_entries_il)
            else:
                for dep_id, rkind in _iter_value_refs(ov):
                    add_edge(
                        dep_id,
                        consumer_inner,
                        sink_port,
                        _ref_kind_to_edge_kind(rkind),
                    )

        add_edge(call.provider_name, node_id, FRESH_BASE_PORT, "fresh")
        for kn in _overlay_kwarg_order_names(call, providers):
            ov = call.overlay_kwargs[kn]
            emit_fresh_overlay_at(node_id, kn, ov)
        if consumer_id is not None and consumer_kw is not None:
            add_edge(node_id, consumer_id, consumer_kw, "shared")

    def attach_kw_to_consumer(
        target_id: str,
        kw_name: str,
        val: Any,
        *,
        coll_path: list[tuple[str, str | int]] | None = None,
        path_root: str | None = None,
    ) -> list[dict[str, Any]]:
        val_u = _unwrap_literal_value(val)
        r = path_root or anchor_binding(target_id)
        if _is_slot_port(kw_name):
            path_steps = list(coll_path or [])
        else:
            path_steps = list(coll_path or []) + [("k", kw_name)]
        if isinstance(val_u, (list, tuple)) and _seq_needs_inline_expand(val_u):
            coll_id = _alloc_synthetic_id(synth_counter, "__pfig_coll")
            all_ids.add(coll_id)
            synthetic_parent[coll_id] = target_id
            add_edge(coll_id, target_id, kw_name, "shared")
            shape = "tuple" if isinstance(val_u, tuple) else "list"
            slots_meta: list[dict[str, str]] = []
            for i, elem in enumerate(val_u):
                port = f"__slot{i}__"
                slots_meta.append({"port": port, "label": str(i)})
                attach_kw_to_consumer(
                    coll_id,
                    port,
                    elem,
                    coll_path=path_steps + [("s", i)],
                    path_root=r,
                )
            pending_collection[coll_id] = {
                "shape": shape,
                "slots": slots_meta,
                "ast_path": path_steps,
                "path_root": r,
            }
            return [
                {
                    "name": kw_name,
                    "valueKind": "collectionNested",
                    "syntheticCollectionId": coll_id,
                    "refTargetId": None,
                    "inlinePreview": None,
                    "overlayKwargs": None,
                }
            ]
        if isinstance(val_u, dict) and _dict_needs_inline_expand(val_u):
            coll_id = _alloc_synthetic_id(synth_counter, "__pfig_coll")
            all_ids.add(coll_id)
            synthetic_parent[coll_id] = target_id
            add_edge(coll_id, target_id, kw_name, "shared")
            slots_meta = []
            for ki, (dkey, elem) in enumerate(val_u.items()):
                port = f"__slot{ki}__"
                label = repr(dkey) if isinstance(dkey, str) else str(dkey)
                slots_meta.append({"port": port, "label": label})
                attach_kw_to_consumer(
                    coll_id,
                    port,
                    elem,
                    coll_path=path_steps + [("s", ki)],
                    path_root=r,
                )
            pending_collection[coll_id] = {
                "shape": "dict",
                "slots": slots_meta,
                "ast_path": path_steps,
                "path_root": r,
            }
            return [
                {
                    "name": kw_name,
                    "valueKind": "collectionNested",
                    "syntheticCollectionId": coll_id,
                    "refTargetId": None,
                    "inlinePreview": None,
                    "overlayKwargs": None,
                }
            ]
        if isinstance(val_u, FreshProviderCall):
            fid = _alloc_synthetic_id(synth_counter, "__pfig_fresh")
            if _is_slot_port(kw_name):
                fresh_nav = list(coll_path or [])
            else:
                fresh_nav = list(coll_path or []) + [("k", kw_name)]
            emit_fresh_subgraph(
                fid,
                val_u,
                target_id,
                kw_name,
                is_named_binding=False,
                overlay_ast_path=fresh_nav,
            )
            return [
                {
                    "name": kw_name,
                    "valueKind": "freshNested",
                    "syntheticFreshId": fid,
                    "refTargetId": None,
                    "inlinePreview": None,
                    "overlayKwargs": None,
                }
            ]
        if isinstance(val_u, ProviderSpec):
            iid = _alloc_synthetic_id(synth_counter, "__pfig_inline")
            all_ids.add(iid)
            synthetic_parent[iid] = target_id
            add_edge(iid, target_id, kw_name, "shared")
            next_path = list(coll_path or []) + [("k", kw_name)]
            next_pr = path_root or anchor_binding(target_id)
            kw_entries_il: list[dict[str, Any]] = []
            for ikw, iv in provider_inputs(val_u):
                kw_entries_il.extend(
                    attach_kw_to_consumer(iid, ikw, iv, coll_path=next_path, path_root=next_pr)
                )
            pending_inline[iid] = (val_u, kw_entries_il)
            return [
                {
                    "name": kw_name,
                    "valueKind": "inlineNested",
                    "syntheticInlineId": iid,
                    "refTargetId": None,
                    "inlinePreview": None,
                    "overlayKwargs": None,
                }
            ]
        kvm = _kwarg_vm_for_value(val, providers, binding=target_id, values=values)
        kvm["name"] = kw_name
        for dep_id, rkind in _iter_value_refs(val):
            add_edge(
                dep_id,
                target_id,
                kw_name,
                _ref_kind_to_edge_kind(rkind),
            )
        return [kvm]

    source_files: dict[str, str] = {}
    for name in sorted(graph_binding_ids):
        if not visible(name):
            continue

        is_entry = name == "__entrypoint__" or (
            entrypoint_name is not None and name == entrypoint_name
        )
        is_ext_decl = name in declared_externals
        is_ext_missing = is_ext_decl and name not in provided_externals

        active_path = authored_override_paths.get(binding_origins.get(name), name)
        loc = sources.binding_location(active_path)
        active_def = {"uri": loc[0], "range": loc[1]} if loc else None
        overridden = loc is not None and loc[2]
        if loc:
            origin = sources.binding_location(name, include_overrides=False) if overridden else loc
            source_files[name] = origin[0] if origin else loc[0]

        source_meta = {"activeDefinition": active_def, "sourceFileUri": source_files.get(name)}

        if name in providers:
            spec = providers[name]
            if not isinstance(spec, ProviderSpec):
                continue
            kw_entries: list[dict[str, Any]] = []
            for kw, val in provider_inputs(spec):
                if _use_attach_for_kwarg(val):
                    kw_entries.extend(attach_kw_to_consumer(name, kw, val))
                else:
                    kvm = _kwarg_vm_for_value(val, providers, binding=name, values=values)
                    kvm["name"] = kw
                    kw_entries.append(kvm)
                    for dep_id, rkind in _iter_value_refs(val):
                        add_edge(
                            dep_id,
                            name,
                            kw,
                            _ref_kind_to_edge_kind(rkind),
                        )

            pos_r = _pfig_position_to_range_dict(getattr(spec, "position", None))
            if pos_r and loc:
                extra = {"providerCallable": pos_r}
            else:
                extra = {"providerCallable": pos_r} if pos_r else {}

            bind_label = (
                _entrypoint_display_binding_name(spec) if name == "__entrypoint__" else name
            )
            nodes.append(
                {
                    "id": name,
                    "bindingName": bind_label,
                    "namespace": ("/".join(name.split("/")[:-1]) if "/" in name else None),
                    "kind": "provider",
                    "provider": {
                        "callableName": _callable_fqn(spec.callable_obj).split(".")[-1],
                        "callableFqn": _callable_fqn(spec.callable_obj),
                        "kwargs": kw_entries,
                    },
                    "freshInstance": None,
                    "binding": None,
                    **source_meta,
                    "extraRanges": extra,
                    "flags": {
                        "isEntrypoint": is_entry,
                        "isReachable": reachability is None or name in reachability,
                        "isExternal": False,
                        "isOverridden": overridden,
                    },
                }
            )
            continue

        if name in values:
            val = values[name]
            while isinstance(val, LiteralValue):
                val = val.value

            kind = "value"
            value_preview: str | None = None
            fresh_vm: dict[str, Any] | None = None
            if isinstance(val, (ProviderRef, ProviderMethodRef)):
                kind = "alias"
            elif isinstance(val, FreshProviderCall):
                kind = "fresh_instance"
                emit_fresh_subgraph(
                    name,
                    val,
                    None,
                    None,
                    is_named_binding=True,
                    overlay_ast_path=[],
                )
                fresh_vm = {
                    "baseProviderId": val.provider_name,
                    "baseProviderBindingName": val.provider_name,
                    "overlayKwargs": _fresh_overlay_kw_display_entries(
                        name,
                        val,
                        providers,
                        nested_fresh_ids,
                        nested_inline_ids,
                        nested_collection_ids,
                        values,
                    ),
                }
            elif is_ext_decl:
                kind = "external"

            if not isinstance(val, FreshProviderCall):
                for dep_id, rkind in _iter_value_refs(val):
                    rk = "fresh" if rkind == "fresh" else "shared"
                    if rkind == "method":
                        rk = "shared"
                    add_edge(dep_id, name, "__binding__", rk)

            sequence = None
            if isinstance(val, (list, tuple)):
                value_preview, sequence = _value_sequence_preview(val, name)
            elif isinstance(val, (BinaryOpExpr, UnaryOpExpr)):
                value_preview = _compiled_expr_preview(val, name.rpartition("/")[0])
                result = _arithmetic_preview_result(val, values)
                if result is not None:
                    value_preview = f"{value_preview} = {result}"
            elif not isinstance(val, (ProviderRef, ProviderMethodRef, FreshProviderCall)):
                prev = str(_strip_literals_for_display(val))
                if len(prev) > 120:
                    prev = prev[:117] + "..."
                value_preview = prev

            bind_payload: dict[str, Any] = {
                "valuePreview": value_preview,
                "isExternal": is_ext_decl,
                "externalMissing": is_ext_missing,
            }
            if sequence is not None:
                bind_payload["valueSequence"] = sequence
            if kind == "value" and name in binding_origins:
                # An origin has one effective value; aliases only rewrite reference paths.
                bind_payload["origin"] = list(binding_origins[name])

            bind_label = (
                _entrypoint_display_binding_name(val)
                if name == "__entrypoint__"
                else _binding_leaf_name(name)
            )
            nodes.append(
                {
                    "id": name,
                    "bindingName": bind_label,
                    "namespace": None,
                    "kind": kind,
                    "provider": None,
                    "freshInstance": fresh_vm,
                    "binding": bind_payload,
                    **source_meta,
                    "extraRanges": {},
                    "flags": {
                        "isEntrypoint": is_entry,
                        "isReachable": reachability is None or name in reachability,
                        "isExternal": is_ext_decl,
                        "isOverridden": overridden,
                    },
                }
            )
            continue

        if name in declared_externals:
            nodes.append(
                {
                    "id": name,
                    "bindingName": _binding_leaf_name(name),
                    "namespace": None,
                    "kind": "external",
                    "provider": None,
                    "freshInstance": None,
                    "binding": {
                        "valuePreview": None,
                        "isExternal": True,
                        "externalMissing": name not in provided_externals,
                    },
                    **source_meta,
                    "extraRanges": {},
                    "flags": {
                        "isEntrypoint": is_entry,
                        "isReachable": reachability is None or name in reachability,
                        "isExternal": True,
                        "isOverridden": overridden,
                    },
                }
            )

    active_def_ast: Any | None | bool = None
    if pending_fresh_synthetic or pending_collection:
        try:
            from pfig import parse_pfig_source

            filename = _uri_to_path(uri) or uri or "<string>"
            active_def_ast = parse_pfig_source(text, filename=filename, with_index=False)
        except Exception:
            active_def_ast = False

    for fid in sorted(pending_fresh_synthetic.keys()):
        call = pending_fresh_synthetic[fid]
        nav = pending_fresh_nav.get(fid)
        ad_fresh = (
            _fresh_call_active_definition(text, uri, nav[0], nav[1], active_def_ast)
            if nav
            else None
        )
        nodes.append(
            {
                "id": fid,
                "bindingName": "<inline>",
                "namespace": None,
                "kind": "fresh_instance",
                "provider": None,
                "freshInstance": {
                    "baseProviderId": call.provider_name,
                    "baseProviderBindingName": call.provider_name,
                    "overlayKwargs": _fresh_overlay_kw_display_entries(
                        fid,
                        call,
                        providers,
                        nested_fresh_ids,
                        nested_inline_ids,
                        nested_collection_ids,
                        values,
                    ),
                },
                "binding": None,
                "activeDefinition": ad_fresh,
                "sourceFileUri": source_files.get(anchor_binding(fid)),
                "extraRanges": {},
                "flags": {
                    "isEntrypoint": False,
                    "isReachable": reachability is None or anchor_binding(fid) in reachability,
                    "isExternal": False,
                    "isOverridden": False,
                    "isAnonymous": True,
                },
            }
        )

    for cid in sorted(pending_collection.keys()):
        meta = pending_collection[cid]
        shape = meta["shape"]
        bind_title = "Tuple" if shape == "tuple" else "Dict" if shape == "dict" else "List"
        pr_coll = meta.get("path_root") or ""
        ap_coll = meta.get("ast_path") or []
        ad_coll = (
            _collection_active_definition(text, uri, pr_coll, ap_coll, active_def_ast)
            if pr_coll and ap_coll
            else None
        )
        nodes.append(
            {
                "id": cid,
                "bindingName": bind_title,
                "namespace": None,
                "kind": "collection",
                "collection": {"shape": shape, "slots": meta["slots"]},
                "provider": None,
                "freshInstance": None,
                "binding": None,
                "activeDefinition": ad_coll,
                "sourceFileUri": source_files.get(anchor_binding(cid)),
                "extraRanges": {},
                "flags": {
                    "isEntrypoint": False,
                    "isReachable": reachability is None or anchor_binding(cid) in reachability,
                    "isExternal": False,
                    "isOverridden": False,
                    "isAnonymous": True,
                },
            }
        )

    for iid in sorted(pending_inline.keys()):
        spec_obj, kw_entries_il = pending_inline[iid]
        pos_il = _pfig_position_to_range_dict(getattr(spec_obj, "position", None))
        extra_il = {"providerCallable": pos_il} if pos_il else {}
        ad_il = {"uri": _normalize_graph_uri(uri), "range": pos_il} if pos_il else None
        nodes.append(
            {
                "id": iid,
                "bindingName": "<inline>",
                "namespace": None,
                "kind": "inline_provider",
                "provider": {
                    "callableName": _callable_fqn(spec_obj.callable_obj).split(".")[-1],
                    "callableFqn": _callable_fqn(spec_obj.callable_obj),
                    "kwargs": kw_entries_il,
                },
                "freshInstance": None,
                "binding": None,
                "activeDefinition": ad_il,
                "sourceFileUri": source_files.get(anchor_binding(iid)),
                "extraRanges": extra_il,
                "flags": {
                    "isEntrypoint": False,
                    "isReachable": reachability is None or anchor_binding(iid) in reachability,
                    "isExternal": False,
                    "isOverridden": False,
                    "isAnonymous": True,
                },
            }
        )

    # Alias copies of an inline expression share their owner's origin and input path.
    for node in nodes:
        owner = node["id"]
        if owner not in synthetic_input:
            continue
        ports = []
        while owner in synthetic_input:
            ports.append(synthetic_input[owner])
            owner = synthetic_parent[owner]
        if owner in binding_origins:
            node["syntheticOrigin"] = [*binding_origins[owner], *reversed(ports)]

    return {
        "ok": True,
        "error": None,
        "nodes": nodes,
        "edges": edges,
        "mode": mode,
        "reachableUnavailable": reachable_unavailable,
        "entrypoint": entrypoint_name,
        "entryFileUri": _normalize_graph_uri(uri) if entry_path else None,
        "allBindingCount": len(graph_binding_ids),
        "visibleBindingCount": len(nodes),
        **(
            {
                "syntheticInputs": {
                    nid: (parent, synthetic_input[nid])
                    for nid, parent in synthetic_parent.items()
                    if nid in synthetic_input
                }
            }
            if comparison
            else {}
        ),
    }
