"""Replay execution: run the entrypoint using the bundle code path."""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..logging import get_logger
from .report import write_report

if TYPE_CHECKING:
    from .workspace import ReplayWorkspace

logger = get_logger("replay.runner")


def replay_run(
    ws: "ReplayWorkspace",
    *,
    set_overrides: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
    allow_baseline_mismatch: bool = False,
    verbose: int = 0,
) -> int:
    """Full replay: prepare (already done) -> validate -> execute.

    Expects *ws* to have been prepared via ``replay_prepare``.
    Returns the process exit code (0 = success).
    """
    if not ws.prepare_ok:
        logger.error("Workspace not prepared; call replay_prepare first")
        return 2

    # ----- validate -----
    from .validate import replay_validate

    ok = replay_validate(
        ws,
        set_overrides=set_overrides,
        allow_baseline_mismatch=allow_baseline_mismatch,
    )
    if not ok:
        logger.error("Validation failed; aborting run")
        write_report(ws)
        return 1

    if dry_run:
        return 0

    # ----- execute -----
    exit_code = _execute(ws, verbose=verbose)
    ws.run_exit_code = exit_code

    write_report(ws)
    return exit_code


def _execute(ws: "ReplayWorkspace", *, verbose: int = 0) -> int:
    """Invoke pfig run --bundle with the canonical inputs checked by validation."""
    if ws.resolved_inputs is None:
        raise RuntimeError("Replay inputs have not been validated")
    python = ws.venv_python or sys.executable
    bundle_path = str(ws.bundle_dir)

    cmd: List[str] = [
        python,
        "-m",
        "pfig",
    ]

    if verbose:
        cmd.append("-" + "v" * verbose)

    snapshot_dir = str(ws.artifacts_dir / "snapshot")
    cmd.extend(
        [
            "run",
            "--bundle",
            bundle_path,
            "--entrypoint",
            ws.snapshot.entrypoint,
            "--snapshot",
            snapshot_dir,
        ]
    )

    # Pass base commit so snapshot can capture patch series (worktree has no @{u})
    if ws.snapshot.git and ws.snapshot.git.upstream_commit:
        cmd.extend(["--base-commit", ws.snapshot.git.upstream_commit])

    # Do not merge paths again: aliases are already resolved by validation.
    if ws.resolved_inputs:
        set_args: List[str] = []
        for k, v in sorted(ws.resolved_inputs.items()):
            set_args.append(f"{k}={_format_value(v)}")
        cmd.extend(["--set"] + set_args)

    cwd = ws.src_dir if ws.src_dir.exists() else ws.root
    log_path = ws.artifacts_dir / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Executing replay: {' '.join(cmd[:6])} ...")
    logger.debug(f"Full command: {cmd}")
    logger.debug(f"CWD: {cwd}")

    with open(log_path, "w") as log_file:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            log_file.write(line)
        proc.wait(timeout=3600)

    if proc.returncode == 0:
        logger.info(f"Replay execution succeeded (exit 0)")
    else:
        logger.error(f"Replay execution failed (exit {proc.returncode})")
    logger.info(f"Run log: {log_path}")

    return proc.returncode


def _format_value(v: Any) -> str:
    """Format a value for passing via --set on the CLI (list argv, no shell).

    Uses ``repr`` so values round-trip through ``parse_set_args`` / ``_parse_cli_value``.
    """
    return repr(v)
