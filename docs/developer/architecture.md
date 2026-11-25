# Architecture & Internals

This guide explains pfig's internal architecture and implementation details.

## Architecture Overview

pfig follows a classic compiler architecture. The execution flow:

1. **Parse**: Source code → AST via lexer + parser
2. **Compile**: AST → Internal representation (dependency graph, provider specs)
3. **Plan**: Find all providers reachable from the entrypoint (backwards traversal)
4. **Execute**: Materialize objects in dependency order, starting from the entrypoint
5. **Run**: Call the `__entrypoint__` binding

The sections below provide implementation details for each component.

## Key Classes

```python
# AST Nodes (ast_nodes.py)
PFigFile          # Root node containing all statements
Binding           # name = value (provider or value binding)
ProviderRefExpr        # @name
ProviderMethodRefExpr  # @name.attr[.attr...]
CallExpr               # @name() or function(kwargs)
IncludeStmt       # include "file" as alias
ExternalStmt      # external name
OverrideStmt      # override path = value

# Execution Engine (execution.py)
ProviderSpec      # Recipe for building objects (callable + args/kwargs)
ProviderRef       # @name (shared reference)
ProviderMethodRef # @name.attr[.attr...] attribute or method chain
FreshProviderCall # @name() (fresh instance call)
ASTCompiler       # Compiles AST to executable form
Materializer      # Converts specs to live Python objects
```

## Core Components

### 1. **Lexer** (`pfig/lexer.py`)

**Purpose**: Tokenizes pfig source code into a stream of tokens

**Key Features**:

- Handles pfig-specific syntax like `@`, provider references, includes
- Supports Python literals (strings, numbers, booleans, None)
- Manages keywords: `import`, `from`, `include`, `external`, `override`
- Position tracking for error reporting (line, column)

**Output**: List of `Token` objects with position tracking

**Implementation Notes**:

- Character-by-character parsing with `peek()` and `advance()` methods
- Maintains position information (line, column) for all tokens
- Handles string escaping, `#` line comments, and number parsing

### 2. **Parser** (`pfig/parser.py`)

**Purpose**: Transforms tokens into an Abstract Syntax Tree (AST)

**Architecture**: Recursive descent parser implementing the formal pfig grammar

**Key Grammar Elements**:

- Import statements: `import torch` or `from torch import nn`
- Include statements: `include "./base.pfig" as base`
- Bindings: `model = MyModel(hidden_size=512)`
- Provider references: `@model` (shared) or `@model()` (fresh)
- External declarations: `external device`
- Overrides: `override base/param = value`

**Implementation Notes**:

- Recursive descent parsing with lookahead
- Error reporting with source positions (parsing stops on an error)
- AST node creation with position tracking

### 3. **AST Nodes** (`pfig/ast_nodes.py`)

**Purpose**: Defines the structure of parsed pfig configurations

**Key Node Types**:

- `PFigFile`: Root node containing all statements
- `Binding`: Variable assignments and provider definitions
- `ProviderRefExpr`: A shared reference, `@model`
- `ProviderMethodRefExpr`: An attribute or method chain, `@model.encoder.weight` or `@trainer.fit`
- `CallExpr`: Function/constructor calls with arguments
- Various expression types for literals, containers, operations

**Design Principles**:

- AST nodes are data structures only (no business logic)
- All nodes track source positions for error reporting
- Nodes are treated as immutable after creation (though not enforced by frozen dataclasses)

### 4. **Execution Engine** (`pfig/execution.py`) - **The Heart of pfig**

**Purpose**: Materializes AST into live Python objects

**Key Responsibilities**:

- **Dependency Resolution**: Builds dependency graphs between providers
- **Shared vs Fresh Semantics**: `@provider` (memoized) vs `@provider()` (new instance)
- **Provider Instantiation**: Constructs objects starting from the entrypoint, tracing backwards through dependencies
- **Include System**: Handles modular configuration with namespacing
- **External Resolution**: Manages runtime environment parameters
- **Override System**: Applies configuration layering

**Implementation Notes**:

- Entrypoint-driven execution: uses `find_reachable_bindings()` to trace backwards from entrypoint, only materializing reachable objects
- Lazy evaluation: providers are instantiated only when referenced in the dependency graph
- Memoization for shared instances (`@provider`) via `shared_cache` dictionary
- Dependency cycle detection: `topological_sort()` detects and reports cycles in dependency graph

### 5. **CLI Interface** (`pfig/cli.py`)

**Purpose**: Provides command-line interface

**Commands**:

- `pfig view config.pfig` - Open the browser graph viewer
- `pfig validate config.pfig` - Validate without provider materialization (imports still execute)
- `pfig run config.pfig` - Snapshot and execute a configuration
- `pfig bundle config.pfig` - Package portable config files
- `pfig diff config1.pfig config2.pfig` - Compare two configurations
- `pfig fmt config.pfig` - Canonically format configs
- `pfig replay prepare|validate|run SNAPSHOT` - Restore and replay a snapshot
- `pfig install-rules cursor|claude|codex` - Install AI authoring rules

**Common Options**:

- `--entrypoint <binding[.attr...]>` - Override entrypoint with a binding or attribute path
- `--set key=value ...` - Set externals or replace bindings; supply multiple pairs in one occurrence
- `--profile profile.pfig` - Load external overrides from profile file (can be used multiple times)
- `--verbose` / `-v` - Global verbosity flag, before the command (`pfig -vv run ...`)

**Implementation Notes**:

- Uses `argparse` for argument parsing with subcommands
- Handles file I/O and error reporting
- Integrates with execution engine
- Parses CLI values using pfig syntax (supports lists, dicts, quoted strings, numbers)

## File Structure

```
pfig/
├── __init__.py     # Public API exports
├── __main__.py     # CLI entry point
├── ast_nodes.py    # AST node definitions
├── cli.py          # Command-line interface
├── diff.py         # Configuration diffing
├── execution.py    # Core execution engine
├── helpers.py      # Small helpers (e.g., entrypoint)
├── lexer.py        # Tokenization
├── logging.py      # Logging utilities
├── parser.py       # AST parsing
├── snapshot.py     # Snapshotting for reproducibility
├── bundle.py       # Portable config bundles
├── fmt.py          # Canonical formatter
├── includes.py     # Explicit filesystem include resolution
├── fs.py           # Local and optional remote filesystem I/O
├── _remote_fs.py   # Remote filesystem adapters
├── replay/         # Workspace restoration, digest checking, replay
├── lsp/            # Optional language server and graph view model
├── lsp_index.py    # Structural document index
├── lsp_utils.py    # LSP coordinate and URI helpers
├── vis/            # Browser graph server and packaged frontend assets
├── rules/          # Canonical AI agent authoring rules
└── utils.py        # Utility functions
```

## Learning Path

Suggested order for understanding pfig internals:

1. **Examples** (`examples/hello_world.pfig`, `examples/mnist_mlp/`) - See pfig in action
2. **AST Nodes** (`pfig/ast_nodes.py`) - Understand the data structures
3. **Main API** (`pfig/__init__.py`) - See what's exposed publicly
4. **Parser** (`pfig/parser.py`) - How source code becomes AST
5. **Execution** (`pfig/execution.py`) - How AST becomes Python objects
6. **Lexer** (`pfig/lexer.py`) - Optional: tokenization details

## Further Reading

- [Development Workflow](development_workflow.md) - How to work on pfig
- [API Reference](../reference/api_reference.md) - Auto-generated from docstrings
- [Developer Guide](developer_guide.md) - Overview of all developer resources

