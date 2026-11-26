Language & Implementation Specification
======================================

This document describes the current implementation, rather than a roadmap of
proposed features. See also the [Syntax Reference](../reference/syntax_reference.md),
[CLI Reference](../reference/cli.md), [Python API](../reference/api_reference.md),
and [Snapshot Format Reference](../reference/snapshot_format.md).

## 1) Scope and principles

pfig is a small DSL for wiring Python object graphs: constructors, keyword
arguments, references, includes, and explicit overrides. It separates algorithm
configuration from runtime environment inputs.

- Wiring only: no conditionals, loops, comprehensions, lambdas, or instance method calls.
- Explicit namespaces: includes require aliases; config references use `@`.
- Lazy providers: constructor/function calls describe recipes, not eager objects.
- Fail-fast validation: imports, signatures, references, and dependency cycles are checked.
- Trusted input: imports and materialization execute Python code. Purity of helpers
  is a convention, not a sandbox or runtime guarantee.

## 2) Statements

```pfig
import myproject.models as models
from myproject.train import Trainer
include "./constants.pfig" as constants
external device

model = models.Model(width=@constants/width)
override constants/batch_size = 128
trainer = Trainer(model=@model, device=@device)
__entrypoint__ = @trainer.fit
```

Bindings have bare identifier names. Slash-separated paths identify bindings
inside include namespaces; dots without `@` identify Python symbols from imports.

### Imports

`import module [as alias]` and `from module import name [as alias], ...` populate
the Python symbol table. Imports and config bindings may not collide in the same
namespace. Python symbols are referenced without `@`.

### Includes

`include "path" as alias` loads a config into an explicit namespace. Refer to its
bindings as `@alias/name`, including nested paths such as `@base/constants/batch`.
Relative paths resolve against the including file. Include cycles and duplicate
aliases are errors. The same source can be included under distinct aliases.
Those aliases identify the same logical bindings, including values, providers,
and declared externals; they are not independent configuration instances.

Each file must include the dependencies it references. References resolve within
that file's bindings, declared externals, and explicit include namespaces. A
parent cannot supply an otherwise unresolved sibling name. This applies inside
containers, arithmetic, attributes, fresh calls, and override expressions.

Includes use explicit filesystem paths, with no search paths, mount table, or
implicit Python-package config discovery. For cross-project reuse, copy/vendor
configs into the project and include them by relative path.

### Overrides

`override path = expr` replaces an existing whole binding. It cannot modify a
field or a provider kwarg in place. To tune nested values, expose them as bindings
in the included config, then override the slash-separated binding path.

```pfig
include "./base.pfig" as base
override base/learning_rate = 0.002
loader = @base/loader(num_workers=8)
```

Overrides resolve before reachability analysis or construction. An override
through any alias replaces the effective definition through every alias of the
same source binding. Shared provider identity is preserved, including when a
value is replaced by a provider or a provider by a value.

Precedence follows inclusion ancestry: an including ancestor's override wins
over overrides authored in its dependencies. Siblings are incomparable, even
when one include path is deeper. Include order, binding order, override order,
and alias spelling do not establish precedence.

A file may author at most one override per canonical binding. Repeating the same
path or targeting another alias is an error, even with identical expressions or
an overriding ancestor. Multiple incomparable sibling overrides also require an
explicit overriding ancestor, even when identical; otherwise they are errors at
the selected graph root. Reaching the same authored declaration through several
include paths is not a duplicate. Override expressions are not compared for equality.
Conflicts are invalid even for unreachable bindings. Diagnostics identify source
locations, alias paths, and the canonical target.

Override expressions resolve in their author's lexical scope. Effective dependency
cycles, including self-references, are errors. Use a separate binding for a fresh
provider variant: `override loader = @loader(...)` does not retain the old recipe.
Fresh calls create distinct objects but continue sharing referenced dependencies.

## 3) Values and expressions

Supported literals are integers (including underscore separators), floats,
strings, booleans (`True`, `False`), and `None`. Containers are lists, tuples, and
dictionaries. Expressions support `+`, `-`, `*`, `/`, `//`, `**`, unary `-`, and
parenthesized grouping. String concatenation uses `+`.

