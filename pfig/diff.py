"""
pfig/diff.py

Implements semantic configuration comparison at the resolved DAG level.
Ref: Spec §15
"""

import inspect
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Literal, Optional

from .ast_nodes import BinaryOpExpr, PFigFile, UnaryOpExpr

# Import types from execution to handle IR comparison
from .execution import (
    ASTCompiler,
    FreshProviderCall,
    LiteralValue,
    ProviderMethodRef,
    ProviderRef,
    ProviderSpec,
)
from .logging import get_logger

logger = get_logger("diff")


class DiffChangeType(Enum):
    """Categorizes the type of change found in a binding."""

    ADDED = auto()
    REMOVED = auto()
    MODIFIED = auto()  # Value changed (e.g. 1 -> 2)
    TYPE_CHANGED = auto()  # Type changed (e.g. Value -> Provider)
    DEPENDENCY_CHANGED = auto()  # Reference changed (@a -> @b)


@dataclass
class DiffDetail:
    """
    Describes a specific change within a binding.
    """

    change_type: DiffChangeType
    old_value: Any = None
    new_value: Any = None
    # For recursive changes (e.g. a specific kwarg inside a provider changed)
    sub_diffs: Dict[str, "DiffDetail"] = field(default_factory=dict)


@dataclass
class DiffReport:
    """
    Container for the full comparison result.
    Ref: Spec §15.3
    """

    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    modified: Dict[str, DiffDetail] = field(default_factory=dict)
    alignment: Optional["NamespaceAlignment"] = None

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.modified)


@dataclass(frozen=True)
class NamespaceAlignment:
    """Mapping from a base namespace to a proposed namespace."""

    base: str
    proposed: str

    def __str__(self) -> str:
        return f"{self.base or '/'} → {self.proposed or '/'}"

    @classmethod
    def parse(cls, spec: str) -> "NamespaceAlignment":
        base, sep, proposed = spec.partition("=")
        valid = r"[^\W\d]\w*(?:/[^\W\d]\w*)*"
        if not sep or any(p != "/" and not re.fullmatch(valid, p) for p in (base, proposed)):
            raise ValueError("Expected BASE=PROPOSED namespace paths; use / for the root")
        return cls(base.removesuffix("/"), proposed.removesuffix("/"))


AlignmentMode = NamespaceAlignment | Literal["auto"] | None
BindingOrigins = Dict[str, tuple[str, str]]


def _binding_groups(
    bindings: Dict[str, Any],
    origins: BindingOrigins,
) -> dict[tuple[str, str], set[str]]:
    groups: dict[tuple[str, str], set[str]] = {}
    for path, origin in origins.items():
        if path in bindings:
            groups.setdefault(origin, set()).add(path)
    return groups


@dataclass(frozen=True)
class BindingMatches:
    # Each pair names one representative per side. Paths of the same compiler
    # origin still resolve to that pair, regardless of which alias was authored.
    pairs: dict[str, str]
    base_aliases: dict[str, str]
    proposed_aliases: dict[str, str]
    removed: list[str]
    added: list[str]
    alignment: NamespaceAlignment | None

    def base_target(self, path: str) -> str | None:
        return self.pairs.get(path) or self.base_aliases.get(path)

    def proposed_target(self, path: str) -> str:
        return self.proposed_aliases.get(path, path)

    def same_target(self, base: str, proposed: str) -> bool:
        matched = self.base_target(base)
        if matched is not None:
            return matched == self.proposed_target(proposed)
        return base == proposed and base not in self.removed and proposed not in self.added


