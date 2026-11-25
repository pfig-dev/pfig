# Snapshots

Snapshots provide **best-effort reproducibility** for pfig runs by capturing everything needed to recreate an experiment: configuration, resolved parameters, source code state, and environment.

## Overview

When you run `pfig run`, pfig automatically creates a snapshot containing:

1. **Payload** - Source files needed to reproduce the run
2. **Log** - The resolved configuration as a flattened IR graph

This dual-artifact strategy enables both reproduction (via payload) and analysis (via log).

> **Best-Effort Replay:** Snapshots are designed for best-effort reproducibility. pfig captures as much state as possible, but some aspects (Git LFS files, submodules, C extension defaults) may require manual intervention. The snapshot explicitly records what was captured and what may be missing via warnings and introspection status fields.

## Quick Start

```bash
# Snapshots are created automatically (default: ./snapshots/)
pfig run config.pfig
# → Snapshot saved to: ./snapshots/20260114_123456_abc1234/

# Specify a custom directory
pfig run config.pfig --snapshot ./experiments/
# → ./experiments/20260114_123456_abc1234/

# Disable snapshotting
pfig run config.pfig --no-snapshot
```

## Snapshot Location

pfig determines where to save snapshots using this precedence:

1. **`--snapshot <dir>`** - CLI override
2. **`__snapshot__` directive** - Default path in config file
3. **Default** - `./snapshots/`

A timestamped subdirectory (e.g., `20260114_123456_abc1234/`) is always created inside the resolved directory.

All three destinations accept local paths and remote URIs such as `gs://` (GCS)
or `s3://` (S3). Remote storage requires `pip install pfig[remote]`.

**Remote limitation:** the current implementation writes `log.json` remotely but
skips the config bundle, requirements lock, and Git patch payloads. For a replayable
snapshot, write locally and upload the complete directory afterward.

### Using `__snapshot__` Directive

Set a default snapshot location in your config:

```pfig
# config.pfig
__snapshot__ = "./experiments/snapshots"

model = MLP(input_dim=784, output_dim=10)
__entrypoint__ = @model.forward
```

Remote URIs work too:

```pfig
# config.pfig
__snapshot__ = "gs://my-bucket/experiments/snapshots"  # GCS
__snapshot__ = "s3://my-bucket/experiments/snapshots"  # S3
```

### CLI Examples

```bash
# Use __snapshot__ from config (or default ./snapshots/)
pfig run config.pfig

# Override snapshot directory
pfig run config.pfig --snapshot ./experiments/
# → ./experiments/20260114_123456_abc1234/

# Write snapshots to remote storage (GCS or S3)
pfig run config.pfig --snapshot gs://my-bucket/snapshots/
pfig run config.pfig --snapshot s3://my-bucket/snapshots/

# Disable snapshotting entirely
pfig run config.pfig --no-snapshot
```

## Organizing Experiments

By default, snapshots are saved with auto-generated names (`20260114_123456_abc1234/`). For better organization, use an **external** to group related runs:

```pfig
# config.pfig
external experiment

# Snapshot paths may be literals, references, or computed expressions
external snapshot_root
__snapshot__ = @snapshot_root

# Reuse the same value for experiment tracking
clearml_task = ClearMLTask(
    project="my_project",
    task_name=@experiment,
)

trainer = Trainer(task=@clearml_task, ...)
__entrypoint__ = @trainer.fit
```

```bash
# Run with experiment name
pfig run config.pfig --set experiment=lr_ablation snapshot_root=./snapshots/lr_ablation
# → ./snapshots/lr_ablation/20260114_123456_abc1234/

pfig run config.pfig --set experiment=lr_ablation snapshot_root=./snapshots/lr_ablation
# → ./snapshots/lr_ablation/20260114_123500_def5678/

pfig run config.pfig --set experiment=batch_size_study snapshot_root=./snapshots/batch_size_study
# → ./snapshots/batch_size_study/20260114_140000_ghi9012/
```

Both CLI and Python `pfig.run()` can evaluate computed directives such as
`__snapshot__ = "./snapshots/" + @experiment`. Inputs for the directive and
entrypoint are validated before constructing either root; their shared providers
retain identity. An explicit destination or disabled snapshotting bypasses the
directive and its requirements. Unlike the CLI, Python `run()` creates no snapshot
without `snapshot_dir` or a root `__snapshot__` binding.

This pattern:

- **Groups related runs** in the same directory
- **Shares the name** with experiment trackers (ClearML, MLflow, W&B)
- **Stays explicit** - you control exactly where the value is used

Browse experiments easily:

```bash
ls ./snapshots/
# batch_size_study/
# lr_ablation/

ls ./snapshots/lr_ablation/
# 20260114_123456_abc1234/
# 20260114_123500_def5678/
```

## Reproducibility Warnings

pfig checks your Git state and warns when a run may not be fully reproducible:

| Warning | Meaning | Resolution |
|---------|---------|------------|
| `no_git` | Not a Git repository | Initialize git: `git init` |
| `no_upstream` | No upstream branch configured | Push with upstream: `git push -u origin <branch>` |
| `dirty_working_tree` | Uncommitted changes, patch failed | Commit or stash changes |
| `unpushed_commits` | Unpushed commits, patch failed | Push commits to remote |
| `uses_lfs` | Repository uses Git LFS | Ensure LFS server access; run `git lfs pull` on restore |
| `has_submodules` | Repository has submodules | Submodules must be manually restored |
| `incomplete_introspection` | Some providers couldn't be fully introspected | Check IR for missing defaults (C extensions, builtins) |