The four expression builtins `len`, `min`, `max`, and `range` accept positional
syntax, for example `range(3)` or `max(1, 2)`. Ordinary Python/provider calls
accept **keyword arguments only**:

```pfig
from decimal import Decimal
amount = Decimal(value="3.14")
count = len([1, 2, 3])
```

`args=[...]` is an ordinary keyword, not a positional-argument escape hatch.
Wrap positional-only Python callables in keyword-friendly Python adapters.

Values and expressions are compiled into intermediate representations and
materialized when needed. Imported helpers may be called as named bindings or
inside kwargs; they should be side-effect free, but this is not enforced.

## 4) Providers and references

| Form | Meaning |
|------|---------|
| `name = Class(k=v)` | Define a lazy provider recipe |
| `@name` | Shared instance, memoized within the execution/materializer |
| `@name()` | Fresh construction from the recipe |
| `@name(k=v)` | Fresh construction with kwarg overlays |
| `@name.attr` | Attribute value or bound method on the shared instance |
| `@name.a.b.c` | Nested attribute chain on the shared instance |
| `@alias/name.attr` | Attribute reference in an included namespace |
| `module.Symbol` | Imported Python symbol passed by reference |

Attribute chains may have any depth. They are evaluated using Python `getattr`,
reading each segment once in order; properties may execute Python code. The final
value may be a plain attribute or callable. `@name.attr(...)` is invalid; use
Python helpers for method invocation.

Providers may be partial (missing required kwargs) when they are completed by
fresh calls. Using an incomplete provider as shared, including through a method
reference, is a validation error.

Fresh calls can follow provider aliases or earlier fresh variants; overlays are
combined along the recipe chain. Freshness concerns the provider construction,
not a deep copy of its shared dependencies.

## 5) Externals and profiles

`external path` declares a runtime input. Supply it using `--set`, Python
`externals`, or profiles. Normal configs cannot override declared externals.
Algorithm parameters should generally be ordinary values; environment settings
such as device and output directory should be externals.

Profiles are `.pfig` files loaded through `--profile` or `profile_paths`. They may
supply external overrides, not ordinary bindings or algorithm-value overrides:

```pfig
# profiles/local.pfig
override device = "cpu"
override output_dir = "./runs"
```

Later profiles win. Runtime `--set`/`externals` values take precedence over profiles.

### Profile prefixes

`--profile train::profiles/local.pfig` applies each override relative to `train/`.
Multi-segment prefixes such as `train/model::profiles/local.pfig` are supported.
Without `::`, overrides apply at the root.

A declared external included under several aliases is supplied once through any
alias. The supplied value applies to every alias. Ordered profiles retain their
explicit precedence: later profiles win across canonical aliases, then API
`externals` or CLI `--set` inputs win. Conflicting assignments through aliases in
the same input layer are errors, not insertion-order precedence. A profile may
replace values inherited from included profiles. Repeating an override path
within the same profile file is an error even when the expressions match.
The same canonical resolution applies to ordinary bindings supplied through
`externals` or `--set`; profiles remain restricted to declared externals.

Unlike config override declarations, equal runtime assignments through aliases
coalesce. Runtime equality preserves literal types and compares builtin lists,
tuples, and dictionaries structurally, including cyclic containers. Opaque Python
objects, including container subclasses such as `defaultdict`, compare only by
identity; user-defined equality is not invoked.

Every effective profile override must target a declared external. An API/CLI
assignment through any canonical alias shadows the profile assignment before
this restriction is checked; unknown targets and same-layer conflicts still fail.
If an unprefixed profile matches externals only inside included namespaces, pfig can
suggest explicit prefixed commands; it never silently retargets a profile.
Python callers can use `pfig.ProfileSpec(path=..., prefix=("train",))`.

## 6) Include portability

Use relative filesystem includes to preserve directory structure when configs
are bundled and relocated. Python imports locate code, not `.pfig` files.

Mount statements, CLI `--mount`, Python `mounts` arguments, `__mounts__`, and
protocol-based config discovery have been removed. Replace legacy protocol
includes with explicit paths and recreate bundles containing mount mappings.
New bundles use manifest version 2; filesystem-only version 1 bundles remain
readable.

## 7) Compilation, validation, and execution