def match_bindings(
    base: Dict[str, Any],
    proposed: Dict[str, Any],
    *,
    alignment: AlignmentMode = "auto",
    base_origins: BindingOrigins | None = None,
    proposed_origins: BindingOrigins | None = None,
) -> BindingMatches:
    """Match authored identities, then explicitly aligned and exact paths."""
    old_origins = (base_origins or {}) if alignment is not None else {}
    new_origins = (proposed_origins or {}) if alignment is not None else {}
    old_groups = _binding_groups(base, old_origins)
    new_groups = _binding_groups(proposed, new_origins)
    explicit = alignment if isinstance(alignment, NamespaceAlignment) else None
    pairs: dict[str, str] = {}
    base_aliases: dict[str, str] = {}
    proposed_aliases: dict[str, str] = {}
    old_remaining, new_remaining = set(base), set(proposed)

    def pair(old: str, new: str) -> None:
        pairs[old] = new
        for alias in old_groups.get(old_origins.get(old, ("", "")), {old}) & old_remaining:
            old_remaining.remove(alias)
            if alias != old:
                base_aliases[alias] = new
        for alias in new_groups.get(new_origins.get(new, ("", "")), {new}) & new_remaining:
            new_remaining.remove(alias)
            if alias != new:
                proposed_aliases[alias] = new

    # The outer entrypoint is the root of comparison, not an imported entrypoint.
    if "__entrypoint__" in old_remaining and "__entrypoint__" in new_remaining:
        pair("__entrypoint__", "__entrypoint__")

    def preferred(paths: set[str]) -> str:
        return min(paths, key=lambda path: (len(path), path))

    for origin in sorted(old_groups.keys() & new_groups.keys()):
        old_paths = old_groups[origin] & old_remaining
        new_paths = new_groups[origin] & new_remaining
        if not old_paths or not new_paths:
            continue
        common = old_paths & new_paths
        if common:
            path = preferred(common)
            pair(path, path)
        else:
            pair(preferred(old_paths), preferred(new_paths))

    if explicit is not None:
        prefix = f"{explicit.base}/" if explicit.base else ""
        dest = f"{explicit.proposed}/" if explicit.proposed else ""
        if prefix and not any(path.startswith(prefix) for path in base):
            raise ValueError(f"Base namespace {explicit.base!r} contains no bindings")
        for old in sorted(old_remaining):
            if old not in old_remaining or not old.startswith(prefix):
                continue
            new = dest + old[len(prefix) :]
            if new in new_remaining:
                pair(old, new)

    for path in sorted(old_remaining & new_remaining):
        if path in old_remaining and path in new_remaining:
            pair(path, path)

    return BindingMatches(
        pairs,
        base_aliases,
        proposed_aliases,
        sorted(old_remaining),
        sorted(new_remaining),
        explicit,
    )


def compare_configs(
    file_a: PFigFile,
    file_b: PFigFile,
    externals: Optional[Dict[str, Any]] = None,
    base_path_a: Optional[str] = None,
    base_path_b: Optional[str] = None,
    *,
    alignment: AlignmentMode = "auto",
) -> DiffReport:
    """
    Main entry point. Compiles both ASTs to IR and compares resolved bindings.
    """
    # 1. Compile both files to get the IR (providers + values)
    compiler_a = ASTCompiler(
        base_path=base_path_a, externals=externals, allow_missing_externals=True
    )
    compiled_a = compiler_a.compile_file(file_a)

    compiler_b = ASTCompiler(
        base_path=base_path_b, externals=externals, allow_missing_externals=True
    )
    compiled_b = compiler_b.compile_file(file_b)

    flat_a, flat_b = compiled_diff_bindings(compiled_a), compiled_diff_bindings(compiled_b)
    matches = match_bindings(
        flat_a,
        flat_b,
        alignment=alignment,
        base_origins=compiled_a.get("binding_origins"),
        proposed_origins=compiled_b.get("binding_origins"),
    )
    modified = {}
    for old, new in matches.pairs.items():
        if change := _compare_values(flat_a[old], flat_b[new], matches):
            modified[new] = change
    return DiffReport(
        added=matches.added, removed=matches.removed, modified=modified, alignment=matches.alignment
    )


def compare_ir_graphs(
    graph_a: Dict[str, Any],
    graph_b: Dict[str, Any],
) -> DiffReport:
    """Compare two IR graph dicts (as produced by ``build_ir_graph``).

    This is the entry point used by replay validate to diff a snapshot's
    stored IR against a freshly recompiled IR.
    """
    return _compare_flat_dicts(graph_a, graph_b)


