"""
pfig Snapshot Module - Creates immutable, deterministic records of resolved configurations.

This module implements the dual-artifact snapshot strategy:
1. Payload: configs/ (raw .pfig files) + requirements.lock (pip freeze)
2. Log: log.json (flattened IR graph with resolved defaults)

The snapshot captures exactly what ran, enabling reproducibility, semantic diffing,
and audit trails without requiring code execution to inspect.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import platform
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from . import fs as _fs
from .ast_nodes import BinaryOpExpr, UnaryOpExpr
from .execution import (
    FreshProviderCall,
    LiteralValue,
    ProviderMethodRef,
    ProviderRef,
    ProviderSpec,
    resolve_fresh_recipe,
)
from .logging import get_logger

logger = get_logger("snapshot")

# Schema version for log.json
SNAPSHOT_VERSION = "2.0"


# ===== Introspection Types =====


class IntrospectionStatus(str, Enum):
    """Status of signature introspection for a provider."""

    OK = "ok"  # Full signature introspection succeeded; all defaults captured
    PARTIAL = "partial"  # Some kwargs inspected, but defaults may be incomplete
    FAILED = "failed"  # No introspection possible; only explicit kwargs recorded


class OpaqueReasonCode(str, Enum):
    """Reason codes for why introspection failed or was incomplete."""

    C_EXTENSION = "c_extension"  # C extension without __signature__
    BUILTIN = "builtin"  # Built-in function/method
    NO_SIGNATURE = "no_signature"  # inspect.signature() raised ValueError
    VARARGS_ONLY = "varargs_only"  # Signature is *args, **kwargs only


@dataclass
class IntrospectionInfo:
    """Introspection metadata for a provider node."""

    status: IntrospectionStatus
    defaults_source: str  # "signature" | "explicit" | "none"
    opaque_reason: Optional[Dict[str, str]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for JSON output."""
        result: Dict[str, Any] = {
            "status": self.status.value,
            "defaults_source": self.defaults_source,
        }
        if self.opaque_reason:
            result["opaque_reason"] = self.opaque_reason
        return result


# ===== Data Classes =====


@dataclass
class GitInfo:
    """Git repository information."""

    commit: Optional[str] = None
    is_dirty: bool = False
    remote_origin_url: Optional[str] = None
    # None = no upstream configured, 0 = synced, N = N commits ahead
    commits_ahead: Optional[int] = None
    # The upstream tracking branch commit (for applying patch series)
    upstream_commit: Optional[str] = None
    # Patch series: individual commit patches from git format-patch
    commit_patches: List[str] = field(default_factory=list)
    # Dirty patches for uncommitted changes (applied with git apply, not git am)
    dirty_patches: List[str] = field(default_factory=list)
    # Whether the repo uses Git LFS (affects reproducibility)
    uses_lfs: bool = False
    # Whether the repo has submodules (not supported for reproduction)
    has_submodules: bool = False

    def to_dict(self) -> Optional[Dict[str, Any]]:
        """Convert to dictionary, returning None if no git info available."""
        if self.commit is None:
            return None
        result = {
            "commit": self.commit,
            "is_dirty": self.is_dirty,
            "remote_origin_url": self.remote_origin_url,
        }
        if self.commits_ahead is None:
            result["has_upstream"] = False
        else:
            result["has_upstream"] = True
            if self.commits_ahead > 0:
                result["commits_ahead"] = self.commits_ahead
            if self.upstream_commit:
                result["upstream_commit"] = self.upstream_commit
        # Serialize patches as structured object
        if self.commit_patches or self.dirty_patches:
            patches_dict: Dict[str, Any] = {}
            if self.commit_patches:
                patches_dict["commits"] = self.commit_patches
            if self.dirty_patches:
                patches_dict["dirty"] = self.dirty_patches
            result["patches"] = patches_dict
        if self.uses_lfs:
            result["uses_lfs"] = True
        if self.has_submodules:
            result["has_submodules"] = True
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GitInfo":
        """Reconstruct a GitInfo from the dict produced by ``to_dict``."""
        patches = data.get("patches", {})
        has_upstream = data.get("has_upstream", False)
        return cls(
            commit=data.get("commit"),
            is_dirty=data.get("is_dirty", False),
            remote_origin_url=data.get("remote_origin_url"),
            commits_ahead=data.get("commits_ahead", 0) if has_upstream else None,
            upstream_commit=data.get("upstream_commit"),
            commit_patches=patches.get("commits", []),
            dirty_patches=patches.get("dirty", []),
            uses_lfs=data.get("uses_lfs", False),
            has_submodules=data.get("has_submodules", False),
        )


