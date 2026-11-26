# LSP API Reference

**Audience:** Developers integrating pfig with LSP-capable editors

**Purpose:** Use pfig as the single source of truth for parsing, indexing, include resolution, and validation. Replace custom regex indexers, resolvers, and validators with pfig's APIs.

---

## Overview

pfig exposes an LSP-oriented API so that editors can:

- Parse source and get a structural index (bindings, refs, includes, call sites)
- Resolve explicit filesystem include paths (`./consts.pfig`)
- Resolve provider refs (`@name`, `@alias/name`) for go-to-definition
- Highlight provider/value semantics with standard LSP semantic tokens
- Get diagnostics from `compile_pfig` with correct positions

Rootless `compile_pfig` diagnostics cover all declarations; they are not a claim
about the inputs needed by one execution. `allow_missing_externals=True`
suppresses missing-input diagnostics without suppressing structural checks.

Graph `all` mode retains all declarations and authored recipe relationships.
`reachable` mode includes recipe context for navigation, even when a fresh call
replaces some arguments. Node `flags.isReachable` describes effective evaluation
dependencies; recipe-only context nodes have it set to false. A displayed missing
declaration is not necessarily a required execution input.

All ranges use **LSP 0-based** `(line, character)` coordinates.

---

## Bundled Language Server

pfig includes a `pygls`-based language server in the optional `lsp` extra:

```bash
python -m pip install "pfig[lsp]"
python -m pfig.lsp
```

Configure your editor's LSP client to launch this command over stdio for `.pfig`
files, using the Python environment where `pfig[lsp]` and your project's config
imports are installed. The `pfig-lsp` console command is equivalent.

Advertised capabilities:

- Text document sync
- Go-to-definition
- Hover
- Completion
- Signature help
- Full-document semantic tokens

---

## Custom server requests

The bundled server also exposes:

- **`pfig/validateFile`** with `{"uri": "file:///path/config.pfig"}`: republishes
  diagnostics for an open document.
- **`pfig/getGraphViewModel`** with `{"uri": "file:///path/config.pfig", "mode": "reachable"}`:
  returns the graph model shared with the browser viewer. `mode` may be
  `reachable` (default) or `all`. The server prefers an open document's current
  text, falling back to the file on disk. Responses include `ok`, `nodes`,
  `edges`, `mode`, `entrypoint`, and `reachableUnavailable`; failures include
  an `error` message.

## Parsing with Index

```python
from pfig import parse_pfig_source, ParseResult

# Option 1: Parse and index in one call (recommended)
result: ParseResult = parse_pfig_source(
    source_text,
    filename="/path/to/config.pfig",
    with_index=True,
)
ast = result.ast
index = result.index

# Option 2: Build index from existing AST
from pfig import index_from_ast

index = index_from_ast(ast, filename, source_text, uri=None)
```

---

## DocumentIndex Structure

`index` is a `DocumentIndex` with:

| Field | Type | Description |
|-------|------|-------------|
| `binding_defs` | `dict[str, BindingDef]` | `name = expr` bindings |
| `external_defs` | `dict[str, BindingDef]` | `external name` definitions |
| `refs` | `list[RefInfo]` | `@provider` and dotted Python symbol refs |
| `imports` | `dict[str, ImportBinding]` | `import x`, `from x import y` |
| `includes` | `list[IncludeEdge]` | `include "path" as alias` |
| `call_sites` | `list[CallSite]` | `callee(...)` with kwarg ranges |
| `externals` | `list[str]` | Names of externals |
| `overrides` | `dict[str, dict]` | Override target → LSP Range |

### Sub-types

- **BindingDef**: `name`, `uri`, `range` (LSP Range)
- **ImportBinding**: `local_name`, `module`, `imported_name`, `range`, `module_range`
- **IncludeEdge**: `target_path`, `alias`, `range`
- **RefInfo**: `name`, `range`, `kind` (`"provider"` \| `"python_symbol"`)
- **CallSite**: `callee`, `callee_range`, `kwargs` (kwarg name → Range), `full_range`

---

## Semantic Tokens

The bundled language server advertises `semanticTokensProvider` with
full-document tokens. Delta and range semantic tokens are not implemented.

The semantic token legend is:

| Token type | Meaning |
|------------|---------|
| `pfigProvider` | Binding name whose compiled expression is a provider recipe, e.g. `model = Model(...)` |
| `pfigValue` | Binding or external name whose compiled expression is a value, e.g. `batch_size = 64` |
| `pfigProviderRef` | Identifier segments inside a provider reference, e.g. `model` in `@model` or `base` and `model` in `@base/model` |
| `pfigValueRef` | Identifier segments inside a value reference, e.g. `batch_size` in `@batch_size` or `consts` and `batch_size` in `@consts/batch_size` |
| `parameter` | Keyword argument name in a call, e.g. `hidden_size=` |
| `function` | Final callable segment in a call, e.g. `Linear` in `torch.nn.Linear(...)` |
| `namespace` | Dotted callable prefix segments, e.g. `torch` and `nn` in `torch.nn.Linear(...)` |

