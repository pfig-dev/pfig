# CLI Reference

pfig provides these commands: `view`, `validate`, `run`, `bundle`, `diff`, `diff-view`, `fmt`, `replay`, and `install-rules`.

## Global options

Use `pfig --version` to print the installed version. Verbosity is a global option
and must precede the command: `pfig -v run config.pfig` or `pfig -vv validate config.pfig`.

`--set` accepts space-separated `KEY=VALUE` arguments. Values use pfig literal
syntax (including lists and dictionaries), falling back to unquoted strings such
as `cuda`. It can supply externals or replace existing bindings with runtime
values; it does not evaluate references to other config bindings.

`--profile` accepts multiple paths and may be repeated; later profiles win, and
`--set` wins over profiles. `--set` accepts multiple values in one occurrence;
use one occurrence of the flag to avoid losing earlier values.

## view

The browser graph viewer is included in the standard `pfig` install. Open it for a configuration:

```bash
pfig view config.pfig
```

Show every binding instead of only bindings reachable from `__entrypoint__`:

```bash
pfig view config.pfig --mode all
```

Start the local server without opening a browser automatically:

```bash
pfig view config.pfig --no-open
```

Enable source navigation through an editor callback:

```bash
pfig view config.pfig --reveal-url http://127.0.0.1:9000/reveal
```

In editor-integrated views, source navigation sends a GET request to the callback
with `uri`, `line`, and `character` query parameters. If the callback URL already
has query parameters, pfig appends these values with `&`.

Without `--reveal-url`, clicking a reference zooms to its target binding
instead of opening a local `file://` URL. The viewer opens the target's file
in Overview and reveals the target if it was folded in Compact mode.
Other open files stay open; clicking a reference does not enter Solo.

**Overview** shows one card per source file, labeled with its directory
and filename. Files in the same folder share a stripe and fill color
that stay the same when expanded. The **pfig files** legend shows examples.
Click a file to expand and select it. Its bindings appear
inside a labeled container. Click **−** in its header to collapse it.
Expanding or collapsing a file or binding card keeps its position on screen
and preserves zoom. Double-click a file or binding card to fit it in the viewport.
Select an expanded file or binding to emphasize its connections. Edges stay behind cards unless you hover over or select an edge.
Click the canvas to clear the selection.
Each binding has one output port. References of the same kind to the same
input share an edge. Hover over or select an edge to see which binding supplies
the input and whether it is a shared reference or a fresh call. Reference counts
exclude duplicate include aliases. You can still search for bindings by alias.
Fresh-instance cards have dashed borders. A number on a dashed arrow counts
fresh calls to the same input. Numbers on
arrows to or from collapsed files count their bundled references. Overrides
stay with their original file; their binding cards show the override site.
Use **Expand all** or **Collapse all** to open or close all files and fit the
resulting graph in the viewport.
Choose **Bindings only** to see every visible binding and its direct connections
without the file cards. It uses the same layout as Overview with all files
expanded. Switching views preserves open files and argument settings. Large
graphs open in Overview. Compact mode can hide files that contain only folded
values. Use **Kwargs** to collapse or expand all
provider and fresh-instance cards. Click a collapsed card to see its arguments.
Click **−** in the header to collapse it again. When arguments are hidden, their
edges share one input beside the hidden-argument count. If one binding supplies
several hidden arguments, hover over or select its edge to see each argument.
Expanding the card restores the separate argument edges. A fresh instance keeps
a separate dashed edge beside its base-provider reference, even when the
provider is in a collapsed file. Hover over a truncated argument
to read the full preview. Numeric expressions show their results when the viewer
can calculate them from config values without running providers.

Open **⋯** beside a card's name to solo it with its **Dependencies**,
**Dependents**, or **Both directions**. All three keep the selected card. File Solo follows the bindings in that file
and shows the files they reach in the chosen direction.
**Find a binding** shows the selected binding and its dependencies.
Select the active option again or press **Esc** to return to the full view.
To inspect literal values as separate cards, change **Values**
from **Compact** to **Separate cards**. List and tuple cards show the item
count and a numbered preview, including arguments for fresh calls.

