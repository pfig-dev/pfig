"""Replay report generation: JSON and human-readable text."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict

from ..logging import get_logger

if TYPE_CHECKING:
    from .workspace import ReplayWorkspace

logger = get_logger("replay.report")


def write_report(ws: "ReplayWorkspace") -> None:
    """Write ``replay_report.json`` and ``replay_report.txt`` to the workspace."""
    data = _build_report_data(ws)

    json_path = ws.root / "replay_report.json"
    json_path.write_text(json.dumps(data, indent=2, default=str) + "\n")

    txt_path = ws.root / "replay_report.txt"
    txt_path.write_text(_format_text(data))

    logger.info(f"Replay report written to {ws.root}")


def _build_report_data(ws: "ReplayWorkspace") -> Dict[str, Any]:
    snap = ws.snapshot
    return {
        "snapshot": {
            "path": snap.snapshot_dir,
            "run_id": snap.run_id,
            "digest": snap.digest,
            "entrypoint": snap.entrypoint,
            "pfig_version": snap.pfig_version,
        },
        "workspace": str(ws.root),
        "git_restore": {
            "method": ws.method,
            "base_commit": ws.base_commit or None,
            "patches_applied": ws.patches_applied,
            "dirty_applied": ws.dirty_applied,
        },
        "python": {
            "snapshot_version": snap.python_version,
            "replay_venv_python": ws.venv_python or None,
        },
        "phases": {
            "prepare_ok": ws.prepare_ok,
            "validate_ok": ws.validate_ok,
            "run_exit_code": ws.run_exit_code,
        },
        "warnings": ws.warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _format_text(data: Dict[str, Any]) -> str:
    lines = [
        "=== pfig Replay Report ===",
        "",
        f"Snapshot:    {data['snapshot']['path']}",
        f"Run ID:      {data['snapshot']['run_id']}",
        f"Entrypoint:  {data['snapshot']['entrypoint']}",
        f"Digest:      {data['snapshot']['digest']}",
        f"Workspace:   {data['workspace']}",
        "",
        "--- Git Restore ---",
        f"Method:          {data['git_restore']['method']}",
        f"Base commit:     {data['git_restore']['base_commit'] or 'N/A'}",
        f"Patches applied: {data['git_restore']['patches_applied']}",
        f"Dirty applied:   {data['git_restore']['dirty_applied']}",
        "",
        "--- Python ---",
        f"Snapshot version:  {data['python']['snapshot_version']}",
        f"Replay venv:       {data['python']['replay_venv_python'] or 'N/A'}",
        "",
        "--- Phases ---",
        f"Prepare: {'OK' if data['phases']['prepare_ok'] else 'FAILED'}",
    ]

    if data["phases"]["validate_ok"] is not None:
        lines.append(f"Validate: {'OK' if data['phases']['validate_ok'] else 'FAILED'}")
    else:
        lines.append("Validate: not run")

    if data["phases"]["run_exit_code"] is not None:
        ec = data["phases"]["run_exit_code"]
        lines.append(f"Run:     exit {ec} ({'OK' if ec == 0 else 'FAILED'})")
    else:
        lines.append("Run:     not run")

    if data["warnings"]:
        lines.append("")
        lines.append("--- Warnings ---")
        for w in data["warnings"]:
            lines.append(f"  - {w}")

    lines.append("")
    lines.append(f"Generated at: {data['generated_at']}")
    lines.append("")
    return "\n".join(lines)
