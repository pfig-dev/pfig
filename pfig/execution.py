"""
pfig Execution Engine - Materializes AST nodes into live Python objects.

This module bridges between the parsed AST and actual Python object instantiation,
handling provider resolution, dependency graphs, and shared vs fresh semantics.
"""

from __future__ import annotations

import builtins
import inspect
from dataclasses import dataclass, field, replace
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

from . import fs as _fs
from .ast_nodes import *
from .bundle import collect_profile_bundle_info
from .includes import IncludeResolution, resolve_include_path
from .lexer import Position
from .logging import get_logger
from .parser import parse_config_file, parse_pfig_source

logger = get_logger("execution")


# ===== Intermediate Representation =====
# These classes bridge between AST nodes and materialized objects


@dataclass
class ProviderSpec:
    """
    Specification for constructing a value: callable + args/kwargs.
    Corresponds to a CallExpr in the AST that defines how to build an object.
    """

    callable_obj: Any  # Python callable (class or function)
    args: List[Any]  # Positional args (may contain other ProviderSpecs/refs)
    kwargs: Dict[str, Any]  # Keyword args
    missing_kwargs: List[str] = field(default_factory=list)
    name: Optional[str] = None  # For error reporting
    canonical_key: Optional[str] = (
        None  # Canonical key for shared instance caching (source_file:name)
    )
    position: Optional[Position] = None  # Source location for error reporting


@dataclass
class ProviderRef:
    """Reference to a shared provider by name: @name"""

    name: str


@dataclass
class ProviderMethodRef:
    """Reference to attributes/methods on a shared provider: @name.a[.b...]"""

    name: str
    attrs: tuple[str, ...]

    def __init__(
        self,
        name: str,
        attrs: str | tuple[str, ...] | list[str] | None = None,
        *,
        method: str | None = None,
    ) -> None:
        # Support ``ProviderMethodRef(name, "method")``,
        # ``ProviderMethodRef(name, method="method")``, and
        # ``ProviderMethodRef(name, ("a", "b"))``.
        if method is not None:
            if attrs is not None:
                raise TypeError("Provide attrs or method, not both")
            attrs = method
        if attrs is None:
            raise TypeError("attrs or method is required")
        if isinstance(attrs, str):
            attrs_t = (attrs,)
        else:
            attrs_t = tuple(attrs)
        if not attrs_t:
            raise ValueError("ProviderMethodRef.attrs must be non-empty")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "attrs", attrs_t)

    @property
    def method(self) -> str:
        """First attribute name (backward-compatible alias)."""
        return self.attrs[0]


@dataclass
class FreshProviderCall:
    """Fresh provider call with kwargs overlay: @name(kwargs)"""

    provider_name: str
    overlay_kwargs: Dict[str, Any]


@dataclass
class LiteralValue:
    """Literal value from the config"""

    value: Any


@dataclass(frozen=True)
class ProfileSpec:
    """Profile file with an optional application prefix.

    When prefix is non-empty, every override in the profile is interpreted
    relative to that prefix.  For example, prefix=("train",) causes
    ``override output_dir = ...`` to apply as ``train/output_dir``.
    """

    path: Path
    prefix: tuple = ()  # tuple[str, ...]


# ===== Utilities =====


class ExecutionError(Exception):
    """Error during provider materialization."""

    def __init__(
        self,
        message: str,
        provider_name: str | None = None,
        position: Position | None = None,
    ):
        self.message = message
        self.provider_name = provider_name
        self.position = position

        error_msg = message
        if provider_name:
            error_msg = f"Provider '{provider_name}': {message}"
        if position:
            error_msg = f"{position}: {error_msg}"

        super().__init__(error_msg)


class NoDispatcherError(ExecutionError):
    """Error when no dispatcher is found for a key."""

    def __init__(self, key: Any):
        super().__init__(f"No dispatcher found for key {key}")


@dataclass
class ValidationIssue:
    """A single validation issue discovered during compilation or validation."""

    code: str  # e.g., "unknown_binding", "missing_external", "unknown_kwarg"
    message: str
    binding: Optional[str] = None  # Which binding caused the issue
    position: Optional[Position] = None  # Source location if available
    severity: Optional[str] = None  # "error" | "warning" (LSP can infer from code)

    def __str__(self) -> str:
        if self.position:
            return f"{self.position} - {self.message}"
        return self.message

    def format(self, include_position: bool = True) -> str:
        """Format the issue for display."""
        if include_position and self.position:
            return f"{self.position} - {self.message}"
        return self.message


class ValidationError(Exception):
    """Raised when validation fails with one or more issues.

    This exception collects multiple validation issues and reports them
    together, allowing users to see all problems at once rather than
    fixing them one at a time.
    """

    def __init__(self, issues: List["ValidationIssue"]):
        # Sort issues by position (file, line, column), with no-position at end
        def sort_key(issue: "ValidationIssue") -> tuple:
            if issue.position:
                return (
                    0,  # Has position - sort first
                    issue.position.filename or "",
                    issue.position.line,
                    issue.position.column,
                )
            return (1, "", 0, 0)  # No position - sort last

        self.issues = sorted(issues, key=sort_key)
        count = len(self.issues)
        summary = f"Validation failed ({count} issue{'s' if count != 1 else ''}):"
        details = "\n".join(f"  - {issue.format()}" for issue in self.issues)
        super().__init__(f"{summary}\n{details}")


class Dispatcher:
    """Visitor pattern dispatcher based on object type."""

    _DISPATCH_KEY = "__dispatch_key__"

    def __init__(self, target: Any):
        self._dispatch_funcs = {}

        methods = filter(callable, target.__class__.__dict__.values())
        for method in methods:
            if isinstance(method, (staticmethod, classmethod)):
                method = method.__func__
            keys = getattr(method, Dispatcher._DISPATCH_KEY, [])
            bound = method.__get__(target, target.__class__)
            for key in keys:
                if key in self._dispatch_funcs:
                    raise ValueError(f"Duplicate dispatch for key {key}")
                self._dispatch_funcs[key] = bound

    @staticmethod
    def register(key: Any):
        def decorator(func):
            if hasattr(func, Dispatcher._DISPATCH_KEY):
                func_keys = getattr(func, Dispatcher._DISPATCH_KEY)
                func_keys.append(key)
                setattr(func, Dispatcher._DISPATCH_KEY, func_keys)
            else:
                setattr(func, Dispatcher._DISPATCH_KEY, [key])
            return func

        return decorator

    def __call__(self, key: Any, *args, **kwargs) -> Optional[Any]:
        if key not in self._dispatch_funcs:
            raise NoDispatcherError(key=key)
        return self._dispatch_funcs[key](*args, **kwargs)


def resolve_fresh_recipe(call: FreshProviderCall, compiled: Dict[str, Any]) -> ProviderSpec:
    """Resolve a fresh recipe without evaluating it; outermost kwargs win.

    Recipe names are not shared-instance dependencies. Both planning and
    materialization use this merge so replaced arguments are never evaluated.
    """
    target = call.provider_name
    overlay = dict(call.overlay_kwargs)
    seen: Set[str] = set()
    while target in compiled["values"]:
        if target in seen:
            raise ExecutionError(f"Cycle in fresh call chain at '{target}'")
        seen.add(target)
        parent = compiled["values"][target]
        if not isinstance(parent, FreshProviderCall):
            raise ExecutionError(f"Cannot make fresh call on non-provider value '{target}'")
        overlay = {**parent.overlay_kwargs, **overlay}
        target = parent.provider_name
    if target not in compiled["providers"]:
        raise ExecutionError(f"Unknown provider '{target}' in fresh call chain")
    base = compiled["providers"][target]
    kwargs = {**base.kwargs, **overlay}
    return replace(
        base,
        kwargs=kwargs,
        missing_kwargs=[key for key in base.missing_kwargs if key not in kwargs],
        name=f"{call.provider_name}(fresh)",
        canonical_key=None,
    )


def missing_external_issues(
    compiled: Dict[str, Any], required_names: Set[str] | None = None
) -> List[ValidationIssue]:
    """One diagnostic per missing canonical input, in deterministic alias order.

    No roots means declaration scope (rootless compilation). Execution callers
    pass the bindings required by their effective dependency plan.
    """
    declared = compiled.get("declared_externals", set())
    origins = compiled.get("binding_origins", {})
    supplied = {origins.get(name, name) for name in compiled.get("provided_externals", set())}
    required = declared if required_names is None else declared & required_names
    reported = set()
    issues = []
    for name in sorted(required):
        origin = origins.get(name, name)
        if origin in supplied or origin in reported:
            continue
        reported.add(origin)
        issues.append(
            ValidationIssue(
                code="missing_external",
                message=f"Missing external value: '{name}' "
                f"(provide via --set {name}=VALUE or --profile)",
                binding=name,
                position=compiled.get("external_positions", {}).get(name),
            )
        )
    return issues


def get_dependencies(value: Any, compiled: Dict[str, Any] | None = None) -> List[str]:
    """Extract lexical edges, or effective evaluation edges when compiled is given."""
    deps = []

    def visit(v):
        if isinstance(v, ProviderRef):
            # Provider references (@name) are always dependencies, regardless of builtin names
            # The @ syntax explicitly means "provider namespace", not builtins
            deps.append(v.name)
        elif isinstance(v, ProviderMethodRef):
            # Method references (@name.method) depend on the provider
            deps.append(v.name)
        elif isinstance(v, FreshProviderCall):
            if compiled is not None:
                visit(resolve_fresh_recipe(v, compiled))
            else:
                deps.append(v.provider_name)
                for arg_val in v.overlay_kwargs.values():
                    visit(arg_val)
        elif isinstance(v, ProviderSpec):
            for arg in v.args:
                visit(arg)
            for kwarg_val in v.kwargs.values():
                visit(kwarg_val)
        elif isinstance(v, BinaryOpExpr):
            visit(v.left)
            visit(v.right)
        elif isinstance(v, UnaryOpExpr):
            visit(v.operand)
        elif isinstance(v, (list, tuple)):
            for item in v:
                visit(item)
        elif isinstance(v, dict):
            for k, val in v.items():
                visit(k)
                visit(val)

    visit(value)
    return deps


def check_missing_kwargs_usage(
    compiled_data: Dict[str, Any],
    reachable_names: Set[str],
) -> List[ValidationIssue]:
    """Check only constructed recipes, after all fresh overlays are merged."""
    issues = []

    def visit(value):
        if isinstance(value, FreshProviderCall):
            visit(resolve_fresh_recipe(value, compiled_data))
        elif isinstance(value, ProviderSpec):
            if value.missing_kwargs:
                issues.append(
                    ValidationIssue(
                        code="missing_kwarg",
                        message=f"Provider '{value.name}' is missing required kwargs "
                        f"{value.missing_kwargs}",
                        binding=value.name,
                        position=value.position,
                    )
                )
            for arg in value.args:
                visit(arg)
            for arg in value.kwargs.values():
                visit(arg)
        elif isinstance(value, BinaryOpExpr):
            visit(value.left)
            visit(value.right)
        elif isinstance(value, UnaryOpExpr):
            visit(value.operand)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                visit(key)
                visit(item)

    bindings = {**compiled_data["providers"], **compiled_data["values"]}
    for name in sorted(reachable_names):
        if name in bindings:
            visit(bindings[name])
    return issues


