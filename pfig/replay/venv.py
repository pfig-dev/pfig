"""Venv creation and dependency installation for replay."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

from .. import fs as _fs
from ..logging import get_logger
from .git_restore import normalize_git_url

if TYPE_CHECKING:
    from .workspace import ReplayWorkspace

logger = get_logger("replay.venv")

_VENV_BIN = "Scripts" if sys.platform == "win32" else "bin"


_VCS_RE = re.compile(
    r"^(?:-e\s+|--editable\s+)"
    r"git\+(?P<url>.+)@(?P<ref>[^@#]+)"
    r"(?:#egg=(?P<egg>\S+))?"
)


def _has_uv() -> Optional[str]:
    """Return the path to ``uv`` if available, else ``None``."""
    return shutil.which("uv")


def _is_editable_line(line: str) -> bool:
    """Return True if a requirements line is an editable/VCS install."""
    stripped = line.strip()
    return stripped.startswith("-e ") or stripped.startswith("--editable")


def _split_lock_file(lock_path: str) -> Tuple[List[str], List[str]]:
    """Split a requirements file into (editable_lines, regular_lines)."""
    editables: List[str] = []
    regulars: List[str] = []
    with open(lock_path) as f:
        for line in f:
            if _is_editable_line(line):
                editables.append(line)
            else:
                regulars.append(line)
    return editables, regulars


def _filter_editables(
    lines: List[str],
    snapshot_url: Optional[str],
) -> List[str]:
    """Drop VCS editable lines that reference the snapshot's own repo.

    The project source is already present in the replay worktree, so
    re-installing it via pip is redundant and will fail when the pinned
    commit hasn't been pushed to the remote.
    """
    if not snapshot_url:
        return lines

    snap_norm = normalize_git_url(snapshot_url)
    result: List[str] = []
    for line in lines:
        m = _VCS_RE.match(line.strip())
        if m and normalize_git_url(m.group("url")) == snap_norm:
            logger.info(
                f"Dropping editable for snapshot repo (source already in worktree): {line.strip()}"
            )
            continue
        result.append(line)
    return result


def create_venv_and_install(
    ws: "ReplayWorkspace",
    python_path: Optional[str] = None,
) -> None:
    """Create an isolated venv and install requirements.lock into it."""
    python = python_path or sys.executable
    venv_dir = ws.venv_dir
    uv = _has_uv()

    lock_path = _resolve_lock_path(ws)

    # When uv is available and the lock file has editable lines, we need
    # pip in the venv for those lines.  ``uv venv --seed`` includes pip.
    has_editables = False
    if lock_path:
        editables, _ = _split_lock_file(lock_path)
        has_editables = bool(editables)

    if uv:
        seed_flag = ["--seed"] if has_editables else []
        logger.info(f"Creating venv at {venv_dir} (using uv)")
        logger.debug(f"Using Python interpreter: {python}")
        subprocess.run(
            [uv, "venv", str(venv_dir), "--python", python] + seed_flag,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    else:
        logger.info(f"Creating venv at {venv_dir}")
        logger.debug(f"Using Python interpreter: {python}")
        subprocess.run(
            [python, "-m", "venv", str(venv_dir)],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )

    venv_python = venv_dir / _VENV_BIN / "python"
    ws.venv_python = str(venv_python)

    # Check for python version mismatch
    snap_py = ws.snapshot.python_version
    actual = _get_python_version(venv_python)
    if actual and actual != snap_py:
        msg = f"Python version mismatch: snapshot={snap_py}, replay venv={actual}"
        ws.warnings.append(msg)
        logger.warning(msg)

    # Install requirements
    if lock_path is None:
        return

    snap_url = ws.snapshot.git.remote_origin_url if ws.snapshot.git else None
    if uv:
        _install_with_uv_split(uv, venv_dir, lock_path, snap_url)
    else:
        _install_with_pip(venv_dir, lock_path, snap_url)


def _resolve_lock_path(ws: "ReplayWorkspace") -> Optional[str]:
    """Resolve requirements.lock to a local path, or None if unavailable."""
    lock_path = ws.snapshot.requirements_lock
    if not _fs.exists(lock_path):
        ws.warnings.append("requirements.lock not found; skipping pip install")
        logger.warning("requirements.lock not found in snapshot payload")
        return None

    if _fs.is_uri(lock_path):
        local_lock = ws.artifacts_dir / "requirements.lock"
        _fs.cp(lock_path, local_lock)
        return str(local_lock)

    return str(lock_path)


def _install_with_uv_split(
    uv: str,
    venv_dir: Path,
    lock_path: str,
    snapshot_url: Optional[str] = None,
) -> None:
    """Install requirements using uv, falling back to pip for editables."""
    import logging

    editables, regulars = _split_lock_file(lock_path)
    editables = _filter_editables(editables, snapshot_url)
    venv_python = str(venv_dir / _VENV_BIN / "python")
    verbose = logger.isEnabledFor(logging.DEBUG)

    # Install editable/VCS lines with pip (uv doesn't support these)
    if editables:
        pip = venv_dir / _VENV_BIN / "pip"
        logger.info(f"Installing {len(editables)} editable requirement(s) with pip")
        _log_packages(editables, label="editable")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix="pfig_editables_", delete=False
        ) as f:
            f.writelines(editables)
            editables_path = f.name
        try:
            _run_install(
                [str(pip), "install", "--no-deps", "-r", editables_path],
                label="pip",
                verbose=verbose,
            )
        finally:
            Path(editables_path).unlink(missing_ok=True)

    # Install the rest with uv
    if regulars:
        logger.info(
            f"Installing {_pkg_count(regulars)} package(s) from requirements.lock (using uv)"
        )
        _log_packages(regulars)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix="pfig_regulars_", delete=False
        ) as f:
            f.writelines(regulars)
            regulars_path = f.name
        try:
            _run_install(
                [
                    uv,
                    "pip",
                    "install",
                    "--no-deps",
                    "--python",
                    venv_python,
                    "-r",
                    regulars_path,
                ],
                label="uv pip",
                verbose=verbose,
            )
        finally:
            Path(regulars_path).unlink(missing_ok=True)

    logger.info(f"Dependencies installed into {venv_dir}")


def _run_install(cmd: List[str], *, label: str, verbose: bool, timeout: int = 600) -> None:
    """Run an install command, streaming output in verbose mode."""
    if verbose:
        result = subprocess.run(cmd, timeout=timeout)
    else:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        stderr = getattr(result, "stderr", None) or ""
        logger.error(f"{label} install failed:\n{stderr}")
        raise RuntimeError(f"{label} install failed (exit {result.returncode})")


def _install_with_pip(
    venv_dir: Path,
    lock_path: str,
    snapshot_url: Optional[str] = None,
) -> None:
    """Install all requirements using the venv's ``pip``."""
    import logging

    pip = venv_dir / _VENV_BIN / "pip"
    verbose = logger.isEnabledFor(logging.DEBUG)

    editables, regulars = _split_lock_file(lock_path)
    filtered = _filter_editables(editables, snapshot_url)
    effective_path = lock_path
    if len(filtered) < len(editables):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix="pfig_rewritten_", delete=False
        ) as f:
            f.writelines(filtered + regulars)
            effective_path = f.name

    all_lines = filtered + regulars
    logger.info(f"Installing {_pkg_count(all_lines)} package(s) from requirements.lock")
    _log_packages(all_lines)
    try:
        _run_install(
            [str(pip), "install", "--no-deps", "-r", effective_path],
            label="pip",
            verbose=verbose,
        )
    finally:
        if effective_path != lock_path:
            Path(effective_path).unlink(missing_ok=True)
    logger.info(f"Dependencies installed into {venv_dir}")


def _pkg_count(lines: List[str]) -> int:
    """Count non-empty, non-comment requirement lines."""
    return sum(1 for line in lines if line.strip() and not line.strip().startswith("#"))


def _log_packages(lines: List[str], *, label: str = "package") -> None:
    """Log each requirement line at DEBUG level."""
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            logger.debug(f"  {label}: {stripped}")


def _get_python_version(python: Path) -> Optional[str]:
    try:
        r = subprocess.run(
            [
                str(python),
                "-c",
                "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
