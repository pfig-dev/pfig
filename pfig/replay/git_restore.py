"""Git operations for replay: worktree, clone, patch application."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import List, Optional

from ..logging import get_logger

logger = get_logger("replay.git")


# ---------------------------------------------------------------------------
# URL normalisation
# ---------------------------------------------------------------------------

_SCP_RE = re.compile(r"^[\w.-]+@([\w.-]+):(.*?)(?:\.git)?$")
_SSH_URL_RE = re.compile(r"^ssh://[\w.-]+@([\w.-]+)/(.*?)(?:\.git)?/?$")
_HTTPS_RE = re.compile(r"^https?://([\w.-]+)/(.*?)(?:\.git)?/?$")


def normalize_git_url(url: str) -> str:
    """Normalise a git remote URL to ``host/path`` for comparison.

    Handles SCP-style SSH (``git@host:org/repo.git``),
    SSH URLs (``ssh://git@host/org/repo.git``), and HTTPS
    (``https://host/org/repo.git``) forms.  Returns the lowered
    ``host/path`` with ``.git`` suffix stripped.
    """
    for pattern in (_SCP_RE, _SSH_URL_RE, _HTTPS_RE):
        m = pattern.match(url)
        if m:
            return f"{m.group(1)}/{m.group(2)}".lower()
    return url.rstrip("/").lower().removesuffix(".git")


def urls_match(a: Optional[str], b: Optional[str]) -> bool:
    """Return True if two git remote URLs point at the same repo."""
    if a is None or b is None:
        return False
    return normalize_git_url(a) == normalize_git_url(b)


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _run(
    args: List[str],
    cwd: Optional[Path] = None,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess:
    logger.debug(f"git {' '.join(args)}")
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip()
        cmd_str = f"git {' '.join(args)}"
        msg = f"`{cmd_str}` failed (exit {result.returncode})"
        if stderr:
            msg += f": {stderr}"
        raise RuntimeError(msg)
    return result


def _run_quiet(args: List[str], cwd: Optional[Path] = None) -> Optional[str]:
    """Run a git command, return stdout or ``None`` on failure."""
    try:
        r = _run(args, cwd=cwd, check=False)
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


# ---------------------------------------------------------------------------
# Repo detection
# ---------------------------------------------------------------------------


def git_toplevel(cwd: Optional[Path] = None) -> Optional[Path]:
    """Return the repo root for *cwd*, or ``None`` if not in a git repo."""
    out = _run_quiet(["rev-parse", "--show-toplevel"], cwd=cwd)
    return Path(out) if out else None


def current_origin_url(cwd: Optional[Path] = None) -> Optional[str]:
    return _run_quiet(["remote", "get-url", "origin"], cwd=cwd)


def commit_exists(sha: str, cwd: Optional[Path] = None) -> bool:
    return _run_quiet(["cat-file", "-t", sha], cwd=cwd) == "commit"


# ---------------------------------------------------------------------------
# Worktree / clone
# ---------------------------------------------------------------------------


def create_worktree(repo_root: Path, dest: Path, commit: str) -> None:
    """Create a detached worktree at *dest* checked out to *commit*.

    Prunes stale worktree references first so that re-using a workspace
    path after a failed run doesn't leave git in a broken state.
    """
    _run(["worktree", "prune"], cwd=repo_root, check=False)
    _run(["worktree", "add", "--detach", str(dest), commit], cwd=repo_root)
    logger.info(f"Created worktree at {dest} (detached {commit[:12]})")


def clone_repo(url: str, dest: Path) -> None:
    """Clone *url* into *dest*."""
    _run(["clone", url, str(dest)], timeout=600)
    logger.info(f"Cloned {url} into {dest}")


def checkout(commit: str, cwd: Path) -> None:
    _run(["checkout", commit], cwd=cwd)
    logger.info(f"Checked out {commit[:12]}")


# ---------------------------------------------------------------------------
# Patch application
# ---------------------------------------------------------------------------


def apply_commit_patches(patches: List[Path], cwd: Path) -> int:
    """Apply commit patches via ``git am``.  Returns count applied."""
    if not patches:
        return 0
    sorted_patches = sorted(p.resolve() for p in patches)
    try:
        _run(["am"] + [str(p) for p in sorted_patches], cwd=cwd)
    except RuntimeError:
        _run(["am", "--abort"], cwd=cwd, check=False)
        raise
    logger.info(f"Applied {len(sorted_patches)} commit patch(es)")
    return len(sorted_patches)


def apply_dirty_patch(patch: Path, cwd: Path) -> None:
    """Apply a dirty (uncommitted-changes) patch via ``git apply``."""
    _run(["apply", str(patch.resolve())], cwd=cwd)
    logger.info(f"Applied dirty patch: {patch.name}")


def restore_code(
    *,
    dest: Path,
    commit: str,
    upstream_commit: Optional[str],
    patches_dir: Optional[Path],
    commit_patch_names: List[str],
    dirty_patch_names: List[str],
    repo_root: Optional[Path] = None,
    clone_url: Optional[str] = None,
) -> str:
    """Set up *dest* with the snapshot's code state.

    Returns a short description of the method used (for the report).
    """
    has_patches = bool(commit_patch_names)
    if repo_root is not None:
        base = _pick_base(commit, upstream_commit, has_patches, repo_root)
        create_worktree(repo_root, dest, base)
        method = "worktree"
    elif clone_url is not None:
        clone_repo(clone_url, dest)
        base = _pick_base(commit, upstream_commit, has_patches, dest)
        checkout(base, dest)
        method = "clone"
    else:
        raise RuntimeError("No repo_root or clone_url provided for code restore")

    _apply_patches(dest, patches_dir, commit_patch_names, dirty_patch_names)
    return method


def _pick_base(
    commit: str,
    upstream_commit: Optional[str],
    has_patches: bool,
    cwd: Path,
) -> str:
    """Choose the best commit to check out before applying patches.

    When commit patches will be applied, prefer *upstream_commit* (the
    state before the unpushed commits) so that ``git am`` can replay
    them cleanly.  Fall back to *commit* when no patches exist or the
    upstream is not reachable.
    """
    if has_patches and upstream_commit and commit_exists(upstream_commit, cwd):
        return upstream_commit
    if commit_exists(commit, cwd):
        return commit
    if upstream_commit and commit_exists(upstream_commit, cwd):
        return upstream_commit
    raise RuntimeError(
        f"Neither commit {commit[:12]} nor upstream {str(upstream_commit)[:12]} "
        f"is reachable. Push your commits or check your remote."
    )


def _safe_child(base: Path, name: str) -> Optional[Path]:
    """Resolve *name* under *base* and return it only if it stays inside *base*."""
    resolved = (base / name).resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError:
        logger.warning(f"Skipping patch with path traversal: {name!r}")
        return None
    return resolved if resolved.exists() else None


def _apply_patches(
    cwd: Path,
    patches_dir: Optional[Path],
    commit_names: List[str],
    dirty_names: List[str],
) -> None:
    if not patches_dir or (not commit_names and not dirty_names):
        return

    commit_files = [p for n in commit_names if (p := _safe_child(patches_dir, n)) is not None]
    if commit_files:
        apply_commit_patches(commit_files, cwd)

    for name in dirty_names:
        p = _safe_child(patches_dir, name)
        if p is not None:
            apply_dirty_patch(p, cwd)