def topological_sort(
    graph: Dict[str, List[str]],
    positions: Optional[Dict[str, Position]] = None,
    all_bindings: Optional[List[str]] = None,
    declared_externals: Optional[Set[str]] = None,
) -> Tuple[List[str], List[ValidationIssue]]:
    """
    Topologically sort a dependency graph, collecting validation issues.

    Returns nodes in dependency order (dependencies before dependents).
    Always collects validation issues instead of raising - caller decides
    whether to raise based on the returned issues.

    Args:
        graph: Dependency graph as adjacency list
        positions: Optional mapping of binding names to source positions
        all_bindings: All available bindings for "did you mean" suggestions
        declared_externals: Set of declared external names (skip unknown binding errors for these)

    Returns:
        Tuple of (sorted_nodes, validation_issues). If issues is non-empty,
        the sorted_nodes may be incomplete.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}
    result = []
    stack = []
    issues: List[ValidationIssue] = []
    seen_unknown: Set[str] = set()  # Track reported unknown bindings to avoid duplicates
    positions = positions or {}
    all_bindings = all_bindings or list(graph.keys())
    declared_externals = declared_externals or set()

    def suggest_similar(name: str) -> str:
        """Return 'did you mean' suggestion if a similar binding exists."""
        matches = get_close_matches(name, all_bindings, n=100, cutoff=0.6)
        return f" - did you mean '{matches[0]}'?" if matches else ""

    def dfs(node: str) -> bool:
        """DFS traversal. Returns False if a fatal issue was found."""
        if node not in graph:
            # Skip unknown binding error if this is a declared external
            # (the "missing external" error is more specific)
            if node not in seen_unknown and node not in declared_externals:
                seen_unknown.add(node)
                suggestion = suggest_similar(node)
                issues.append(
                    ValidationIssue(
                        code="unknown_binding",
                        message=f"Unknown binding: '{node}'{suggestion}",
                        binding=node,
                        position=positions.get(node),
                    )
                )
            return False

        color[node] = GRAY
        stack.append(node)

        for dependency in graph[node]:
            if dependency not in graph:
                # Skip unknown binding error if this is a declared external
                if dependency not in seen_unknown and dependency not in declared_externals:
                    seen_unknown.add(dependency)
                    suggestion = suggest_similar(dependency)
                    # Use the referencing node's position since the unknown binding has none
                    issues.append(
                        ValidationIssue(
                            code="unknown_binding",
                            message=f"Unknown binding: '{dependency}' (referenced by '{node}'){suggestion}",
                            binding=dependency,
                            position=positions.get(node),
                        )
                    )
                continue  # Skip unknown dependency but continue checking others

            if color[dependency] == WHITE:
                if not dfs(dependency):
                    continue  # Skip if dependency had issues
            elif color[dependency] == GRAY:
                # Found a cycle
                cycle_start = stack.index(dependency)
                cycle = stack[cycle_start:] + [dependency]
                issues.append(
                    ValidationIssue(
                        code="dependency_cycle",
                        message=f"Dependency cycle detected: {' -> '.join(cycle)}",
                        binding=node,
                        position=positions.get(node),
                    )
                )
                # Don't return False - let other nodes still be processed

        stack.pop()
        color[node] = BLACK
        result.append(node)
        return True

    for node in graph:
        if color[node] == WHITE:
            dfs(node)

    return result, issues


# ===== AST to Execution Bridge =====


BindingId = tuple[str, str]


@dataclass(frozen=True)
class OverrideCandidate:
    """An unresolved override, retaining its author and diagnostic spelling."""

    target: BindingId
    author: str
    path: str
    value: Any
    position: Position


@dataclass(frozen=True)
class RuntimeInputLayer:
    """One explicitly ordered input layer; aliases within it have equal precedence."""

    values: Dict[str, Any]
    source: str = "cli"


@dataclass(frozen=True)
class RuntimeAssignment:
    """A winning input with its original path and provenance."""

    path: str
    value: Any
    source: str


def _runtime_values_equal(left: Any, right: Any) -> bool:
    """Compare runtime literals without collapsing types or invoking opaque equality.

    Builtin containers compare structurally, including cycles. Other Python
    objects (including container subclasses) compare only by identity.
    """
    scalar_types = (type(None), str, int, float, bool, bytes)

    def key(value):
        # Dictionary keys cannot contain cyclic builtin containers. Preserve key
        # types as well: Python's usual lookup would conflate True, 1, and 1.0.
        if type(value) is tuple:
            return (tuple, tuple(key(item) for item in value))
        if type(value) in scalar_types:
            return (type(value), value)
        return (type(value), id(value))

    pending = [(left, right)]
    seen = set()
    while pending:
        left, right = pending.pop()
        if left is right:
            continue
        if type(left) is not type(right):
            return False
        if type(left) in scalar_types:
            if left != right:
                return False
            continue
        if type(left) not in (list, tuple, dict):
            return False
        pair = (id(left), id(right))
        if pair in seen:
            continue
        seen.add(pair)
        if len(left) != len(right):
            return False
        if type(left) is dict:
            right_values = {key(k): v for k, v in right.items()}
            for k, value in left.items():
                typed_key = key(k)
                if typed_key not in right_values:
                    return False
                pending.append((value, right_values[typed_key]))
        else:
            pending.extend(zip(left, right))
    return True


def _validate_runtime_targets(
    assignments: Dict[str, RuntimeAssignment],
    binding_origins: Dict[str, BindingId],
    declared_externals: Set[str],
):
    """Reject unknown runtime targets and profiles targeting ordinary bindings."""
    # Preserve path-specific profile diagnostics before exporting canonical inputs.
    runtime_overrides = {k: v for k, v in assignments.items() if k not in declared_externals}

    if not runtime_overrides:
        return

    logger.debug(f"Validating {len(runtime_overrides)} ordinary runtime targets")

    # Collect failures from unprefixed profiles so we can emit a grouped suggestion.
    # Key: profile path string.  Value: list of override paths that failed.
    failed_unprefixed: "Dict[str, list[str]]" = {}
    # Collect failures from prefixed profiles for per-override hints.
    failed_prefixed: "list[tuple[str, str]]" = []  # (path_str, profile_path)

    for path_str in runtime_overrides:
        source = runtime_overrides[path_str].source

        if source.startswith("profile-prefixed:"):
            # Profiles applied with an explicit prefix — collect for per-override hint.
            profile_path = source[len("profile-prefixed:") :]
            failed_prefixed.append((path_str, profile_path))
            continue

        if source.startswith("profile:"):
            # Unprefixed profile — collect all failures before building the suggestion.
            profile_path = source[len("profile:") :]
            if profile_path not in failed_unprefixed:
                failed_unprefixed[profile_path] = []
            failed_unprefixed[profile_path].append(path_str)
            continue

        if path_str not in binding_origins:
            raise ExecutionError(
                f"Runtime override target '{path_str}' not found in configuration. "
                f"Cannot override non-existent binding via --set."
            )

    # Raise grouped error for unprefixed-profile failures with a prefix suggestion.
    for profile_path, override_paths in failed_unprefixed.items():
        suggestion = infer_missing_profile_target_suggestion(
            failed_overrides=override_paths,
            declared_externals=declared_externals,
            profile_path=profile_path,
        )
        override_list = ", ".join(f"'{p}'" for p in sorted(override_paths))
        msg = (
            f"Profile override(s) {override_list} are not declared externals in "
            f"the configuration. Profiles may only override externals."
        )
        raise ExecutionError(msg + (f"\n\n{suggestion}" if suggestion else ""))

    # Raise per-override errors for prefixed-profile failures.
    for path_str, profile_path in failed_prefixed:
        hint = _profile_override_hint(path_str, declared_externals, profile_path)
        raise ExecutionError(
            f"Profile override '{path_str}' is not a declared external in configuration. "
            f"Profiles may only override externals; declare 'external {path_str}' (or use --set for runtime overrides)."
            + (f"\n\n{hint}" if hint else "")
        )


def resolve_runtime_layers(
    binding_origins: Dict[str, BindingId],
    declared_externals: Set[str],
    layers: Sequence[RuntimeInputLayer],
) -> Dict[BindingId, RuntimeAssignment]:
    """Resolve input layers canonically, without modifying inputs or expanding aliases.

    All layers are checked for conflicts, including those shadowed by later layers.
    Used by compilation and replay; only its result may be flattened for transport.
    """
    resolved: Dict[BindingId, RuntimeAssignment] = {}
    unknown_paths: Dict[str, RuntimeAssignment] = {}
    issues = []
    for layer in layers:
        grouped: Dict[BindingId, list[RuntimeAssignment]] = {}
        for path, value in layer.values.items():
            assignment = RuntimeAssignment(path, value, layer.source)
            origin = binding_origins.get(path)
            if origin is not None:
                grouped.setdefault(origin, []).append(assignment)
            else:
                unknown_paths[path] = assignment
        for origin, assignments in grouped.items():
            if any(
                not _runtime_values_equal(assignments[0].value, a.value) for a in assignments[1:]
            ):
                issues.append(
                    ValidationIssue(
                        code="override_conflict",
                        message=f"Conflicting runtime assignments for '{origin}': "
                        + ", ".join(a.path for a in assignments),
                    )
                )
            else:
                resolved[origin] = min(assignments, key=lambda a: a.path)
    if issues:
        raise ValidationError(issues)
    # Profile restrictions apply to canonical winners, not shadowed alias paths.
    # Unknown paths have no canonical identity and must still produce diagnostics.
    effective_paths = {**unknown_paths, **{a.path: a for a in resolved.values()}}
    _validate_runtime_targets(effective_paths, binding_origins, declared_externals)
    return resolved


class ASTCompiler:
    """Compiles pfig AST nodes into executable intermediate representations."""

    def __init__(
        self,
        base_path: str | None = None,
        include_stack: List[str] | None = None,
        loaded_files: Dict[str, Dict[str, Any]] | None = None,
        file_origins: Dict[str, IncludeResolution] | None = None,
        externals: Dict[str, Any] | None = None,
        runtime_layers: Sequence[RuntimeInputLayer] | None = None,
        is_profile: bool = False,
        strict: bool = False,
        allow_missing_externals: bool = False,
        resolve_imports: bool = True,
        compile_bindings: bool = True,
        _include_graph: Dict[str, Set[str]] | None = None,
        defer_external_validation: bool = False,
    ):
        self.import_table: Dict[str, Any] = {}  # Python imports
        self.providers: Dict[str, ProviderSpec] = {}  # Provider definitions
        self.values: Dict[str, Any] = {}  # Simple values
        self.runtime_layers = (
            tuple(runtime_layers)
            if runtime_layers is not None
            else (RuntimeInputLayer(dict(externals or {})),)
        )
        # Output views, never fed back into resolution as supplied inputs.
        self.resolved_externals: Dict[str, Any] = {}
        self.runtime_inputs: Dict[str, Any] = {}
        self.runtime_sources: Dict[str, str] = {}
        self.declared_externals: Set[str] = set()  # External paths declared in config
        self.external_positions: Dict[str, Position] = {}  # Positions of external declarations
        self.is_profile = is_profile  # Whether this is a profile file (can override externals)
        self.base_path = base_path  # Base path for resolving relative includes
        self.include_stack = (
            include_stack if include_stack is not None else []
        )  # For cycle detection (shared)
        self.loaded_files = (
            loaded_files if loaded_files is not None else {}
        )  # Cache for included files (shared)
        self.file_origins: Dict[str, IncludeResolution] = (
            file_origins if file_origins is not None else {}
        )  # Tracks origin of each loaded file for bundling (shared)
        self._include_graph: Dict[str, Set[str]] = (
            _include_graph if _include_graph is not None else {}
        )  # filename -> set of included filenames (shared)
        self.strict = (
            strict  # If True, treat validation warnings (like signature missing) as errors
        )
        self.allow_missing_externals = allow_missing_externals
        # Target-bearing callers check inputs only after planning the effective graph.
        self.defer_external_validation = defer_external_validation
        # When bundling / file discovery, we do NOT want to import Python modules
        # or compile bindings (which requires imports and can trigger runtime-override checks).
        self.resolve_imports = resolve_imports
        self.compile_bindings = compile_bindings

        self.validation_issues: List[ValidationIssue] = []  # Collected validation issues
        self._used_include_aliases: Set[str] = set()  # Aliases used by includes in this file
        self._profile_override_paths: Set[str] = set()  # Overrides authored in this file
        # Unresolved module metadata. Only the selected root resolves candidates.
        self.binding_origins: Dict[str, BindingId] = {}
        self.override_candidates: list[OverrideCandidate] = []
        # Root-qualified path to the winning authored override, shared by all aliases.
        self.authored_override_paths: Dict[BindingId, str] = {}
        self.source_id = (
            base_path
            if base_path and _fs.is_uri(base_path)
            else str(Path(base_path).resolve())
            if base_path
            else "<source>"
        )

    def add_validation_issue(
        self,
        code: str,
        message: str,
        binding: Optional[str] = None,
        position: Optional[Position] = None,
    ) -> None:
        """Record a validation issue without raising immediately.

        This allows collecting multiple validation errors to report them
        all at once, rather than stopping at the first error.
        """
        self.validation_issues.append(
            ValidationIssue(code=code, message=message, binding=binding, position=position)
        )

    def raise_if_validation_issues(self) -> None:
        """Raise ValidationError if any issues were collected."""
        if self.validation_issues:
            raise ValidationError(self.validation_issues)

    # Alias for convenience
    raise_if_issues = raise_if_validation_issues

    def compile_file(self, ast_file: PFigFile) -> Dict[str, Any]:
        """Compile a complete pfig file AST into execution-ready form."""
        logger.debug("Starting compilation of pfig file")

        # Process imports first
        if self.resolve_imports:
            import_stmts = list(ast_file.imports())
            if import_stmts:
                logger.debug(f"Processing {len(import_stmts)} import statements")

            for stmt in ast_file.imports():
                self.process_import(stmt)

            if self.import_table:
                logger.debug(f"Imported symbols: {list(self.import_table.keys())}")
        else:
            # File discovery / bundling: skip Python imports entirely.
            # We only need include resolution + file origin tracking.
            if list(ast_file.imports()):
                logger.debug("Skipping import resolution (resolve_imports=False)")

        # Process includes before bindings (bindings might reference included symbols)
        include_stmts = list(ast_file.includes())
        if include_stmts:
            logger.debug(f"Processing {len(include_stmts)} include statements")

        for include_stmt in include_stmts:
            self.process_include(include_stmt)

        # Process external declarations
        external_stmts = list(ast_file.externals())
        if external_stmts:
            logger.debug(f"Processing {len(external_stmts)} external statements")

        for external_stmt in external_stmts:
            self.process_external(external_stmt)

        # Process bindings into providers and values (skip for file discovery/bundling)
        if self.compile_bindings:
            bindings = ast_file.bindings()
            if bindings:
                logger.debug(f"Processing {len(bindings)} bindings")

            for binding in bindings:
                logger.debug(f"Compiling binding: {binding.name}")

                # Check if this is a profile file - profiles cannot define regular bindings
                if self.is_profile:
                    raise ExecutionError(
                        f"Profile files cannot define regular bindings. Found binding '{binding.name}'. "
                        f"Use 'override {binding.name} = ...' instead."
                    )

                compiled_value = self.compile_expr(binding.value)

                # Check for name collision (import vs binding)
                if binding.name in self.import_table:
                    self.add_validation_issue(
                        code="name_collision",
                        message=f"Binding '{binding.name}' shadows import. "
                        f"Choose a different name or use an alias in the import.",
                        binding=binding.name,
                        position=binding.position,
                    )
                    continue

                # Check for duplicate bindings (shadowing without override)
                if (
                    binding.name in self.providers
                    or binding.name in self.values
                    or binding.name in self.declared_externals
                ):
                    self.add_validation_issue(
                        code="duplicate_binding",
                        message=f"Binding '{binding.name}' redefines existing binding. "
                        f"Use 'override {binding.name} = ...' to explicitly override.",
                        binding=binding.name,
                        position=binding.position,
                    )
                    continue  # Skip this binding but continue processing others

                if isinstance(compiled_value, ProviderSpec):
                    compiled_value.name = binding.name
                    self.providers[binding.name] = compiled_value
                    logger.debug(f"Added provider: {binding.name}")
                else:
                    self.values[binding.name] = compiled_value
                    logger.debug(f"Added value: {binding.name}")
        else:
            if list(ast_file.bindings()):
                logger.debug("Skipping binding compilation (compile_bindings=False)")

        for name in self.providers.keys() | self.values.keys() | self.declared_externals:
            self.binding_origins.setdefault(name, (self.source_id, name))

        # Check references in the author's scope, before a parent can supply names.
        if self.compile_bindings and not self.is_profile:
            for binding in ast_file.bindings():
                self._validate_lexical_references(
                    self.providers.get(binding.name, self.values.get(binding.name)),
                    binding.name,
                    binding.position,
                )

        # Process overrides after bindings (skip in file discovery mode)
        if self.compile_bindings:
            override_stmts = list(ast_file.overrides())
            if override_stmts:
                logger.debug(f"Processing {len(override_stmts)} override statements")

            for override_stmt in override_stmts:
                self.process_override(override_stmt)
        else:
            if list(ast_file.overrides()):
                logger.debug("Skipping overrides (compile_bindings=False)")

        if self.compile_bindings and not self.include_stack and not self.is_profile:
            self._resolve_effective_bindings()

        # If this is a profile, validate that it only contains overrides of externals
        if self.is_profile:
            self._validate_profile()

        logger.debug(
            f"Compilation complete: {len(self.providers)} providers, {len(self.values)} values, {len(self.declared_externals)} externals"
        )

        compiled = {
            "providers": self.providers,
            "values": self.values,
            "import_table": self.import_table,
            "declared_externals": self.declared_externals,
            "external_positions": self.external_positions,
            "provided_externals": set(self.resolved_externals),
            "resolved_externals": self.resolved_externals,
            "runtime_inputs": self.runtime_inputs,
            "runtime_sources": self.runtime_sources,
            "include_graph": self._include_graph,
            "binding_origins": self.binding_origins,
            "override_candidates": self.override_candidates,
            "authored_override_paths": self.authored_override_paths,
            "validation_issues": self.validation_issues,
        }
        if (
            not self.is_profile
            and not self.include_stack
            and not self.defer_external_validation
            and not self.allow_missing_externals
        ):
            self.validation_issues.extend(missing_external_issues(compiled))
        return compiled

    def process_import(self, import_stmt: Union[ImportStmt, FromImportStmt]):
        """Process import statements and populate import table."""
        import importlib

        try:
            if isinstance(import_stmt, ImportStmt):
                if import_stmt.module is None:
                    raise ExecutionError("Import statement missing module name")
                logger.debug(f"Importing module: {import_stmt.module}")
                module = importlib.import_module(import_stmt.module)
                name = import_stmt.alias or import_stmt.module.split(".")[0]
                self.import_table[name] = module
                logger.debug(f"Added import: {name} -> {import_stmt.module}")

            elif isinstance(import_stmt, FromImportStmt):
                if import_stmt.module is None:
                    raise ExecutionError("From import statement missing module name")
                logger.debug(
                    f"From import: {import_stmt.module} import {[item.name for item in import_stmt.names]}"
                )
                module = importlib.import_module(import_stmt.module)
                for item in import_stmt.names:
                    obj = getattr(module, item.name)
                    name = item.alias or item.name
                    self.import_table[name] = obj
                    logger.debug(f"Added from import: {name} -> {import_stmt.module}.{item.name}")
        except (ModuleNotFoundError, ImportError) as e:
            logger.error(f"Failed to import: {e}")
            pos = getattr(import_stmt, "module_position", None) or import_stmt.position
            raise ValidationError(
                [
                    ValidationIssue(
                        code="import_error",
                        message=str(e),
                        position=pos,
                        severity="error",
                    )
                ]
            ) from e
        except AttributeError as e:
            logger.error(f"Import attribute error: {e}")
            pos = getattr(import_stmt, "module_position", None) or import_stmt.position
            raise ValidationError(
                [
                    ValidationIssue(
                        code="import_error",
                        message=str(e),
                        position=pos,
                        severity="error",
                    )
                ]
            ) from e

    def process_include(self, include_stmt: IncludeStmt):
        """Process include statements and populate namespace with included bindings."""
        from .parser import parse_pfig_source

        # Check for duplicate include alias
        alias = include_stmt.alias
        if alias in self._used_include_aliases:
            self.add_validation_issue(
                code="duplicate_include_alias",
                message=f"Duplicate include alias '{alias}'. Each include must use a unique alias.",
                binding=alias,
                position=include_stmt.position,
            )
            return
        self._used_include_aliases.add(alias)

        # Resolve an explicit filesystem include relative to the containing file.
        try:
            # base_path for resolve_include_path should be the current file's path
            if self.base_path:
                base_path = Path(self.base_path)
            else:
                # If no base_path, use current working directory as fallback
                base_path = Path.cwd() / "dummy.pfig"

            resolution = resolve_include_path(include_stmt.path, base_path=base_path)
            include_path = resolution.path
        except ValueError as e:
            pos = getattr(include_stmt, "path_position", None) or include_stmt.position
            raise ValidationError(
                [
                    ValidationIssue(
                        code="include_error",
                        message=str(e),
                        position=pos,
                        severity="error",
                    )
                ]
            ) from e
        except FileNotFoundError as e:
            pos = getattr(include_stmt, "path_position", None) or include_stmt.position
            raise ValidationError(
                [
                    ValidationIssue(
                        code="include_error",
                        message=str(e),
                        position=pos,
                        severity="error",
                    )
                ]
            ) from e

        include_path_str = str(include_path)

        # Track file origin for bundling (only if not already tracked)
        if include_path_str not in self.file_origins:
            self.file_origins[include_path_str] = resolution

        # Check for cycles
        if include_path_str in self.include_stack:
            cycle_chain = " -> ".join(self.include_stack + [include_path_str])
            pos = getattr(include_stmt, "path_position", None) or include_stmt.position
            raise ValidationError(
                [
                    ValidationIssue(
                        code="include_cycle",
                        message=f"Include cycle detected: {cycle_chain}",
                        position=pos,
                        severity="error",
                    )
                ]
            )

        # Also check if we're trying to include ourselves
        if (
            self.base_path
            and (
                self.base_path
                if _fs.is_uri(self.base_path)
                else str(Path(self.base_path).resolve())
            )
            == include_path_str
        ):
            pos = getattr(include_stmt, "path_position", None) or include_stmt.position
            raise ValidationError(
                [
                    ValidationIssue(
                        code="include_error",
                        message=f"Self-include detected: {include_stmt.path}",
                        position=pos,
                        severity="error",
                    )
                ]
            )

        # Ancestry vertices must use the same IDs as override authors, even for
        # an in-memory root with no filename.
        self._include_graph.setdefault(self.source_id, set()).add(include_path_str)

        # Check if already loaded
        if include_path_str in self.loaded_files:
            logger.debug(f"Using cached include: {include_stmt.path}")
            included_data = self.loaded_files[include_path_str]
            cached_graph = included_data.get("include_graph", {})
            if cached_graph is not self._include_graph:
                # Restore this module's ancestry, not an earlier root's context.
                pending = [include_path_str]
                visited = set()
                while pending:
                    source = pending.pop()
                    if source in visited:
                        continue
                    visited.add(source)
                    children = cached_graph.get(source, ())
                    self._include_graph.setdefault(source, set()).update(children)
                    pending.extend(children)
        else:
            # Load and compile included file
            logger.debug(f"Loading include: {include_stmt.path} -> {include_path}")

            pos = getattr(include_stmt, "path_position", None) or include_stmt.position
            try:
                if not _fs.exists(include_path):
                    raise ValidationError(
                        [
                            ValidationIssue(
                                code="include_error",
                                message=f"Include file not found: {include_stmt.path}",
                                position=pos,
                                severity="error",
                            )
                        ]
                    )

                include_content = _fs.read_text(include_path)
            except (OSError, UnicodeDecodeError) as e:
                raise ValidationError(
                    [
                        ValidationIssue(
                            code="include_error",
                            message=f"Failed to read include file {include_stmt.path}: {e}",
                            position=pos,
                            severity="error",
                        )
                    ]
                ) from e

            # Parse included file - use original path as written for explicitness
            try:
                include_ast = parse_pfig_source(include_content, filename=include_stmt.path)
            except Exception as e:
                parse_pos = getattr(e, "position", None) or pos
                raise ValidationError(
                    [
                        ValidationIssue(
                            code="include_error",
                            message=f"Failed to parse include file {include_stmt.path}: {e}",
                            position=parse_pos,
                            severity="error",
                        )
                    ]
                ) from e

            # Compile included file recursively
            self.include_stack.append(include_path_str)
            try:
                # Cache unresolved modules, independent of root runtime inputs.
                include_compiler = ASTCompiler(
                    base_path=include_path_str,
                    include_stack=self.include_stack,  # Share the stack
                    loaded_files=self.loaded_files,  # Share the cache
                    file_origins=self.file_origins,  # Share file origins for bundling
                    externals={},
                    is_profile=self.is_profile,  # Share profile mode
                    strict=self.strict,  # Share strict mode setting
                    allow_missing_externals=self.allow_missing_externals,
                    resolve_imports=self.resolve_imports,
                    compile_bindings=self.compile_bindings,
                    _include_graph=self._include_graph,  # Share include graph
                )
                included_data = include_compiler.compile_file(include_ast)
                self.loaded_files[include_path_str] = included_data
            except Exception as e:
                raise
            finally:
                self.include_stack.pop()

        # Retain unresolved candidates until the complete include DAG is known.
        alias = include_stmt.alias
        self.validation_issues.extend(included_data.get("validation_issues", []))
        for name, origin in included_data.get("binding_origins", {}).items():
            self.binding_origins[f"{alias}/{name}"] = origin
        for candidate in included_data.get("override_candidates", []):
            self.override_candidates.append(
                replace(
                    candidate,
                    path=f"{alias}/{candidate.path}",
                    value=self._add_namespace_to_value(candidate.value, alias, included_data),
                )
            )

        # Add included bindings to current namespace with alias prefix
        logger.debug(
            f"Including {len(included_data.get('providers', {}))} providers, {len(included_data.get('values', {}))} values, and {len(included_data.get('declared_externals', set()))} externals as {alias}/*"
        )

        # Add providers with namespace prefix, updating internal references
        for name, provider in included_data.get("providers", {}).items():
            namespaced_name = f"{alias}/{name}"
            if namespaced_name in self.providers:
                self.add_validation_issue(
                    code="duplicate_provider",
                    message=f"Duplicate provider after include: {namespaced_name}",
                    binding=namespaced_name,
                    position=include_stmt.position,
                )
                continue  # Skip but continue processing

            # Update provider to use namespaced references for included dependencies
            namespaced_provider = self._add_namespace_to_provider(provider, alias, included_data)

            self.providers[namespaced_name] = namespaced_provider
            logger.debug(f"Added included provider: {namespaced_name}")

        # Add values with namespace prefix, updating internal references
        for name, value in included_data.get("values", {}).items():
            namespaced_name = f"{alias}/{name}"
            if namespaced_name in self.values:
                self.add_validation_issue(
                    code="duplicate_value",
                    message=f"Duplicate value after include: {namespaced_name}",
                    binding=namespaced_name,
                    position=include_stmt.position,
                )
                continue  # Skip but continue processing

            # Update value to use namespaced references for included dependencies
            namespaced_value = self._add_namespace_to_value(value, alias, included_data)
            self.values[namespaced_name] = namespaced_value
            logger.debug(f"Added included value: {namespaced_name}")

        # Add external declarations with namespace prefix
        declared_externals = included_data.get("declared_externals", set())
        included_external_positions = included_data.get("external_positions", {})
        for external_name in declared_externals:
            namespaced_external = f"{alias}/{external_name}"
            self.declared_externals.add(namespaced_external)
            # Copy position with namespaced name
            if external_name in included_external_positions:
                self.external_positions[namespaced_external] = included_external_positions[
                    external_name
                ]
            logger.debug(f"Added included external declaration: {namespaced_external}")

    def _add_namespace_to_provider(
        self, provider: ProviderSpec, alias: str, included_data: Dict[str, Any]
    ) -> ProviderSpec:
        """Add namespace prefix to provider references that point to included bindings."""
        return ProviderSpec(
            callable_obj=provider.callable_obj,
            name=provider.name,
            position=provider.position,
            args=[self._add_namespace_to_value(arg, alias, included_data) for arg in provider.args],
            kwargs={
                k: self._add_namespace_to_value(v, alias, included_data)
                for k, v in provider.kwargs.items()
            },
            missing_kwargs=provider.missing_kwargs,
            canonical_key=provider.canonical_key,  # Preserve canonical key
        )

    def _add_namespace_to_value(self, value: Any, alias: str, included_data: Dict[str, Any]) -> Any:
        """Add namespace prefix to references that point to included bindings."""
        if isinstance(value, ProviderRef):
            # Check if this reference points to an included binding
            if value.name in included_data.get("binding_origins", {}):
                return ProviderRef(f"{alias}/{value.name}")
            else:
                return value
        elif isinstance(value, ProviderMethodRef):
            # Check if this reference points to an included binding
            if value.name in included_data.get("binding_origins", {}):
                return ProviderMethodRef(f"{alias}/{value.name}", value.attrs)
            else:
                return value
        elif isinstance(value, FreshProviderCall):
            # Check if this provider call points to an included provider
            provider_name = value.provider_name
            if provider_name in included_data.get("binding_origins", {}):
                namespaced_provider_name = f"{alias}/{provider_name}"
            else:
                namespaced_provider_name = provider_name

            return FreshProviderCall(
                provider_name=namespaced_provider_name,
                overlay_kwargs={
                    k: self._add_namespace_to_value(v, alias, included_data)
                    for k, v in value.overlay_kwargs.items()
                },
            )
        elif isinstance(value, ProviderSpec):
            return self._add_namespace_to_provider(value, alias, included_data)
        elif isinstance(value, (BinaryOpExpr, UnaryOpExpr)):
            # Handle binary and unary operations
            if isinstance(value, BinaryOpExpr):
                return BinaryOpExpr(
                    position=value.position,
                    left=self._add_namespace_to_value(value.left, alias, included_data),
                    operator=value.operator,
                    right=self._add_namespace_to_value(value.right, alias, included_data),
                )
            else:  # UnaryOpExpr
                return UnaryOpExpr(
                    position=value.position,
                    operator=value.operator,
                    operand=self._add_namespace_to_value(value.operand, alias, included_data),
                )
        elif isinstance(value, (list, tuple)):
            items = [self._add_namespace_to_value(item, alias, included_data) for item in value]
            return tuple(items) if isinstance(value, tuple) else items
        elif isinstance(value, dict):
            return {
                k: self._add_namespace_to_value(v, alias, included_data) for k, v in value.items()
            }
        else:
            # Literal values, etc. - no changes needed
            return value

    def _validate_lexical_references(self, value, binding, position):
        for ref in get_dependencies(value):
            if ref not in self.binding_origins:
                self.add_validation_issue(
                    "unknown_binding",
                    f"Unknown binding: '{ref}'; add an explicit include "
                    "in the referencing file."
                    + (
                        f" Did you mean '{matches[0]}'?"
                        if (matches := get_close_matches(ref, self.binding_origins, n=1))
                        else ""
                    ),
                    binding,
                    position,
                )

    def _resolve_effective_bindings(self):
        """Resolve by inclusion ancestry, not namespace depth or traversal order."""
        # Keep one definition per source binding until resolution is complete.
        # Alias spelling is deterministic, but never establishes precedence.
        defaults = {**self.values, **self.providers}
        effective: Dict[BindingId, Any] = {}
        for name in sorted(defaults):
            effective.setdefault(self.binding_origins[name], defaults[name])
        descendants = {}

        def reachable(source):
            if source not in descendants:
                result = set()
                for child in self._include_graph.get(source, ()):
                    result.add(child)
                    result.update(reachable(child))
                descendants[source] = result
            return descendants[source]

        grouped = {}
        # A diamond can expose one declaration through several paths. Deduplicate
        # by source location, not expression equality; choose a stable alias view
        # of that declaration so include order cannot change the serialized graph.
        for candidate in sorted(self.override_candidates, key=lambda c: c.path):
            declaration = (candidate.author, candidate.position.line, candidate.position.column)
            grouped.setdefault(candidate.target, {}).setdefault(declaration, candidate)
        for target, declarations in grouped.items():
            candidates = list(declarations.values())
            by_author = {}
            for candidate in candidates:
                by_author.setdefault(candidate.author, []).append(candidate)
            ambiguous = False
            for authored in by_author.values():
                if len(authored) > 1:
                    self._override_conflict(target, authored)
                    ambiguous = True
            if ambiguous:
                continue
            winners = [
                c
                for c in candidates
                if not any(
                    c.author in reachable(other.author)
                    for other in candidates
                    if other.author != c.author
                )
            ]
            if len(winners) > 1:
                self._override_conflict(target, winners)
                continue
            winner = winners[0]
            effective[target] = winner.value
            self.authored_override_paths[target] = winner.path

        resolution_issues = [
            issue
            for issue in self.validation_issues
            if issue.code in {"override_conflict", "unknown_binding"}
        ]
        if resolution_issues:
            raise ValidationError(self.validation_issues)
        runtime = resolve_runtime_layers(
            self.binding_origins, self.declared_externals, self.runtime_layers
        )
        effective.update({origin: LiteralValue(a.value) for origin, a in runtime.items()})
        self.runtime_inputs = {a.path: a.value for a in runtime.values()}
        self.runtime_sources = {a.path: a.source for a in runtime.values()}
        self.resolved_externals = {
            name: runtime[origin].value
            for name, origin in self.binding_origins.items()
            if origin in runtime
        }

        # Project once into the existing path-keyed IR. Cached unresolved modules
        # are untouched; downstream consumers need no separate override machinery.
        self.providers = {}
        self.values = {}
        replaced = grouped.keys() | runtime.keys()
        for name, origin in self.binding_origins.items():
            if origin not in effective:
                continue  # An external that has not been supplied.
            value = effective[origin]
            if origin not in replaced:
                # Untouched defaults retain their lexical alias spelling in the
                # flattened IR, preserving existing graph digests. These are views
                # of the same source recipe, not independently resolved definitions.
                value = defaults[name]
            if isinstance(value, ProviderSpec):
                self.providers[name] = replace(
                    value, name=name, canonical_key=f"{origin[0]}:{origin[1]}"
                )
            else:
                self.values[name] = value
        recipes = {**self.values, **self.providers}
        _, issues = topological_sort(
            {name: get_dependencies(value) for name, value in recipes.items()},
            declared_externals=self.declared_externals,
        )
        cycles = [issue for issue in issues if issue.code == "dependency_cycle"]
        if cycles:
            raise ValidationError(cycles)

    def _override_conflict(self, target, candidates):
        details = "; ".join(f"{c.position} ({c.path})" for c in candidates)
        self.add_validation_issue(
            "override_conflict",
            f"Conflicting overrides for canonical target '{target[0]}:{target[1]}': {details}",
            candidates[0].path,
            candidates[0].position,
        )

    def process_override(self, override_stmt: OverrideStmt):
        """Process override statements to modify existing bindings."""
        # Convert path to string (e.g., base/learning_rate)
        path_str = "/".join(override_stmt.path.parts)

        logger.debug(f"Processing override: {path_str}")

        # Check if trying to override an external in a non-profile file
        if path_str in self.declared_externals and not self.is_profile:
            raise ExecutionError(
                f"Cannot override external '{path_str}' in normal configuration. "
                f"Externals can only be overridden in profile files or via runtime parameters."
            )

        # Compile the override value
        compiled_value = self.compile_expr(override_stmt.value)

        if not self.is_profile:
            self._validate_lexical_references(compiled_value, path_str, override_stmt.position)
            if path_str in self.binding_origins:
                self.override_candidates.append(
                    OverrideCandidate(
                        target=self.binding_origins[path_str],
                        author=self.source_id,
                        path=path_str,
                        value=compiled_value,
                        position=override_stmt.position,
                    )
                )
            else:
                self.add_validation_issue(
                    "override_not_found",
                    f"Override target '{path_str}' not found. "
                    "Cannot override non-existent binding.",
                    path_str,
                    override_stmt.position,
                )
            return

        # Inherited profile values may be replaced; only repeated declarations
        # authored in this file are duplicates, even when their expressions match.
        if path_str in self._profile_override_paths:
            self.add_validation_issue(
                "override_conflict",
                f"Conflicting overrides for '{path_str}'",
                path_str,
                override_stmt.position,
            )
        self._profile_override_paths.add(path_str)

        # Profile targets are checked against the selected configuration later.
        self.values.pop(path_str, None)
        self.providers.pop(path_str, None)
        if isinstance(compiled_value, ProviderSpec):
            compiled_value.name = path_str
            self.providers[path_str] = compiled_value
        else:
            self.values[path_str] = compiled_value

    def process_external(self, external_stmt: ExternalStmt):
        """Record an external declaration; inputs resolve only at the selected root."""
        # Convert path to string (e.g., config/batch_size)
        path_str = "/".join(external_stmt.path.parts)

        logger.debug(f"Processing external declaration: {path_str}")

        # Add to declared externals with position tracking
        self.declared_externals.add(path_str)
        self.external_positions[path_str] = external_stmt.position

    def _validate_profile(self):
        """Validate that profile files only contain imports and overrides."""
        # Profile files should only contain:
        # 1. Import statements (allowed - though unusual)
        # 2. Override statements (allowed and expected)
        # 3. No regular bindings or providers (not allowed)
        # 4. No external declarations needed (externals declared in main config)

        if self.providers:
            provider_names = list(self.providers.keys())
            raise ExecutionError(
                f"Profile files cannot define providers. Found: {', '.join(provider_names)}. "
                f"Use 'override' statements instead."
            )

        logger.debug(f"Profile validation passed: {len(self.values)} overrides provided")

    def compile_expr(self, expr: Expr) -> Any:
        """Compile an expression AST node into executable form."""

        if isinstance(expr, LiteralExpr):
            return LiteralValue(expr.value)

        elif isinstance(expr, ProviderRefExpr):
            path_str = "/".join(expr.path.parts)  # Convert PathExpr to string
            return ProviderRef(path_str)

        elif isinstance(expr, ProviderMethodRefExpr):
            path_str = "/".join(expr.path.parts)  # Convert PathExpr to string
            return ProviderMethodRef(path_str, tuple(expr.attrs))

        elif isinstance(expr, CallExpr):
            if isinstance(expr.callable, ProviderRefExpr):
                # Provider call: @name(kwargs)
                provider_name = "/".join(expr.callable.path.parts)
                compiled_kwargs = {k: self.compile_expr(v) for k, v in expr.kwargs.items()}
                return FreshProviderCall(provider_name, compiled_kwargs)

            elif isinstance(expr.callable, DottedNameExpr):
                # Regular function/class call: pkg.Class(kwargs)
                parts = expr.callable.parts

                # Try to resolve the callable by following the dot path
                # First try as a direct import
                full_name = ".".join(parts)
                if full_name in self.import_table:
                    callable_obj = self.import_table[full_name]
                else:
                    # Try to resolve as module.attribute.attribute...
                    if len(parts) >= 2:
                        module_name = parts[0]
                        if module_name in self.import_table:
                            callable_obj = self.import_table[module_name]
                            # Follow the attribute chain
                            try:
                                for attr_name in parts[1:]:
                                    callable_obj = getattr(callable_obj, attr_name)
                            except AttributeError as e:
                                raise ExecutionError(f"Unknown attribute: {e}") from e
                        else:
                            raise ExecutionError(f"Unknown import: {module_name}")
                    else:
                        # Check if it's a Python builtin
                        if hasattr(builtins, full_name):
                            callable_obj = getattr(builtins, full_name)
                        else:
                            raise ExecutionError(f"Unknown import: {full_name}")
                compiled_kwargs = {k: self.compile_expr(v) for k, v in expr.kwargs.items()}

                # Handle special builtin constructors that need positional args
                if (
                    hasattr(callable_obj, "__name__")
                    and callable_obj.__name__ == "defaultdict"
                    and "default_factory" in compiled_kwargs
                ):
                    # defaultdict expects factory as first positional arg
                    factory = compiled_kwargs.pop("default_factory")
                    return ProviderSpec(
                        callable_obj=callable_obj,
                        args=[factory],
                        kwargs=compiled_kwargs,
                        missing_kwargs=[],
                        position=expr.position,
                    )

                # --- STRICT SIGNATURE VALIDATION ---
                missing_kwargs = []

                try:
                    sig = inspect.signature(callable_obj)

                    # 1. Check for UNKNOWN kwargs (typos)
                    has_var_keyword = any(
                        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                    )

                    if not has_var_keyword:
                        valid_params = set(sig.parameters.keys())
                        unknown = [k for k in compiled_kwargs.keys() if k not in valid_params]
                        if unknown:
                            self.add_validation_issue(
                                code="unknown_kwarg",
                                message=f"Unknown kwargs for '{full_name}': {', '.join(unknown)}. "
                                f"Valid parameters are: {list(valid_params)}",
                                binding=full_name,
                                position=expr.position,
                            )

                    # 2. Check for MISSING required args
                    # Note: We only collect missing_kwargs here, not report as validation error.
                    # This is because pfig supports partial providers that get kwargs overlaid
                    # via fresh calls like @provider(missing_arg=value). The actual check
                    # happens at materialization time when we know the final kwargs.
                    for param_name, param in sig.parameters.items():
                        if param_name in ("self", "cls"):
                            continue
                        if param.kind in (
                            inspect.Parameter.VAR_POSITIONAL,
                            inspect.Parameter.VAR_KEYWORD,
                        ):
                            continue
                        if (
                            param.default is inspect.Parameter.empty
                            and param_name not in compiled_kwargs
                        ):
                            missing_kwargs.append(param_name)

                except (ValueError, TypeError) as e:
                    # 3. Handle non-inspectable objects (C-extensions, builtins like dict)
                    # Spec §9: Warn unless --strict
                    if self.strict:
                        raise ExecutionError(
                            f"Could not inspect signature for {full_name}: {e}. "
                            "Strict mode enabled."
                        ) from e
                    else:
                        # In normal mode, log a warning and assume no missing args
                        logger.debug(
                            f"Could not inspect signature for {full_name}: {e}. Skipping check."
                        )
                        missing_kwargs = []

                return ProviderSpec(
                    callable_obj=callable_obj,
                    args=[],  # pfig only uses keyword args
                    kwargs=compiled_kwargs,
                    missing_kwargs=missing_kwargs,
                    position=expr.position,
                )

        elif isinstance(expr, ListExpr):
            return [self.compile_expr(elem) for elem in expr.elements]

        elif isinstance(expr, TupleExpr):
            return tuple(self.compile_expr(elem) for elem in expr.elements)

        elif isinstance(expr, DictExpr):
            result = {}
            for k, v in expr.pairs:
                compiled_key = self.compile_expr(k)
                # If key is a LiteralValue, use its value
                if isinstance(compiled_key, LiteralValue):
                    key = compiled_key.value
                else:
                    key = compiled_key
                result[key] = self.compile_expr(v)
            return result

        elif isinstance(expr, DottedNameExpr):
            # Handle bare dotted names as variable references
            parts = expr.parts
            full_name = ".".join(parts)

            # First try as direct import
            if full_name in self.import_table:
                return self.import_table[full_name]

            # Try as module.attribute chain
            if len(parts) >= 2:
                module_name = parts[0]
                if module_name in self.import_table:
                    obj = self.import_table[module_name]
                    try:
                        for attr_name in parts[1:]:
                            obj = getattr(obj, attr_name)
                        return obj
                    except AttributeError as e:
                        raise ExecutionError(f"Unknown attribute: {e}") from e

            # Try as single name (could be builtin or binding reference)
            if len(parts) == 1:
                name = parts[0]
                # Check for Python builtins
                if hasattr(builtins, name):
                    return getattr(builtins, name)
                # Could be a reference to another binding (handled at materialization time)
                return ProviderRef(name)

            raise ExecutionError(f"Unknown reference: {full_name}")

        elif isinstance(expr, BuiltinCallExpr):
            # Handle builtin function calls: len, min, max, range
            builtin_funcs = {"len": len, "min": min, "max": max, "range": range}

            if expr.function not in builtin_funcs:
                raise ExecutionError(f"Unknown builtin function: {expr.function}")

            func = builtin_funcs[expr.function]
            compiled_args = [self.compile_expr(arg) for arg in expr.args]

            return ProviderSpec(
                callable_obj=func,
                args=compiled_args,
                kwargs={},
                missing_kwargs=[],
                position=expr.position,
            )

        elif isinstance(expr, BinaryOpExpr):
            left = self.compile_expr(expr.left)
            right = self.compile_expr(expr.right)

            # If both operands are literals, compute the result at compile time
            if isinstance(left, LiteralValue) and isinstance(right, LiteralValue):
                try:
                    if expr.operator == "+":
                        result = left.value + right.value
                    elif expr.operator == "-":
                        result = left.value - right.value
                    elif expr.operator == "*":
                        result = left.value * right.value
                    elif expr.operator == "/":
                        result = left.value / right.value
                    elif expr.operator == "//":
                        result = left.value // right.value
                    elif expr.operator == "**":
                        result = left.value**right.value
                    else:
                        raise ExecutionError(f"Unsupported binary operator: {expr.operator}")
                    return LiteralValue(result)
                except Exception as e:
                    raise ExecutionError(
                        f"Arithmetic error in {left.value} {expr.operator} {right.value}: {e}"
                    ) from e
            else:
                # Defer evaluation to runtime
                return BinaryOpExpr(expr.position, left, expr.operator, right)

        elif isinstance(expr, UnaryOpExpr):
            operand = self.compile_expr(expr.operand)
            # Handle unary operations during compilation
            if expr.operator == "-":
                # If the operand is a literal, we can compute the result now
                if isinstance(operand, LiteralValue):
                    return LiteralValue(-operand.value)
                else:
                    # Otherwise, return a UnaryOpExpr for later evaluation
                    return UnaryOpExpr(expr.position, expr.operator, operand)
            else:
                raise ExecutionError(f"Unsupported unary operator: {expr.operator}")

        else:
            raise ExecutionError(f"Unsupported expression type: {type(expr)}")


# ===== Entrypoint Resolution & Planning =====


def _cli_entrypoint_method_ref(
    cli_entrypoint: str,
    all_bindings: Dict[str, Any],
) -> ProviderMethodRef | None:
    """Parse ``provider.attr[.attr...]`` against known bindings.

    Prefers the longest binding-name prefix so namespaced providers like
    ``dataset/dataset_manager.upload`` resolve correctly. The remaining
    segments are a normal attribute chain.
    """
    if "." not in cli_entrypoint:
        return None
    parts = cli_entrypoint.split(".")
    for index in range(len(parts) - 1, 0, -1):
        name = ".".join(parts[:index])
        if name in all_bindings:
            attrs = tuple(parts[index:])
            if not attrs:
                return None
            return ProviderMethodRef(name, attrs)
    return None


def resolve_entrypoint(compiled_data: Dict[str, Any], cli_entrypoint: str | None = None) -> str:
    """
    Resolve entrypoint: CLI takes precedence over __entrypoint__ binding.

    CLI overrides may be a binding name (``upload``) or an attribute path
    (``dataset_manager.upload`` / ``dataset/dataset_manager.upload`` /
    ``outer.data.copy``). Attribute paths are injected as a synthetic value
    binding so reachability and materialization reuse ``ProviderMethodRef``.

    Args:
        compiled_data: Compiled providers and values
        cli_entrypoint: CLI-provided entrypoint override

    Returns:
        Name of the entrypoint binding

    Raises:
        ExecutionError: If no entrypoint is available or if entrypoint doesn't exist
    """
    providers = compiled_data.setdefault("providers", {})
    values = compiled_data.setdefault("values", {})
    all_bindings = {
        **dict.fromkeys(compiled_data.get("declared_externals", set())),
        **providers,
        **values,
    }

    if cli_entrypoint:
        logger.debug(f"Using CLI entrypoint override: {cli_entrypoint}")
        if cli_entrypoint in all_bindings:
            logger.debug(f"CLI entrypoint '{cli_entrypoint}' found and will be used")
            return cli_entrypoint

        method_ref = _cli_entrypoint_method_ref(cli_entrypoint, all_bindings)
        if method_ref is not None:
            values[cli_entrypoint] = method_ref
            logger.debug(
                "CLI entrypoint '%s' resolved as @%s.%s",
                cli_entrypoint,
                method_ref.name,
                ".".join(method_ref.attrs),
            )
            return cli_entrypoint

        logger.error(
            f"CLI entrypoint '{cli_entrypoint}' not found in available bindings: "
            f"{list(all_bindings.keys())}"
        )
        raise ExecutionError(
            f"CLI entrypoint '{cli_entrypoint}' not found in bindings. "
            f"Use a binding name or provider.attr path "
            f"(e.g. dataset_manager.upload)."
        )

    if "__entrypoint__" in providers:
        logger.debug("Using __entrypoint__ provider")
        return "__entrypoint__"
    if "__entrypoint__" in values:
        logger.debug("Using __entrypoint__ value")
        return "__entrypoint__"

    logger.error("No entrypoint found in configuration")
    raise ExecutionError(
        "No entrypoint provided. Define '__entrypoint__ = callable' in your config "
        "or specify an entrypoint when calling the execution function."
    )


def find_reachable_bindings(compiled_data: Dict[str, Any], entrypoint_name: str) -> Set[str]:
    """
    Find effective evaluation dependencies, including unsupplied external names.

    Fresh recipe names are not shared-instance dependencies: traversal follows
    merged arguments instead. Declaration and recipe metadata remain in compiled_data.

    Args:
        compiled_data: Compiled providers and values
        entrypoint_name: Name of the entrypoint binding

    Returns:
        Set of binding names that are reachable from the entrypoint
    """
    logger.debug(f"Finding bindings reachable from entrypoint: {entrypoint_name}")

    providers = compiled_data.get("providers", {})
    values = compiled_data.get("values", {})
    all_bindings = {**providers, **values}

    if entrypoint_name not in all_bindings and entrypoint_name not in compiled_data.get(
        "declared_externals", set()
    ):
        raise ExecutionError(f"Entrypoint '{entrypoint_name}' not found in bindings")

    # Depth-first traversal from entrypoint to find all reachable bindings
    visited = set()
    to_visit = [entrypoint_name]

    logger.debug(f"Starting dependency traversal from: {entrypoint_name}")
    while to_visit:
        current = to_visit.pop()
        if current in visited:
            continue

        visited.add(current)
        logger.debug(f"Visiting binding: {current}")

        # Add dependencies to visit list
        if current in all_bindings:
            for dep in get_dependencies(all_bindings[current], compiled_data):
                if dep not in visited:
                    logger.debug(f"Adding dependency to visit: {dep}")
                    to_visit.append(dep)

    binding_names = set(all_bindings.keys())
    reachable_bindings = visited & binding_names
    logger.info(
        f"Reachability analysis complete: {len(reachable_bindings)}/{len(all_bindings)} bindings reachable from entrypoint '{entrypoint_name}'"
    )
    logger.debug(f"Reachable bindings: {sorted(reachable_bindings)}")

    return visited


# ===== Validation (without materialization) =====


def validate_compiled(
    compiled: Dict[str, Any],
    reachable_names: Set[str],
    entrypoint_name: str,
) -> None:
    """
    Validate compiled config without materialization.

    Checks that the entrypoint references a callable.

    Note: missing-kwargs validation for providers is handled separately by
    :func:`check_missing_kwargs_usage`, which is overlay-aware (partial
    providers that are only used via ``@name(...)`` fresh calls are allowed
    to have missing kwargs as long as the overlay supplies them).

    Args:
        compiled: Compiled providers, values, and import_table
        reachable_names: Set of binding names reachable from entrypoint
        entrypoint_name: Name of the entrypoint binding

    Raises:
        ExecutionError: If validation fails
    """
    # Check entrypoint is callable (static check on callable_obj or value)
    if entrypoint_name in compiled["providers"]:
        spec = compiled["providers"][entrypoint_name]
        if not callable(spec.callable_obj):
            raise ExecutionError(
                f"Entrypoint '{entrypoint_name}' references non-callable: {spec.callable_obj}"
            )
    elif entrypoint_name in compiled["values"]:
        val = compiled["values"][entrypoint_name]
        # Check various value types
        if isinstance(val, (ProviderSpec, FreshProviderCall)):
            spec = (
                resolve_fresh_recipe(val, compiled) if isinstance(val, FreshProviderCall) else val
            )
            if not callable(spec.callable_obj):
                raise ExecutionError(
                    f"Entrypoint '{entrypoint_name}' references non-callable: {spec.callable_obj}"
                )
        elif isinstance(val, ProviderMethodRef):
            # Method references (@provider.method) are always callable
            # We can't fully validate the method exists without materializing,
            # but methods are inherently callable
            pass
        elif isinstance(val, ProviderRef):
            # Entrypoint is a reference to another binding - check that binding
            ref_name = val.name
            if ref_name in compiled["providers"]:
                ref_spec = compiled["providers"][ref_name]
                if not callable(ref_spec.callable_obj):
                    raise ExecutionError(
                        f"Entrypoint '{entrypoint_name}' references non-callable provider '{ref_name}'"
                    )
            elif ref_name in compiled["values"]:
                ref_val = compiled["values"][ref_name]
                if isinstance(ref_val, LiteralValue) and not callable(ref_val.value):
                    raise ExecutionError(
                        f"Entrypoint '{entrypoint_name}' resolved to {type(ref_val.value).__name__}, "
                        f"but entrypoints must be callable"
                    )
        elif isinstance(val, LiteralValue):
            if not callable(val.value):
                raise ExecutionError(
                    f"Entrypoint '{entrypoint_name}' resolved to {type(val.value).__name__}, "
                    f"but entrypoints must be callable"
                )
        elif not callable(val):
            raise ExecutionError(
                f"Entrypoint '{entrypoint_name}' resolved to {type(val).__name__}, "
                f"but entrypoints must be callable"
            )

    logger.debug(f"Validation passed for entrypoint '{entrypoint_name}'")


# ===== Materializer =====


class Materializer:
    """
    Materializes compiled provider specifications into live Python objects.
    Handles shared vs fresh semantics and dependency resolution.
    """

    def __init__(self):
        self.shared_cache: Dict[Union[str, BindingId], Any] = {}  # Canonical provider cache
        self.values_cache: Dict[Union[str, BindingId], Any] = {}  # Canonical value cache
        self.compiled_data: Dict[str, Any] = {}  # Compiled providers/values
        self.dispatcher = Dispatcher(self)

    def materialize_reachable(
        self,
        compiled_data: Dict[str, Any],
        reachable_names: Set[str],
        validate_only: bool = False,
        target_name: str | None = None,
    ) -> Tuple[Dict[str, Any], List[ValidationIssue]]:
        """
        Materialize only the bindings reachable from an entrypoint.

        Always collects validation issues instead of raising - caller decides
        whether to raise based on the returned issues.

        Args:
            compiled_data: Compiled providers and values
            reachable_names: Set of binding names that are reachable and should be materialized
            validate_only: If True, only perform graph validation without materializing
            target_name: Optional root binding to materialize even if it is a provider.
                Other providers remain lazy so fresh-call templates are not constructed.

        Returns:
            Tuple of (materialized_dict, validation_issues). If issues is non-empty,
            materialization may be incomplete.
        """
        self.compiled_data = compiled_data
        providers = compiled_data["providers"]
        values = compiled_data["values"]

        # Filter to only reachable bindings
        reachable_providers = {
            name: spec for name, spec in providers.items() if name in reachable_names
        }
        reachable_values = {name: spec for name, spec in values.items() if name in reachable_names}
        reachable_bindings = {**reachable_providers, **reachable_values}

        # Effective edges exclude fresh recipe names and replaced arguments.
        dep_graph = {
            name: get_dependencies(reachable_bindings[name], compiled_data)
            for name in reachable_names
            if name in reachable_bindings
        }

        # Build positions map for error messages
        positions: Dict[str, Position] = {}
        for name, spec in compiled_data["providers"].items():
            if spec.position:
                positions[name] = spec.position

        # Collect all available bindings for "did you mean" suggestions
        all_bindings = list(compiled_data["providers"].keys()) + list(
            compiled_data["values"].keys()
        )

        # Get declared externals to avoid duplicate errors
        # (missing external is more specific than unknown binding)
        declared_externals = compiled_data.get("declared_externals", set())

        # Topologically sort dependencies (always collects issues)
        topo_order, graph_issues = topological_sort(
            dep_graph, positions, all_bindings, declared_externals
        )

        # If we have graph issues, return early with issues
        # (can't safely materialize with unknown bindings or cycles)
        if graph_issues:
            return {}, graph_issues

        # Incomplete validation never authorizes construction of unresolved inputs.
        if validate_only:
            return {}, []
        input_issues = missing_external_issues(compiled_data, reachable_names)
        if input_issues:
            return {}, input_issues

        # Materialize in dependency order (only reachable bindings)
        result = {}
        for name in topo_order:
            if name in reachable_providers:
                # Providers are materialized when referenced via @ (handled in materialize_value)
                # Store the provider spec for potential materialization via @name references
                # Don't materialize directly - only when accessed via ProviderRef or FreshProviderCall
                pass  # Provider specs are accessed via materialize_provider when needed
            elif name in reachable_values:
                result[name] = self.materialize_shared_value(name)
            elif name in compiled_data["providers"]:
                # Included provider - don't materialize directly, will be materialized when referenced
                pass
            elif name in compiled_data["values"]:
                # Included value - materialize it
                result[name] = self.materialize_shared_value(name)
            # Skip builtins - they don't need materialization

        # A direct provider root has no @ reference to trigger construction.
        # Use the shared cache, just as a ProviderRef would, without eagerly
        # constructing dependency providers (which may be overlay-only templates).
        if target_name is not None and target_name in reachable_providers:
            result[target_name] = self.materialize_provider(target_name)

        return result, graph_issues

    def materialize_shared_value(self, name: str) -> Any:
        """Materialize a value binding once across all aliases of its source."""
        # Synthetic bindings and manually assembled IR may have no source origin.
        cache_key = self.compiled_data.get("binding_origins", {}).get(name, name)
        if cache_key not in self.values_cache:
            self.values_cache[cache_key] = self.materialize_value(
                self.compiled_data["values"][name]
            )
        return self.values_cache[cache_key]

    def materialize_provider(self, name: str) -> Any:
        """Materialize a provider (shared instance)."""
        provider_spec = self.compiled_data["providers"][name]

        # Values and providers use the same source identity. Keep the fallback for
        # synthetic bindings and manually assembled IR without binding_origins.
        cache_key = self.compiled_data.get("binding_origins", {}).get(
            name, provider_spec.canonical_key or name
        )

        if cache_key in self.shared_cache:
            return self.shared_cache[cache_key]

        if provider_spec.missing_kwargs:
            raise ExecutionError(
                f"Cannot materialize provider '{name}' - missing required kwargs: {provider_spec.missing_kwargs}",
                name,
            )

        instance = self.materialize_provider_spec(provider_spec)
        self.shared_cache[cache_key] = instance
        return instance

    def materialize_provider_spec(self, spec: ProviderSpec) -> Any:
        """Materialize a ProviderSpec into a live object."""
        if spec.missing_kwargs:
            raise ExecutionError(
                f"Cannot materialize provider '{spec.name}' - missing required kwargs: {spec.missing_kwargs}",
                spec.name,
            )

        try:
            args = [self.materialize_value(arg) for arg in spec.args]
            kwargs = {k: self.materialize_value(v) for k, v in spec.kwargs.items()}
            result = spec.callable_obj(*args, **kwargs)
            return result
        except Exception as e:
            provider_name = f" '{spec.name}'" if spec.name else ""
            raise ExecutionError(f"Failed to construct{provider_name}: {e}", spec.name) from e

    @Dispatcher.register(LiteralValue)
    def materialize_literal(self, literal: LiteralValue) -> Any:
        return literal.value

    @Dispatcher.register(ProviderSpec)
    def materialize_provider_spec_dispatch(self, spec: ProviderSpec) -> Any:
        """Dispatcher method for ProviderSpec - delegates to existing materialize_provider_spec."""
        return self.materialize_provider_spec(spec)

    @Dispatcher.register(type)
    def materialize_type(self, type_obj: type) -> Any:
        """Handle Python type objects directly (like int, str, list, etc.)"""
        return type_obj

    @Dispatcher.register(ProviderRef)
    def materialize_provider_ref(self, ref: ProviderRef) -> Any:
        # Provider references (@name) ONLY look in provider namespace, never builtins
        # The @ syntax explicitly disambiguates from bare names

        # Check if it's a value first, then provider
        if ref.name in self.compiled_data["values"]:
            return self.materialize_shared_value(ref.name)
        elif ref.name in self.compiled_data["providers"]:
            return self.materialize_provider(ref.name)
        else:
            raise ExecutionError(f"Unknown binding: @{ref.name}")

    @Dispatcher.register(ProviderMethodRef)
    def materialize_provider_method_ref(self, ref: ProviderMethodRef) -> Any:
        # First materialize the object instance (could be value, provider, or import)
        instance = None

        if ref.name in self.compiled_data["values"]:
            instance = self.materialize_shared_value(ref.name)
        elif ref.name in self.compiled_data["providers"]:
            instance = self.materialize_provider(ref.name)
        elif ref.name in self.compiled_data["import_table"]:
            # Handle imported modules/objects
            instance = self.compiled_data["import_table"][ref.name]
        else:
            raise ExecutionError(f"Unknown provider for method reference: {ref.name}")

        # Walk @provider.attr[.attr...] — values and callables are both allowed.
        # Look up each segment once so properties and __getattr__ run a single time.
        obj = instance
        walked: list[str] = []
        for attr in ref.attrs:
            try:
                obj = getattr(obj, attr)
            except AttributeError as exc:
                path = ".".join([ref.name, *walked, attr])
                raise ExecutionError(
                    f"Object '{ref.name}' has no attribute '{attr}' (while resolving @{path})"
                ) from exc
            walked.append(attr)
        return obj

    @Dispatcher.register(FreshProviderCall)
    def materialize_fresh_call(self, call: FreshProviderCall) -> Any:
        return self.materialize_provider_spec(resolve_fresh_recipe(call, self.compiled_data))

    @Dispatcher.register(list)
    @Dispatcher.register(tuple)
    def materialize_sequence(
        self, seq: Union[List[Any], Tuple[Any, ...]]
    ) -> Union[List[Any], Tuple[Any, ...]]:
        return type(seq)(self.materialize_value(item) for item in seq)

    @Dispatcher.register(dict)
    def materialize_dict(self, d: dict) -> dict:
        result = {}
        for k, v in d.items():
            key = (
                self.materialize_value(k)
                if isinstance(k, (LiteralValue, ProviderRef, ProviderMethodRef, FreshProviderCall))
                else k
            )
            value = self.materialize_value(v)
            result[key] = value
        return result

    @Dispatcher.register(UnaryOpExpr)
    def materialize_unary_op(self, expr: UnaryOpExpr) -> Any:
        """Handle unary operations like -42"""
        operand = self.materialize_value(expr.operand)
        if expr.operator == "-":
            return -operand
        else:
            raise ExecutionError(f"Unsupported unary operator: {expr.operator}")

    @Dispatcher.register(BinaryOpExpr)
    def materialize_binary_op(self, expr: BinaryOpExpr) -> Any:
        """Handle binary operations like 2 + 3, a * b, etc."""
        left = self.materialize_value(expr.left)
        right = self.materialize_value(expr.right)

        try:
            if expr.operator == "+":
                return left + right
            elif expr.operator == "-":
                return left - right
            elif expr.operator == "*":
                return left * right
            elif expr.operator == "/":
                return left / right
            elif expr.operator == "//":
                return left // right
            elif expr.operator == "**":
                return left**right
            else:
                raise ExecutionError(f"Unsupported binary operator: {expr.operator}")
        except Exception as e:
            raise ExecutionError(f"Arithmetic error in {left} {expr.operator} {right}: {e}") from e

    def materialize_value(self, value: Any) -> Any:
        """Materialize any value using the dispatcher."""
        if value is None:
            return None

        # Try the dispatcher first
        try:
            return self.dispatcher(type(value), value)
        except ExecutionError:
            # Re-raise ExecutionError for pfig internal types (they should be handled by dispatcher)
            if isinstance(
                value,
                (
                    ProviderRef,
                    ProviderMethodRef,
                    FreshProviderCall,
                    LiteralValue,
                    BinaryOpExpr,
                    UnaryOpExpr,
                    ProviderSpec,
                ),
            ):
                raise

            # Fallback for unknown types - if it's already a Python object, just return it
            # This handles callables, modules, and other Python objects
            if callable(value) or hasattr(value, "__module__"):
                return value
            # Re-raise the original error if it's not a recognizable Python object
            raise


# ===== Public API =====


@dataclass
class ProfilePrefixMatch:
    override_path: tuple
    external_path: tuple
    prefix: tuple


def _path_to_segments(path_str: str) -> tuple:
    return tuple(path_str.split("/")) if path_str else ()


def _segments_to_path(segs: tuple) -> str:
    return "/".join(segs)


def infer_prefix_candidates(
    failed_path: tuple,
    declared_externals: "set[tuple]",
) -> "list[tuple[tuple, tuple]]":
    """Return (prefix, external_path) pairs where external_path ends with failed_path segments."""
    matches = []
    for ext_path in declared_externals:
        if len(ext_path) <= len(failed_path):
            continue
        if ext_path[-len(failed_path) :] == failed_path:
            prefix = ext_path[: -len(failed_path)]
            matches.append((prefix, ext_path))
    return matches


def infer_missing_profile_target_suggestion(
    failed_overrides: "list[str]",
    declared_externals: "set[str]",
    profile_path: "str | None",
) -> str:
    """Generate a grouped suggestion when an unprefixed profile's overrides all fail.

    Returns an empty string when no useful suggestion can be made.
    """
    if not failed_overrides:
        return ""

    failed_segs = [_path_to_segments(p) for p in failed_overrides]
    ext_segs = {_path_to_segments(e) for e in declared_externals}

    prefix_score: "dict[tuple, list[ProfilePrefixMatch]]" = {}
    for failed_path in failed_segs:
        for prefix, ext_path in infer_prefix_candidates(failed_path, ext_segs):
            if prefix not in prefix_score:
                prefix_score[prefix] = []
            prefix_score[prefix].append(
                ProfilePrefixMatch(
                    override_path=failed_path,
                    external_path=ext_path,
                    prefix=prefix,
                )
            )

    if not prefix_score:
        return ""

    total_failed = len(failed_segs)
    best_count = max(len(v) for v in prefix_score.values())
    best_prefixes = sorted(
        [p for p, v in prefix_score.items() if len(v) == best_count],
        key=_segments_to_path,
    )

    profile_label = profile_path or "<profile>"

    if best_count == total_failed and len(best_prefixes) == 1:
        # High confidence: every failed override matches exactly one prefix.
        prefix = best_prefixes[0]
        matches = sorted(prefix_score[prefix], key=lambda m: m.override_path)
        prefix_str = _segments_to_path(prefix)
        overrides_table = "\n".join(
            f"  {_segments_to_path(m.override_path)} -> {_segments_to_path(m.external_path)}"
            for m in matches
        )
        return (
            f"Profile {profile_label} was applied at the root, but these overrides do not\n"
            f"match root externals:\n\n"
            f"{overrides_table}\n\n"
            f"They do match externals under '{prefix_str}/'.\n\n"
            f"Did you mean:\n\n"
            f"  --profile {prefix_str}::{profile_label}"
        )

    if best_count == total_failed and len(best_prefixes) > 1:
        # Ambiguous: multiple prefixes each cover all failed overrides.
        seen = set()
        all_ext_lines = []
        for prefix in best_prefixes:
            for m in sorted(prefix_score[prefix], key=lambda m: m.external_path):
                line = f"  {_segments_to_path(m.external_path)}"
                if line not in seen:
                    seen.add(line)
                    all_ext_lines.append(line)
        ext_list = "\n".join(all_ext_lines)
        candidate_cmds = "\n".join(
            f"  --profile {_segments_to_path(p)}::{profile_label}" for p in best_prefixes
        )
        if len(failed_segs) == 1:
            override_str = _segments_to_path(failed_segs[0])
            return (
                f"Profile {profile_label} was applied at the root, but override "
                f"'{override_str}'\n"
                f"does not match a root external.\n\n"
                f"Matching externals exist under multiple namespaces:\n\n"
                f"{ext_list}\n\n"
                f"Apply the profile explicitly with one of:\n\n"
                f"{candidate_cmds}"
            )
        return (
            f"Profile {profile_label} was applied at the root, but these overrides\n"
            f"do not match root externals.\n\n"
            f"Matching externals exist under multiple namespaces:\n\n"
            f"{ext_list}\n\n"
            f"Apply the profile explicitly with one of:\n\n"
            f"{candidate_cmds}"
        )

    # Medium confidence: best prefix covers some but not all failed overrides.
    prefix = best_prefixes[0]
    matches = sorted(prefix_score[prefix], key=lambda m: m.override_path)
    prefix_str = _segments_to_path(prefix)
    matched_paths = {m.override_path for m in matches}
    unmatched = sorted(p for p in failed_segs if p not in matched_paths)

    matched_lines = "\n".join(
        f"  {_segments_to_path(m.override_path)} -> {_segments_to_path(m.external_path)}"
        for m in matches
    )
    unmatched_lines = "\n".join(f"  {_segments_to_path(p)}" for p in unmatched)

    return (
        f"Profile {profile_label} was applied at the root, but some overrides do not\n"
        f"match root externals.\n\n"
        f"These failed overrides match externals under '{prefix_str}/':\n\n"
        f"{matched_lines}\n\n"
        f"You may have meant:\n\n"
        f"  --profile {prefix_str}::{profile_label}\n\n"
        f"These overrides still do not match declared externals:\n\n"
        f"{unmatched_lines}"
    )


def _profile_override_hint(
    path_str: str,
    declared_externals: set,
    profile_path: "str | None",
) -> str:
    """Build an actionable hint when a profile override doesn't match any declared external."""
    parts = path_str.split("/")

    # Case A: unprefixed override (no slash) — check for suffix matches
    if len(parts) == 1:
        suffix_matches = [
            ext for ext in declared_externals if ext == path_str or ext.endswith("/" + path_str)
        ]
        if len(suffix_matches) == 1:
            prefix_parts = suffix_matches[0][: -(len(path_str) + 1)]  # strip "/path_str"
            hint = f"A matching external exists at:\n  {suffix_matches[0]}"
            if profile_path and prefix_parts:
                hint += f"\n\nDid you mean:\n  --profile {prefix_parts}::{profile_path}"
            return hint
        if len(suffix_matches) > 1:
            match_list = "\n  ".join(suffix_matches)
            return (
                f"Matching externals exist at:\n  {match_list}\n\n"
                "Apply an explicit prefix to select the right namespace, e.g.:\n"
                + (
                    f"  --profile <prefix>::{profile_path}"
                    if profile_path
                    else "  --profile <prefix>::<profile-path>"
                )
            )
        return ""

    # Case B / C: prefixed override (has slash) — check if first segment is a known namespace
    given_prefix = parts[0]
    known_prefixes = {ext.split("/")[0] for ext in declared_externals if "/" in ext}
    if given_prefix not in known_prefixes and known_prefixes:
        known_list = "\n  ".join(sorted(known_prefixes))
        return (
            f"Profile prefix '{given_prefix}' did not match any external namespace.\n\n"
            f"Known external prefixes:\n  {known_list}"
        )

    # Case C: prefix valid but path doesn't match any external
    bare = "/".join(parts[1:])
    return (
        f"Profile override '{bare}' was applied as '{path_str}', "
        f"but that path is not a declared external."
    )