@dataclass
class ExternalInfo:
    """Information about an external value."""

    value: Any
    source: str  # "cli", "profile", or "default"

    def to_dict(self) -> Dict[str, Any]:
        return {"value": self.value, "source": self.source}


@dataclass
class EnvironmentInfo:
    """Runtime environment information."""

    python_version: str
    platform: str
    command: List[str]
    working_dir: str
    git: Optional[GitInfo] = None
    externals: Dict[str, ExternalInfo] = field(default_factory=dict)
    requirements_lock_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "python_version": self.python_version,
            "platform": self.platform,
            "command": self.command,
            "working_dir": self.working_dir,
        }
        if self.git:
            result["git"] = self.git.to_dict()
        if self.externals:
            result["externals"] = {k: v.to_dict() for k, v in self.externals.items()}
        if self.requirements_lock_path:
            result["requirements_lock_path"] = self.requirements_lock_path
        return result


@dataclass
class SnapshotMeta:
    """Metadata about the snapshot."""

    pfig_version: str
    generated_at: str
    entrypoint: str
    digest: str
    run_id: str
    pfig_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pfig_version": self.pfig_version,
            "generated_at": self.generated_at,
            "entrypoint": self.entrypoint,
            "digest": self.digest,
            "run_id": self.run_id,
            "pfig_files": self.pfig_files,
        }


# ===== Environment Collection =====


