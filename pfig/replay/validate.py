"""Digest validation for replay: recompile IR and compare."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional

from .. import fs as _fs
from ..bundle import load_bundle
from ..diff import DiffReport, compare_ir_graphs
from ..execution import RuntimeInputLayer, resolve_runtime_layers, validate_pfig
from ..logging import get_logger
from ..parser import parse_pfig_source
from ..snapshot import build_ir_graph, compute_graph_digest

if TYPE_CHECKING:
    from .workspace import ReplayWorkspace

logger = get_logger("replay.validate")


def _venv_site_packages_paths(venv_dir: Path) -> List[str]:
    """Return absolute ``site-packages`` paths for a stdlib-style venv layout."""
    if sys.platform == "win32":
        p = (venv_dir / "Lib" / "site-packages").resolve()
        return [str(p)] if p.is_dir() else []
    lib = venv_dir / "lib"
    if not lib.is_dir():
        return []
    out: List[str] = []
    for child in sorted(lib.iterdir()):
        if child.is_dir() and child.name.startswith("python"):
            sp = (child / "site-packages").resolve()
            if sp.is_dir():
                out.append(str(sp))
    return out


@contextmanager
def _replay_validation_syspath(ws: "ReplayWorkspace") -> Iterator[None]:
    """Match replay execution import resolution for in-process ``validate_pfig``.

    Execution runs ``python -m pfig`` from the replay venv with ``cwd=src``, so
    project code and locked dependencies are importable. Validation used to run
    only in the CLI interpreter, so workspace modules and venv-only packages
    (e.g. ``torch``) were missing.
    """
    ordered: List[str] = []
    if ws.src_dir.is_dir():
        ordered.append(str(ws.src_dir.resolve()))
    if ws.venv_python:
        ordered.extend(p for p in _venv_site_packages_paths(ws.venv_dir) if p not in ordered)

    inserted: List[str] = []
    for path in reversed(ordered):
        if path not in sys.path:
            sys.path.insert(0, path)
            inserted.append(path)
    try:
        yield
    finally:
        for path in inserted:
            try:
                sys.path.remove(path)
            except ValueError:
                pass


def replay_validate(
    ws: "ReplayWorkspace",
    *,
    set_overrides: Optional[Dict[str, Any]] = None,
    allow_baseline_mismatch: bool = False,
) -> bool:
    """Validate the baseline digest and optionally record override diff.

    Returns ``True`` if validation passes (or is allowed to pass).
    Updates *ws.validate_ok* accordingly.
    """
    ws.validate_ok = False
    ws.resolved_inputs = None
    expected_digest = ws.snapshot.digest

    with _replay_validation_syspath(ws):
        bundle_dir, manifest = load_bundle(ws.bundle_dir)

        entry_path = bundle_dir / manifest.entry
        config_src = _fs.read_text(entry_path)
        ast = parse_pfig_source(config_src, filename=str(entry_path))

        profile_paths = (
            [str(bundle_dir / p) for p in manifest.profiles] if manifest.profiles else []
        )

        # ----- baseline (no overrides) -----
        baseline_externals = dict(ws.snapshot.externals)

        validation = validate_pfig(
            ast,
            entrypoint=ws.snapshot.entrypoint,
            filename=str(entry_path),
            externals=baseline_externals,
            profile_paths=profile_paths or None,
        )

        baseline_graph = build_ir_graph(validation.compiled, validation.reachable_names)
        baseline_digest = compute_graph_digest(baseline_graph)

        _write_json(ws.artifacts_dir / "generated_ir.json", baseline_graph)

        if baseline_digest != expected_digest:
            msg = f"Baseline digest MISMATCH: expected {expected_digest}, got {baseline_digest}"
            logger.error(msg)
            ws.warnings.append(msg)

            report = compare_ir_graphs(ws.snapshot.graph, baseline_graph)
            _write_diff_artifact(ws, report, "diff.txt")
            _log_diff(report, "snapshot IR", "recompiled IR")

            if not allow_baseline_mismatch:
                ws.validate_ok = False
                return False
            else:
                logger.warning("Continuing despite mismatch (--allow-baseline-mismatch)")
        else:
            logger.info(f"Baseline digest OK: {baseline_digest}")

        # Recorded values and replay overrides are distinct precedence layers.
        # Resolve once here; the subprocess receives these same canonical winners.
        resolved = resolve_runtime_layers(
            validation.compiled["binding_origins"],
            validation.compiled["declared_externals"],
            [
                RuntimeInputLayer(validation.compiled["runtime_inputs"], "snapshot"),
                RuntimeInputLayer(set_overrides or {}),
            ],
        )
        execution_inputs = {a.path: a.value for a in resolved.values()}

        # ----- override pass (informational) -----
        if set_overrides:
            override_externals = execution_inputs
            ov_validation = validate_pfig(
                ast,
                entrypoint=ws.snapshot.entrypoint,
                filename=str(entry_path),
                externals=override_externals,
                profile_paths=profile_paths or None,
            )
            override_graph = build_ir_graph(ov_validation.compiled, ov_validation.reachable_names)
            _write_json(ws.artifacts_dir / "override_ir.json", override_graph)

            override_report = compare_ir_graphs(baseline_graph, override_graph)
            _write_diff_artifact(ws, override_report, "override_diff.txt")
            _log_diff(override_report, "baseline IR", "override IR")

            override_digest = compute_graph_digest(override_graph)
            logger.info(f"Override digest: {override_digest} (baseline: {baseline_digest})")

        ws.resolved_inputs = execution_inputs
        ws.validate_ok = True
        return True


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _fs.write_json(path, data)


def _write_diff_artifact(
    ws: "ReplayWorkspace",
    report: DiffReport,
    filename: str,
) -> None:
    """Write a textual diff artifact from a ``DiffReport``."""
    dest = ws.artifacts_dir / filename
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not report.has_changes:
        dest.write_text("(no differences)\n")
    else:
        dest.write_text(_format_diff_text(report))

    logger.info(f"Wrote diff artifact: {dest}")


def _format_diff_text(report: DiffReport) -> str:
    """Render a ``DiffReport`` as human-readable text for artifact files."""
    from ..diff import DiffChangeType

    lines: list[str] = []

    for name in report.removed:
        lines.append(f"- {name}")

    for name in report.added:
        lines.append(f"+ {name}")

    for name, detail in sorted(report.modified.items()):
        if detail.change_type == DiffChangeType.TYPE_CHANGED:
            lines.append(f"~ {name}:")
            lines.append(f"  old: {detail.old_value!r}")
            lines.append(f"  new: {detail.new_value!r}")
        elif detail.sub_diffs:
            lines.append(f"~ {name}:")
            for sub_key, sub in detail.sub_diffs.items():
                if sub.change_type == DiffChangeType.ADDED:
                    lines.append(f"  + {sub_key}: {sub.new_value!r}")
                elif sub.change_type == DiffChangeType.REMOVED:
                    lines.append(f"  - {sub_key}: {sub.old_value!r}")
                else:
                    lines.append(f"  ~ {sub_key}: {sub.old_value!r} -> {sub.new_value!r}")
        else:
            lines.append(f"~ {name}:")
            lines.append(f"  old: {detail.old_value!r}")
            lines.append(f"  new: {detail.new_value!r}")

    return "\n".join(lines) + "\n"


def _log_diff(report: DiffReport, label_a: str, label_b: str) -> None:
    """Log a summary of the diff to the console."""
    from ..diff import log_diff_details, log_diff_summary

    if not report.has_changes:
        return

    log_diff_summary(report, label_a, label_b)
    log_diff_details(report)
