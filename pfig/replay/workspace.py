"""Replay workspace: creation, layout, and the ``prepare`` orchestrator."""

from __future__ import annotations

import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import fs as _fs
from ..bundle import _extractall_with_data_filter
from ..logging import get_logger
from .git_restore import (
    clone_repo,
    commit_exists,
    current_origin_url,
    git_toplevel,
    restore_code,
    urls_match,
)
from .snapshot_loader import SnapshotData, load_and_validate_snapshot

logger = get_logger("replay.workspace")


# ---------------------------------------------------------------------------
# ReplayWorkspace
# ---------------------------------------------------------------------------


@dataclass
class ReplayWorkspace:
    """Tracks all paths and metadata for a single replay invocation."""

    root: Path
    snapshot: SnapshotData

    # Populated during prepare
    method: str = "unknown"
    base_commit: str = ""
    patches_applied: int = 0
    dirty_applied: bool = False
    venv_python: str = ""
    warnings: List[str] = field(default_factory=list)

    # Phase results
    prepare_ok: bool = False
    validate_ok: Optional[bool] = None
    run_exit_code: Optional[int] = None
    # One portable assignment per canonical target, produced by validation.
    resolved_inputs: Optional[Dict[str, Any]] = None

    @property
    def src_dir(self) -> Path:
        return self.root / "src"

    @property
    def bundle_dir(self) -> Path:
        return self.root / "bundle"

    @property
    def venv_dir(self) -> Path:
        return self.root / "venv"

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    def create_dirs(self) -> None:
        for d in (self.root, self.bundle_dir, self.artifacts_dir):
            d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Repo selection
# ---------------------------------------------------------------------------


@dataclass
class _RepoChoice:
    repo_root: Optional[Path] = None
    clone_url: Optional[str] = None
    label: str = ""


def _select_repo(snapshot: SnapshotData) -> _RepoChoice:
    snap_url = snapshot.git.remote_origin_url if snapshot.git else None

    cwd_top = git_toplevel()
    if cwd_top is not None:
        cwd_url = current_origin_url(cwd_top)
        if urls_match(cwd_url, snap_url):
            return _RepoChoice(repo_root=cwd_top, label="local repo (origin match)")

    if snap_url:
        return _RepoChoice(clone_url=snap_url, label=f"clone {snap_url}")

    raise RuntimeError(
        "Cannot determine repo source. The snapshot has no remote_origin_url, "
        "and the current directory is not in a matching git repo. "
        "Run this command from inside the project's git checkout."
    )


# ---------------------------------------------------------------------------
# Bundle extraction
# ---------------------------------------------------------------------------


def _extract_bundle(snapshot: SnapshotData, dest: Path) -> Path:
    """Extract config_bundle.tar.gz into *dest* and return the path."""
    tarball = snapshot.bundle_tarball
    if _fs.is_uri(tarball):
        with tempfile.TemporaryDirectory(prefix="pfig_replay_dl_") as tmp:
            local_tar = Path(tmp) / "config_bundle.tar.gz"
            _fs.cp(tarball, local_tar)
            dest.mkdir(parents=True, exist_ok=True)
            with tarfile.open(str(local_tar), "r:gz") as tf:
                _extractall_with_data_filter(tf, dest)
    else:
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tarball, "r:gz") as tf:
            _extractall_with_data_filter(tf, dest)

    logger.info(f"Extracted config bundle to {dest}")
    return dest


# ---------------------------------------------------------------------------
# prepare orchestrator
# ---------------------------------------------------------------------------


