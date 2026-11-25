"""pfig Language Server built on pygls."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, cast

from lsprotocol import types as lsp
from pygls.server import LanguageServer

from pfig.lsp.state import (
    BindingDef,
    CallSite,
    DocumentIndex,
    DocumentState,
    IncludeEdge,
    WorkspaceState,
)

# Pending debounce timers: uri -> threading.Timer
_pending_timers: dict[str, threading.Timer] = {}

PFIG_VERSION_ERROR = (
    "pfig >= 1.11.0 is required but not available. "
    "Install or upgrade pfig[lsp] in the Python environment used by your editor."
)
from pfig.lsp.analysis.pfig_adapter import index_document, node_at_position
from pfig.lsp.analysis.pfig_validator import validate_with_pfig
from pfig.lsp.analysis.python_resolver import PythonResolver
from pfig.lsp.analysis.resolver import (
    _path_to_uri,
    _uri_to_path,
    load_index_for_uri,
    resolve_includes,
)
from pfig.lsp.graph_view_model import build_graph_view_model

logger = logging.getLogger(__name__)

DEBOUNCE_MS = 300

SEMANTIC_TOKEN_TYPES = [
    "pfigProvider",
    "pfigValue",
    "pfigProviderRef",
    "pfigValueRef",
    "parameter",
    "function",
    "namespace",
]

SEMANTIC_TOKEN_MODIFIERS: list[str] = []


def _request_params_as_dict(params: Any) -> dict[str, Any]:
    """Normalize JSON-RPC params from VS Code / pygls."""
    if params is None:
        return {}
    if isinstance(params, dict):
        d = dict(params)
        # Nested LSP-style payload
        if "uri" not in d and isinstance(d.get("textDocument"), dict):
            td = d["textDocument"]
            if isinstance(td.get("uri"), str):
                d["uri"] = td["uri"]
        return d
    if isinstance(params, (list, tuple)):
        if len(params) == 1:
            return _request_params_as_dict(params[0])
        if len(params) >= 1 and isinstance(params[0], str):
            out: dict[str, Any] = {"uri": params[0]}
            if (
                len(params) >= 2
                and isinstance(params[1], str)
                and params[1] in ("reachable", "all")
            ):
                out["mode"] = params[1]
            return out
        return {}
    out: dict[str, Any] = {}
    for key in ("uri", "mode"):
        if hasattr(params, key):
            val = getattr(params, key)
            if val is not None:
                out[key] = val
    text_document = getattr(params, "text_document", None) or getattr(params, "textDocument", None)
    if "uri" not in out and text_document is not None:
        if isinstance(text_document, dict):
            val = text_document.get("uri")
        else:
            val = getattr(text_document, "uri", None)
        if val is not None:
            out["uri"] = val
    return out


def _find_workspace_document(ws: WorkspaceState, uri: str) -> DocumentState | None:
    """Match LSP document by URI string or by resolved filesystem path (VS Code URI quirks)."""
    if uri in ws.documents:
        return ws.documents[uri]
    path_from_uri = _uri_to_path(uri)
    if path_from_uri:
        try:
            norm = str(Path(path_from_uri).resolve())
            canon = _path_to_uri(norm)
            if canon in ws.documents:
                return ws.documents[canon]
        except OSError:
            norm = path_from_uri
        for stored_uri, doc in ws.documents.items():
            p = _uri_to_path(stored_uri)
            if not p:
                continue
            try:
                if str(Path(p).resolve()) == str(Path(path_from_uri).resolve()):
                    return doc
            except OSError:
                if p == path_from_uri:
                    return doc
    return None


class PfigLanguageServer(LanguageServer):
    def __init__(self) -> None:
        super().__init__("pfig-lsp", "v0.1.0")
        self.workspace_state = WorkspaceState()
        self.python_resolver = PythonResolver()


def _publish_diagnostics(server: PfigLanguageServer, uri: str) -> None:
    ws = server.workspace_state
    doc = ws.documents.get(uri)
    if doc is None:
        return

    try:
        doc.index = index_document(doc.text, uri)
    except Exception as e:
        logger.debug("Index build failed (parse error?): %s", e)
        doc.index = DocumentIndex()

    included_uris = set()
    for inc in doc.index.includes:
        resolved = resolve_includes(inc, uri, ws)
        if resolved:
            included_uris.add(resolved)
    ws.update_include_graph(uri, included_uris)

    diagnostics, compiled = validate_with_pfig(
        doc.text,
        uri,
        profiles=ws.profiles,
        set_overrides=ws.set_overrides,
        allow_missing_externals=ws.allow_missing_externals,
        strict=ws.strict_signatures,
        index=doc.index,
    )
    # Use include_graph from compile result for cycle detection and dependent revalidation
    if compiled:
        from pfig.lsp.analysis.resolver import _path_to_uri

        include_graph = compiled.get("include_graph", {})
        for path, inc_paths in include_graph.items():
            uri_key = _path_to_uri(path)
            inc_uris = {_path_to_uri(p) for p in inc_paths}
            ws.update_include_graph(uri_key, inc_uris)

    # pfig may not report unknown bindings for refs in kwarg values or assignments
    existing_ranges = {(d.range.start.line, d.range.start.character) for d in diagnostics}
    for d in _validate_provider_refs(server, uri, doc):
        key = (d.range.start.line, d.range.start.character)
        if key not in existing_ranges:
            diagnostics.append(d)
            existing_ranges.add(key)

    doc.diagnostics = diagnostics
    server.publish_diagnostics(uri, diagnostics)


def _validate_provider_refs(
    server: PfigLanguageServer, uri: str, doc: DocumentState
) -> list[lsp.Diagnostic]:
    """Report unknown provider refs (e.g. @typo in kwarg values or assignments)."""
    diags: list[lsp.Diagnostic] = []
    for ref in doc.index.refs:
        if ref.kind != "provider":
            continue
        name = ref.name.lstrip("@")
        # For @provider.method (e.g. __entrypoint__ = @trainer.fit), only validate the base
        base_name = name.split(".", 1)[0] if "." in name else name
        if _resolve_provider_ref(base_name, uri, doc.index, server.workspace_state) is None:
            diags.append(
                lsp.Diagnostic(
                    range=ref.range,
                    message=f"Unknown binding: @{name}",
                    severity=lsp.DiagnosticSeverity.Error,
                    source="pfig",
                )
            )
    return diags


def _revalidate_dependents(server: PfigLanguageServer, uri: str) -> None:
    for dep_uri in server.workspace_state.get_dependents(uri):
        if dep_uri in server.workspace_state.documents:
            _publish_diagnostics(server, dep_uri)


def create_server() -> PfigLanguageServer:
    server = PfigLanguageServer()

    @server.feature(lsp.INITIALIZE)
    def on_initialize(params: lsp.InitializeParams) -> lsp.InitializeResult:
        try:
            from pfig import node_at_position, resolve_include_path, resolve_provider_ref
        except ImportError as e:
            raise RuntimeError(PFIG_VERSION_ERROR) from e

        ws = server.workspace_state
        if params.root_uri:
            ws.root_uri = params.root_uri

        return lsp.InitializeResult(
            capabilities=lsp.ServerCapabilities(
                text_document_sync=lsp.TextDocumentSyncOptions(
                    open_close=True,
                    change=lsp.TextDocumentSyncKind.Incremental,
                    save=lsp.SaveOptions(include_text=True),
                ),
                definition_provider=True,
                hover_provider=True,
                completion_provider=lsp.CompletionOptions(
                    trigger_characters=["@", ".", "(", "=", "/"],
                ),
                signature_help_provider=lsp.SignatureHelpOptions(
                    trigger_characters=["(", ","],
                ),
                semantic_tokens_provider=lsp.SemanticTokensOptions(
                    legend=lsp.SemanticTokensLegend(
                        token_types=SEMANTIC_TOKEN_TYPES,
                        token_modifiers=SEMANTIC_TOKEN_MODIFIERS,
                    ),
                    full=True,
                    range=False,
                ),
            ),
            server_info=lsp.InitializeResultServerInfoType(
                name="pfig-lsp",
                version="0.1.0",
            ),
        )

    @server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
    def on_did_open(params: lsp.DidOpenTextDocumentParams) -> None:
        uri = params.text_document.uri
        text = params.text_document.text
        version = params.text_document.version

        doc = DocumentState(uri=uri, version=version, text=text)
        server.workspace_state.documents[uri] = doc
        _publish_diagnostics(server, uri)

    @server.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
    def on_did_change(params: lsp.DidChangeTextDocumentParams) -> None:
        uri = params.text_document.uri
        doc = server.workspace_state.documents.get(uri)
        if doc is None:
            return

        for change in params.content_changes:
            change_obj = cast(Any, change)
            if hasattr(change_obj, "range") and change_obj.range is not None:
                doc.text = _apply_incremental_change(doc.text, change_obj.range, change_obj.text)
            else:
                doc.text = change_obj.text

        doc.version = params.text_document.version

        # Debounce: cancel pending timer, schedule new one
        if uri in _pending_timers:
            _pending_timers[uri].cancel()
            del _pending_timers[uri]

        def run_validation() -> None:
            _pending_timers.pop(uri, None)
            _publish_diagnostics(server, uri)

        t = threading.Timer(DEBOUNCE_MS / 1000.0, run_validation)
        t.daemon = True
        _pending_timers[uri] = t
        t.start()

    @server.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
    def on_did_save(params: lsp.DidSaveTextDocumentParams) -> None:
        uri = params.text_document.uri
        _revalidate_dependents(server, uri)

    @server.feature(lsp.TEXT_DOCUMENT_DID_CLOSE)
    def on_did_close(params: lsp.DidCloseTextDocumentParams) -> None:
        uri = params.text_document.uri
        server.workspace_state.documents.pop(uri, None)

    @server.feature(lsp.WORKSPACE_DID_CHANGE_CONFIGURATION)
    def on_did_change_configuration(
        params: lsp.DidChangeConfigurationParams,
    ) -> None:
        settings: dict[str, Any] = params.settings or {}
        pfig_settings = settings.get("pfig", {})
        ws = server.workspace_state
        ws.profiles = pfig_settings.get("profiles", [])
        ws.set_overrides = pfig_settings.get("set", {})
        ws.allow_missing_externals = pfig_settings.get("allowMissingExternals", True)
        ws.strict_signatures = pfig_settings.get("strictSignatures", False)
        ws.use_runtime_imports = pfig_settings.get("python", {}).get("useRuntimeImports", False)

        for uri in list(ws.documents):
            _publish_diagnostics(server, uri)

    @server.feature(lsp.WORKSPACE_DID_CHANGE_WATCHED_FILES)
    def on_did_change_watched_files(
        params: lsp.DidChangeWatchedFilesParams,
    ) -> None:
        for change in params.changes:
            _revalidate_dependents(server, change.uri)

    # -- Definition ----------------------------------------------------------

    @server.feature(lsp.TEXT_DOCUMENT_DEFINITION)
    def on_definition(
        params: lsp.DefinitionParams,
    ) -> lsp.Location | list[lsp.Location] | None:
        uri = params.text_document.uri
        doc = server.workspace_state.documents.get(uri)
        if doc is None:
            return None

        pos = params.position
        node = node_at_position(doc.index, pos)
        if node is None:
            return None

        if node.kind == "provider":
            name = node.name.lstrip("@")
            base_name = name.split(".", 1)[0] if "." in name else name
            location = _resolve_provider_ref(
                base_name,
                uri,
                doc.index,
                server.workspace_state,
            )
            if location:
                return lsp.Location(uri=location[0], range=location[1])

        if node.kind == "override":
            binding = _find_overridden_definition(server.workspace_state, uri, node.name)
            if binding:
                return lsp.Location(uri=binding.uri, range=binding.range)

        if node.kind == "python_symbol":
            location = server.python_resolver.goto_definition(
                node.name,
                doc.index.imports,
                server.workspace_state.use_runtime_imports,
            )
            if location:
                return location

        if node.kind == "import":
            location = server.python_resolver.goto_definition(
                node.name,
                doc.index.imports,
                server.workspace_state.use_runtime_imports,
            )
            if location:
                return location

        return None

    # -- Hover ---------------------------------------------------------------

    @server.feature(lsp.TEXT_DOCUMENT_HOVER)
    def on_hover(params: lsp.HoverParams) -> lsp.Hover | None:
        uri = params.text_document.uri
        doc = server.workspace_state.documents.get(uri)
        if doc is None:
            return None

        node = node_at_position(doc.index, params.position)
        if node is None:
            return None

        if node.kind == "provider":
            name = node.name.lstrip("@")
            base_name = name.split(".", 1)[0] if "." in name else name
            location = _resolve_provider_ref(
                base_name,
                uri,
                doc.index,
                server.workspace_state,
            )
            if location:
                loc_uri, loc_range = location
                bdoc = server.workspace_state.documents.get(loc_uri)
                if bdoc:
                    lines = bdoc.text.splitlines()
                else:
                    # Target file not open - read from disk (e.g. @consts/batch_size in included file)
                    path = _uri_to_path(loc_uri)
                    if path and os.path.isfile(path):
                        try:
                            with open(path, encoding="utf-8") as f:
                                lines = f.read().splitlines()
                        except OSError:
                            lines = []
                    else:
                        lines = []
                line_idx = loc_range.start.line
                if 0 <= line_idx < len(lines):
                    snippet = lines[line_idx].strip()
                    title = (
                        "Override" if snippet.startswith(("override ", "override\t")) else "Binding"
                    )
                    return lsp.Hover(
                        contents=lsp.MarkupContent(
                            kind=lsp.MarkupKind.Markdown,
                            value=f"**{title}**\n\n```pfig\n{snippet}\n```",
                        )
                    )

        if node.kind == "override":
            binding = _find_overridden_definition(server.workspace_state, uri, node.name)
            if binding:
                bdoc = server.workspace_state.documents.get(binding.uri)
                if bdoc:
                    lines = bdoc.text.splitlines()
                else:
                    path = _uri_to_path(binding.uri)
                    if path and os.path.isfile(path):
                        try:
                            with open(path, encoding="utf-8") as f:
                                lines = f.read().splitlines()
                        except OSError:
                            lines = []
                    else:
                        lines = []
                line_idx = binding.range.start.line
                if 0 <= line_idx < len(lines):
                    snippet = lines[line_idx].strip()
                    return lsp.Hover(
                        contents=lsp.MarkupContent(
                            kind=lsp.MarkupKind.Markdown,
                            value=f"**Overrides**\n\n```pfig\n{snippet}\n```",
                        )
                    )

        if node.kind == "python_symbol" or node.kind == "import":
            info = server.python_resolver.get_hover_info(node.name, doc.index.imports)
            if info:
                return lsp.Hover(
                    contents=lsp.MarkupContent(
                        kind=lsp.MarkupKind.Markdown,
                        value=f"**Documentation**\n\n{info}",
                    )
                )

        return None

    # -- Completion ----------------------------------------------------------

    @server.feature(lsp.TEXT_DOCUMENT_COMPLETION)
    def on_completion(
        params: lsp.CompletionParams,
    ) -> lsp.CompletionList | None:
        uri = params.text_document.uri
        doc = server.workspace_state.documents.get(uri)
        if doc is None:
            return None

        pos = params.position
        line_text = _get_line(doc.text, pos.line)
        col = pos.character
        prefix = line_text[:col]

        items: list[lsp.CompletionItem] = []

        if "@" in prefix:
            at_idx = prefix.rfind("@")
            typed = prefix[at_idx + 1 :]
            items.extend(_complete_bindings(server.workspace_state, uri, typed))
        elif "(" in prefix:
            items.extend(_complete_kwargs(doc, pos, server.python_resolver))

        return lsp.CompletionList(is_incomplete=False, items=items)

    # -- Semantic Tokens -----------------------------------------------------

    @server.feature(lsp.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL)
    def on_semantic_tokens(params: lsp.SemanticTokensParams) -> lsp.SemanticTokens:
        uri = params.text_document.uri
        doc = server.workspace_state.documents.get(uri)
        if doc is None:
            return lsp.SemanticTokens(data=[])

        return lsp.SemanticTokens(data=_build_semantic_tokens(server, uri, doc))

    # -- Signature Help ------------------------------------------------------

    @server.feature(lsp.TEXT_DOCUMENT_SIGNATURE_HELP)
    def on_signature_help(
        params: lsp.SignatureHelpParams,
    ) -> lsp.SignatureHelp | None:
        uri = params.text_document.uri
        doc = server.workspace_state.documents.get(uri)
        if doc is None:
            return None

        pos = params.position
        for cs in doc.index.call_sites:
            if cs.full_range and _position_in_range(pos, cs.full_range):
                sig = server.python_resolver.get_signature(cs.callee, doc.index.imports)
                if sig:
                    return lsp.SignatureHelp(
                        signatures=[sig],
                        active_signature=0,
                    )
        return None

    # -- Custom request: validate file ---------------------------------------

    @server.feature("pfig/validateFile")
    def on_validate_file(params: Any) -> None:
        uri = params.get("uri") if isinstance(params, dict) else None
        if uri and uri in server.workspace_state.documents:
            _publish_diagnostics(server, uri)

    @server.feature("pfig/getGraphViewModel")
    def on_get_graph_view_model(params: Any) -> dict[str, Any]:
        p = _request_params_as_dict(params)
        uri = p.get("uri")
        if uri is not None:
            uri = str(uri).strip() or None
        mode = p.get("mode", "reachable")
        if mode not in ("reachable", "all"):
            mode = "reachable"
        if not uri:
            return {
                "ok": False,
                "error": "Missing document URI.",
                "nodes": [],
                "edges": [],
                "mode": mode,
                "reachableUnavailable": True,
                "entrypoint": None,
            }

        ws = server.workspace_state
        doc = _find_workspace_document(ws, uri)
        if doc is not None:
            return build_graph_view_model(
                text=doc.text,
                uri=doc.uri,
                ws=ws,
                index=doc.index,
                mode=mode,
            )

        # Fallback: file on disk (handles didOpen race vs graph request, subtle URI mismatch).
        path_str = _uri_to_path(uri)
        if path_str and os.path.isfile(path_str):
            try:
                text = Path(path_str).read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return {
                    "ok": False,
                    "error": f"Could not read {path_str}: {exc}",
                    "nodes": [],
                    "edges": [],
                    "mode": mode,
                    "reachableUnavailable": True,
                    "entrypoint": None,
                }
            try:
                canon_uri = _path_to_uri(str(Path(path_str).resolve()))
            except OSError:
                canon_uri = uri
            from pfig.lsp.analysis.pfig_adapter import index_document

            idx = index_document(text, canon_uri)
            return build_graph_view_model(
                text=text,
                uri=canon_uri,
                ws=ws,
                index=idx,
                mode=mode,
            )

        return {
            "ok": False,
            "error": "Document is not open in the language server and could not be read from disk.",
            "nodes": [],
            "edges": [],
            "mode": mode,
            "reachableUnavailable": True,
            "entrypoint": None,
        }

    return server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_incremental_change(text: str, change_range: lsp.Range, new_text: str) -> str:
    lines = text.splitlines(True)
    start_line = change_range.start.line
    start_char = change_range.start.character
    end_line = change_range.end.line
    end_char = change_range.end.character

    if start_line >= len(lines):
        return text + new_text

    before = lines[start_line][:start_char]
    after = lines[end_line][end_char:] if end_line < len(lines) else ""

    new_lines = lines[:start_line]
    new_lines.append(before + new_text + after)
    if end_line + 1 < len(lines):
        new_lines.extend(lines[end_line + 1 :])

    return "".join(new_lines)


def _get_line(text: str, line: int) -> str:
    lines = text.splitlines()
    if 0 <= line < len(lines):
        return lines[line]
    return ""


def _position_in_range(pos: lsp.Position, r: lsp.Range) -> bool:
    if pos.line < r.start.line or pos.line > r.end.line:
        return False
    if pos.line == r.start.line and pos.character < r.start.character:
        return False
    if pos.line == r.end.line and pos.character > r.end.character:
        return False
    return True


SemanticTokenTuple = tuple[int, int, int, str]


def _encode_semantic_tokens(tokens: list[SemanticTokenTuple]) -> list[int]:
    tokens.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
    data: list[int] = []
    prev_line = 0
    prev_char = 0

    for line, char, length, token_type in tokens:
        if length <= 0:
            continue
        try:
            token_index = SEMANTIC_TOKEN_TYPES.index(token_type)
        except ValueError:
            continue
        delta_line = line - prev_line
        delta_char = char - prev_char if delta_line == 0 else char
        data.extend([delta_line, delta_char, length, token_index, 0])
        prev_line = line
        prev_char = char

    return data


def _add_range_token(
    tokens: list[SemanticTokenTuple],
    rng: lsp.Range,
    token_type: str,
) -> None:
    start = rng.start
    end = rng.end
    if start.line != end.line:
        return
    tokens.append((start.line, start.character, end.character - start.character, token_type))


def _add_pfig_ref_tokens(
    tokens: list[SemanticTokenTuple],
    rng: lsp.Range,
    name: str,
    token_type: str,
) -> None:
    start = rng.start
    end = rng.end
    if start.line != end.line:
        return

    offset = 1 if end.character > start.character else 0
    for part in name.replace(".", "/").split("/"):
        if not part:
            offset += 1
            continue
        tokens.append((start.line, start.character + offset, len(part), token_type))
        offset += len(part) + 1


def _build_semantic_tokens(
    server: PfigLanguageServer,
    uri: str,
    doc: DocumentState,
) -> list[int]:
    try:
        ws = server.workspace_state
        diagnostics, compiled = validate_with_pfig(
            doc.text,
            uri,
            profiles=ws.profiles,
            set_overrides=ws.set_overrides,
            allow_missing_externals=ws.allow_missing_externals,
            strict=ws.strict_signatures,
            index=doc.index,
        )
        del diagnostics

        providers = set((compiled or {}).get("providers", {}))
        values = set((compiled or {}).get("values", {})) | set(doc.index.external_defs)
        tokens: list[SemanticTokenTuple] = []

        for name, bdef in doc.index.binding_defs.items():
            if name in providers:
                _add_range_token(tokens, bdef.range, "pfigProvider")
            elif name in values:
                _add_range_token(tokens, bdef.range, "pfigValue")

        for bdef in doc.index.external_defs.values():
            _add_range_token(tokens, bdef.range, "pfigValue")

        for import_binding in doc.index.imports.values():
            if import_binding.module_range is not None:
                _add_dotted_tokens(
                    tokens,
                    import_binding.module_range,
                    import_binding.module,
                    "namespace",
                )
            _add_range_token(tokens, import_binding.range, "pfigValue")

        for include in doc.index.includes:
            _add_range_token(tokens, include.range, "namespace")
            if include.alias:
                alias_range = _include_alias_range(doc.text, include)
                if alias_range is not None:
                    _add_range_token(tokens, alias_range, "namespace")

        include_cache: dict[str, tuple[set[str], set[str]]] = {}
        for ref in doc.index.refs:
            if ref.kind != "provider":
                continue
            name = ref.name.lstrip("@")
            token_type = _semantic_token_type_for_ref(
                name, providers, values, uri, ws, include_cache
            )
            _add_pfig_ref_tokens(tokens, ref.range, name, token_type)

        for call in doc.index.call_sites:
            for kwarg_range in call.kwargs.values():
                _add_range_token(tokens, kwarg_range, "parameter")
            if call.callee.startswith("@"):
                name = call.callee.lstrip("@")
                token_type = _semantic_token_type_for_ref(
                    name, providers, values, uri, ws, include_cache
                )
                _add_pfig_ref_tokens(tokens, call.callee_range, name, token_type)
            else:
                _add_callee_tokens(tokens, call)

        return _encode_semantic_tokens(tokens)
    except Exception:
        logger.debug("semantic token build failed", exc_info=True)
        return []


def _semantic_token_type_for_ref(
    name: str,
    providers: set[str],
    values: set[str],
    uri: str,
    ws: WorkspaceState,
    include_cache: dict[str, tuple[set[str], set[str]]],
) -> str:
    if "/" in name:
        alias, target = name.split("/", 1)
        target_base = target.split(".", 1)[0]
        include_sets = _provider_value_sets_for_include(alias, uri, ws, include_cache)
        if include_sets is not None:
            include_providers, include_values = include_sets
            if target_base in include_providers:
                return "pfigProviderRef"
            if target_base in include_values:
                return "pfigValueRef"

    base = name.split(".", 1)[0]
    if base in providers:
        return "pfigProviderRef"
    if base in values:
        return "pfigValueRef"
    return "pfigProviderRef"


def _provider_value_sets_for_include(
    alias: str,
    current_uri: str,
    ws: WorkspaceState,
    include_cache: dict[str, tuple[set[str], set[str]]],
) -> tuple[set[str], set[str]] | None:
    doc = ws.documents.get(current_uri)
    if doc is None:
        return None

    for inc in doc.index.includes:
        if inc.alias != alias:
            continue
        resolved_uri = resolve_includes(inc, current_uri, ws)
        if not resolved_uri:
            return None
        if resolved_uri in include_cache:
            return include_cache[resolved_uri]

        inc_doc = ws.documents.get(resolved_uri)
        if inc_doc is not None:
            text = inc_doc.text
            index = inc_doc.index
        else:
            path_str = _uri_to_path(resolved_uri)
            if not path_str:
                return None
            try:
                text = Path(path_str).read_text(encoding="utf-8", errors="replace")
                index = index_document(text, resolved_uri)
            except OSError:
                return None

        _diagnostics, compiled = validate_with_pfig(
            text,
            resolved_uri,
            profiles=ws.profiles,
            set_overrides=ws.set_overrides,
            allow_missing_externals=ws.allow_missing_externals,
            strict=ws.strict_signatures,
            index=index,
        )
        providers = set((compiled or {}).get("providers", {}))
        values = set((compiled or {}).get("values", {})) | set(index.external_defs)
        include_cache[resolved_uri] = (providers, values)
        return providers, values

    return None


def _add_callee_tokens(tokens: list[SemanticTokenTuple], call: CallSite) -> None:
    callee_range = call.callee_range
    if callee_range.start.line != callee_range.end.line:
        return

    _add_dotted_tokens(tokens, callee_range, call.callee, "namespace", final_token_type="function")


def _add_dotted_tokens(
    tokens: list[SemanticTokenTuple],
    rng: lsp.Range,
    text: str,
    token_type: str,
    *,
    final_token_type: str | None = None,
) -> None:
    if rng.start.line != rng.end.line:
        return

    segments = text.split(".")
    offset = 0
    for idx, segment in enumerate(segments):
        if not segment:
            offset += 1
            continue
        current_token_type = (
            final_token_type if idx == len(segments) - 1 and final_token_type else token_type
        )
        tokens.append(
            (
                rng.start.line,
                rng.start.character + offset,
                len(segment),
                current_token_type,
            )
        )
        offset += len(segment) + 1


def _include_alias_range(text: str, include: IncludeEdge) -> lsp.Range | None:
    line_no = include.range.start.line
    lines = text.splitlines()
    if line_no >= len(lines):
        return None

    line = lines[line_no]
    search_start = include.range.end.character
    as_pos = line.find(" as ", search_start)
    if as_pos < 0:
        return None

    alias_start = as_pos + 4
    alias_end = alias_start + len(include.alias or "")
    if line[alias_start:alias_end] != include.alias:
        return None

    return lsp.Range(
        start=lsp.Position(line=line_no, character=alias_start),
        end=lsp.Position(line=line_no, character=alias_end),
    )


def _build_resolved_includes(includes: list, uri: str, ws: WorkspaceState) -> dict[str, str]:
    """Build {target_path: resolved_abs_path} for resolve_provider_ref."""
    resolved: dict[str, str] = {}
    for inc in includes:
        if inc.target_path in resolved:
            continue
        r = resolve_includes(inc, uri, ws)
        if r:
            from pfig.lsp.analysis.resolver import _uri_to_path

            resolved[inc.target_path] = _uri_to_path(r) or r
    return resolved


def _ensure_lsp_range(r: object) -> lsp.Range:
    """Convert pfig range (dict or object) to lsprotocol Range."""
    if hasattr(r, "start") and hasattr(r, "end"):
        range_obj = cast(Any, r)
        return lsp.Range(start=range_obj.start, end=range_obj.end)
    if isinstance(r, dict):
        start = r.get("start", r)
        end = r.get("end", start)
        return lsp.Range(
            start=lsp.Position(
                line=start.get("line", 0),
                character=start.get("character", 0),
            ),
            end=lsp.Position(
                line=end.get("line", 0),
                character=end.get("character", 0),
            ),
        )
    return lsp.Range(lsp.Position(0, 0), lsp.Position(0, 0))


def _resolve_provider_ref(
    name: str,
    current_uri: str,
    index: DocumentIndex,
    ws: WorkspaceState,
) -> tuple[str, lsp.Range] | None:
    """Resolve @name to (uri, range) using pfig (requires pfig >= 1.11)."""
    from pfig import resolve_provider_ref
    from pfig.lsp.analysis.resolver import _path_to_uri, _uri_to_path

    current_path = _uri_to_path(current_uri)
    if not current_path:
        return None
    resolved_includes = _build_resolved_includes(index.includes, current_uri, ws)
    result = cast(Any, resolve_provider_ref)(
        name,
        current_path,
        index,
        resolved_includes,
    )
    if result is None:
        return None
    uri_out, range_out = result
    if not uri_out.startswith("file://"):
        uri_out = _path_to_uri(uri_out)
    return (uri_out, _ensure_lsp_range(range_out))


def _find_overridden_definition(
    ws: WorkspaceState, current_uri: str, override_target: str
) -> BindingDef | None:
    """Find the definition that an override overrides (next in the chain)."""
    doc = ws.documents.get(current_uri)
    if doc is None:
        return None

    parts = override_target.split("/")
    if len(parts) == 1:
        # Local override: find binding/external in same file
        local_name = parts[0]
        bdef = doc.index.binding_defs.get(local_name)
        if bdef:
            return bdef
        bdef = doc.index.external_defs.get(local_name)
        if bdef:
            return bdef
        return None

    if len(parts) != 2:
        return None

    alias, local_name = parts
    for inc in doc.index.includes:
        if inc.alias == alias:
            resolved = resolve_includes(inc, current_uri, ws)
            if not resolved:
                continue
            inc_doc = ws.documents.get(resolved)
            inc_index = inc_doc.index if inc_doc else load_index_for_uri(resolved)
            if not inc_index:
                continue

            # Direct definition (binding or external)
            bdef = inc_index.binding_defs.get(local_name)
            if bdef:
                return bdef
            bdef = inc_index.external_defs.get(local_name)
            if bdef:
                return bdef

            # Another override in the included file (e.g. override base/epochs)
            for target, override_range in inc_index.overrides.items():
                if target == local_name or target.endswith("/" + local_name):
                    return BindingDef(
                        name=target,
                        uri=resolved,
                        range=override_range,
                    )

    return None


def _complete_bindings(ws: WorkspaceState, uri: str, typed: str) -> list[lsp.CompletionItem]:
    items: list[lsp.CompletionItem] = []
    doc = ws.documents.get(uri)
    if doc is None:
        return items

    for name in doc.index.binding_defs:
        if name.startswith(typed):
            items.append(
                lsp.CompletionItem(
                    label=name,
                    kind=lsp.CompletionItemKind.Variable,
                )
            )
    for name in doc.index.external_defs:
        if name.startswith(typed):
            items.append(
                lsp.CompletionItem(
                    label=name,
                    kind=lsp.CompletionItemKind.Variable,
                )
            )

    for inc in doc.index.includes:
        if inc.alias and (typed.startswith(inc.alias) or inc.alias.startswith(typed)):
            resolved = resolve_includes(inc, uri, ws)
            if resolved:
                inc_index = None
                inc_doc = ws.documents.get(resolved)
                if inc_doc:
                    inc_index = inc_doc.index
                else:
                    inc_index = load_index_for_uri(resolved)
                if inc_index:
                    for bname in inc_index.binding_defs:
                        full = f"{inc.alias}/{bname}"
                        if full.startswith(typed):
                            items.append(
                                lsp.CompletionItem(
                                    label=full,
                                    kind=lsp.CompletionItemKind.Variable,
                                )
                            )
                    for ename in inc_index.external_defs:
                        full = f"{inc.alias}/{ename}"
                        if full.startswith(typed):
                            items.append(
                                lsp.CompletionItem(
                                    label=full,
                                    kind=lsp.CompletionItemKind.Variable,
                                )
                            )

    return items


def _complete_kwargs(
    doc: DocumentState,
    pos: lsp.Position,
    python_resolver: PythonResolver,
) -> list[lsp.CompletionItem]:
    items: list[lsp.CompletionItem] = []
    for cs in doc.index.call_sites:
        if cs.full_range and _position_in_range(pos, cs.full_range):
            params = python_resolver.get_callable_params(cs.callee, doc.index.imports)
            existing = set(cs.kwargs.keys())
            for p in params:
                if p not in existing:
                    items.append(
                        lsp.CompletionItem(
                            label=f"{p}=",
                            kind=lsp.CompletionItemKind.Property,
                            insert_text=f"{p}=",
                        )
                    )
            break
    return items