def _load_runtime_layers(
    externals: Dict[str, Any] | None = None,
    profile_paths: Sequence[str | Path | ProfileSpec] | None = None,
) -> list[RuntimeInputLayer]:
    """Load ordered profiles followed by API/CLI inputs, without merging paths."""
    layers = []
    for entry in profile_paths or ():
        spec = entry if isinstance(entry, ProfileSpec) else ProfileSpec(path=Path(entry))
        values, _file_origins = load_profile(path=spec.path)
        if spec.prefix:
            prefix = "/".join(spec.prefix) + "/"
            values = {prefix + path: value for path, value in values.items()}
            source = f"profile-prefixed:{spec.path}"
        else:
            source = f"profile:{spec.path}"
        layers.append(RuntimeInputLayer(values, source))
    layers.append(RuntimeInputLayer(dict(externals or {})))
    return layers


@dataclass
class ExecutionMetadata:
    """Metadata from pfig execution, used for snapshotting and introspection.

    This dataclass captures the full execution context including compiled configuration,
    materialized objects, and the execution result.

    Attributes:
        compiled: The compiled configuration dictionary containing all resolved bindings.
        entrypoint_name: The resolved entrypoint binding name (e.g., "__entrypoint__").
        reachable_names: Set of binding names reachable from the entrypoint.
        materialized: Dictionary mapping binding names to their materialized Python objects.
        externals: Alias-expanded resolved runtime values for lookup. The compiled
            runtime_inputs/runtime_sources maps hold compact snapshot assignments.
        included_files: List of paths to all included .pfig files (resolved absolute paths).
        file_origins: Dictionary mapping file paths to their IncludeResolution (for bundling).
        result: Result of calling the entrypoint. None when returned from validate_pfig().

    Example:
        ```python
        from pfig import parse_pfig_source
        from pfig.execution import validate_pfig

        config = '''
        from collections import OrderedDict
        settings = OrderedDict(batch_size=64)
        __entrypoint__ = @settings.keys
        '''
        ast = parse_pfig_source(config)
        metadata = validate_pfig(ast)
        metadata.entrypoint_name  # '__entrypoint__'
        'settings' in metadata.reachable_names  # True
        ```
    """

    compiled: Dict[str, Any]
    entrypoint_name: str
    reachable_names: Set[str]
    materialized: Dict[str, Any]
    externals: Dict[str, Any]
    included_files: List[str]
    file_origins: Dict[str, "IncludeResolution"]
    result: Any = None


