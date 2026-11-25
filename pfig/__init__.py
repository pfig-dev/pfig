"""
pfig - A tiny, strict DSL for declarative object configuration.

Built for ML: deterministic configs, shared vs fresh semantics, and snapshot/diff
for reproducible runs.

Main API Functions
------------------
run()                       - Execute a pfig config file (simplest API)
instantiate()               - Instantiate specific bindings from a configuration
execute_pfig()              - Execute a parsed pfig configuration
validate_pfig()             - Validate a configuration without materialization
compile_pfig()              - Compile without requiring an entrypoint (for tooling)
parse_pfig_source()         - Parse pfig source code into an AST
parse_config_file()         - Parse a pfig configuration file into an AST
get_materialized_bindings() - Get materialized objects for testing/debugging

Snapshot Functions
------------------
create_snapshot()           - Create a snapshot of a run (IR graph + payload)
load_snapshot()             - Load a snapshot from disk
build_ir_graph()            - Build IR graph from compiled config

Example:
    Simple execution::

        from pfig import run
        result = run("config.pfig")  # Execute a config file

    With more control::

        from pfig import parse_pfig_source, execute_pfig

        config = '''
        from collections import OrderedDict
        data = OrderedDict(a=1, b=2, c=3)
        __entrypoint__ = @data.keys
        '''
        ast = parse_pfig_source(config)
        result = execute_pfig(ast)  # Returns odict_keys(['a', 'b', 'c'])
"""

# AST node types for advanced usage
from .ast_nodes import (  # Top-level statements; Expressions; Utilities
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
    LiteralExpr,
    OverrideStmt,
    PathExpr,
    PFigFile,
    ProviderRefExpr,
    TupleExpr,
    UnaryOpExpr,
)
from .execution import (
    ExecutionError,
    ProfileSpec,
    ValidationError,
    ValidationIssue,
    compile_pfig,
    execute_pfig,
    get_materialized_bindings,
    instantiate,
    run,
    validate_pfig,
)
from .fmt import FormatError, FormatOptions, format_source
from .helpers import entrypoint
from .lexer import LexerError

# LSP API (for pfig-vscode-syntax)
from .lsp import (
    DocumentIndex,
    ParseResult,
    index_from_ast,
    node_at_position,
    resolve_include_path,
    resolve_provider_ref,
)

# Core API exports
from .parser import ParseError, parse_config_file, parse_pfig_source

# Snapshot API
from .snapshot import (
    IntrospectionInfo,
    IntrospectionStatus,
    OpaqueReasonCode,
    build_ir_graph,
    check_introspection_warnings,
    create_snapshot,
    load_snapshot,
)

# All public exports
__all__ = [
    # Core functions
    "run",
    "parse_pfig_source",
    "parse_config_file",
    "index_from_ast",
    "node_at_position",
    "resolve_include_path",
    "resolve_provider_ref",
    "ParseResult",
    "DocumentIndex",
    "execute_pfig",
    "compile_pfig",
    "validate_pfig",
    "get_materialized_bindings",
    "instantiate",
    "entrypoint",
    # Snapshot functions
    "create_snapshot",
    "load_snapshot",
    "build_ir_graph",
    "check_introspection_warnings",
    # Snapshot types
    "IntrospectionStatus",
    "IntrospectionInfo",
    "OpaqueReasonCode",
    # Formatter
    "format_source",
    "FormatOptions",
    "FormatError",
    # Exception types
    "ParseError",
    "ExecutionError",
    "ValidationError",
    "ValidationIssue",
    "LexerError",
    # AST nodes
    "PFigFile",
    "ImportStmt",
    "FromImportStmt",
    "IncludeStmt",
    "ExternalStmt",
    "OverrideStmt",
    "Binding",
    "Expr",
    "LiteralExpr",
    "ListExpr",
    "TupleExpr",
    "DictExpr",
    "PathExpr",
    "DottedNameExpr",
    "ProviderRefExpr",
    "CallExpr",
    "BinaryOpExpr",
    "UnaryOpExpr",
    "BuiltinCallExpr",
]