1. Parse source into AST nodes with source positions.
2. Load profiles as ordered input layers; resolve Python imports and includes.
3. Compile unresolved modules and collect canonical bindings and override candidates.
   Resolve one effective definition per canonical binding using inclusion ancestry,
   then apply runtime input layers. Project aliases into the execution graph only
   after resolution; cached modules remain independent of root inputs.
4. Resolve the entrypoint or requested binding and find its reachable subgraph.
5. Check required externals and kwargs for the effective evaluation graph before construction.
   Fresh calls use merged arguments (outermost overlay wins), not shared base instances.
   Whole-file structural checks, including unknown references and cycles, remain active.
6. Materialize reachable values/providers, memoizing shared providers.
7. For execution APIs, call the entrypoint with no arguments.

Compilation still processes the whole config and imports; reachability is not a
promise that unrelated syntax/import/compile-time errors are ignored.

`validate_pfig()` performs validation without constructing providers or calling
the entrypoint. It **does import Python modules**, which may execute module-level
code. `compile_pfig()` supports tooling without requiring an entrypoint and
returns compiled data plus validation issues.

Declarations identify inputs; the selected operation determines which are required.
`validate` checks the selected entrypoint, including an explicit override.
`run` also checks enabled runner roots such as `__snapshot__` before constructing
either root. `instantiate()` checks the requested binding after fresh kwargs apply.
Unused declarations remain available to profiles, bundles, and tooling; supplied
unused inputs remain in provenance. Supplied `None` counts as a value.
Any canonical alias can satisfy an input; missing inputs produce one diagnostic
per canonical declaration.

Rootless `compile_pfig()` instead reports missing inputs across **all declarations**,
without choosing an entrypoint. `allow_missing_externals=True` suppresses those
diagnostics or permits incomplete targeted validation, never execution with
unresolved dependencies. Partial providers may be completed by fresh overlays;
replaced arguments are neither required nor eagerly constructed. A separate shared
use of the base provider still requires its original arguments.

`strict=True` / CLI `validate --strict` promotes supported validation warnings,
such as uninspectable signatures, to errors. Checks that rely on introspection
are best-effort for builtins and C extensions.

Validation collects multiple issues where possible, but not every failure is
aggregated: some parsing, import, profile, and runtime failures stop processing.

## 8) Entrypoints

`__entrypoint__` must resolve to a zero-argument callable: an imported function,
a callable object, or a bound method reference.

```pfig
fit = @trainer.fit
__entrypoint__ = @fit
```

`--entrypoint fit` selects an existing binding, overriding `__entrypoint__`.
It may also select an attribute path on a binding (for example,
`--entrypoint trainer.fit`); pfig resolves the longest binding-name prefix and
walks the remaining attributes. It does not resolve arbitrary Python import
paths. With no directive or explicit entrypoint, execution and validation fail.

For plain functions requiring arguments, write a keyword-friendly Python adapter
that returns a callable, for example `functools.partial(train, model=model)`.
`pfig.entrypoint(fn, **kwargs)` is available to Python callers, but its positional-only
`fn` parameter cannot currently be supplied directly from the keyword-only DSL.

## 9) CLI

Commands: `view`, `diff-view`, `validate`, `run`, `bundle`, `diff`, `fmt`, `replay`, `install-rules`.
See the [CLI Reference](../reference/cli.md) for flags and per-command exit codes.

Global verbosity precedes the command: `pfig -v run config.pfig`.

`--set` accepts space-separated `key=value` arguments in one occurrence.
Repeating the flag replaces its earlier arguments; commas are not pair
separators. `--profile` accepts multiple paths and can be repeated.

`--set` parses values independently using DSL expressions/literals, falling back
to strings such as `cuda`. It can supply externals or replace existing bindings
with runtime values. It does not resolve recipes such as `@model(width=32)`
against the config namespace. Unknown target names are errors.

`diff` compares two `.pfig` configurations using the same `--set` values for
both. It does not accept `--profile` or snapshot JSON files. For
offline graph comparison use `pfig.diff.compare_ir_graphs` in Python. Config
diffing compares compiled provider/value representations, not materialized
objects; do not assume algebraically equivalent expressions normalize equally.

`bundle` discovers config files without resolving Python imports or materializing
providers. It does not require all external values. Bundles carry config files
and profile files, not Python project code or installed packages.

