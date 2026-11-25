"""Semantic comparison and overlay serialization for the graph viewer.

The text diff has its own presentation contract. Here we compare the full recipes
before scoping, then project changes onto the graph's inputs and authored files.
"""

from __future__ import annotations

import inspect
import json
from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator

from pfig.ast_nodes import BinaryOpExpr, UnaryOpExpr
from pfig.diff import (
    MISSING_EXTERNAL,
    AlignmentMode,
    BindingMatches,
    compiled_diff_bindings,
    match_bindings,
)
from pfig.execution import FreshProviderCall, ProviderMethodRef, ProviderRef, ProviderSpec
from pfig.lsp.analysis.resolver import _uri_to_path
from pfig.lsp.graph_view_model import (
    FRESH_BASE_PORT,
    _callable_fqn,
    _compiled_expr_preview,
    _unwrap_literal_value,
)

_ABSENT = object()
Step = tuple[str, str | int]


def _parts(value: Any) -> dict[Step, Any] | None:
    if isinstance(value, ProviderSpec):
        return {
            ("callable", ""): value.callable_obj,
            **{("arg", i): v for i, v in enumerate(value.args)},
            **{("kwarg", k): v for k, v in value.kwargs.items()},
        }
    if isinstance(value, FreshProviderCall):
        return {
            ("freshBase", ""): ProviderRef(value.provider_name),
            **{("kwarg", k): v for k, v in value.overlay_kwargs.items()},
        }
    if isinstance(value, (list, tuple)):
        return {("item", i): v for i, v in enumerate(value)}
    if isinstance(value, dict):
        # Compiled dict keys are literals. Include the type in the key's spelling.
        return {("key", repr(k)): v for k, v in value.items()}
    if isinstance(value, BinaryOpExpr):
        return {
            ("operator", ""): value.operator,
            ("operand", 0): value.left,
            ("operand", 1): value.right,
        }
    if isinstance(value, UnaryOpExpr):
        return {("operator", ""): value.operator, ("operand", 0): value.operand}
    return None


def _same_callable(before: Any, after: Any) -> bool:
    # Attribute lookup can produce a new bound method for the same function and receiver.
    return before is after or (
        type(before) is type(after)
        and (inspect.ismethod(before) or inspect.isbuiltin(before))
        and before == after
    )


def _leaf_changes(
    before: Any,
    after: Any,
    path: tuple[Step, ...],
    matches: BindingMatches | None,
) -> Iterator[tuple]:
    before, after = _unwrap_literal_value(before), _unwrap_literal_value(after)
    if type(before) is type(after):
        if matches and isinstance(before, (ProviderRef, ProviderMethodRef)):
            if matches.same_target(before.name, after.name) and (
                not isinstance(before, ProviderMethodRef) or before.attrs == after.attrs
            ):
                return
            yield path, before, after
            return
        if before is after and (matches is None or _parts(before) is None):
            return
        if (parts := _parts(before)) is not None:
            new = _parts(after) or {}
            for step in dict.fromkeys([*parts, *new]):
                yield from _leaf_changes(
                    parts.get(step, _ABSENT),
                    new.get(step, _ABSENT),
                    (*path, step),
                    matches,
                )
            return
        if path and path[-1][0] == "callable":
            if _same_callable(before, after):
                return
        elif before == after:
            return
    yield path, before, after


def _side(value: Any, step: Step, *, qualified: bool = False) -> dict[str, Any]:
    if value is _ABSENT:
        return {"present": False}
    target = None
    if isinstance(value, (ProviderRef, ProviderMethodRef)):
        target = value.name
    elif isinstance(value, FreshProviderCall):
        target = value.provider_name
    if step[0] == "callable":
        full = _callable_fqn(value)
        preview = full if qualified else full.rsplit(".", 1)[-1]
    else:
        preview = (
            "External (not supplied)"
            if value is MISSING_EXTERNAL
            else _compiled_expr_preview(value, "")
        )
        full = repr(value) if isinstance(value, (str, int, float, bool)) else preview
    return {
        "present": True,
        "preview": preview,
        "title": full,
        **({"targetId": target} if target else {}),
    }


