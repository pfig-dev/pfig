"""Document and workspace state management."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lsprotocol.types import Diagnostic, Position, Range


@dataclass
class BindingDef:
    """A pfig binding definition (``name = expr``)."""

    name: str
    uri: str
    range: Range


@dataclass
class RefInfo:
    """A reference to a provider or symbol."""

    name: str
    range: Range
    kind: str  # "provider", "python_symbol", "import"


@dataclass
class ImportBinding:
    """An import binding (``import x`` or ``from x import y``)."""

    local_name: str
    module: str
    imported_name: str | None  # None for bare ``import``
    range: Range
    module_range: Range | None = None  # Range of module path; use for module-level errors


@dataclass
class IncludeEdge:
    """An include relationship between files."""

    target_path: str
    alias: str | None
    range: Range


@dataclass
class CallSite:
    """A function/provider call site with kwarg information."""

    callee: str
    callee_range: Range
    kwargs: dict[str, Range] = field(default_factory=dict)
    full_range: Range | None = None


@dataclass
class DocumentIndex:
    """Index built from parsing a single document."""

    binding_defs: dict[str, BindingDef] = field(default_factory=dict)
    external_defs: dict[str, BindingDef] = field(default_factory=dict)
    refs: list[RefInfo] = field(default_factory=list)
    imports: dict[str, ImportBinding] = field(default_factory=dict)
    includes: list[IncludeEdge] = field(default_factory=list)
    call_sites: list[CallSite] = field(default_factory=list)
    externals: list[str] = field(default_factory=list)
    overrides: dict[str, Range] = field(default_factory=dict)
    # pfig's native index (dict-style ranges) for node_at_position
    raw_index: object | None = None


@dataclass
class DocumentState:
    """Per-document state tracked by the server."""

    uri: str
    version: int = 0
    text: str = ""
    ast: Any = None
    index: DocumentIndex = field(default_factory=DocumentIndex)
    diagnostics: list[Diagnostic] = field(default_factory=list)


@dataclass
class WorkspaceState:
    """Workspace-wide state."""

    root_uri: str = ""
    profiles: list[str] = field(default_factory=list)
    set_overrides: dict[str, str] = field(default_factory=dict)
    allow_missing_externals: bool = True
    strict_signatures: bool = False
    use_runtime_imports: bool = False

    documents: dict[str, DocumentState] = field(default_factory=dict)

    # include graph: file_uri -> set of included file URIs
    include_graph: dict[str, set[str]] = field(default_factory=dict)
    # reverse deps: file_uri -> set of files that include it
    reverse_deps: dict[str, set[str]] = field(default_factory=dict)

    def update_include_graph(self, uri: str, includes: set[str]) -> None:
        old = self.include_graph.get(uri, set())
        for removed in old - includes:
            self.reverse_deps.get(removed, set()).discard(uri)
        for added in includes - old:
            self.reverse_deps.setdefault(added, set()).add(uri)
        self.include_graph[uri] = includes

    def get_dependents(self, uri: str) -> set[str]:
        return self.reverse_deps.get(uri, set())