Warnings appear in both console output (as a checklist) and `log.json`.

### Reproducibility Checklist

When warnings exist, pfig prints a checklist to stderr after creating the snapshot:

```
Snapshot written to ./snapshots/20260114_123456_abc123/

Reproducibility checklist:
  [✓] All commits pushed to origin
  [✓] Dirty patch captured (dirty.patch)
  [✗] LFS pointers only - run `git lfs pull` after applying
  [✗] 2 submodules not captured (vendor/lib1, vendor/lib2)
  [✗] Incomplete introspection for some providers - see IR for details
```

The checklist transforms warning codes into actionable items:
- `[✓]` indicates no issue
- `[✗]` indicates action may be required

> **Note:** Git submodules are not supported. See the [Snapshot Format Reference](../reference/snapshot_format.md#limitations) for details.

### When Patches Save You

If your working tree is dirty or you have unpushed commits, pfig captures the differences as patch files. Unpushed commits are stored as a patch series using `git format-patch`, preserving individual commit messages and authorship. With these patches, the run is still reproducible:

```bash
# To reproduce a run with patches:
git checkout <upstream_commit>      # From log.json git.upstream_commit

# Apply commit patches (preserves commit history)
git am payload/patches/0001-*.patch payload/patches/0002-*.patch
# Or apply all numbered patches: git am payload/patches/[0-9]*.patch

# Apply uncommitted changes (if any)
git apply payload/patches/dirty.patch

pip install -r payload/requirements.lock
pfig run payload/configs/main.pfig
```

## Use Cases

### Audit Trail

Every run creates a permanent record. Compare experiments over time:

```bash
ls ./snapshots/
# 20260110_090000_abc123/
# 20260112_143000_def567/
# 20260114_123456_ghi901/
```

### Semantic Diffing

Compare the resolved configurations of two runs:

```bash
# Compare IR graphs
diff snapshots/run1/log.json snapshots/run2/log.json
```

Because the IR graph captures resolved values with defaults, you can detect:

- Parameter changes
- New/removed bindings
- Schema changes from library updates

### Debugging

When a run fails or produces unexpected results:

1. Check `log.json` for the exact configuration used
2. Verify Git state and patches
3. Compare with a known-good snapshot

### Sharing Experiments

To share a reproducible experiment:

1. Ensure Git state is clean and pushed
2. Share the snapshot directory or `log.json`
3. Recipient can reproduce from Git commit + config

## Automated Replay

The `pfig replay` command attempts to reconstruct the code and environment from a
snapshot and re-executes the experiment. This is best-effort: external data,
services, platform differences, and non-determinism are not captured. It has three subcommands that can be used independently
or combined:

### Quick start

```bash
# Full replay (prepare + verify + execute) in one command
pfig replay run ./snapshots/20260114_123456_a1b2c3/

# Preview what will happen without making changes
pfig replay run ./snapshots/20260114_123456_a1b2c3/ --dry-run

# Override an external for the replayed run
pfig replay run ./snapshots/20260114_123456_a1b2c3/ --set learning_rate=0.01
```

### Subcommands

**`pfig replay prepare`** sets up the workspace (git worktree or clone, venv,
config bundle) without executing the experiment. Preparation can clone code and
install packages; only use trusted snapshots:

```bash
pfig replay prepare ./snapshots/20260114_123456_a1b2c3/
pfig replay prepare ./snapshots/20260114_123456_a1b2c3/ --no-deps
```

**`pfig replay validate`** recompiles the IR from the bundled configs and compares
the digest to the snapshot. Fails if they differ (unless `--allow-baseline-mismatch`):

```bash
pfig replay validate ./snapshots/20260114_123456_a1b2c3/ --workspace .pfig/replays/a1b2c3/20260306_142530
```

**`pfig replay run`** does all three phases: prepare, verify, then execute.

### Workspace layout

Replay creates an isolated workspace under `REPO_ROOT/.pfig/replays/<run_id>/<timestamp>/`:

```
.pfig/replays/a1b2c3/20260306_142530/
  src/                 # git worktree (detached HEAD) or clone
  bundle/              # extracted config_bundle.tar.gz
  venv/                # isolated Python venv with requirements.lock installed
  artifacts/           # generated IR, diffs, run logs
  replay_report.json   # structured report
  replay_report.txt    # human-readable summary
```

### How code is restored

- **Same repo:** If you run replay from the same repository (origin URLs match),
  a detached git worktree is created. This is fast and requires no network access
  if the commit is available locally.
- **Different repo:** If the origin doesn't match or you're not in a repo, pfig
  automatically clones from the snapshot's `remote_origin_url`.
- **Patches:** Commit patches are applied via `git am`, dirty patches via `git apply`.

### Safety

- Your current working tree is never modified.
- All git operations happen inside the workspace `src/` directory.
- Dependency installs happen in the workspace `venv/`; `--no-deps` skips the venv
  and uses the current environment instead.
- The workspace is kept after completion for inspection.

## Further Reading

- **[Snapshot Format Reference](../reference/snapshot_format.md)** - Detailed specification of `log.json` and payload contents
- **[API Reference](../reference/api_reference.md)** - Programmatic access to snapshot functionality