`pfig view` accepts the same graph-relevant inputs as validation and execution,
including `--profile`, `--set`, `--strict`, and
`--allow-missing-externals`. `--strict` treats warnings as errors;
`--allow-missing-externals` allows inspecting incomplete configs. Both flags also
work with `diff-view` and `validate`, but not `run`.

| Option | Description |
|--------|-------------|
| `--uri URI` | Alternative to the positional config path; accepts a `file://` URI |
| `--mode reachable\|all` | Graph scope (default: `reachable`) |
| `--host HOST` | Bind address (default: `127.0.0.1`) |
| `--port PORT` | Bind port (default: `0`, chooses an available port) |
| `--no-open` | Do not open a browser automatically |
| `--reveal-url URL` | Editor callback for source navigation |

## diff-view

Compare two compiled plans in the graph viewer:

```bash
pfig diff-view base.pfig proposed.pfig
```

The viewer matches binding identities the same way as [`pfig diff`](#diff).
Use `--align-namespace base=/` when a namespace move also changed the authored
identities, or `--no-align-namespace` to compare exact paths.

The proposed graph shows added and modified bindings and changed fields.
Collapsed cards show only changed argument rows. Expand a card to see every
argument, including unchanged ones. Old-only arguments appear beneath a
divider. If the replacement callable's signature rules out an old keyword
argument, it appears under **Not accepted by** the new callable. Otherwise it
appears under **Removed arguments**.
Removed bindings and old wires start hidden. Use **Show removed**, the
removal count menu, or a crossed-out reference to explore old wiring.
The **Scope** control counts changes reachable in either plan; a binding
that only changes reachability is not itself added or removed. If either
plan has no usable entrypoint, the viewer shows all bindings.

Each plan resolves includes from its own configuration. Both receive the
same explicit `--profile` and `--set` inputs; profile paths resolve from
the launch directory. Neither plan inherits inputs from the other.
Refresh recompiles both plans without running providers or the entrypoint.

Like `pfig diff`, this command takes the base first and the proposed configuration second.
It accepts the same server, scope, and runtime options as [`pfig view`](#view).

## validate

Validate a configuration file without executing it:

```bash
pfig validate config.pfig
```

Validate with external parameters:

```bash
pfig validate config.pfig --set device=cuda epochs=20
```

This checks:

- Syntax correctness
- Externals required by the selected entrypoint are provided (unless `--allow-missing-externals`)
- Provider dependencies resolve correctly (no unknown bindings)
- No circular dependencies
- Provider kwarg validation (unknown/missing required kwargs)
- Entrypoint resolves to a callable

`pfig validate` does **not** materialize providers or call the entrypoint. It **does** resolve `import` statements via Python's import system, so imported modules may run module-level code during validation.

`--entrypoint` changes the validation root. Unused declarations need no values;
syntax, imports, unknown references, overrides, and conflicts remain checked.
Incomplete validation does not authorize running with unresolved inputs.

## run

Execute a configuration:

```bash
pfig run config.pfig
```

### Setting External Parameters

Provide external values via `--set`:

```bash
pfig run config.pfig --set device=cuda data_dir=/path/to/data
```

Multiple values:
```bash
pfig run config.pfig --set device=cuda epochs=20 learning_rate=0.001
```

### Using Profiles

Apply a profile file:

```bash
pfig run config.pfig --profile profiles/gpu.pfig
```

Profiles may only override **declared externals** (they cannot override normal bindings). You can still override profile-provided external values via `--set`:

```bash
pfig run config.pfig --profile profiles/gpu.pfig --set device=cpu
```

#### Profile application prefixes

A profile may be applied relative to a binding namespace using the `<prefix>::<profile-path>` syntax:

```bash
pfig run train_lora_r16.pfig --profile train::profiles/local.pfig
```

When a profile has an application prefix, every `override` path inside the profile is interpreted relative to that prefix. For example, if `profiles/local.pfig` contains:

```pfig
override output_dir = "./runs/local"
override bf16 = False
```

then `--profile train::profiles/local.pfig` applies those overrides as `train/output_dir` and `train/bf16`.

This is useful when a config can be run both as a root config and as an included module — the same profile file works in both cases by choosing the appropriate prefix at the call site.

The prefix must be a valid pFig path (`IDENT` or `IDENT/IDENT/…`). Multi-segment prefixes are supported:

```bash
pfig run config.pfig --profile train/model::profiles/model_env.pfig
```

After prefixing, all standard profile validation rules apply: each effective override path must resolve to a declared external. Profiles still cannot override ordinary values or providers.

Without an explicit prefix, a profile is applied at the root namespace, preserving existing behavior:

```bash
pfig run train.pfig --profile profiles/local.pfig
```

#### Missing-prefix suggestions

When a root-applied profile fails because its overrides do not match any root external, pFig checks whether the overrides match externals under an included namespace and, if so, suggests the corrected command. For example:

```
Profile profiles/local.pfig was applied at the root, but these overrides do not
match root externals:

  bf16       -> train/bf16
  output_dir -> train/output_dir

They do match externals under 'train/'.

Did you mean:

  --profile train::profiles/local.pfig
```

If the overrides match externals under multiple namespaces (e.g. the same config is included twice under different aliases), pFig lists all candidate commands and leaves the choice to you. pFig never silently retargets a profile.

### Specifying Entrypoint

By default, pfig uses `__entrypoint__` from the config. `--entrypoint` overrides it. The value is a binding name or an attribute path rooted at a binding. pfig takes the longest prefix that names a binding, then walks the remaining attributes using the same rules as `@provider.attr.attr`; the last attribute must be callable. Namespaced bindings keep their `/`.

```bash
# Select a binding or a callable attribute path
pfig run config.pfig --entrypoint trainer
pfig run config.pfig --entrypoint trainer.train
pfig run config.pfig --entrypoint dataset/dataset_manager.upload
pfig run config.pfig --entrypoint outer.data.copy
```

The argument must name an existing binding or an attribute path rooted at one
(including a slash-separated included binding). Arbitrary Python import paths
are not resolved.

### Snapshotting

pfig automatically creates **snapshots** of each run for reproducibility, capturing configuration, Git state, and environment.

Snapshots are created **before** entrypoint execution. The runner validates inputs
for both the selected entrypoint and an enabled `__snapshot__` directive before
constructing either root. Computed directives may construct providers; shared
instances are reused by the entrypoint. `--snapshot` or `--no-snapshot` bypasses
the directive and its input requirements.

```bash
# Snapshots are created automatically (default: ./snapshots/)
pfig run config.pfig
# → Snapshot saved to: ./snapshots/20260114_123456_abc1234/

# Specify snapshot directory
pfig run config.pfig --snapshot ./experiments/
# → ./experiments/20260114_123456_abc1234/

# Write snapshots to remote storage (GCS or S3)
pfig run config.pfig --snapshot gs://my-bucket/snapshots/
pfig run config.pfig --snapshot s3://my-bucket/snapshots/

# Disable snapshotting
pfig run config.pfig --no-snapshot
```

A timestamped subdirectory is always created inside the snapshot directory. Both local paths and remote URIs (`gs://`, `s3://`) are supported (requires `pip install pfig[remote]`).

Remote destinations currently receive `log.json` only; the config bundle,
requirements lock, and Git patches are not uploaded. For automated replay,
create a local snapshot and upload its complete directory.

`--base-commit COMMIT` selects the base commit for captured Git patches, useful in
replay worktrees without an upstream branch.

See the [Snapshots Guide](../guides/snapshots.md) for full details on snapshot contents, reproducibility warnings, and usage.

### Running from a Bundle

Execute a configuration from a portable bundle (created with `pfig bundle`):

```bash
pfig run --bundle bundle.tar.gz
```

Run from an extracted bundle directory:

```bash
pfig run --bundle ./my-bundle/
```

Run from a remote bundle:

```bash
pfig run --bundle gs://my-bucket/experiments/bundle.tar.gz
```

You can still set externals when running from a bundle:

```bash
pfig run --bundle bundle.tar.gz --set device=cuda
```

#### Profiles and bundles

If the bundle was created with one or more `--profile` files, those profile files (and any files they include) are embedded into the bundle and recorded in the bundle manifest **in the same order** they were supplied during bundling.

- If you run the bundle **without** CLI `--profile`, pfig will automatically apply the embedded profiles in manifest order.
- If you run the bundle **with** CLI `--profile`, the CLI profiles **replace** the embedded profiles entirely.

## bundle

Create a portable config bundle that packages all included `.pfig` files together:

```bash
pfig bundle config.pfig
```

This creates `bundle.tar.gz` containing the entry config and all explicitly included configuration files.

### Output Options

Specify a custom output path:

```bash
pfig bundle config.pfig -o my-experiment.tar.gz
```

Create a directory bundle instead of a tarball:

```bash
pfig bundle config.pfig -d -o ./my-bundle
```

### Bundle Structure

A bundle contains:

```
configs/                  # Filesystem files (relative structure preserved)
__profiles__/             # Profile files (and their includes), if any were supplied
bundle_manifest.json      # Entry point, profile list, file list
```

- **configs/**: Contains all `.pfig` files loaded via filesystem includes, preserving their relative directory structure
- **__profiles__/**: Contains all profile files passed to `pfig bundle --profile ...` and any `.pfig` files they include
- **bundle_manifest.json**: Metadata describing the entry point, bundled profiles, and file list

New bundles use manifest version 2. Filesystem-only version 1 bundles remain readable. Older bundles with mount mappings must be recreated after replacing protocol includes with explicit filesystem paths. Use relative includes to preserve portability when the bundle is relocated.

### Bundle Options

```bash
pfig bundle config.pfig [options]
```

| Option | Description |
|--------|-------------|
| `-o`, `--output PATH` | Output path for bundle (default: `bundle.tar.gz`) |
| `-d`, `--directory` | Create bundle as directory instead of tarball |
| `--entrypoint BINDING[.ATTR...]` | Override entrypoint with a binding or callable attribute path |
| `--set KEY=VALUE` | Set runtime values (optional during file discovery) |
| `--profile PATH` | Load from profile file(s) (also embedded into the bundle) |

Bundling preserves unresolved externals without constructing providers. It performs
file discovery, not execution validation. Running from the bundle checks the inputs
required by the selected graph.

### Use Cases

**Sharing configurations across machines:**

```bash
# On machine A: create bundle
pfig bundle experiments/train.pfig -o train-config.tar.gz

# Transfer to machine B, then run
pfig run --bundle train-config.tar.gz --set data_dir=/data/datasets
```

**Archiving experiment configurations:**

```bash
# Bundle captures the entry config and all explicit includes
pfig bundle experiment.pfig -o archive/exp-2026-02-03.tar.gz
```

## diff

Compare two configurations:

```bash
pfig diff base.pfig proposed.pfig
```

Compare with external parameters:

```bash
pfig diff base.pfig proposed.pfig --set device=cuda
```

By default, pfig matches bindings by their compiler identity (defining file and
binding name), then by exact qualified path. References to the same binding
compare equal even when they use different include aliases. Equal recipes do
not make distinct bindings interchangeable.

When a move changes the authored identity and the qualified path, supply a
namespace mapping. Use `--no-align-namespace` to compare exact paths only:

```bash
pfig diff wrapped.pfig flat.pfig --align-namespace base=/
pfig diff wrapped.pfig flat.pfig --no-align-namespace
```

The left side is the namespace in the first config; the right side is the
namespace in the second. Use `/` for the root. Bindings in the aligned
subtree are paired by relative path after identity matching and before
exact-path matching. A mapping cannot override a confirmed identity match.
Real recipe changes on paired bindings still appear in the diff.

This shows added, removed, and modified providers **and values**. Exit status is
`0` for equivalent configs, `3` for differences, and `2` for parsing/comparison
errors. Both configs receive the same `--set` values. `diff` does not accept
`--profile` or snapshot JSON files; offline graph comparison is
available in Python via `pfig.diff.compare_ir_graphs`.

### Verbose Output

Get detailed trace of provider resolution:

```bash
pfig -v run config.pfig
```

Shows:

- Provider resolution order
- Shared vs fresh instances
- Namespace information
- Dependency graph

## fmt

Format `.pfig` files to a canonical style — 4-space indent, 100-char lines, black-style magic trailing comma, sorted imports within blank-line groups, double-quote string preference. Comments are preserved.

```bash
pfig fmt config.pfig             # rewrite in place
pfig fmt path/to/configs/        # recurse into a directory
pfig fmt --check config.pfig     # exit 1 if it would change; don't write
pfig fmt --diff config.pfig      # print a unified diff; don't write
pfig fmt --stdout config.pfig    # write to stdout instead of in place
pfig fmt -                       # read stdin, write stdout
```

With no arguments and stdin piped (`cat config.pfig | pfig fmt`) the behavior matches `pfig fmt -`.

### Options

| Option | Description |
|--------|-------------|
| `--check` | Exit non-zero if any file would change; do not write. Mutually exclusive with `--diff`. |
| `--diff` | Print a unified diff to stdout; do not write. |
| `--stdout` | Write formatted output to stdout instead of overwriting the file. |
| `--line-length N` | Maximum line length before wrapping (default: 100). |

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Nothing to do, or files written successfully |
| `1` | `--check` / `--diff` found differences |
| `2` | I/O error, parse error, or invalid flag combination |

### Style rules

| Rule | Value |
|------|-------|
| Indent | 4 spaces |
| Line length | 100 (configurable) |
| Wrapping | single-line if it fits, otherwise each item on its own line with a trailing comma |
| Magic trailing comma | A trailing comma in the input forces multi-line output (black-style) |
| String quotes | Prefer `"`, fall back to `'` when value contains an unescaped `"` |
| Import sorting | isort-style within blank-line-separated groups: `import x` before `from x import …`, alphabetical within each kind; names within a `from` import also sorted |
| Kwarg spacing | `Foo(a=1, b=2)` — no spaces around `=` |
| Top-level binding | `name = value` — one space each side |

### Library API

For editor integrations (e.g. the LSP's `textDocument/formatting`), the formatter is also exposed as a library:

```python
from pfig import format_source, FormatOptions, FormatError

formatted = format_source(source_text, filename="config.pfig")
formatted = format_source(source_text, options=FormatOptions(line_length=88))
```

### Comments

Comments are preserved in five positions:

- **Leading the file** — block above the first statement, separated by a blank line.
- **Standalone between statements** — emitted on their own line, attached to the next statement.
- **Trailing on the same source line as a statement** — emitted after the statement with two spaces before `#`.
- **Trailing the file** — emitted after the last statement.
- **Inside List/Dict/Tuple containers** — leading an element, trailing an element on the same line, or dangling at the end before the closing bracket. A container that contains any internal comment is always rendered multi-line.

**Known limitation:** comments inside a `Call`/`BuiltinCall`'s parentheses (e.g. `Foo(a=1, # …\n b=2)`) currently get pulled out to lead the following statement rather than staying with the call. See the `TODO` in [`pfig/fmt.py`](https://github.com/pfig-dev/pfig/blob/main/pfig/fmt.py).

## replay

Reproduce a run from a snapshot directory or its `log.json`:

```bash
pfig replay prepare snapshots/run/ --workspace ./replay-workspace
pfig replay validate snapshots/run/ --workspace ./replay-workspace
pfig replay run snapshots/run/ --set device=cpu
pfig replay run snapshots/run/ --dry-run
```

| Subcommand | Behavior |
|------------|----------|
| `prepare` | Restore code and patches, extract the config bundle, create a venv and install dependencies |
| `validate` | Recompile a prepared workspace and compare its baseline graph digest with the snapshot |
| `run` | Prepare, validate, then execute using the bundled config |

| Option | Available on | Description |
|--------|--------------|-------------|
| `--workspace PATH` | All | Workspace directory; required for `validate`, automatically chosen otherwise |
| `--python PATH` | `prepare`, `run` | Interpreter used to create the replay venv |
| `--no-deps` | `prepare`, `run` | Skip venv creation and dependency installation; use the current environment |
| `--dry-run` | `prepare`, `run` | Print the preparation plan without executing it |
| `--set KEY=VALUE ...` | `validate`, `run` | Record an override diff; `run` also applies these values during execution |
| `--allow-baseline-mismatch` | `validate`, `run` | Continue despite a baseline digest mismatch |

The baseline is checked **before** applying `--set`; intentional overrides are
reported separately. Replay is best-effort, not a guarantee of identical results.
Only replay trusted snapshots: preparation may clone code and install packages,
and validation imports Python modules.

See the [Snapshots Guide](../guides/snapshots.md#automated-replay) for workspace
layout, restoration details, and limitations.

## install-rules

Install pfig rules into the current project so an AI coding agent knows how to write `.pfig` files and structure Python code for use with pfig. The same canonical rule sources are rendered into each agent's native format.

```bash
pfig install-rules <agent>
```

Supported agents:

| Agent | Target | Format |
|-------|--------|--------|
| `cursor` | `.cursor/rules/*.mdc` | Per-file rules with `globs:` frontmatter for conditional loading |
| `claude` | `.claude/rules/*.md` | Per-file rules with `paths:` frontmatter for conditional loading |
| `codex` | `AGENTS.md` (repo root) | Single concatenated document — Codex does not support scoped rule files |

For `cursor` and `claude`, three rule files are installed:

| Rule | Scope | Purpose |
|------|-------|---------|
| `pfig-spec` | `**/*.pfig` | Full DSL syntax reference |
| `pfig-developer-guide` | `**/*.pfig` | Mental model, best practices, common patterns |
| `pfig-friendly-python` | `**/*.py` | How to structure Python code for pfig wiring |

For `codex`, all three are concatenated into a single `AGENTS.md` (the cross-reference "See Also" sections are dropped since they don't make sense in a single file).

### Re-running install-rules

For **cursor / claude**, existing rule files are not overwritten by default. Use `--force` to update them:

```bash
pfig install-rules cursor --force
```

For **codex**, the rendered bundle is wrapped in HTML-comment markers:

```markdown
<!-- BEGIN pfig install-rules -->
…pfig rules…
<!-- END pfig install-rules -->
```

The installer manages this region in place — your own content above or below the markers is preserved.

| Situation | Behavior |
|-----------|----------|
| `AGENTS.md` doesn't exist | Created with the managed region as its only content |
| Exists, no markers | Managed region is **appended** to the end of the file |
| Exists, markers present | The region between the markers is **replaced**; everything else is untouched |

Don't hand-edit content between the markers — it will be overwritten on the next install. Put your own guidance outside the markers.

### Options

| Option | Description |
|--------|-------------|
| `--force` | Overwrite existing per-file rules (`cursor` / `claude`). No effect for `codex` — its merge is non-destructive by design. |

## Common Usage Patterns

### Development Workflow

```bash
# Validate before running
pfig validate config.pfig --set device=cuda

# Run with verbose output for debugging
pfig -v run config.pfig --set device=cuda

# Compare different configurations
pfig diff config.pfig config_v2.pfig --set device=cuda
```

### Production Deployment

```bash
# Use production profile
pfig run config.pfig --profile profiles/production.pfig --set device=gpu
```

### Testing Different Configurations

```bash
# Test CPU configuration
pfig run config.pfig --profile profiles/cpu.pfig

# Test GPU configuration
pfig run config.pfig --profile profiles/gpu.pfig

# Compare two algorithm configurations (diff has no --profile option)
pfig diff config.pfig config_v2.pfig --set device=cpu data_dir=./data
```

### Sharing Configurations

```bash
# Create a portable bundle
pfig bundle config.pfig -o experiment.tar.gz

# Send to colleague, who can run it without your original config checkout
pfig run --bundle experiment.tar.gz --set data_dir=/their/data
```

## Error Messages

pfig provides detailed error messages to help diagnose issues:

- **Missing externals**: Lists all required externals that weren't provided
- **Circular dependencies**: Shows the dependency cycle
- **Invalid references**: Points to the exact location of the error
- **Type errors**: Explains expected vs actual types

For more details on troubleshooting, see the [Troubleshooting](../guides/troubleshooting.md) page.
