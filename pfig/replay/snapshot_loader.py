"""Load and validate a snapshot for replay."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import fs as _fs
from ..logging import get_logger
from ..snapshot import SNAPSHOT_VERSION, GitInfo, load_snapshot

logger = get_logger("replay.snapshot")


class SnapshotData:
    """Parsed and validated snapshot ready for replay."""

    def __init__(self, raw: Dict[str, Any], snapshot_dir: str | Path) -> None:
        self.raw = raw
        self.snapshot_dir = str(snapshot_dir)

        meta = raw["meta"]
        env = raw["environment"]

        self.run_id: str = meta["run_id"]
        self.digest: str = meta["digest"]
        self.entrypoint: str = meta["entrypoint"]
        self.pfig_version: str = meta["pfig_version"]
        self.pfig_files: List[str] = meta.get("pfig_files", [])

        self.python_version: str = env["python_version"]
        self.platform: str = env["platform"]
        self.working_dir: str = env.get("working_dir", "")
        self.command: List[str] = env.get("command", [])

        git_raw = env.get("git")
        self.git: Optional[GitInfo] = GitInfo.from_dict(git_raw) if git_raw else None

        self.externals: Dict[str, Any] = {}
        for name, info in env.get("externals", {}).items():
            self.externals[name] = info["value"]

        self.graph: Dict[str, Any] = raw.get("graph", {})
        self.warnings: List[Dict[str, str]] = raw.get("warnings", [])

    # ----- payload paths (relative to snapshot_dir) -----

    @property
    def bundle_tarball(self) -> str:
        return _join(self.snapshot_dir, "payload/config_bundle.tar.gz")

    @property
    def requirements_lock(self) -> str:
        return _join(self.snapshot_dir, "payload/requirements.lock")

    @property
    def patches_dir(self) -> str:
        return _join(self.snapshot_dir, "payload/patches")

    def has_bundle(self) -> bool:
        return _fs.exists(self.bundle_tarball)

    def has_requirements(self) -> bool:
        return _fs.exists(self.requirements_lock)

    def has_patches(self) -> bool:
        return _fs.exists(self.patches_dir)


def _join(base: str, rel: str) -> str:
    if _fs.is_uri(base):
        return f"{base.rstrip('/')}/{rel}"
    return str(Path(base) / rel)


def load_and_validate_snapshot(snapshot_path: str | Path) -> SnapshotData:
    """Load a snapshot and validate it has the fields needed for replay.

    Raises ``ValueError`` when required fields are missing and
    ``FileNotFoundError`` when the snapshot itself cannot be found.
    """
    raw = load_snapshot(snapshot_path)

    # Resolve the directory that contains log.json
    sp = str(snapshot_path)
    if _fs.is_uri(sp):
        snapshot_dir = sp.rstrip("/") if _fs.is_dir(sp) else sp.rsplit("/", 1)[0]
    else:
        p = Path(sp)
        snapshot_dir = str(p if p.is_dir() else p.parent)

    _require(raw, "meta", "meta")
    _require(raw["meta"], "run_id", "meta.run_id")
    _require(raw["meta"], "digest", "meta.digest")
    _require(raw["meta"], "entrypoint", "meta.entrypoint")
    _require(raw, "environment", "environment")
    if raw.get("version") != SNAPSHOT_VERSION:
        raise ValueError(
            f"Unsupported snapshot version {raw.get('version')!r}; this runtime requires "
            f"{SNAPSHOT_VERSION}. Replay older snapshots with their recorded pfig version."
        )

    snap = SnapshotData(raw, snapshot_dir)

    if not snap.has_bundle():
        raise ValueError(f"Snapshot payload missing config_bundle.tar.gz: {snap.bundle_tarball}")

    return snap


def _require(d: Dict, key: str, label: str) -> None:
    if key not in d:
        raise ValueError(f"Snapshot missing required field: {label}")