def validate_pfig(
    ast_file: PFigFile,
    entrypoint: str | None = None,
    filename: str | None = None,
    externals: Dict[str, Any] | None = None,
    profile_paths: Sequence[str | Path | ProfileSpec] | None = None,
    strict: bool = False,
    allow_missing_externals: bool = False,
) -> "ExecutionMetadata":
    """
    Validate a pfig configuration without materialization.

    Performs structural validation including:
    - Syntax validation (via parsing, done before this function)
    - Import resolution
    - Signature validation (missing/unknown kwargs)
    - Dependency resolution (topological sort, cycle detection)
    - Missing-kwargs-usage checks
    - Entrypoint callable check (static)

    Does NOT materialize providers or call the entrypoint. Compilation still
    resolves `import` statements via Python's import system, so module-level
    side effects in imported modules may occur during validation.

    Args:
        ast_file: Parsed pfig AST
        entrypoint: Optional override: binding name or attribute path (``outer.data.copy``). If None, uses the __entrypoint__ binding.
        filename: Optional filename for error reporting
        externals: Optional external values to override
        profile_paths: Optional paths to profile files to load
        strict: If True, treat validation warnings as errors
        allow_missing_externals: If True, permit incomplete validation without required
            inputs. Otherwise requirements follow the selected entrypoint, not all
            declarations. This flag never permits unresolved inputs to be materialized.

    Returns:
        ExecutionMetadata with compiled data, entrypoint info, reachable bindings, etc.
        The ``materialized`` dict is empty and ``result`` is None (no objects are constructed).

    Raises:
        ExecutionError: If validation fails (missing kwargs, non-callable entrypoint, etc.)
    """
    logger.info(f"Validating pfig config (no materialization): {entrypoint or '__entrypoint__'}")

    runtime_layers = _load_runtime_layers(externals, profile_paths)

    # 1. Compilation phase
    compiler = ASTCompiler(
        base_path=filename,
        runtime_layers=runtime_layers,
        strict=strict,
        defer_external_validation=True,
    )
    compiled = compiler.compile_file(ast_file)

    if compiler.validation_issues:
        raise ValidationError(compiler.validation_issues)

    # 2. Resolve entrypoint
    target_name = resolve_entrypoint(compiled, entrypoint)

    # 3. Find reachable bindings
    reachable_names = find_reachable_bindings(compiled, target_name)
    logger.info(
        f"Reachability analysis: {len(reachable_names)} bindings reachable from '{target_name}'"
    )

    if not allow_missing_externals:
        input_issues = missing_external_issues(compiled, reachable_names)
        if input_issues:
            raise ValidationError(input_issues)

    # 4. Check for missing-kwargs usage (providers used without required overlays)
    missing_kwargs_issues = check_missing_kwargs_usage(compiled, reachable_names)
    if missing_kwargs_issues:
        raise ValidationError(missing_kwargs_issues)

    # 5. Graph validation via topological sort (catches unknown bindings, cycles)
    materializer = Materializer()
    _, graph_issues = materializer.materialize_reachable(
        compiled, reachable_names, validate_only=True
    )
    if graph_issues:
        raise ValidationError(graph_issues)

    # 6. Static validation (entrypoint callable check, etc.)
    validate_compiled(compiled, reachable_names, target_name)

    logger.info("Validation passed (no objects materialized)")

    included_files = list(compiler.loaded_files.keys())

    return ExecutionMetadata(
        compiled=compiled,
        entrypoint_name=target_name,
        reachable_names=reachable_names,
        materialized={},
        externals=compiled["resolved_externals"],
        included_files=included_files,
        file_origins=compiler.file_origins,
        result=None,
    )


