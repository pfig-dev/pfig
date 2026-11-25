# API Reference

This reference is automatically generated from docstrings in the source code.

`validate_pfig()` does not materialize providers or execute the entrypoint, but it does import Python modules while resolving configuration imports. Treat validation like running trusted code when imports are involved.

## External requirement scope

Execution and `validate_pfig()` require only the selected entrypoint's effective
inputs; `run()` also checks an enabled `__snapshot__` directive before construction.
`instantiate()` checks its requested binding after fresh-call kwargs apply.
Unused declarations and supplied unused values are retained. Supplying `None`
satisfies a declaration.

Rootless `compile_pfig()` reports missing inputs across all declarations, even
when a default entrypoint exists. `allow_missing_externals=True` suppresses
missing-input diagnostics for incomplete validation or packaging; it does not
permit materialization of unresolved dependencies.

## Core Functions

Python `run()` creates snapshots only when `snapshot_dir` is supplied or the root
config defines `__snapshot__`; unlike CLI `pfig run`, it does not default to
`./snapshots`. Python `run()` can evaluate computed snapshot directives.

::: pfig.run

::: pfig.instantiate

::: pfig.execute_pfig

::: pfig.validate_pfig

::: pfig.compile_pfig

::: pfig.execution.execute_pfig_with_metadata

::: pfig.parse_pfig_source

::: pfig.parse_config_file

::: pfig.get_materialized_bindings

---

## LSP API (for editor integration)

::: pfig.index_from_ast

::: pfig.node_at_position

::: pfig.resolve_include_path

::: pfig.resolve_provider_ref

::: pfig.ParseResult

::: pfig.DocumentIndex

See [LSP API Reference](lsp_api.md) for a full integration guide.

---

## Snapshot Functions

::: pfig.snapshot.create_snapshot

::: pfig.snapshot.load_snapshot

::: pfig.snapshot.build_ir_graph

::: pfig.check_introspection_warnings

::: pfig.IntrospectionInfo

::: pfig.IntrospectionStatus

::: pfig.OpaqueReasonCode

---

## Formatter

::: pfig.format_source

::: pfig.FormatOptions

See the [CLI reference](cli.md#fmt) for style rules and the `pfig fmt` command-line interface.

---

## Data Classes

::: pfig.execution.ExecutionMetadata

::: pfig.ProfileSpec

::: pfig.ValidationIssue

---

## Helpers

::: pfig.entrypoint

`entrypoint(fn, **kwargs)` is a Python helper. Its `fn` parameter is positional-only
and cannot currently be supplied directly in a `.pfig` call. For the DSL, expose
a keyword-friendly Python factory returning a zero-argument callable; see
[Wiring a plain function](../guides/patterns.md#wiring-a-plain-function).

---

## Exceptions

::: pfig.ExecutionError

::: pfig.ValidationError

::: pfig.ParseError

::: pfig.LexerError

::: pfig.FormatError
