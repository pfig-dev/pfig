# Snapshot Format Reference

This document specifies the structure and contents of pfig snapshots.

> **Best-Effort Replay:** Snapshots provide best-effort reproducibility. pfig captures as much state as possible, but explicitly records limitations via warnings and introspection status. Users should check the warnings array and provider introspection fields to understand what may require manual intervention.

## Directory Structure

```
snapshot_dir/
├── payload/
│   ├── config_bundle.tar.gz  # Portable config bundle (all .pfig files)
│   ├── requirements.lock  # pip freeze output
│   └── patches/           # Git patch series for reproducibility
│       ├── 0001-first-commit.patch   # Individual commit patches (git format-patch)
│       ├── 0002-second-commit.patch
│       └── dirty.patch               # Uncommitted changes (if working tree was dirty)
└── log.json               # Resolved IR graph + metadata
```

## Payload Directory

The `payload/` directory contains artifacts needed to reproduce the run.
Payload creation is best-effort: individual artifacts may be absent if capture
fails. Remote snapshot destinations currently receive `log.json` only (no config
bundle, requirements lock, or patches). Write locally and upload the complete
directory when automated replay is required.

### `config_bundle.tar.gz`

A portable bundle containing all `.pfig` files needed to reproduce the run, including:

- `configs/`: the entry config and any filesystem-included `.pfig` files (relative layout preserved)
- `__profiles__/`: any profile files used for the run (and any `.pfig` files they include)
- `bundle_manifest.json`: entry path, bundled profile order, and file list

### `requirements.lock`

Output of `pip freeze` capturing exact package versions:

```
torch==2.1.0
numpy==1.24.3
pfig==0.5.0
...
```

### `patches/`

Git patch series for reproducing the exact code state. Contains individual patches that preserve commit history.

#### Commit Patches (`0001-*.patch`, `0002-*.patch`, ...)

Individual patches for each commit ahead of upstream, generated via `git format-patch`. These preserve commit messages, authorship, and timestamps.

Apply with: `git am patches/0001-*.patch patches/0002-*.patch ...`

#### `dirty.patch`

Git diff for uncommitted changes. Only present if working tree was dirty.

Generated via: `git diff HEAD`

Apply with: `git apply patches/dirty.patch`

## Log File (`log.json`)

The log file contains metadata and the resolved IR graph.

### Schema

Schema 2.0 uses canonical bindings and declarative inclusion-ancestry override
resolution. Replay requires this version; use the recorded pfig version to replay
older snapshots. `load_snapshot` can still read older logs for inspection.

```json
{
  "version": "2.0",
  "meta": {
    "pfig_version": "<pfig package version>",
    "generated_at": "<ISO 8601 timestamp>",
    "entrypoint": "<entrypoint binding name>",
    "digest": "<sha256:hash of graph>",
    "run_id": "<6-char hex run ID>",
    "pfig_files": ["<list of config file paths>"]
  },
  "environment": { ... },
  "graph": { ... },
  "warnings": [ ... ]
}
```

### Top-Level Fields

| Field | Type | Description |
|-------|------|-------------|
| `version` | string | Schema and resolution-semantics version (currently `"2.0"`) |
| `meta` | object | Snapshot metadata |
| `environment` | object | Runtime environment information |
| `graph` | object | Flattened, resolved configuration graph |
| `warnings` | array | Reproducibility warnings (only present if any) |

### `meta` Object

| Field | Type | Description |
|-------|------|-------------|
| `pfig_version` | string | Version of pfig that created the snapshot |
| `generated_at` | string | ISO 8601 timestamp of snapshot creation |
| `entrypoint` | string | Name of the entrypoint binding |
| `digest` | string | SHA-256 hash of the graph (e.g., `"sha256:abc123..."`) |
| `run_id` | string | Short unique run identifier (6 hex chars) |
| `pfig_files` | array | List of `.pfig` file paths used |

### `environment` Object

| Field | Type | Description |
|-------|------|-------------|
| `python_version` | string | Python version (e.g., `"3.10.12"`) |
| `platform` | string | Platform identifier (e.g., `"linux-x86_64"`) |
| `command` | array | Command line arguments (`sys.argv`) |
| `working_dir` | string | Working directory when snapshot was created |
| `git` | object | Git repository state (omitted if not a Git repo) |
| `externals` | object | External values with their sources |
| `requirements_lock_path` | string | Relative path to requirements.lock (omitted if unavailable) |

`git`, `externals`, and `requirements_lock_path` are omitted when unavailable or
empty, rather than emitted as null/empty values.

### `environment.git` Object

| Field | Type | Description |
|-------|------|-------------|
| `commit` | string | Full commit SHA of HEAD |
| `is_dirty` | boolean | Whether working tree has uncommitted changes |
| `remote_origin_url` | string \| null | URL of origin remote |
| `has_upstream` | boolean | Whether an upstream branch is configured |
| `commits_ahead` | integer | Number of commits ahead of upstream (only if > 0) |
| `upstream_commit` | string | Full commit SHA of upstream (only if commits_ahead > 0) |
| `patches` | object | Patch files created for reproducibility (see below) |
| `uses_lfs` | boolean | Whether repository uses Git LFS (only if true) |
| `has_submodules` | boolean | Whether repository has submodules (only if true) |