def replay_prepare(
    snapshot_path: str,
    *,
    workspace_path: Optional[str] = None,
    python: Optional[str] = None,
    no_deps: bool = False,
    dry_run: bool = False,
) -> ReplayWorkspace:
    """Prepare a replay workspace from a snapshot.

    If *dry_run* is True the plan is printed but nothing is executed;
    a ``ReplayWorkspace`` is still returned (with ``prepare_ok=False``).
    """
    snapshot = load_and_validate_snapshot(snapshot_path)
    choice = _select_repo(snapshot)
    logger.debug(f"Repo source: {choice.label}")

    ws_root = _resolve_workspace_root(snapshot, choice, workspace_path)
    ws = ReplayWorkspace(root=ws_root, snapshot=snapshot)
    logger.debug(f"Workspace root: {ws_root}")

    patches_dir: Optional[Path] = None
    if snapshot.has_patches() and not _fs.is_uri(snapshot.patches_dir):
        patches_dir = Path(snapshot.patches_dir)

    commit = snapshot.git.commit if snapshot.git else None
    upstream = snapshot.git.upstream_commit if snapshot.git else None
    commit_patch_names = snapshot.git.commit_patches if snapshot.git else []
    dirty_patch_names = snapshot.git.dirty_patches if snapshot.git else []

    steps = _build_plan(
        ws,
        choice,
        commit,
        upstream,
        commit_patch_names,
        dirty_patch_names,
        patches_dir,
        snapshot,
        python,
        no_deps,
    )

    if dry_run:
        _print_plan(snapshot, steps)
        return ws

    ws.create_dirs()

    # 1. Restore code
    if snapshot.git and commit:
        ws.method = restore_code(
            dest=ws.src_dir,
            commit=commit,
            upstream_commit=upstream,
            patches_dir=patches_dir,
            commit_patch_names=commit_patch_names,
            dirty_patch_names=dirty_patch_names,
            repo_root=choice.repo_root,
            clone_url=choice.clone_url,
        )
        ws.base_commit = commit
        ws.patches_applied = len(commit_patch_names)
        ws.dirty_applied = bool(dirty_patch_names)
    else:
        ws.method = "bundle-only"
        ws.warnings.append("No git info in snapshot; code restored from bundle only")

    # 2. Extract bundle
    _extract_bundle(snapshot, ws.bundle_dir)

    # 3. Venv + deps
    if not no_deps:
        from .venv import create_venv_and_install

        create_venv_and_install(ws, python_path=python)
    else:
        ws.warnings.append("Dependency installation skipped (--no-deps)")

    # 4. LFS / submodule warnings
    if snapshot.git:
        if snapshot.git.uses_lfs:
            msg = (
                "Snapshot repo uses Git LFS. Pointer files were restored; "
                "run 'git lfs pull' inside workspace/src/ to fetch real content."
            )
            ws.warnings.append(msg)
            logger.warning(msg)
        if snapshot.git.has_submodules:
            msg = (
                "Snapshot repo has submodules (not captured in patches). "
                "Run 'git submodule update --init' inside workspace/src/ if needed."
            )
            ws.warnings.append(msg)
            logger.warning(msg)

    ws.prepare_ok = True
    logger.info(f"Replay workspace ready: {ws.root}")
    return ws


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _resolve_workspace_root(
    snapshot: SnapshotData,
    choice: _RepoChoice,
    override: Optional[str],
) -> Path:
    if override:
        return Path(override).resolve()

    if choice.repo_root:
        base = choice.repo_root / ".pfig" / "replays"
    else:
        base = Path.cwd() / ".pfig" / "replays"

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return base / snapshot.run_id / ts


def _build_plan(
    ws: ReplayWorkspace,
    choice: _RepoChoice,
    commit: Optional[str],
    upstream: Optional[str],
    commit_names: List[str],
    dirty_names: List[str],
    patches_dir: Optional[Path],
    snapshot: SnapshotData,
    python: Optional[str],
    no_deps: bool,
) -> List[str]:
    steps: List[str] = []
    steps.append(f"Create workspace at {ws.root}")

    if snapshot.git and commit:
        if choice.repo_root:
            steps.append(f"Create detached worktree at workspace/src/ from commit {commit[:12]}")
        elif choice.clone_url:
            steps.append(f"Clone {choice.clone_url} into workspace/src/")
            steps.append(f"Checkout commit {commit[:12]}")

        if commit_names:
            steps.append(f"Apply {len(commit_names)} commit patch(es) ({', '.join(commit_names)})")
        if dirty_names:
            steps.append("Apply dirty.patch")
    else:
        steps.append("No git info; code will come from config bundle only")

    steps.append(f"Extract config bundle to workspace/bundle/")

    if not no_deps:
        py_label = python or f"Python {snapshot.python_version}"
        steps.append(f"Create venv at workspace/venv/ ({py_label})")
        if snapshot.has_requirements():
            steps.append("Install packages from requirements.lock")
    else:
        steps.append("Skip dependency installation (--no-deps)")

    return steps


def _print_plan(snapshot: SnapshotData, steps: List[str]) -> None:
    print(f"Replay plan for snapshot {snapshot.run_id}:")
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step}")
    print()
    print("Run without --dry-run to execute.")