def compile_pfig(
    ast_file: PFigFile,
    filename: str | None = None,
    externals: Dict[str, Any] | None = None,
    profile_paths: Sequence[str | Path | ProfileSpec] | None = None,
    strict: bool = False,
    allow_missing_externals: bool = False,
) -> Tuple[Dict[str, Any], List[ValidationIssue]]:
    """
    Compile a pfig configuration without requiring an entrypoint.

    Runs the Parse -> Compose -> Validate phases (import resolution, include
    resolution, signature validation, duplicate-binding checks, etc.) but
    skips the entrypoint-dependent phases (reachability analysis,
    missing-kwargs-usage, graph materialization).

    This is the recommended entry point for editor tooling (LSP servers,
    linters) where the file being checked may not define ``__entrypoint__``.

    Args:
        ast_file: Parsed pfig AST
        filename: Optional filename for error reporting and relative include resolution
        externals: Optional external values to override
        profile_paths: Optional paths to profile files to load
        strict: If True, treat validation warnings as errors
        allow_missing_externals: If True, suppress whole-file missing-input diagnostics.
            Unlike execution validation, rootless compilation checks all declarations.

    Returns:
        Tuple of (compiled_data, issues) where compiled_data is the compiler
        output dict and issues is a list of ``ValidationIssue`` objects.
        Does not raise on validation problems — all issues are returned in
        the list.
    """
    logger.info("Compiling pfig config (no entrypoint required)")

    runtime_layers = _load_runtime_layers(externals, profile_paths)

    compiler = ASTCompiler(
        base_path=filename,
        runtime_layers=runtime_layers,
        strict=strict,
        allow_missing_externals=allow_missing_externals,
    )

    issues: List[ValidationIssue] = []

    try:
        compiled = compiler.compile_file(ast_file)
    except Exception as exc:
        inner_issues = getattr(exc, "issues", None)
        if inner_issues is not None:
            issues.extend(inner_issues)
        else:
            pos = getattr(exc, "position", None)
            issues.append(
                ValidationIssue(
                    code="compile_error",
                    message=str(exc),
                    position=pos,
                )
            )
        return {}, issues

    issues.extend(compiler.validation_issues)

    return compiled, issues