#### `environment.git.patches` Object

| Field | Type | Description |
|-------|------|-------------|
| `commits` | array | List of commit patch filenames (e.g., `["0001-add-feature.patch", "0002-fix-bug.patch"]`) |
| `dirty` | array | List of dirty patch filenames (e.g., `["dirty.patch"]`) |

Both fields are optional and only present when patches were created.

### `environment.externals` Object

Native API/CLI snapshots record one winning runtime assignment per canonical
binding, using a portable binding path rather than expanding every include alias.
This also includes runtime overrides of ordinary bindings. Replay accepts older
schema-2 snapshots containing repeated aliases and applies replay overrides as a
higher-precedence layer.

Each recorded assignment is an object with:

| Field | Type | Description |
|-------|------|-------------|
| `value` | any | The external value |
| `source` | string | Provenance label: API/CLI inputs use `"cli"`; profiles use `"profile:<path>"` or `"profile-prefixed:<path>"`. Older/custom logs may use other labels. |

### `warnings` Array

Each warning is an object with:

| Field | Type | Description |
|-------|------|-------------|
| `code` | string | Warning identifier |
| `message` | string | Human-readable description |

Warning codes:

| Code | Meaning |
|------|---------|
| `no_git` | Not a Git repository |
| `no_upstream` | No upstream branch configured |
| `dirty_working_tree` | Working tree dirty but dirty.patch creation failed |
| `unpushed_commits` | Unpushed commits but patch series creation failed |
| `uses_lfs` | Repository uses Git LFS; patches contain pointer files only |
| `has_submodules` | Repository has submodules (not supported); patches capture pointers only |
| `incomplete_introspection` | Some providers couldn't be fully introspected; default kwargs may be missing |

The `incomplete_introspection` warning includes a `providers` field listing affected
binding names. Nested recipes are reported once under their containing binding.

### `graph` Object

The graph serializes the **compiled wiring**, without materializing providers.
Each key is a binding name, and each value describes that binding. Expressions
and nested calls may remain inline provider recipes rather than evaluated values.

#### Binding Types

**Provider binding:**

```json
{
  "type": "provider",
  "target": "<fully qualified callable path>",
  "is_shared": true,
  "introspection": {
    "status": "ok",
    "defaults_source": "signature"
  },
  "kwargs": {
    "<arg_name>": "<resolved value or $ref>"
  }
}
```

The `introspection` field indicates how reliably the kwargs were captured:

| Field | Values | Description |
|-------|--------|-------------|
| `status` | `ok`, `partial`, `failed` | Whether signature introspection succeeded |
| `defaults_source` | `signature`, `explicit`, `none` | Where default values came from |
| `opaque_reason` | object (optional) | Why introspection failed (only if status != ok) |

**Introspection with opaque callable (e.g., C extension):**

```json
{
  "type": "provider",
  "target": "torch._C._nn.Conv2d",
  "is_shared": true,
  "introspection": {
    "status": "failed",
    "defaults_source": "explicit",
    "opaque_reason": {
      "code": "c_extension",
      "detail": "torch._C._nn.Conv2d: ValueError from inspect.signature"
    }
  },
  "kwargs": {
    "in_channels": 3,
    "out_channels": 64
  }
}
```

Opaque reason codes:

| Code | Meaning |
|------|---------|
| `c_extension` | C extension without `__signature__` |
| `builtin` | Built-in function/method |
| `no_signature` | `inspect.signature()` raised ValueError |
| `varargs_only` | Signature is `*args, **kwargs` only; no named parameters |

**Fresh provider binding (all-bindings graph):**

```json
{
  "type": "fresh_provider",
  "base_provider": "<provider name>",
  "is_shared": false,
  "overlay_kwargs": {
    "<arg_name>": "<override value>"
  }
}
```

Execution-scoped snapshots instead store the merged effective recipe, including
inherited arguments. The shared base and superseded arguments need not be reachable:

```json
{
  "type": "fresh_provider",
  "is_shared": false,
  "recipe": {
    "$inline_provider": true,
    "target": "collections.OrderedDict",
    "kwargs": {"value": "selected", "inherited": 7}
  }
}
```

Recipes retain signature defaults and introspection metadata; positional arguments,
when present, appear in `args`. Scoped arithmetic uses `$binary` with `left`/`right`
or `$unary` with `operand`, so nested fresh recipes remain part of the digest.
Nested fresh expressions use `{"$fresh": true, "recipe": {...}}`. Replay recompiles
the same selected graph and rechecks its required inputs. Historical snapshots
with different fresh-graph digests require their recorded pfig version.

**Alias binding:**

```json
{
  "type": "alias",
  "target": {"$ref": "<binding name>"}
}
```

**Value binding:**

```json
{
  "type": "value",
  "value": "<resolved value>"
}
```

#### References

References to other bindings use the `$ref` marker:

```json
{
  "model": {"$ref": "model"}
}
```

Single-segment attribute and method references retain the legacy `method` field:

```json
{
  "$ref": "model",
  "method": "parameters"
}
```