Semantic token classification is derived from the same parse/index and
`compile_pfig` validation pipeline used for diagnostics. Included references are
classified best-effort: the server uses an open included document when available,
otherwise it reads and indexes the included file from disk.

Reference tokens intentionally exclude the `@`, `/`, and `.` separators. A ref
like `@consts/input_size` emits separate semantic tokens for `consts` and
`input_size`, both with the same provider/value classification. Provider calls
such as `@loader(...)` are classified as provider reference tokens, not function
tokens. External definitions and references are classified as values.

Example source:

```pfig
external device
input_size = 784
model = dict(input_size=@input_size, device=@device)
copy = @model
```

Expected semantic tokens:

| Source range | Token type |
|--------------|------------|
| `device` in `external device` | `pfigValue` |
| `input_size` in `input_size = 784` | `pfigValue` |
| `model` in `model = dict(...)` | `pfigProvider` |
| `dict` | `function` |
| `input_size` in `input_size=` | `parameter` |
| `input_size` in `@input_size` | `pfigValueRef` |
| `device` in `@device` | `pfigValueRef` |
| `copy` | `pfigValue` |
| `model` in `@model` | `pfigProviderRef` |

Editor integrations can map the custom pfig token types to their preferred
highlight groups. For example, a Neovim configuration might link provider tokens
to constructor-style highlighting and value tokens to variable-style
highlighting.

---

## Node at Position

```python
from pfig import node_at_position

# pos: LSP Position {"line": int, "character": int} (0-based)
node = node_at_position(index, pos)

# Returns dict with kind, name, range, or None
# kind: "binding" | "external" | "import" | "ref" | "include" | "override" | "call_site" | "kwarg"
```

---

## Include Resolution

```python
from pfig import resolve_include_path

resolved = resolve_include_path(
    target_path="./consts.pfig",
    current_filename="/abs/path/to/main.pfig",
)
# Returns: absolute path string, or None if not found
```

---

## Provider Ref Resolution (Go-to-Definition)

```python
from pfig import resolve_provider_ref

# Pre-resolve includes (e.g. from compile result or your own resolution)
resolved_includes = {
    "./consts.pfig": "/abs/path/to/consts.pfig",
}

location = resolve_provider_ref(
    name="consts/epochs",  # or "epochs" for local
    current_filename="/abs/path/to/main.pfig",
    index=index,
    resolved_includes=resolved_includes,
)
# Returns: (uri, range) tuple, or None
# For overrides: returns override location first
```

---

## Diagnostics via compile_pfig

```python
from pfig import compile_pfig

compiled, issues = compile_pfig(
    ast,
    filename="/path/to/config.pfig",
)

for issue in issues:
    # ValidationIssue: code, message, binding?, position?, severity?
    # position is 1-based (line, column) - convert to LSP 0-based for display
    # severity: "error" | "warning" (optional)
```

`compile_pfig` returns issues for:

- Unresolvable include path, include cycle, self-include
- Import errors (module not found, cannot import name)
- Unknown binding, missing external, override target not found
- Name collision (import vs binding), shadowing, duplicate include alias

Import and include failures are returned as `ValidationIssue` entries, not raised
by `compile_pfig`. A failed compilation may return an empty compiled result.
Parse errors occur before compilation and should be handled separately.

---

## Include Graph

The compile result includes an include graph:

```python
compiled, issues = compile_pfig(...)
include_graph = compiled.get("include_graph", {})  # filename -> set of included filenames
```

---

## Example: Document Open

```python
def on_document_open(uri: str, content: str):
    result = parse_pfig_source(content, filename=uri_to_path(uri), with_index=True)
    return {"ast": result.ast, "index": result.index}
```

---

## Example: Go-to-Definition

```python
def goto_definition(uri: str, line: int, char: int):
    result = parse_pfig_source(content, filename=uri_to_path(uri), with_index=True)
    node = node_at_position(result.index, {"line": line, "character": char})
    if not node or node["kind"] != "ref":
        return None
    name = node["name"].lstrip("@")  # "@consts/epochs" -> "consts/epochs"
    return resolve_provider_ref(
        name, uri_to_path(uri), result.index, resolved_includes
    )
```

---

## Example: Diagnostics

```python
def get_diagnostics(uri: str, content: str):
    ast = parse_pfig_source(content, filename=uri_to_path(uri))
    compiled, issues = compile_pfig(ast, filename=uri_to_path(uri))
    return [
        {
            "range": to_lsp_range(issue.position),  # convert 1-based -> 0-based
            "message": issue.message,
            "severity": issue.severity or "error",
            "code": issue.code,
        }
        for issue in issues
    ]
```

---

## See Also

- [API Reference](api_reference.md) – Full Python API
- [Architecture](../developer/architecture.md) – Implementation overview