def _run_git_command(args: List[str], cwd: Optional[Path] = None) -> Optional[str]:
    """Run a git command and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def collect_git_info(
    working_dir: Optional[Path] = None,
    base_commit: Optional[str] = None,
) -> Optional[GitInfo]:
    """Collect git repository information from the working directory."""
    cwd = working_dir or Path.cwd()

    # Check if in a git repo
    commit = _run_git_command(["rev-parse", "HEAD"], cwd)
    if commit is None:
        return None

    # Check if dirty (only tracked files, ignore untracked)
    status = _run_git_command(["status", "--porcelain", "--untracked-files=no"], cwd)
    is_dirty = bool(status)  # Non-empty means dirty

    # Get remote origin URL
    remote_url = _run_git_command(["remote", "get-url", "origin"], cwd)

    # Check for unpushed commits (commits ahead of upstream)
    # This helps identify if the recorded commit is reachable from remote
    # None = no upstream configured, 0 = synced, N = N commits ahead
    commits_ahead: Optional[int] = None
    upstream_commit: Optional[str] = None

    if base_commit is not None:
        # Explicit base (e.g. replay worktree with detached HEAD)
        ahead_count = _run_git_command(["rev-list", "--count", f"{base_commit}..HEAD"], cwd)
        if ahead_count is not None:
            try:
                commits_ahead = int(ahead_count)
                upstream_commit = base_commit
            except ValueError:
                pass
    else:
        # Use upstream tracking ref (normal run with branch)
        ahead_count = _run_git_command(["rev-list", "--count", "@{u}..HEAD"], cwd)
        if ahead_count is not None:
            try:
                commits_ahead = int(ahead_count)
                upstream_commit = _run_git_command(["rev-parse", "@{u}"], cwd)
            except ValueError:
                pass  # Keep as None (no upstream)

    # Check if repo uses Git LFS (has tracked LFS files)
    lfs_files = _run_git_command(["lfs", "ls-files", "-n"], cwd)
    uses_lfs = bool(lfs_files)  # Non-empty means LFS is in use

    # Check if repo has submodules
    has_submodules = (cwd / ".gitmodules").exists()

    return GitInfo(
        commit=commit,
        is_dirty=is_dirty,
        remote_origin_url=remote_url,
        commits_ahead=commits_ahead,
        upstream_commit=upstream_commit,
        uses_lfs=uses_lfs,
        has_submodules=has_submodules,
    )


def collect_environment(
    externals: Optional[Dict[str, Any]] = None,
    external_sources: Optional[Dict[str, str]] = None,
    requirements_lock_path: Optional[str] = None,
) -> EnvironmentInfo:
    """
    Collect runtime environment information.

    Args:
        externals: External values provided at runtime
        external_sources: Mapping of external names to their source ("cli", "profile")
        requirements_lock_path: Relative path to requirements.lock in snapshot

    Returns:
        EnvironmentInfo with collected data
    """
    externals = externals or {}
    external_sources = external_sources or {}

    # Build external info with sources
    external_infos = {}
    for name, value in externals.items():
        source = external_sources.get(name, "cli")
        external_infos[name] = ExternalInfo(value=value, source=source)

    return EnvironmentInfo(
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        platform=f"{platform.system().lower()}-{platform.machine()}",
        command=sys.argv.copy(),
        working_dir=str(Path.cwd()),
        git=collect_git_info(),
        externals=external_infos,
        requirements_lock_path=requirements_lock_path,
    )


# ===== IR Graph Building =====


def _get_callable_fqn(obj: Any) -> str:
    """Get the fully qualified name of a callable."""
    if hasattr(obj, "__module__") and hasattr(obj, "__qualname__"):
        module = obj.__module__
        if module and module != "builtins":
            return f"{module}.{obj.__qualname__}"
        return obj.__qualname__
    elif hasattr(obj, "__name__"):
        return obj.__name__
    return str(obj)


def _serialize_value(value: Any, compiled: Dict[str, Any] | None = None) -> Any:
    """
    Serialize a value to JSON-compatible format.

    Handles:
    - LiteralValue -> unwrapped value
    - ProviderRef -> {"$ref": "name"}
    - ProviderMethodRef -> {"$ref": "name", "method": "a"} or {"$ref": "name", "attrs": ["a", "b"]}
    - FreshProviderCall -> serialized with overlay kwargs
    - ProviderSpec -> serialized inline (for anonymous providers)
    - Python callables -> {"$import": "fqn"}
    - Lists, dicts, tuples -> recursively serialized
    """
    if isinstance(value, LiteralValue):
        return _serialize_value(value.value, compiled)

    elif isinstance(value, ProviderRef):
        return {"$ref": value.name}

    elif isinstance(value, ProviderMethodRef):
        if len(value.attrs) == 1:
            return {"$ref": value.name, "method": value.attrs[0]}
        return {"$ref": value.name, "attrs": list(value.attrs)}

    elif isinstance(value, FreshProviderCall):
        if compiled is not None:
            spec = resolve_fresh_recipe(value, compiled)
            kwargs, introspection = _get_resolved_kwargs(spec.callable_obj, spec.kwargs)
            recipe = {
                "$inline_provider": True,
                "target": _get_callable_fqn(spec.callable_obj),
                "kwargs": {k: _serialize_value(v, compiled) for k, v in sorted(kwargs.items())},
                "introspection": introspection.to_dict(),
            }
            if spec.args:
                recipe["args"] = [_serialize_value(arg, compiled) for arg in spec.args]
            return {"$fresh": True, "recipe": recipe}
        # All-bindings views retain the authored recipe reference and overlay.
        return {
            "$ref": value.provider_name,
            "$fresh": True,
            "overlay_kwargs": {
                k: _serialize_value(v, compiled) for k, v in sorted(value.overlay_kwargs.items())
            },
        }

    elif isinstance(value, ProviderSpec):
        # Inline provider spec (anonymous)
        serialized = {
            "$inline_provider": True,
            "target": _get_callable_fqn(value.callable_obj),
            "kwargs": {k: _serialize_value(v, compiled) for k, v in sorted(value.kwargs.items())},
        }
        if compiled is not None and value.args:
            serialized["args"] = [_serialize_value(arg, compiled) for arg in value.args]
        return serialized

    elif compiled is not None and isinstance(value, BinaryOpExpr):
        return {
            "$binary": value.operator,
            "left": _serialize_value(value.left, compiled),
            "right": _serialize_value(value.right, compiled),
        }

    elif compiled is not None and isinstance(value, UnaryOpExpr):
        return {"$unary": value.operator, "operand": _serialize_value(value.operand, compiled)}

    elif callable(value) and hasattr(value, "__module__"):
        # Python callable passed by reference
        return {"$import": _get_callable_fqn(value)}

    elif isinstance(value, dict):
        return {
            k: _serialize_value(v, compiled)
            for k, v in sorted(value.items(), key=lambda x: str(x[0]))
        }

    elif isinstance(value, (list, tuple)):
        return [_serialize_value(item, compiled) for item in value]

    elif isinstance(value, (str, int, float, bool, type(None))):
        return value

    else:
        # Fallback: try to represent as string
        return str(value)


def _classify_introspection_failure(callable_obj: Any) -> OpaqueReasonCode:
    """
    Classify why introspection failed for a callable.

    Args:
        callable_obj: The callable that failed introspection

    Returns:
        OpaqueReasonCode indicating the likely reason for failure
    """
    # Check if it's a builtin
    if isinstance(callable_obj, type) and callable_obj.__module__ == "builtins":
        return OpaqueReasonCode.BUILTIN
    if hasattr(callable_obj, "__module__") and callable_obj.__module__ == "builtins":
        return OpaqueReasonCode.BUILTIN

    # Check if it's a C extension (common pattern: module starts with underscore or is in C)
    module = getattr(callable_obj, "__module__", "")
    if module and (module.startswith("_") or ".C" in module or "._" in module):
        return OpaqueReasonCode.C_EXTENSION

    # Default to no_signature for other cases
    return OpaqueReasonCode.NO_SIGNATURE


def _get_resolved_kwargs(
    callable_obj: Any, provided_kwargs: Dict[str, Any]
) -> Tuple[Dict[str, Any], IntrospectionInfo]:
    """
    Get kwargs with Python defaults filled in using inspect.signature.

    Args:
        callable_obj: The callable to inspect
        provided_kwargs: The kwargs explicitly provided in the config

    Returns:
        Tuple of (kwargs dict with defaults, IntrospectionInfo describing status)
    """
    result = dict(provided_kwargs)

    try:
        sig = inspect.signature(callable_obj)

        # Check for varargs-only signature (*args, **kwargs only)
        params = list(sig.parameters.values())
        named_params = [
            p
            for p in params
            if p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        ]
        if not named_params and params:
            # All parameters are *args or **kwargs - can't determine defaults
            return result, IntrospectionInfo(
                status=IntrospectionStatus.PARTIAL,
                defaults_source="none",
                opaque_reason={
                    "code": OpaqueReasonCode.VARARGS_ONLY.value,
                    "detail": "Signature is *args, **kwargs only; no named parameters",
                },
            )

        bound = sig.bind_partial(**{k: v for k, v in provided_kwargs.items()})
        bound.apply_defaults()

        # Track if we actually got defaults from signature
        got_defaults = False

        # Add any defaults that weren't in provided_kwargs
        for param_name, param_value in bound.arguments.items():
            if param_name not in result:
                # This is a default value from the signature
                result[param_name] = param_value
                got_defaults = True

        # Determine defaults_source
        if got_defaults:
            defaults_source = "signature"
        elif provided_kwargs:
            defaults_source = "explicit"
        else:
            defaults_source = "signature"  # No kwargs needed, signature was inspected

        return result, IntrospectionInfo(
            status=IntrospectionStatus.OK,
            defaults_source=defaults_source,
        )

    except (ValueError, TypeError) as e:
        # Can't inspect signature (C extension, etc.) - just use provided kwargs
        logger.debug(f"Could not inspect signature for {callable_obj}: {e}")

        reason_code = _classify_introspection_failure(callable_obj)
        fqn = _get_callable_fqn(callable_obj)

        return result, IntrospectionInfo(
            status=IntrospectionStatus.FAILED,
            defaults_source="explicit" if provided_kwargs else "none",
            opaque_reason={
                "code": reason_code.value,
                "detail": f"{fqn}: {e}",
            },
        )


def build_ir_graph(
    compiled: Dict[str, Any],
    reachable_names: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """
    Transform compiled data into a serializable IR graph.

    The IR graph is a flat dictionary where:
    - Keys are binding names (e.g., "model", "consts/lr")
    - Values are node definitions with resolved kwargs and defaults

    Args:
        compiled: Compiled configuration data from ASTCompiler
        reachable_names: Optional set of names to include (for sub-DAG snapshots)

    Returns:
        Dict representing the flattened IR graph
    """
    providers = compiled.get("providers", {})
    values = compiled.get("values", {})

    graph = {}
    # Scoped snapshots must capture effective recipes even when their shared base
    # is not constructed. All-bindings views retain authored recipe relationships.
    recipe_context = compiled if reachable_names is not None else None

    # Process providers
    for name, spec in sorted(providers.items()):
        if reachable_names is not None and name not in reachable_names:
            continue

        # Get kwargs with defaults resolved and introspection status
        resolved_kwargs, introspection = _get_resolved_kwargs(spec.callable_obj, spec.kwargs)

        # Serialize kwargs
        serialized_kwargs = {
            k: _serialize_value(v, recipe_context) for k, v in sorted(resolved_kwargs.items())
        }

        graph[name] = {
            "type": "provider",
            "target": _get_callable_fqn(spec.callable_obj),
            "is_shared": True,  # Providers are shared by default
            "introspection": introspection.to_dict(),
            "kwargs": serialized_kwargs,
        }
        if recipe_context is not None and spec.args:
            graph[name]["args"] = [_serialize_value(arg, recipe_context) for arg in spec.args]

    # Process values
    for name, value in sorted(values.items()):
        if reachable_names is not None and name not in reachable_names:
            continue

        serialized = _serialize_value(value, recipe_context)

        # Determine node type based on value
        if isinstance(value, FreshProviderCall) and recipe_context is not None:
            graph[name] = {
                "type": "fresh_provider",
                "is_shared": False,
                "recipe": serialized["recipe"],
                "introspection": serialized["recipe"]["introspection"],
            }
        elif isinstance(value, FreshProviderCall):
            # Fresh calls get their own node type
            graph[name] = {
                "type": "fresh_provider",
                "base_provider": value.provider_name,
                "is_shared": False,
                "overlay_kwargs": {
                    k: _serialize_value(v) for k, v in sorted(value.overlay_kwargs.items())
                },
            }
        elif isinstance(value, (ProviderRef, ProviderMethodRef)):
            # References are stored as aliases
            graph[name] = {
                "type": "alias",
                "target": serialized,
            }
        else:
            # Plain values
            graph[name] = {
                "type": "value",
                "value": serialized,
            }

    return graph


def compute_graph_digest(graph: Dict[str, Any]) -> str:
    """
    Compute a deterministic SHA-256 digest of the graph.

    The digest is computed from the JSON-serialized graph with sorted keys,
    ensuring identical graphs produce identical digests.
    """
    # Serialize with sorted keys for determinism
    json_bytes = json.dumps(graph, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(json_bytes).hexdigest()}"


# ===== Payload Building =====


def copy_config_files(
    pfig_files: List[Path],
    output_dir: Path,
) -> List[str]:
    """
    Copy pfig configuration files to the snapshot payload directory.

    Args:
        pfig_files: List of .pfig file paths to include
        output_dir: Directory to copy files into

    Returns:
        List of relative paths of copied files
    """
    copied = []
    output_dir_resolved = output_dir.resolve()

    for pfig_file in pfig_files:
        pfig_file_resolved = pfig_file.resolve()

        if not _fs.exists(pfig_file_resolved):
            continue

        try:
            rel_path = pfig_file_resolved.relative_to(Path.cwd().resolve())
        except ValueError:
            rel_path = Path(pfig_file_resolved.name)

        dest_path = (output_dir_resolved / rel_path).resolve()

        try:
            dest_path.relative_to(output_dir_resolved)
        except ValueError:
            logger.warning(f"Skipping {pfig_file}: destination would be outside snapshot directory")
            continue

        _fs.mkdirs(dest_path.parent)
        _fs.write_text(dest_path, _fs.read_text(pfig_file_resolved))
        copied.append(str(rel_path))
        logger.debug(f"Copied config: {rel_path}")

    return copied


def create_requirements_lock(output_path: Path) -> bool:
    """
    Create requirements.lock file with pip freeze output.

    Args:
        output_path: Path to write the requirements.lock file

    Returns:
        True if successful, False otherwise
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "freeze"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            _fs.write_text(output_path, result.stdout)
            logger.debug(
                f"Created requirements.lock with {len(result.stdout.splitlines())} packages"
            )
            return True
        else:
            logger.warning(f"pip freeze failed: {result.stderr}")
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning(f"Failed to run pip freeze: {e}")
        return False