def execute_pfig_with_metadata(
    ast_file: PFigFile,
    entrypoint: str | None = None,
    filename: str | None = None,
    externals: Dict[str, Any] | None = None,
    profile_paths: Sequence[str | Path | ProfileSpec] | None = None,
    strict: bool = False,
    allow_missing_externals: bool = False,
    compile_only: bool = False,
) -> ExecutionMetadata:
    """
    Execute pfig configuration and return full execution metadata.

    This is the core execution function that orchestrates the complete pipeline:

    1. Resolves entrypoint (CLI takes precedence over __entrypoint__)
    2. Plans by finding bindings reachable from entrypoint
    3. Materializes only the minimal required set of objects
    4. Validates entrypoint is callable
    5. Calls the entrypoint

    For validation without materialization or execution, use :func:`validate_pfig`.

    Returns ExecutionMetadata with compiled data, materialized objects, and result.
    Used by CLI and by execute_pfig() for simple execution.

    Args:
        ast_file: Parsed pfig AST
        entrypoint: Optional override: binding name or attribute path (``outer.data.copy``). If None, uses the __entrypoint__ binding.
        filename: Optional filename for error reporting
        externals: Optional external values to override
        profile_paths: Optional paths to profile files to load
        strict: If True, treat validation warnings as errors
        allow_missing_externals: Packaging compatibility flag. File discovery preserves
            unresolved inputs; execution always checks required inputs.
        compile_only: If True, only compile (skip planning/materialization) for file discovery

    Returns:
        ExecutionMetadata with compiled data, materialized objects, and execution result

    Raises:
        ExecutionError: If entrypoint is missing, not callable, or execution fails

    Example:
        ```python
        from pfig import parse_pfig_source
        from pfig.execution import execute_pfig_with_metadata

        config = '''
        from collections import OrderedDict
        settings = OrderedDict(batch_size=64, lr=0.001)
        __entrypoint__ = @settings.keys
        '''
        ast = parse_pfig_source(config)
        metadata = execute_pfig_with_metadata(ast)
        metadata.result  # odict_keys(['batch_size', 'lr'])
        ```
    """
    logger.info(f"Starting pfig execution with entrypoint: {entrypoint or '__entrypoint__'}")

    # Use shared pipeline with entrypoint resolution
    def target_resolver(compiled):
        return resolve_entrypoint(compiled, entrypoint)

    runtime_layers = _load_runtime_layers(externals, profile_paths)

    result = execute_pipeline(
        ast_file,
        target_resolver,
        filename=filename,
        runtime_layers=runtime_layers,
        strict=strict,
        allow_missing_externals=allow_missing_externals,
        compile_only=compile_only,
    )

    # For compile_only mode (bundling), return early with just file_origins
    if compile_only:
        return ExecutionMetadata(
            compiled=result.compiled,
            entrypoint_name="",
            reachable_names=set(),
            materialized={},
            externals={
                path: value for layer in runtime_layers for path, value in layer.values.items()
            },
            included_files=result.included_files,
            file_origins=result.file_origins,
            result=None,
        )

    logger.info(f"Materialized {len(result.materialized)} objects")

    # Validation phase: ensure entrypoint is callable
    if result.target_name not in result.materialized:
        raise ExecutionError(f"Entrypoint '{result.target_name}' was not materialized")

    entrypoint_callable = result.materialized[result.target_name]
    if not callable(entrypoint_callable):
        raise ExecutionError(
            f"Entrypoint '{result.target_name}' resolved to {type(entrypoint_callable).__name__}, "
            f"but entrypoints must be callable"
        )

    # Execution phase: call the entrypoint
    try:
        logger.info(f"Calling entrypoint: {result.target_name}")
        execution_result = entrypoint_callable()
        logger.info(f"Entrypoint execution completed successfully")
    except Exception as e:
        raise ExecutionError(
            f"Error during execution of entrypoint '{result.target_name}': {e}"
        ) from e

    return ExecutionMetadata(
        compiled=result.compiled,
        entrypoint_name=result.target_name,
        reachable_names=result.reachable_names,
        materialized=result.materialized,
        externals=result.compiled["resolved_externals"],
        included_files=result.included_files,
        file_origins=result.file_origins,
        result=execution_result,
    )


