"""Explicit filesystem include resolution; no search paths or package discovery."""

from dataclasses import dataclass
from pathlib import Path

from . import fs as _fs


@dataclass
class IncludeResolution:
    """Resolved include location, retained for file discovery and bundling."""

    path: Path


def resolve_include_path(include_string: str, base_path: Path) -> IncludeResolution:
    """Resolve a filesystem path relative to the file containing the include."""
    if "://" in include_string:
        raise ValueError(
            f"Protocol-based includes are not supported: {include_string!r}. "
            "Use an explicit filesystem path."
        )
    resolved_path = (base_path.parent / include_string).resolve()
    if not _fs.exists(resolved_path):
        raise FileNotFoundError(
            f"Include file not found: {include_string} (resolved to {resolved_path})"
        )
    return IncludeResolution(path=resolved_path)
