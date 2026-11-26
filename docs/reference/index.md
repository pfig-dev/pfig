# Reference

Complete reference documentation for pfig syntax, API, and command-line interface.

## Documentation Sections

- **[Syntax Reference](syntax_reference.md)** - Complete pfig language syntax, including bindings, providers, includes, externals, and expressions
- **[CLI Reference](cli.md)** - Command-line interface for view, validate, run, diff, fmt, bundle, replay, and install-rules
- **[API Reference](api_reference.md)** - Python API documentation for `instantiate()`, `execute_pfig()`, and `parse_pfig_source()`
- **[LSP API](lsp_api.md)** - API for LSP-based editors (parsing, indexing, go-to-definition, diagnostics, semantic tokens)
- **[Snapshot Format](snapshot_format.md)** - Specification of snapshot `log.json` and payload contents
- **[Troubleshooting](../guides/troubleshooting.md)** - Common issues and solutions

## Quick Links

### Syntax
- [The `@` Sigil](syntax_reference.md#the-sigil) - Understanding when to use `@` for references
- [Imports](syntax_reference.md#imports) - Importing Python modules and symbols
- [Includes](syntax_reference.md#includes) - Modularize configs with namespaces
- [Values](syntax_reference.md#values) - Constants and configuration values
- [Providers](syntax_reference.md#providers) - Lazy recipes for creating objects

### API
- [`instantiate()`](api_reference.md#pfig.instantiate) - Instantiate a specific binding
- [`execute_pfig()`](api_reference.md#pfig.execute_pfig) - Execute a configuration
- [`execute_pfig_with_metadata()`](api_reference.md#pfig.execution.execute_pfig_with_metadata) - Execute with full metadata
- [`parse_pfig_source()`](api_reference.md#pfig.parse_pfig_source) - Parse source code to AST

### CLI
- [`pfig view`](cli.md#view) - Open the browser graph viewer
- [`pfig diff-view`](cli.md#diff-view) - Compare compiled plans in the browser
- [`pfig validate`](cli.md#validate) - Validate configuration files
- [`pfig run`](cli.md#run) - Execute configurations
- [`pfig bundle`](cli.md#bundle) - Create portable config bundles
- [`pfig replay`](cli.md#replay) - Reproduce runs from snapshots
- [`pfig diff`](cli.md#diff) - Compare configurations
- [`pfig fmt`](cli.md#fmt) - Format `.pfig` files to a canonical style
- [`pfig install-rules`](cli.md#install-rules) - Install AI agent authoring rules

### Snapshots
- [Snapshot Format](snapshot_format.md) - `log.json` schema and payload structure
- [IR Graph](snapshot_format.md#graph-object) - Resolved configuration representation