def create_patch_series(
    patches_dir: Path,
    working_dir: Optional[Path] = None,
    base_commit: Optional[str] = None,
) -> tuple[List[str], List[str]]:
    """
    Create a patch series for unpushed commits and uncommitted changes.

    Uses `git format-patch` to create individual patches for each commit ahead
    of upstream, preserving commit messages and authorship. Uncommitted changes
    are captured separately as dirty.patch.

    Apply commit patches with: git am patches/0001-*.patch patches/0002-*.patch ...
    Apply dirty patches with: git apply patches/dirty.patch

    Args:
        patches_dir: Directory to write patch files into
        working_dir: Working directory for git commands
        base_commit: Explicit base commit for patch range (e.g. replay worktree).
            When None, uses @{u}..HEAD.

    Returns:
        Tuple of (list of commit patch filenames, list of dirty patch filenames)
    """
    cwd = working_dir or Path.cwd()
    commit_patches: List[str] = []
    dirty_patches: List[str] = []

    _fs.mkdirs(patches_dir)

    # Generate patch series for unpushed commits using git format-patch
    patch_range = f"{base_commit}..HEAD" if base_commit else "@{u}..HEAD"
    try:
        result = subprocess.run(
            ["git", "format-patch", "-o", str(patches_dir), patch_range],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            # format-patch outputs full paths, one per line
            for patch_path in result.stdout.strip().split("\n"):
                if patch_path:
                    commit_patches.append(Path(patch_path).name)
            logger.debug(f"Created {len(commit_patches)} commit patches")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning(f"Failed to create patch series: {e}")

    # Create dirty.patch for uncommitted changes (still uses git diff)
    dirty_patch_path = patches_dir / "dirty.patch"
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            _fs.write_text(dirty_patch_path, result.stdout)
            dirty_patches.append("dirty.patch")
            logger.debug(f"Created dirty.patch ({len(result.stdout)} bytes)")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning(f"Failed to create dirty patch: {e}")

    return commit_patches, dirty_patches


# ===== Reproducibility Checks =====


def check_reproducibility_warnings(git_info: Optional[GitInfo]) -> List[Dict[str, str]]:
    """
    Check for conditions that may compromise reproducibility.

    Only warns when we DON'T have a patch to fix the issue.
    If patches were successfully created, the run IS reproducible.

    Returns a list of warning dicts with 'code' and 'message' fields.
    """
    warnings = []

    if git_info is None:
        warnings.append(
            {
                "code": "no_git",
                "message": "Not a git repository. Source code cannot be reproduced by others.",
            }
        )
        return warnings  # No point checking other git-related warnings

    if git_info.commits_ahead is None:
        # No upstream configured - can't create patch series, can't reproduce
        warnings.append(
            {
                "code": "no_upstream",
                "message": "No upstream branch configured. "
                "Run 'git push -u origin <branch>' to enable reproducibility tracking.",
            }
        )
    elif git_info.commits_ahead > 0 and not git_info.commit_patches:
        # Commits ahead but no patch series was created
        warnings.append(
            {
                "code": "unpushed_commits",
                "message": f"{git_info.commits_ahead} commit(s) not pushed to remote "
                "and patch series was not created.",
            }
        )

    if git_info.is_dirty and not git_info.dirty_patches:
        # Dirty working tree but no patch was created
        warnings.append(
            {
                "code": "dirty_working_tree",
                "message": "Working tree has uncommitted changes and dirty.patch was not created.",
            }
        )

    if git_info.uses_lfs:
        # LFS files require external server - patches only contain pointers
        warnings.append(
            {
                "code": "uses_lfs",
                "message": "Repository uses Git LFS. Patches contain pointer files only; "
                "reproduction requires LFS server access and 'git lfs pull'.",
            }
        )

    if git_info.has_submodules:
        # Submodules are not supported - patches only update the pointer
        warnings.append(
            {
                "code": "has_submodules",
                "message": "Repository has submodules (not supported). Patches capture "
                "submodule pointers only, not content.",
            }
        )

    return warnings


def check_introspection_warnings(graph: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Check graph nodes for incomplete introspection.

    Returns a list of warning dicts if any providers have non-ok introspection status.
    Nested recipes are reported once under their containing binding.
    """
    opaque_providers = []

    def has_opaque_recipe(value: Any) -> bool:
        if isinstance(value, dict):
            if value.get("$inline_provider") is True:
                status = value.get("introspection", {}).get("status")
                if status and status != IntrospectionStatus.OK.value:
                    return True
            return any(has_opaque_recipe(child) for child in value.values())
        if isinstance(value, list):
            return any(has_opaque_recipe(child) for child in value)
        return False

    for name, node in graph.items():
        status = None
        if node.get("type") in {"provider", "fresh_provider"}:
            status = node.get("introspection", {}).get("status")
        if (status and status != IntrospectionStatus.OK.value) or has_opaque_recipe(node):
            opaque_providers.append(name)

    if opaque_providers:
        # Truncate list for display if too long
        display_names = opaque_providers[:3]
        suffix = f"... (+{len(opaque_providers) - 3} more)" if len(opaque_providers) > 3 else ""
        names_str = ", ".join(display_names) + suffix

        return [
            {
                "code": "incomplete_introspection",
                "message": f"{len(opaque_providers)} provider(s) have incomplete introspection: {names_str}. "
                "Default kwargs may be missing from the IR.",
                "providers": opaque_providers,
            }
        ]

    return []


# ===== Main Snapshot Functions =====


def get_pfig_version() -> str:
    """Get the pfig package version."""
    try:
        from importlib.metadata import version as get_package_version

        return get_package_version("pfig")
    except Exception:
        return "0.0.0"


def generate_run_id() -> str:
    """Generate a short unique run ID (6 hex chars = ~16M combinations)."""
    return uuid.uuid4().hex[:6]


def generate_snapshot_dirname(run_id: str) -> str:
    """Generate snapshot directory name: <timestamp>_<run_id>."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{timestamp}_{run_id}"


def create_snapshot(
    compiled: Dict[str, Any],
    entrypoint: str,
    output_dir: str | Path,
    pfig_files: Optional[Sequence[str | Path]] = None,
    externals: Optional[Dict[str, Any]] = None,
    external_sources: Optional[Dict[str, str]] = None,
    reachable_names: Optional[Set[str]] = None,
    run_id: Optional[str] = None,
    include_payload: bool = True,
    file_origins: Optional[Dict[str, Any]] = None,
    entry_path: Optional[Path] = None,
    profile_paths: Optional[Sequence[Path]] = None,
    profile_file_origins: Optional[Dict[str, Any]] = None,
    base_commit: Optional[str] = None,
) -> str | Path:
    """
    Create a complete snapshot for a pfig run.

    This creates the following artifacts:

    - `log.json`: The IR graph with resolved defaults
    - `payload/config_bundle.tar.gz`: Portable config bundle (all .pfig files)
    - `payload/requirements.lock`: pip freeze output
    - `payload/patches/`: Git patch series for reproducibility
        - `0001-*.patch`, `0002-*.patch`, ...: Individual commit patches (git format-patch)
        - `dirty.patch`: Uncommitted changes (if working tree is dirty)

    Args:
        compiled: Compiled configuration data from ASTCompiler
        entrypoint: Name of the entrypoint binding
        output_dir: Base directory for snapshots
        pfig_files: List of .pfig files to include in payload
        externals: External values provided at runtime
        external_sources: Mapping of external names to sources
        reachable_names: Optional set of reachable binding names
        run_id: Optional run ID (generated if not provided)
        include_payload: Whether to include config files and requirements.lock
        file_origins: Optional file origins dict for creating portable bundle
        entry_path: Optional entry config path (required if file_origins is provided)
        profile_paths: Optional list of resolved profile paths for bundling
        profile_file_origins: Optional file origins from profile includes for bundling
        base_commit: Explicit base commit for patch range (e.g. replay worktree).
            When None, uses @{u} for upstream.

    Returns:
        Path to the snapshot directory
    """
    pfig_files = pfig_files or []
    externals = externals or {}
    external_sources = external_sources or {}

    # Generate run ID if not provided
    if run_id is None:
        run_id = generate_run_id()

    # Create snapshot directory
    snapshot_dirname = generate_snapshot_dirname(run_id)
    is_uri = _fs.is_uri(str(output_dir))
    if is_uri:
        # For URIs, use string joining (Path would mangle the scheme)
        base = str(output_dir).rstrip("/")
        snapshot_dir = f"{base}/{snapshot_dirname}"
        payload_dir = f"{snapshot_dir}/payload"
        local_payload_dir = None
    else:
        snapshot_dir = Path(output_dir) / snapshot_dirname
        payload_dir = snapshot_dir / "payload"
        local_payload_dir = payload_dir
    _fs.mkdirs(snapshot_dir)

    logger.debug(f"Creating snapshot in: {snapshot_dir}")

    # Create payload directory
    if include_payload:
        _fs.mkdirs(payload_dir)

    # Build IR graph
    graph = build_ir_graph(compiled, reachable_names)

    # Compute digest
    digest = compute_graph_digest(graph)

    # Collect git info first (needed to determine if we should create patches)
    git_info = collect_git_info(base_commit=base_commit)

    # Create payload files
    requirements_lock_path = None
    if include_payload:
        # Create portable config bundle (contains all .pfig files)
        if file_origins is not None and entry_path is not None and local_payload_dir is not None:
            from .bundle import create_bundle

            bundle_path = local_payload_dir / "config_bundle.tar.gz"
            try:
                create_bundle(
                    file_origins=file_origins,
                    entry_path=entry_path,
                    output_path=bundle_path,
                    as_tarball=True,
                    profile_paths=list(profile_paths) if profile_paths is not None else None,
                    profile_file_origins=profile_file_origins,
                )
                bundle_name = bundle_path.name
                logger.info(f"Created config bundle: {bundle_name}")
            except Exception as e:
                logger.warning(f"Failed to create config bundle: {e}")

        # Create requirements.lock
        requirements_lock_file = (
            None if local_payload_dir is None else local_payload_dir / "requirements.lock"
        )
        if requirements_lock_file is not None and create_requirements_lock(requirements_lock_file):
            requirements_lock_path = "payload/requirements.lock"

        # Create git patch series for reproducibility
        if git_info and (
            git_info.is_dirty or (git_info.commits_ahead and git_info.commits_ahead > 0)
        ):
            if local_payload_dir is None:
                commit_patches, dirty_patches = [], []
            else:
                patches_dir = local_payload_dir / "patches"
                commit_patches, dirty_patches = create_patch_series(
                    patches_dir, base_commit=base_commit
                )

            # Update git_info with created patches
            git_info.commit_patches = commit_patches
            git_info.dirty_patches = dirty_patches

            if commit_patches:
                logger.info(f"Created {len(commit_patches)} commit patches")
            if dirty_patches:
                logger.info("Created dirty.patch for uncommitted changes")

    # Collect environment info (with updated git_info)
    env_info = collect_environment(
        externals=externals,
        external_sources=external_sources,
        requirements_lock_path=requirements_lock_path,
    )
    # Override the auto-collected git info with our updated one (includes patches)
    env_info.git = git_info

    # Check for reproducibility issues and warn loudly
    reproducibility_warnings = check_reproducibility_warnings(git_info)
    introspection_warnings = check_introspection_warnings(graph)
    all_warnings = reproducibility_warnings + introspection_warnings

    for warning in all_warnings:
        logger.warning(f"⚠️  REPRODUCIBILITY: {warning['message']}")

    # Build metadata
    meta = SnapshotMeta(
        pfig_version=get_pfig_version(),
        generated_at=datetime.now(timezone.utc).isoformat(),
        entrypoint=entrypoint,
        digest=digest,
        run_id=run_id,
        pfig_files=[str(p) for p in pfig_files],
    )

    # Build complete log
    log = {
        "version": SNAPSHOT_VERSION,
        "meta": meta.to_dict(),
        "environment": env_info.to_dict(),
        "graph": graph,
    }

    # Add warnings to log if any
    if all_warnings:
        log["warnings"] = all_warnings

    # Write log.json with deterministic formatting
    log_path = (
        f"{snapshot_dir}/log.json"
        if local_payload_dir is None
        else local_payload_dir.parent / "log.json"
    )
    _fs.write_json(log_path, log)

    logger.info(f"Snapshot created: {snapshot_dir}")
    logger.debug(f"  - Graph nodes: {len(graph)}")
    logger.debug(f"  - Digest: {digest}")
    if all_warnings:
        logger.warning(f"  - {len(all_warnings)} warning(s) - see log.json")

    return snapshot_dir


def load_snapshot(snapshot_path: str | Path) -> Dict[str, Any]:
    """
    Load a snapshot from disk or GCS.

    Args:
        snapshot_path: Path to snapshot directory or log.json file

    Returns:
        Dict containing the snapshot data
    """
    if _fs.is_dir(snapshot_path):
        if _fs.is_uri(str(snapshot_path)):
            log_path = f"{str(snapshot_path).rstrip('/')}/log.json"
        else:
            log_path = Path(snapshot_path) / "log.json"
    else:
        log_path = snapshot_path

    if not _fs.exists(log_path):
        raise FileNotFoundError(f"Snapshot log not found: {log_path}")

    return _fs.read_json(log_path)