## 10) Snapshots and replay

Snapshots provide **best-effort reproducibility**, not identical results or a
capture of all runtime state. The payload contains a portable config bundle,
`pip freeze` lock file, and available Git patches. `log.json` records metadata,
environment, a compiled wiring graph, and warnings.

CLI `run` writes a snapshot after successful validation and **before** entrypoint
execution. Providers needed to compute the destination are constructed first. Destination precedence is `--snapshot`,
then a supported `__snapshot__` value, then `./snapshots`. A timestamped child
directory is created. `--no-snapshot` disables writing.

Both runners can evaluate computed `__snapshot__` directives and retain shared
identity across snapshot and entrypoint phases. An explicit destination or disabled
snapshotting bypasses the directive and its input requirements. Python `run()`
snapshots only when `snapshot_dir` is supplied or a root `__snapshot__` binding exists.

The IR records named bindings at the top level, with `$ref` edges, method refs,
`$import` symbols, fresh-provider overlays, and inline anonymous providers.
Python defaults are included where signatures can be inspected. It is a
serialized **compiled graph**, not a fully evaluated dump of live objects.
The digest hashes only the graph, not timestamps or environment metadata.
Remote destinations currently receive `log.json` only, without replay payloads;
write locally and upload the directory for replayable remote archives.
See the [format reference](../reference/snapshot_format.md) for exact fields.

`replay prepare` restores code, patches, configs, and dependencies in a workspace.
`replay validate` compares the recompiled baseline digest with the snapshot;
intentional `--set` overrides are reported separately. Recorded inputs and replay
`--set` overrides are separate precedence layers: an override through either alias
replaces the recorded canonical value. Validation and execution use the same
resolved inputs.

Snapshot schema `2.0` records graphs under canonical, declarative override
semantics. Current replay rejects older or unknown schema versions: replay older
snapshots with their recorded pfig version. Older logs remain readable for
inspection with `load_snapshot`; they are not silently reinterpreted as identical
experiments. Canonical source IDs used during compilation are not serialized as
host-specific semantic identities. Independent statement reordering preserves the
effective graph digest; source and bundle byte hashes may still change.

`replay run` prepares, validates, and executes. Git LFS contents, submodules, external data/services,
and non-deterministic Python behavior may require manual intervention.

## 11) Python API — subgraph instantiation

```python
from pfig import instantiate

model = instantiate("model", path="train.pfig", externals={"device": "cpu"})
loader = instantiate("loader", path="train.pfig", call_kwargs={"num_workers": 8})
```

`instantiate(name, path=None, source=None, *, fresh=False, call_kwargs=None,
externals=None, profile_paths=None, strict=False)` requires exactly
one of `path` and `source`. It does not require or execute an entrypoint.

Only reachable objects are materialized. Shared instances are memoized within a
single call, not across separate `instantiate()` calls. Supplying `call_kwargs`
implies fresh construction for a provider. Profiles apply in order, with runtime
externals taking precedence. Debug logging is available; there is no public
`instantiate_with_trace()` API.

## 12) Errors and implementation map

Public error classes are `LexerError`, `ParseError`, `ExecutionError`,
`ValidationError` (with `ValidationIssue` objects), and `FormatError`. Source
positions and suggestions are included where available. Specialized labels such
as “missing external” or “signature error” describe issue categories, not separate
public exception classes.

| Module | Responsibility |
|--------|----------------|
| `lexer.py`, `parser.py`, `ast_nodes.py` | Tokens, parsing, AST |
| `execution.py` | Compilation, validation, reachability, materialization, execution |
| `includes.py`, `fs.py`, `_remote_fs.py` | Filesystem include resolution and local/remote I/O |
| `bundle.py`, `snapshot.py`, `replay/` | Portable configs and reproducibility |
| `diff.py`, `fmt.py` | Semantic comparison and canonical formatting |
| `cli.py` | CLI argument parsing and command handlers |
| `lsp/`, `lsp_index.py`, `lsp_utils.py` | Editor server and indexing/resolution APIs |
| `vis/`, `webview/` | Browser graph server, packaged assets, frontend source |

See [Architecture](architecture.md) for further implementation details.