def _compare_flat_dicts(flat_a: Dict[str, Any], flat_b: Dict[str, Any]) -> DiffReport:
    """Shared comparison logic for two flat binding dictionaries."""
    keys_a = set(flat_a.keys())
    keys_b = set(flat_b.keys())

    added = sorted(list(keys_b - keys_a))
    removed = sorted(list(keys_a - keys_b))
    modified = {}

    for key in keys_a & keys_b:
        diff = _compare_values(flat_a[key], flat_b[key])
        if diff:
            modified[key] = diff

    return DiffReport(added=added, removed=removed, modified=modified)


def flatten_compiled_bindings(compiled_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merges 'providers' and 'values' into a single dictionary for comparison.
    """
    return {**compiled_data.get("providers", {}), **compiled_data.get("values", {})}


MISSING_EXTERNAL = object()


def compiled_diff_bindings(compiled: dict[str, Any]) -> dict[str, Any]:
    return {
        **dict.fromkeys(compiled.get("declared_externals", ()), MISSING_EXTERNAL),
        **flatten_compiled_bindings(compiled),
    }


def _compare_values(
    val_a: Any,
    val_b: Any,
    matches: BindingMatches | None = None,
) -> Optional[DiffDetail]:
    """
    Recursive comparator.
    """
    # 1. Check for None
    if val_a is None and val_b is None:
        return None
    if (val_a is None) != (val_b is None):
        return DiffDetail(DiffChangeType.MODIFIED, val_a, val_b)

    # 2. Check Types
    if type(val_a) != type(val_b):
        return DiffDetail(DiffChangeType.TYPE_CHANGED, val_a, val_b)

    def same_target(old: str, new: str) -> bool:
        return matches.same_target(old, new) if matches else old == new

    # 3. Handle LiteralValues (unwrap)
    if isinstance(val_a, LiteralValue):
        if matches:
            return _compare_values(val_a.value, val_b.value, matches)
        if val_a.value != val_b.value:
            return DiffDetail(DiffChangeType.MODIFIED, val_a.value, val_b.value)
        return None

    # 4. Handle References (Dependency Change detection)
    if isinstance(val_a, ProviderRef):
        if not same_target(val_a.name, val_b.name):
            return DiffDetail(DiffChangeType.DEPENDENCY_CHANGED, val_a.name, val_b.name)
        return None

    if isinstance(val_a, ProviderMethodRef):
        if not same_target(val_a.name, val_b.name) or val_a.attrs != val_b.attrs:
            return DiffDetail(
                DiffChangeType.DEPENDENCY_CHANGED,
                f"{val_a.name}.{'.'.join(val_a.attrs)}",
                f"{val_b.name}.{'.'.join(val_b.attrs)}",
            )
        return None

    if isinstance(val_a, FreshProviderCall):
        if not same_target(val_a.provider_name, val_b.provider_name):
            return DiffDetail(
                DiffChangeType.DEPENDENCY_CHANGED,
                val_a.provider_name,
                val_b.provider_name,
            )
        sub_diffs = {}
        keys_a = set(val_a.overlay_kwargs.keys())
        keys_b = set(val_b.overlay_kwargs.keys())
        for k in keys_a - keys_b:
            sub_diffs[k] = DiffDetail(DiffChangeType.REMOVED, old_value=val_a.overlay_kwargs[k])
        for k in keys_b - keys_a:
            sub_diffs[k] = DiffDetail(DiffChangeType.ADDED, new_value=val_b.overlay_kwargs[k])
        for k in keys_a & keys_b:
            d = _compare_values(val_a.overlay_kwargs[k], val_b.overlay_kwargs[k], matches)
            if d:
                sub_diffs[k] = d
        if sub_diffs:
            return DiffDetail(DiffChangeType.MODIFIED, sub_diffs=sub_diffs)
        return None

    # 5. Handle ProviderSpec
    if isinstance(val_a, ProviderSpec):
        return _compare_providers(val_a, val_b, matches)

    # 6. Handle Containers (List/Tuple)
    if isinstance(val_a, (list, tuple)):
        if len(val_a) != len(val_b):
            return DiffDetail(DiffChangeType.MODIFIED, val_a, val_b)

        sub_diffs = {}
        for i, (item_a, item_b) in enumerate(zip(val_a, val_b)):
            diff = _compare_values(item_a, item_b, matches)
            if diff:
                sub_diffs[str(i)] = diff

        if sub_diffs:
            return DiffDetail(DiffChangeType.MODIFIED, sub_diffs=sub_diffs)
        return None

    # 7. Handle Containers (Dict)
    if isinstance(val_a, dict):
        keys_a = set(val_a.keys())
        keys_b = set(val_b.keys())

        sub_diffs = {}

        # Keys present in one but not the other are modifications
        for k in keys_a - keys_b:
            sub_diffs[k] = DiffDetail(DiffChangeType.REMOVED, old_value=val_a[k])
        for k in keys_b - keys_a:
            sub_diffs[k] = DiffDetail(DiffChangeType.ADDED, new_value=val_b[k])

        # Recursively compare common keys
        for k in keys_a & keys_b:
            diff = _compare_values(val_a[k], val_b[k], matches)
            if diff:
                sub_diffs[k] = diff

        if sub_diffs:
            return DiffDetail(DiffChangeType.MODIFIED, sub_diffs=sub_diffs)
        return None

    if matches and isinstance(val_a, (BinaryOpExpr, UnaryOpExpr)):
        if val_a.operator != val_b.operator:
            return DiffDetail(DiffChangeType.MODIFIED, val_a, val_b)
        operands = ("left", "right") if isinstance(val_a, BinaryOpExpr) else ("operand",)
        changes = {
            operand: change
            for operand in operands
            if (
                change := _compare_values(getattr(val_a, operand), getattr(val_b, operand), matches)
            )
        }
        return DiffDetail(DiffChangeType.MODIFIED, sub_diffs=changes) if changes else None

    # Fallback for basic python types (int, str, float, bool)
    if val_a != val_b:
        return DiffDetail(DiffChangeType.MODIFIED, val_a, val_b)

    return None


def _compare_providers(
    spec_a: ProviderSpec,
    spec_b: ProviderSpec,
    matches: BindingMatches | None = None,
) -> Optional[DiffDetail]:
    """
    Specific comparison for ProviderSpecs.
    """
    # 1. Compare Callable Implementation
    if spec_a.callable_obj != spec_b.callable_obj:
        return DiffDetail(DiffChangeType.MODIFIED, spec_a.callable_obj, spec_b.callable_obj)

    # 2. Compare Positional Args (Recursive)
    sub_diffs = {}
    if len(spec_a.args) != len(spec_b.args):
        sub_diffs["<args>"] = DiffDetail(DiffChangeType.MODIFIED, spec_a.args, spec_b.args)
    else:
        for i, (arg_a, arg_b) in enumerate(zip(spec_a.args, spec_b.args)):
            d = _compare_values(arg_a, arg_b, matches)
            if d:
                sub_diffs[f"<arg{i}>"] = d

    if sub_diffs:
        return DiffDetail(DiffChangeType.MODIFIED, sub_diffs=sub_diffs)

    # 3. Compare Kwargs (Recursive)
    sub_diffs = {}

    keys_a = set(spec_a.kwargs.keys())
    keys_b = set(spec_b.kwargs.keys())

    # Kwargs Added/Removed
    for k in keys_a - keys_b:
        sub_diffs[k] = DiffDetail(DiffChangeType.REMOVED, old_value=spec_a.kwargs[k])
    for k in keys_b - keys_a:
        sub_diffs[k] = DiffDetail(DiffChangeType.ADDED, new_value=spec_b.kwargs[k])

    # Kwargs Modified
    for k in keys_a & keys_b:
        diff = _compare_values(spec_a.kwargs[k], spec_b.kwargs[k], matches)
        if diff:
            sub_diffs[k] = diff

    if sub_diffs:
        return DiffDetail(DiffChangeType.MODIFIED, sub_diffs=sub_diffs)

    return None


# ---------------------------------------------------------------------------
# Diff logging helpers (used by cli and replay)
# ---------------------------------------------------------------------------


def _format_callable(obj: Any) -> str:
    """Format a callable/class to a clean string like 'pkg.module.Class'."""
    if hasattr(obj, "__qualname__"):
        name = obj.__qualname__
    elif hasattr(obj, "__name__"):
        name = obj.__name__
    else:
        return str(obj)

    if hasattr(obj, "__module__") and obj.__module__ not in ("builtins", "__main__"):
        return f"{obj.__module__}.{name}"
    return name


def format_value_short(val: Any) -> str:
    """Format an IR value for diff output."""
    if isinstance(val, LiteralValue):
        return repr(val.value)
    if isinstance(val, ProviderRef):
        return f"@{val.name}"
    if isinstance(val, FreshProviderCall):
        return f"@{val.provider_name}(...)"
    if isinstance(val, ProviderSpec):
        return _format_callable(val.callable_obj) + "(...)"
    if inspect.isclass(val) or inspect.isfunction(val):
        return _format_callable(val)
    return str(val)


def log_diff_summary(report: DiffReport, config1: str, config2: str) -> None:
    """Log high-level diff statistics."""
    logger.info(f"diff --pfig a/{config1} b/{config2}")
    logger.info(f"--- a/{config1}", extra={"color_role": "removed"})
    logger.info(f"+++ b/{config2}", extra={"color_role": "added"})

    mod_count = len(report.modified)
    add_count = len(report.added)
    rem_count = len(report.removed)

    changes = []
    if add_count:
        changes.append(f"{add_count} insertions(+)")
    if rem_count:
        changes.append(f"{rem_count} deletions(-)")
    if mod_count:
        changes.append(f"{mod_count} modified(~)")

    if changes:
        logger.info(f"@@ {', '.join(changes)} @@", extra={"color_role": "hunk"})


def log_diff_details(report: DiffReport) -> None:
    """Log detailed structural diffs in unified format."""
    for name in sorted(report.removed):
        logger.info(f"- {name}", extra={"color_role": "removed"})

    for name in sorted(report.added):
        logger.info(f"+ {name}", extra={"color_role": "added"})

    for name, detail in sorted(report.modified.items()):
        _log_diff_detail_recursive(name, detail, indent=0)


def _log_diff_detail_recursive(key: str, detail: DiffDetail, indent: int) -> None:
    """Recursive logger for diff details."""
    prefix = "  " * indent

    if detail.change_type == DiffChangeType.DEPENDENCY_CHANGED:
        logger.info(f"{prefix}- {key}: @{detail.old_value}", extra={"color_role": "removed"})
        logger.info(f"{prefix}+ {key}: @{detail.new_value}", extra={"color_role": "added"})
        return

    if detail.change_type == DiffChangeType.TYPE_CHANGED:
        type_old = type(detail.old_value).__name__
        type_new = type(detail.new_value).__name__
        val_old = format_value_short(detail.old_value)
        val_new = format_value_short(detail.new_value)

        logger.info(
            f"{prefix}- {key}: {val_old} ({type_old})",
            extra={"color_role": "removed"},
        )
        logger.info(
            f"{prefix}+ {key}: {val_new} ({type_new})",
            extra={"color_role": "added"},
        )
        return

    if detail.change_type == DiffChangeType.MODIFIED:
        if detail.sub_diffs:
            logger.info(f"{prefix}  {key}:")
            for sub_key, sub_detail in detail.sub_diffs.items():
                if sub_detail.change_type == DiffChangeType.ADDED:
                    val_str = format_value_short(sub_detail.new_value)
                    logger.info(
                        f"{prefix}  + {sub_key}: {val_str}",
                        extra={"color_role": "added"},
                    )
                elif sub_detail.change_type == DiffChangeType.REMOVED:
                    val_str = (
                        format_value_short(sub_detail.old_value) if sub_detail.old_value else ""
                    )
                    logger.info(
                        f"{prefix}  - {sub_key}: {val_str}",
                        extra={"color_role": "removed"},
                    )
                else:
                    _log_diff_detail_recursive(sub_key, sub_detail, indent + 1)
        else:
            old_str = format_value_short(detail.old_value)
            new_str = format_value_short(detail.new_value)
            logger.info(f"{prefix}- {key}: {old_str}", extra={"color_role": "removed"})
            logger.info(f"{prefix}+ {key}: {new_str}", extra={"color_role": "added"})