def execute_pfig(
    ast_file: PFigFile,
    entrypoint: str | None = None,
    filename: str | None = None,
    externals: Dict[str, Any] | None = None,
    profile_paths: Sequence[str | Path | ProfileSpec] | None = None,
    strict: bool = False,
) -> Any:
    """
    Execute a parsed pfig AST and return the result.

    This is a convenience wrapper around execute_pfig_with_metadata() that
    returns just the execution result. Use execute_pfig_with_metadata() if you
    need access to execution metadata (e.g., for validation or snapshotting).

    Args:
        ast_file: Parsed pfig AST
        entrypoint: Optional override: binding name or attribute path (``outer.data.copy``). If None, uses the __entrypoint__ binding.
        filename: Optional filename for error reporting
        externals: Optional external values to override
        profile_paths: Optional paths to profile files to load
        strict: If True, treat validation warnings as errors

    Returns:
        Result of calling the entrypoint function

    Raises:
        ExecutionError: If entrypoint is missing, not callable, or execution fails

    Example:
        ```python
        from pfig import parse_pfig_source, execute_pfig

        config = '''
        from collections import OrderedDict
        data = OrderedDict(a=1, b=2)
        __entrypoint__ = @data.keys
        '''
        ast = parse_pfig_source(config)
        result = execute_pfig(ast)  # Returns odict_keys(['a', 'b'])
        ```
    """
    metadata = execute_pfig_with_metadata(
        ast_file,
        entrypoint=entrypoint,
        filename=filename,
        externals=externals,
        profile_paths=profile_paths,
        strict=strict,
    )

    return metadata.result


def _ast_defines_snapshot(ast_file: PFigFile) -> bool:
    return any(binding.name == "__snapshot__" for binding in ast_file.bindings()) or any(
        external.path.parts == ["__snapshot__"] for external in ast_file.externals()
    )


def _materialize_snapshot_dir(
    compiled: Dict[str, Any],
    materializer: Materializer,
) -> str | Path:
    """Evaluate ``__snapshot__`` and its dependencies on ``materializer``.

    ``materializer`` is the same instance later used for the entrypoint, so
    providers shared by both phases stay in its cache.
    """
    names = find_reachable_bindings(compiled, "__snapshot__")
    issues = missing_external_issues(compiled, names)
    issues.extend(check_missing_kwargs_usage(compiled, names))
    if issues:
        raise ValidationError(issues)
    materialized, issues = materializer.materialize_reachable(
        compiled, names, target_name="__snapshot__"
    )
    if issues:
        raise ValidationError(issues)
    resolved = materialized.get("__snapshot__")
    if not isinstance(resolved, str) or not resolved:
        raise ExecutionError("__snapshot__ must be a path string")
    return resolved if _fs.is_uri(resolved) else Path(resolved)


def _prepare_snapshot_materializer(
    validation: ExecutionMetadata,
    *,
    enabled: bool,
    snapshot_dir: str | Path | None,
) -> Materializer:
    """Validate runner-owned inputs before construction; retain the shared cache."""
    materializer = Materializer()
    compiled = validation.compiled
    if (
        enabled
        and snapshot_dir is None
        and (
            "__snapshot__" in compiled["values"]
            or "__snapshot__" in compiled["providers"]
            or "__snapshot__" in compiled["declared_externals"]
        )
    ):
        resolved = _materialize_snapshot_dir(compiled, materializer)
        validation.materialized["__snapshot__"] = str(resolved)
    return materializer


def _execute_validated(validation: ExecutionMetadata, materializer: Materializer) -> Any:
    """Execute an already validated graph using the snapshot phase's shared cache."""
    materialized, issues = materializer.materialize_reachable(
        validation.compiled,
        validation.reachable_names,
        target_name=validation.entrypoint_name,
    )
    if issues:
        raise ValidationError(issues)
    validation.materialized.update(materialized)
    target = validation.entrypoint_name
    if target not in materialized:
        raise ExecutionError(f"Entrypoint '{target}' was not materialized")
    entrypoint_callable = materialized[target]
    if not callable(entrypoint_callable):
        raise ExecutionError(
            f"Entrypoint '{target}' resolved to {type(entrypoint_callable).__name__}, "
            f"but entrypoints must be callable"
        )
    try:
        logger.info(f"Calling entrypoint: {target}")
        validation.result = entrypoint_callable()
        return validation.result
    except Exception as exc:
        raise ExecutionError(f"Error during execution of entrypoint '{target}': {exc}") from exc