def _delta(path: tuple[Step, ...], before: Any, after: Any) -> dict[str, Any]:
    step = path[-1]
    qualified = (
        step[0] == "callable"
        and before is not _ABSENT
        and after is not _ABSENT
        and _callable_fqn(before).rsplit(".", 1)[-1] == _callable_fqn(after).rsplit(".", 1)[-1]
    )
    return {
        "path": path,
        "before": _side(before, step, qualified=qualified),
        "after": _side(after, step, qualified=qualified),
    }


def _rejected_kwargs(before: Any, after: Any) -> set[str]:
    """Report old-only kwargs that the replacement's signature rules out."""
    if not isinstance(before, ProviderSpec) or not isinstance(after, ProviderSpec):
        return set()
    if _same_callable(before.callable_obj, after.callable_obj):
        return set()
    omitted = before.kwargs.keys() - after.kwargs.keys()
    if not omitted:
        return set()
    try:
        params = inspect.signature(after.callable_obj).parameters
    except (TypeError, ValueError):
        return set()
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return set()
    accepted = {
        name
        for name, param in params.items()
        if param.kind in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    }
    return omitted - accepted


def _row_changes(
    before: Any,
    after: Any,
    matches: BindingMatches | None = None,
) -> list[dict[str, Any]]:
    before, after = _unwrap_literal_value(before), _unwrap_literal_value(after)

    def rows(value: Any) -> dict[Step, Any]:
        if isinstance(value, (ProviderSpec, FreshProviderCall)):
            return _parts(value) or {}
        return {} if value is _ABSENT else {("value", ""): value}

    base, proposed = rows(before), rows(after)
    rejected = _rejected_kwargs(before, after)
    changes = []
    for step in dict.fromkeys([*base, *proposed]):
        old, new = base.get(step, _ABSENT), proposed.get(step, _ABSENT)
        leaves = list(_leaf_changes(old, new, (step,), matches))
        if not leaves:
            continue
        row = _delta((step,), _unwrap_literal_value(old), _unwrap_literal_value(new))
        if step[0] == "kwarg" and step[1] in rejected:
            row["notAccepted"] = True
        nested = [_delta(path, a, b) for path, a, b in leaves if len(path) > 1]
        if nested:
            row["nested"] = nested
        changes.append(row)
    return changes


def _graph_identities(vm: dict) -> tuple[dict, dict, dict]:
    """Synthetic nodes inherit the owning binding and a typed input path, never a counter."""
    nodes = {n["id"]: n for n in vm["nodes"]}
    parents = vm["syntheticInputs"]
    slots = {
        (n["id"], slot["port"]): slot["label"]
        for n in nodes.values()
        if n.get("collection")
        for slot in n["collection"]["slots"]
    }

    def port_key(target: str, port: str) -> Step:
        if (target, port) in slots:
            kind = nodes[target]["collection"]["shape"]
            return (
                ("key", slots[target, port])
                if kind == "dict"
                else ("item", int(slots[target, port]))
            )
        if port.startswith("#"):
            return "arg", int(port[1:]) - 1
        if port == FRESH_BASE_PORT:
            return "freshBase", ""
        if port == "__binding__":
            return "value", ""
        return "kwarg", port

    identities: dict[str, tuple] = {}

    def identity(nid: str) -> tuple:
        if nid not in identities:
            if nid not in parents:
                identities[nid] = (nid, ())
            else:
                parent, port = parents[nid]
                owner, path = identity(parent)
                identities[nid] = (owner, (*path, (port_key(parent, port), nodes[nid]["kind"])))
        return identities[nid]

    for nid in nodes:
        identity(nid)
    edge_keys = {
        e["id"]: (
            identity(e["source"]),
            identity(e["target"]),
            port_key(e["target"], e["targetPort"]),
            e["refKind"],
        )
        for e in vm["edges"]
    }
    return nodes, identities, edge_keys