Nested references use an `attrs` array instead. For example,
`@model.encoder.weight` is serialized as:

```json
{
  "$ref": "model",
  "attrs": ["encoder", "weight"]
}
```

The final value may be a plain attribute or a callable; the snapshot records the
reference without reading the attributes.

#### Example Graph

```json
{
  "graph": {
    "model": {
      "type": "provider",
      "target": "my_lib.models.MLP",
      "is_shared": true,
      "introspection": {
        "status": "ok",
        "defaults_source": "signature"
      },
      "kwargs": {
        "input_dim": 784,
        "hidden_dim": 256,
        "output_dim": 10,
        "dropout": 0.1
      }
    },
    "optimizer": {
      "type": "provider",
      "target": "torch.optim.Adam",
      "is_shared": true,
      "introspection": {
        "status": "ok",
        "defaults_source": "signature"
      },
      "kwargs": {
        "params": {"$ref": "model", "method": "parameters"},
        "lr": 0.001,
        "betas": [0.9, 0.999],
        "eps": 1e-08,
        "weight_decay": 0
      }
    },
    "batch_size": {
      "type": "value",
      "value": 64
    }
  }
}
```

### Graph Properties

- **Flattened** - All bindings at top level, no nesting
- **Compiled** - References become `$ref` markers; expressions and anonymous calls may be serialized as `$inline_provider` recipes
- **Explicit defaults** - Python default arguments included (via `inspect.signature`) when introspection succeeds
- **Introspection status** - Each provider records whether defaults were successfully captured
- **Deterministic** - Keys sorted alphabetically for consistent hashing

## Full Example

```json
{
  "version": "2.0",
  "meta": {
    "pfig_version": "0.5.0",
    "generated_at": "2026-01-14T12:34:56.789012+00:00",
    "entrypoint": "__entrypoint__",
    "digest": "sha256:abc1234def5678901234567890abcdef12345678901234567890abcdef123456",
    "run_id": "a1b2c3",
    "pfig_files": ["main.pfig", "includes/model.pfig"]
  },
  "environment": {
    "python_version": "3.10.12",
    "platform": "linux-x86_64",
    "command": ["pfig", "run", "main.pfig"],
    "working_dir": "/home/user/project",
    "git": {
      "commit": "abc1234def5678901234567890abcdef12345678",
      "is_dirty": true,
      "has_upstream": true,
      "commits_ahead": 2,
      "upstream_commit": "def5678901234567890abcdef12345678901234",
      "remote_origin_url": "git@github.com:org/repo.git",
      "patches": {
        "commits": ["0001-add-model-architecture.patch", "0002-implement-training.patch"],
        "dirty": ["dirty.patch"]
      }
    },
    "externals": {},
    "requirements_lock_path": "payload/requirements.lock"
  },
  "graph": {
    "model": {
      "type": "provider",
      "target": "my_lib.models.MLP",
      "is_shared": true,
      "introspection": {
        "status": "ok",
        "defaults_source": "signature"
      },
      "kwargs": {
        "input_dim": 784,
        "hidden_dim": 256,
        "output_dim": 10
      }
    },
    "__entrypoint__": {
      "type": "alias",
      "target": {"$ref": "trainer", "method": "fit"}
    }
  }
}
```

## Limitations

### Git LFS

Repositories using Git LFS store large files on an external server. Patches created by pfig contain only LFS **pointer files**, not the actual file content.

**Impact:** Reproduction requires:
1. Access to the original LFS server
2. Running `git lfs pull` after applying patches

pfig detects LFS usage and includes a `uses_lfs` warning in the snapshot.

### Git Submodules

Submodules are **not supported**. Patches capture only the submodule commit pointer, not the submodule content itself.

**Impact:** Reproduction would require:
1. The submodule's remote to be accessible
2. Running `git submodule update --init --recursive` after applying patches

For projects using submodules, consider vendoring dependencies or using alternative dependency management.

## API Access

For programmatic snapshot creation:

```python
from pathlib import Path
from pfig import parse_pfig_source, create_snapshot
from pfig import validate_pfig

ast = parse_pfig_source(config_source, filename="config.pfig")
metadata = validate_pfig(ast, filename="config.pfig")

snapshot_path = create_snapshot(
    compiled=metadata.compiled,
    entrypoint=metadata.entrypoint_name,
    output_dir=Path("./snapshots"),
    pfig_files=[Path("config.pfig")],
    externals=metadata.externals,
    reachable_names=metadata.reachable_names,
    file_origins=metadata.file_origins,
    entry_path=Path("config.pfig"),
)
```

`output_dir` accepts both local `Path` objects and remote URI strings such as `gs://` (GCS) or `s3://` (S3). Remote storage requires `pip install pfig[remote]`:

```python
snapshot_path = create_snapshot(
    compiled=metadata.compiled,
    entrypoint=metadata.entrypoint_name,
    output_dir="gs://my-bucket/snapshots",
    pfig_files=[Path("config.pfig")],
    externals=metadata.externals,
    reachable_names=metadata.reachable_names,
)
```

See the [API Reference](api_reference.md) for full function signatures.