def run(
    path: str | Path,
    *,
    entrypoint: str | None = None,
    externals: Dict[str, Any] | None = None,
    profile_paths: Sequence[str | Path | ProfileSpec] | None = None,
    snapshot_dir: str | Path | None = None,
    no_snapshot: bool = False,
    strict: bool = False,
) -> Any:
    """
    Execute a pfig configuration file from Python.

    Handles parsing, planning, materialization, and execution in a single call.
    Unlike CLI ``pfig run``, this API does not create snapshots by default: set
    ``snapshot_dir`` or define a root ``__snapshot__`` binding to enable them.
    Computed ``__snapshot__`` expressions are supported by this API.

    Args:
        path: Path to the .pfig configuration file
        entrypoint: Optional override: binding name or attribute path (``outer.data.copy``). If None, uses the __entrypoint__ binding.
        externals: Optional external values to satisfy external declarations
        profile_paths: Optional paths to profile files to load
        snapshot_dir: Overrides ``__snapshot__``. A timestamped subdirectory is created inside.
        no_snapshot: Skip snapshot creation even when ``__snapshot__`` is set.
        strict: If True, treat validation warnings as errors

    Returns:
        Result of calling the entrypoint function

    Raises:
        ExecutionError: If entrypoint is missing, not callable, or execution fails
        ParseError: If config parsing fails
        FileNotFoundError: If the path does not exist

    Example:
        ```python
        from pfig import run

        # Simple execution
        result = run("config.pfig")

        # With externals
        result = run("config.pfig", externals={"batch_size": 64})

        # With snapshotting from the config directive
        # __snapshot__ = @output_dir + "/snapshots"
        result = run("config.pfig", externals={"output_dir": "./runs"})
        ```
    """
    from .snapshot import create_snapshot

    is_uri = _fs.is_uri(str(path))
    path_for_display = str(path) if is_uri else str(Path(path))
    ast_file = parse_config_file(path=path)

    # Snapshot before execution. ``snapshot_dir`` overrides ``__snapshot__``.
    # When ``__snapshot__`` is materialized, keep that compiled graph and
    # materializer for the entrypoint so shared providers are constructed once.
    snapshot_out: str | Path | None = None
    validation: ExecutionMetadata | None = None
    materializer: Materializer | None = None
    if not no_snapshot:
        if snapshot_dir is not None:
            snapshot_out = snapshot_dir if _fs.is_uri(str(snapshot_dir)) else Path(snapshot_dir)
        elif _ast_defines_snapshot(ast_file):
            validation = validate_pfig(
                ast_file,
                entrypoint=entrypoint,
                filename=path_for_display,
                externals=externals,
                profile_paths=profile_paths,
                strict=strict,
            )
            materializer = Materializer()
            snapshot_out = _materialize_snapshot_dir(validation.compiled, materializer)

    if snapshot_out is not None:
        if validation is None:
            validation = validate_pfig(
                ast_file,
                entrypoint=entrypoint,
                filename=path_for_display,
                externals=externals,
                profile_paths=profile_paths,
                strict=strict,
            )

        pfig_files = [Path(path)]
        pfig_files.extend(Path(p) for p in validation.included_files)

        profile_paths_resolved = None
        profile_file_origins = None
        if profile_paths:
            profile_paths_resolved, profile_file_origins = collect_profile_bundle_info(
                profile_paths
            )

        create_snapshot(
            compiled=validation.compiled,
            entrypoint=validation.entrypoint_name,
            output_dir=snapshot_out,
            pfig_files=pfig_files,
            externals=validation.compiled["runtime_inputs"],
            external_sources=validation.compiled["runtime_sources"],
            reachable_names=validation.reachable_names,
            file_origins=validation.file_origins,
            # Portable config bundles currently require a local entry path.
            entry_path=None if is_uri else Path(path),
            profile_paths=profile_paths_resolved,
            profile_file_origins=profile_file_origins,
        )

    if materializer is not None and validation is not None:
        return _execute_validated(validation, materializer)

    # Execute (materialize + call entrypoint)
    metadata = execute_pfig_with_metadata(
        ast_file,
        entrypoint=entrypoint,
        filename=path_for_display,
        externals=externals,
        profile_paths=profile_paths,
        strict=strict,
    )

    return metadata.result


def get_materialized_bindings(
    ast_file: PFigFile,
    entrypoint: str | None = None,
    filename: str | None = None,
    externals: Dict[str, Any] | None = None,
    profile_paths: Sequence[str | Path | ProfileSpec] | None = None,
    strict: bool = False,
) -> Dict[str, Any]:
    """
    Get all materialized bindings reachable from entrypoint without executing it.

    This function is primarily for testing and debugging purposes. It performs
    the same materialization as execute_pfig() but returns the materialized
    bindings dictionary instead of calling the entrypoint.

    Args:
        ast_file: Parsed pfig AST
        entrypoint: Optional override: binding name or attribute path (``outer.data.copy``). If None, uses the __entrypoint__ binding.
        filename: Optional filename for error reporting
        externals: Optional external values to override
        profile_paths: Optional paths to profile files to load
        strict: If True, treat validation warnings as errors

    Returns:
        Dictionary mapping binding names to materialized objects (only reachable ones)

    Raises:
        ExecutionError: If entrypoint is missing, not callable, or materialization fails
    """
    logger.debug("Getting materialized bindings (testing/debugging mode)")

    runtime_layers = _load_runtime_layers(externals, profile_paths)

    # Use shared pipeline with entrypoint resolution
    def target_resolver(compiled):
        return resolve_entrypoint(compiled, entrypoint)

    result = execute_pipeline(
        ast_file,
        target_resolver,
        filename=filename,
        runtime_layers=runtime_layers,
        strict=strict,
    )

    logger.debug(f"Returning {len(result.materialized)} materialized bindings for inspection")

    return result.materialized


# ===== Shared Execution Pipeline =====


@dataclass
class ExecutionResult:
    """Result of the execution pipeline containing all materialized objects and metadata."""

    compiled: Dict[str, Any]
    target_name: str
    reachable_names: Set[str]
    materialized: Dict[str, Any]
    included_files: List[str]  # Paths of all included .pfig files
    file_origins: Dict[str, "IncludeResolution"]  # Origin tracking for bundling


def execute_pipeline(
    ast_file: PFigFile,
    target_resolver: Callable[[Dict[str, Any]], str] | None,
    compiled_modifier: Callable | None = None,
    filename: str | None = None,
    externals: Dict[str, Any] | None = None,
    runtime_layers: Sequence[RuntimeInputLayer] | None = None,
    strict: bool = False,
    allow_missing_externals: bool = False,
    compile_only: bool = False,
) -> ExecutionResult:
    """
    Shared execution pipeline for compile → plan → materialize phases.

    This extracts the common pattern used by execute_pfig, get_materialized_bindings,
    and instantiate functions.

    Args:
        ast_file: Parsed pfig AST
        target_resolver: Function that takes (compiled, *args) and returns target_name
        compiled_modifier: Optional function to modify compiled data before planning
        filename: Optional filename for error reporting
        externals: Optional external values to override
        runtime_layers: Ordered input layers, used instead of externals when provided
        strict: If True, treat validation warnings as errors
        allow_missing_externals: Packaging compatibility flag; never bypasses execution
            requirements. Use validate_pfig for incomplete validation.
        compile_only: If True, skip planning and materialization (for bundling/file discovery)

    Returns:
        ExecutionResult with all materialized objects and metadata
    """
    logger.debug("Starting shared execution pipeline")

    # Collect all validation issues across phases
    all_issues: List[ValidationIssue] = []

    # 1. Compilation phase
    # Bundling / file discovery mode: we only need include resolution + file origin tracking.
    # Skip import resolution and binding compilation so that missing Python deps (e.g. torch)
    # don't prevent creating a bundle.
    resolve_imports = not compile_only
    compile_bindings = not compile_only
    compiler = ASTCompiler(
        base_path=filename,
        externals=externals,
        runtime_layers=runtime_layers,
        strict=strict,
        defer_external_validation=True,
        resolve_imports=resolve_imports,
        compile_bindings=compile_bindings,
    )
    compiled = compiler.compile_file(ast_file)

    # Collect compilation issues
    all_issues.extend(compiler.validation_issues)

    # Capture included files from compiler before we lose the reference
    included_files = list(compiler.loaded_files.keys())

    # For compile_only mode (used by bundling), skip planning and materialization
    if compile_only:
        return ExecutionResult(
            compiled=compiled,
            target_name="",  # No target in compile_only mode
            reachable_names=set(),
            materialized={},
            included_files=included_files,
            file_origins=compiler.file_origins,
        )

    # 2. Optional modification phase (for fresh/shared semantics, etc.)
    if compiled_modifier:
        target_name = compiled_modifier(compiled)
    else:
        if target_resolver is None:
            raise ExecutionError(
                "target_resolver is required when compiled_modifier is not provided"
            )
        target_name = target_resolver(compiled)

    # 3. Planning phase: find reachable bindings
    reachable_names = find_reachable_bindings(compiled, target_name)

    # 3b. Check for missing kwargs on providers used without overlays
    missing_kwargs_issues = check_missing_kwargs_usage(compiled, reachable_names)
    all_issues.extend(missing_kwargs_issues)

    # Execution always requires complete inputs, even after incomplete validation.
    all_issues.extend(missing_external_issues(compiled, reachable_names))

    if all_issues:
        # Skip materialization - just do graph validation to collect other issues
        materializer = Materializer()
        _, graph_issues = materializer.materialize_reachable(
            compiled, reachable_names, validate_only=True
        )
        all_issues.extend(graph_issues)
        raise ValidationError(all_issues)

    # 5. Materialization phase: instantiate only reachable objects
    # (also collects graph validation issues like unknown bindings and cycles)
    materializer = Materializer()
    materialized, graph_issues = materializer.materialize_reachable(
        compiled, reachable_names, target_name=target_name
    )
    all_issues.extend(graph_issues)

    # 6. Final validation checkpoint: raise any collected issues
    if all_issues:
        raise ValidationError(all_issues)

    return ExecutionResult(
        compiled=compiled,
        target_name=target_name,
        reachable_names=reachable_names,
        materialized=materialized,
        included_files=included_files,
        file_origins=compiler.file_origins,
    )


def instantiate(
    name: str,
    path: Union[str, Path] | None = None,
    source: str | None = None,
    *,
    fresh: bool = False,
    call_kwargs: Optional[Dict[str, Any]] = None,
    externals: Optional[Dict[str, Any]] = None,
    profile_paths: Sequence[str | Path | ProfileSpec] | None = None,
    strict: bool = False,
) -> Any:
    """
    Instantiate a specific binding from a pfig configuration.

    The whole configuration is parsed and compiled (including Python imports
    and compile-time signature checks). Only objects reachable from the
    requested binding are materialized, and required externals are scoped
    to that sub-DAG. No entrypoint is required.

    Args:
        name: Binding path to instantiate (e.g. "trainer", "alias/model").
        path: Filesystem path to a `.pfig` file. Mutually exclusive with `source`.
        source: pfig source code provided inline. Mutually exclusive with `path`.
        fresh: If False, behaves like `@name` (shared instance). If True, behaves like
            `@name(...)` (fresh instance).
        call_kwargs: Keyword arguments to overlay for a fresh construction of the
            provider; providing `call_kwargs` implies `fresh=True`.
        externals: External values to satisfy `external` declarations. Applied after
            profiles and validated against declarations.
        profile_paths: List of profile files to load in order. Later profiles override
            earlier ones. Values from `externals` override all profile values.
        strict: If True, treat validation warnings as errors

    Returns:
        The materialized object for the specified binding.

    Raises:
        ExecutionError: If the binding is missing/not found or materialization fails.
        ParseError: If config parsing fails.
        FileNotFoundError: If the `path` does not exist.

    Examples:
        Basic usage:

        ```python
        from pfig import instantiate

        config = '''
        from collections import Counter
        word_counter = Counter()
        '''
        counter = instantiate("word_counter", source=config)
        isinstance(counter, Counter)  # True
        ```

        Fresh instance with kwargs (overlays imply fresh):

        ```python
        fresh_counter = instantiate(
            "word_counter",
            source=config,
            call_kwargs={"iterable": ["a", "b", "a"]},
        )
        ```
    """
    logger.debug(f"Instantiating binding '{name}' from config")

    if path is None and source is None:
        raise ValueError("Either path or source must be provided")
    if path is not None and source is not None:
        raise ValueError("Only one of path or source must be provided")

    if path is not None:
        ast_file = parse_config_file(path=path)
    else:
        assert source is not None
        ast_file = parse_pfig_source(source)

    # Custom compiled modifier for fresh/shared semantics
    def compiled_modifier(compiled):
        target_name = name
        effective_fresh = fresh
        if call_kwargs is not None:
            # call_kwargs implies fresh=True
            effective_fresh = True

        # Create a reference to the binding that can be materialized
        if name in compiled["providers"]:
            # This is a provider - we need to create a reference to materialize it
            if effective_fresh or call_kwargs is not None:
                # Create a temporary binding that represents the fresh call
                fresh_name = f"_fresh_{name}_instance"
                overlay_kwargs = {}

                if call_kwargs:
                    # Compile the call_kwargs into the appropriate form
                    for k, v in call_kwargs.items():
                        # For simplicity, wrap raw values as LiteralValue
                        overlay_kwargs[k] = LiteralValue(v)

                # Add the fresh call to compiled values
                compiled["values"][fresh_name] = FreshProviderCall(name, overlay_kwargs)
                target_name = fresh_name
            else:
                # Create a shared reference (@name)
                shared_name = f"_shared_{name}_instance"
                compiled["values"][shared_name] = ProviderRef(name)
                target_name = shared_name
        elif name in compiled["values"] or name in compiled["declared_externals"]:
            target_name = name
            if effective_fresh and isinstance(compiled["values"].get(name), FreshProviderCall):
                fresh_name = f"_fresh_{name}_instance"
                compiled["values"][fresh_name] = FreshProviderCall(
                    name, {k: LiteralValue(v) for k, v in (call_kwargs or {}).items()}
                )
                target_name = fresh_name
        elif name in compiled["import_table"]:
            # This is an import - create a temporary binding for it
            import_name = f"_import_{name}_instance"
            compiled["values"][import_name] = compiled["import_table"][name]
            target_name = import_name
        else:
            raise ExecutionError(f"Binding '{name}' not found in configuration")

        return target_name

    runtime_layers = _load_runtime_layers(externals, profile_paths)

    # Use shared pipeline with custom compiled modifier
    result = execute_pipeline(
        ast_file,
        None,
        compiled_modifier,
        filename=str(path) if path is not None else "<inline>",
        runtime_layers=runtime_layers,
        strict=strict,
    )

    # Return the specific object requested
    if result.target_name not in result.materialized:
        raise ExecutionError(
            f"Failed to materialize binding '{name}' - not reachable or materialization failed"
        )

    materialized_object = result.materialized[result.target_name]
    logger.debug(f"Successfully instantiated '{name}': {type(materialized_object)}")
    return materialized_object


def load_profile(
    path: str | Path,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Load a profile file and extract its external overrides and file origins.

    Args:
        path: Path to the profile file.

    Returns:
        Tuple of (profile_bindings, file_origins) where:
        - profile_bindings: Materialized override values from the profile
        - file_origins: Dictionary mapping absolute paths to IncludeResolution
          for all files included by the profile (for bundling)
    """
    logger.info(f"Loading profile: {path}")

    try:
        from . import fs as _fs

        path_str = str(path)
        if _fs.is_uri(path_str):
            resolved_path = path_str
        else:
            resolved_path = str(Path(path).resolve())

        # Load and parse profile file
        profile_ast = parse_config_file(path=path)

        # Simple approach: compile the profile and extract all values
        # Note: We skip external validation during profile loading since externals
        # are declared in the main config, not the profile
        compiler = ASTCompiler(is_profile=True, base_path=resolved_path)
        compiled_profile = compiler.compile_file(profile_ast)
        compiler.raise_if_validation_issues()

        materializer = Materializer()
        profile_bindings = {
            name: materializer.materialize_value(value)
            for name, value in compiled_profile["values"].items()
        }

        file_origins = compiler.file_origins

        logger.debug(
            f"Loaded {len(profile_bindings)} external overrides, "
            f"{len(file_origins)} file origins from profile"
        )
        return profile_bindings, file_origins

    except Exception as e:
        raise ValueError(f"Failed to load profile '{path}': {e}") from e