def _base_card(node: dict, display_ids: dict[str, str]) -> dict:
    """Route a historical card's links to overlay IDs without changing its authored labels."""
    card = deepcopy(node)

    def remap(row: dict, key: str) -> None:
        target = row.get(key)
        if target in display_ids and display_ids[target] != target:
            row[key] = display_ids[target]
            if key == "refTargetId":
                row["refQualPath"] = target

    def remap_kwargs(rows: list) -> None:
        for row in rows:
            for key in (
                "refTargetId",
                "syntheticInlineId",
                "syntheticFreshId",
                "syntheticCollectionId",
            ):
                remap(row, key)
            if row.get("overlayKwargs"):
                remap_kwargs(row["overlayKwargs"])

    if provider := card.get("provider"):
        remap_kwargs(provider["kwargs"])
    if fresh := card.get("freshInstance"):
        remap(fresh, "baseProviderId")
        remap_kwargs(fresh["overlayKwargs"])
    if sequence := (card.get("binding") or {}).get("valueSequence"):
        for item in sequence["items"]:
            remap(item, "targetId")
    return card


def _file_key(file_uri: str, entry_uri: str) -> tuple[str, str]:
    src, entry = _uri_to_path(file_uri), _uri_to_path(entry_uri)
    if src and entry:
        try:
            return "relative", Path(src).relative_to(Path(entry).parent).as_posix()
        except ValueError:
            pass
    return "absolute", file_uri


def compare_graph_plans(
    base: dict,
    proposed: dict,
    base_plan: dict,
    proposed_plan: dict,
    *,
    mode: str,
    base_label: str,
    proposed_label: str,
    alignment: AlignmentMode = "auto",
) -> dict[str, Any]:
    base_recipes, next_recipes = (
        compiled_diff_bindings(base_plan),
        compiled_diff_bindings(proposed_plan),
    )
    matches = match_bindings(
        base_recipes,
        next_recipes,
        alignment=alignment,
        base_origins=base_plan.get("binding_origins"),
        proposed_origins=proposed_plan.get("binding_origins"),
    )
    reference_matches = matches if alignment is not None else None
    base_nodes, base_ids, base_edges = _graph_identities(base)
    next_nodes, next_ids, next_edges = _graph_identities(proposed)
    unavailable = base["reachableUnavailable"] or proposed["reachableUnavailable"]
    scoped = mode == "reachable" and not unavailable
    old_scope = {k for k, n in base_nodes.items() if not scoped or n["flags"]["isReachable"]}
    new_scope = {k for k, n in next_nodes.items() if not scoped or n["flags"]["isReachable"]}

    statuses = {}
    removed_statuses = {}
    logical_changes: dict[tuple, list[str]] = defaultdict(list)

    def record(nid: str, status: str, fields: list) -> None:
        removed = status == "removed"
        (removed_statuses if removed else statuses)[nid] = {"status": status, "fields": fields}
        plan = base_plan if removed else proposed_plan
        origin = plan.get("binding_origins", {}).get(nid, (None, nid))
        logical_changes[(status, *origin)].append(nid)

    old_matched_scope = {matches.base_target(nid) for nid in old_scope}
    new_matched_scope = {matches.proposed_target(nid) for nid in new_scope}
    old_by_match = {new: old for old, new in matches.pairs.items()}
    for nid in matches.added:
        if nid in new_scope:
            record(nid, "added", [])
    for nid in matches.removed:
        if nid in old_scope:
            record(nid, "removed", [])
    for new in next_recipes:
        match = matches.proposed_target(new)
        if match not in old_by_match:
            continue
        if new in new_scope or (
            match in old_matched_scope and match not in new_matched_scope and new == match
        ):
            old = old_by_match[match]
            if fields := _row_changes(base_recipes[old], next_recipes[new], reference_matches):
                record(new, "modified", fields)

    def file_uri(n: dict) -> str | None:
        return n.get("sourceFileUri") or (n.get("activeDefinition") or {}).get("uri")

    next_files = {
        _file_key(uri, proposed["entryFileUri"]): uri
        for n in next_nodes.values()
        if (uri := file_uri(n))
    }

    def base_group_uri(n: dict) -> str | None:
        uri = file_uri(n)
        return next_files.get(_file_key(uri, base["entryFileUri"]), uri) if uri else None

    counts = dict.fromkeys(("added", "modified", "removed"), 0)
    by_file: dict[str, dict] = defaultdict(lambda: dict.fromkeys(counts, 0))
    removals = []
    for (status, *_), aliases in logical_changes.items():
        nid = min(aliases, key=lambda k: (len(k), k))
        if status == "removed":
            uri = base_group_uri(base_nodes[nid])
            removals.append({"id": nid, "aliases": aliases, "fileUri": file_uri(base_nodes[nid])})
        else:
            uri = file_uri(next_nodes[nid])
        counts[status] += 1
        by_file[f"file:{uri}" if uri else "group:unlocated"][status] += 1

    def aligned_identity(key: tuple) -> tuple:
        owner, path = key
        target = matches.base_target(owner)
        # A different, unmatched recipe can still have the same authored spelling.
        return (target or (f"base:{owner}" if owner in next_recipes else owner), path)

    def proposed_identity(key: tuple) -> tuple:
        owner, path = key
        return matches.proposed_target(owner), path

    # Compare aliases by identity, but route the overlay to a visible authored card.
    aligned_next_ids = {nid: proposed_identity(key) for nid, key in next_ids.items()}
    next_by_identity = {}
    for nid in sorted(next_ids, key=lambda k: (k not in new_scope, len(k), k)):
        next_by_identity.setdefault(aligned_next_ids[nid], nid)
    aligned_base_ids = {nid: aligned_identity(key) for nid, key in base_ids.items()}

    def display_id(nid: str, key: tuple) -> str:
        target = next_by_identity.get(key)
        if target is not None:
            # Keep the old spelling when it still resolves to this identity and
            # doesn't hide the wire behind a different reachable alias.
            if aligned_next_ids.get(nid) == key and (nid in new_scope or target not in new_scope):
                return nid
            return target
        # The base namespace is invalid in authored keys and disjoint from proposed synthetic IDs.
        return "base:" + sha256(json.dumps(key).encode()).hexdigest()[:24] if key[1] else key[0]

    map_base = {nid: display_id(nid, key) for nid, key in aligned_base_ids.items()}
    for removal in removals:
        removal["label"] = removal["id"]
        removal["id"] = map_base[removal["id"]]
        removal["aliases"] = [map_base[nid] for nid in removal["aliases"]]
    removal_id = {alias: r["id"] for r in removals for alias in r["aliases"]}
    base_only: dict[str, dict] = {}
    for nid, node in base_nodes.items():
        owner, path = base_ids[nid]
        if nid not in old_scope or aligned_base_ids[nid] in next_by_identity:
            continue
        if not path and nid not in removed_statuses:
            continue
        # Aliases of one removed inline input share a display ID.
        display = map_base[nid]
        if display in base_only:
            continue
        base_only[display] = {
            **_base_card(node, map_base),
            "id": display,
            "primaryPathId": nid if not path else node["bindingName"],
            "sourceFileUri": file_uri(node),
            "comparisonGroupUri": base_group_uri(node),
            "activeDefinition": None,
            "extraRanges": {},
            "diff": {
                "status": "removed",
                "fields": [],
                "ownerId": removal_id.get(map_base[owner], map_base[owner]),
                **({"logicalId": removal_id[map_base[nid]]} if map_base[nid] in removal_id else {}),
            },
        }

    def recipe_at(recipes: dict, owner: str, path: tuple) -> Any:
        value = recipes.get(owner, _ABSENT)
        for step, _kind in path:
            value = (_parts(_unwrap_literal_value(value)) or {}).get(step, _ABSENT)
        return value

    base_by_identity = {aligned_base_ids[nid]: key for nid, key in base_ids.items()}
    # Prefer the explicit pair over any same-origin alias for synthetic recipe lookup.
    base_by_identity.update(
        {aligned_base_ids[nid]: key for nid, key in base_ids.items() if key[0] in matches.pairs}
    )
    for nid, node in next_nodes.items():
        owner, path = next_ids[nid]
        if not path:
            if nid in statuses:
                node["diff"] = statuses[nid]
        elif nid in new_scope or (scoped and matches.proposed_target(owner) in old_matched_scope):
            old_key = base_by_identity.get(aligned_next_ids[nid])
            if old_key is None:
                if nid in new_scope:
                    node["diff"] = {"status": "added", "fields": [], "ownerId": owner}
            elif fields := _row_changes(
                recipe_at(base_recipes, *old_key),
                recipe_at(next_recipes, owner, path),
                reference_matches,
            ):
                node["diff"] = {"status": "modified", "fields": fields, "ownerId": owner}

    def edge_groups(edges: list, keys: dict) -> dict:
        # Copies of one consumer's wiring under different aliases are not extra
        # calls. Repeated references to the same input on one card still count.
        groups = defaultdict(lambda: defaultdict(list))
        for e in edges:
            groups[keys[e["id"]]][e["target"]].append(e["id"])
        return groups

    def excess(groups: dict, other_groups: dict) -> set[str]:
        extra = set()
        for key, by_consumer in groups.items():
            seen = max((len(ids) for ids in other_groups.get(key, {}).values()), default=0)
            for ids in by_consumer.values():
                extra.update(ids[seen:])
        return extra

    aligned_base_edges = {
        eid: (aligned_identity(src), aligned_identity(dst), port, kind)
        for eid, (src, dst, port, kind) in base_edges.items()
    }
    aligned_next_edges = {
        eid: (proposed_identity(src), proposed_identity(dst), port, kind)
        for eid, (src, dst, port, kind) in next_edges.items()
    }
    old_groups = edge_groups(base["edges"], aligned_base_edges)
    new_groups = edge_groups(proposed["edges"], aligned_next_edges)
    removed_edges = excess(old_groups, new_groups)
    added_edges = excess(new_groups, old_groups)
    allowed_base = base_only.keys() | next_nodes.keys()
    old_edges = []
    for e in base["edges"]:
        src, dst, port = e["source"], e["target"], e["targetPort"]
        mapped_src, mapped_dst = map_base[src], map_base[dst]
        if (
            e["id"] not in removed_edges
            or src not in old_scope
            or dst not in old_scope
            or not {mapped_src, mapped_dst} <= allowed_base
        ):
            continue
        # A dictionary insertion can shift generated slot numbers; route to the matching slot.
        key = base_edges[e["id"]][2]
        if key[0] in ("key", "item") and mapped_dst in next_nodes:
            slots = (next_nodes[mapped_dst].get("collection") or {}).get("slots", [])
            matched = next((s["port"] for s in slots if s["label"] == str(key[1])), None)
            port = matched or f"base:{port}"
        old_edges.append(
            {
                **e,
                "id": "base:" + e["id"],
                "source": mapped_src,
                "target": mapped_dst,
                "sourcePath": src,
                "targetPath": dst,
                "targetPort": port,
                "basePortLabel": str(key[1]) if key[0] in ("key", "item") else e["targetPort"],
                "diffStatus": "removed",
            }
        )

    if reference_matches is not None:
        # Keep authored previews but route old links to the base overlay's IDs.
        for node in next_nodes.values():
            if "diff" not in node:
                continue
            for field in node["diff"]["fields"]:
                for delta in (field, *field.get("nested", ())):
                    old = delta["before"]
                    if (target := old.get("targetId")) in map_base:
                        old["targetId"] = map_base[target]

    visible_nodes = [n for n in next_nodes.values() if n["id"] in new_scope]
    proposed_edges = [
        dict(e, diffStatus="added") if e["id"] in added_edges else e for e in proposed["edges"]
    ]
    visible_edges = [e for e in proposed_edges if {e["source"], e["target"]} <= new_scope]
    # Surviving nodes that were reachable only in the base can provide old consumer context.
    context_ids = {e[end] for e in old_edges for end in ("source", "target")} & (
        next_nodes.keys() - new_scope
    )
    return {
        **{k: v for k, v in proposed.items() if k != "syntheticInputs"},
        "nodes": visible_nodes,
        "edges": visible_edges,
        "visibleBindingCount": len(visible_nodes),
        "mode": "reachable" if scoped else "all",
        "reachableUnavailable": unavailable,
        "diff": {
            "baseLabel": base_label,
            "proposedLabel": proposed_label,
            **(
                {"namespaceAlignment": str(matches.alignment)}
                if matches.alignment is not None
                else {}
            ),
            "counts": counts,
            "nodesById": {
                **statuses,
                **{map_base[nid]: data for nid, data in removed_statuses.items()},
            },
            "byFile": dict(by_file),
            "removals": removals,
            "baseOnlyNodes": list(base_only.values()),
            "baseOnlyEdges": old_edges,
            "contextNodes": [next_nodes[k] for k in sorted(context_ids)],
        },
    }
