#!/usr/bin/env python3

"""
pfig Command Line Interface

Provides the main CLI commands:
    pfig validate CONFIG.pfig [options]  - Validate configuration without execution
    pfig run CONFIG.pfig [options]       - Execute configuration
    pfig run --bundle BUNDLE [options]   - Execute from a bundle
    pfig bundle CONFIG.pfig [options]    - Create portable config bundle
    pfig diff CONFIG1.pfig CONFIG2.pfig [options] - Compare configurations
    pfig fmt [PATHS...] [options]        - Format .pfig files (or check/diff)
    pfig replay prepare SNAPSHOT [options] - Set up replay workspace
    pfig replay validate SNAPSHOT [options] - Validate replay digest
    pfig replay run SNAPSHOT [options]     - Full replay (prepare + validate + execute)
"""

import argparse
import difflib
import sys
import traceback
from importlib.metadata import version as get_package_version
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

# Import full execution stack for parsing CLI values
from . import ExecutionError, LexerError, ParseError, execute_pfig, parse_pfig_source
from . import fs as _fs
from .bundle import create_bundle, load_bundle
from .diff import (
    AlignmentMode,
    DiffReport,
    NamespaceAlignment,
    compare_configs,
    log_diff_details,
    log_diff_summary,
)
from .execution import (
    ASTCompiler,
    ExecutionMetadata,
    LiteralValue,
    Materializer,
    ProfileSpec,
    ProviderRef,
    ProviderSpec,
    ValidationError,
    _execute_validated,
    _prepare_snapshot_materializer,
    _runtime_values_equal,
    execute_pfig_with_metadata,
    validate_pfig,
)
from .fmt import FormatError, FormatOptions, format_source
from .logging import color_text, get_logger, supports_color
from .parser import parse_config_file
from .snapshot import create_snapshot, load_snapshot
from .utils import load_config_file, normalize_path_display

logger = get_logger("cli")


def get_version() -> str:
    """Get the pfig package version."""
    try:
        return get_package_version("pfig")
    except Exception:
        # Fallback if package not installed or metadata unavailable
        return "0.0.0"


def enhance_execution_error(error: ExecutionError) -> str:
    """Enhance ExecutionError with CLI-specific guidance when appropriate."""
    error_msg = str(error)

    if error_msg.startswith("Missing external values:"):
        return f"{error_msg}. Provide them via --set or --profile flag(s)."

    if "Externals can only be overridden in profile files or via runtime parameters" in error_msg:
        return error_msg.replace("via runtime parameters", "via CLI flags (--profile or --set)")

    if "remove them from the provided external values" in error_msg:
        return error_msg.replace(
            "from the provided external values", "from --set/--profile flag(s)"
        )

    if "specify an entrypoint when calling the execution function" in error_msg:
        return error_msg.replace(
            "specify an entrypoint when calling the execution function",
            "use --entrypoint <binding_name>",
        )

    return error_msg


def cmd_validate(args) -> int:
    """Validate a pfig configuration without executing it."""
    logger.info(f"Validating configuration: {args.config}")

    try:
        _ensure_cwd_on_syspath()

        config_path = normalize_path_display(args.config)
        config_content = load_config_file(args.config)
        ast = parse_pfig_source(config_content, filename=config_path)

        metadata = validate_pfig(
            ast,
            entrypoint=args.entrypoint,
            filename=config_path,
            externals=args.set_values,
            profile_paths=args.profiles,
            strict=getattr(args, "strict", False),
            allow_missing_externals=args.allow_missing_externals,
        )

        logger.info("✓ Configuration is valid")

        if args.verbose:
            logger.info(f"  Entrypoint: {metadata.entrypoint_name}")
            logger.info(f"  Reachable bindings: {len(metadata.reachable_names)}")
            if args.verbose > 1:
                logger.info(f"  Bindings: {sorted(metadata.reachable_names)}")

        return 0

    except (ParseError, LexerError) as e:
        logger.error(f"✗ Validation failed: {e}")
        if args.verbose:
            traceback.print_exc()
        return 1
    except ValidationError as e:
        # ValidationError has a pre-formatted multi-line message
        logger.error(f"✗ {e}")
        if args.verbose:
            traceback.print_exc()
        return 1
    except ExecutionError as e:
        enhanced_msg = enhance_execution_error(e)
        logger.error(f"✗ Validation failed: {enhanced_msg}")
        if args.verbose:
            traceback.print_exc()
        return 1
    except Exception as e:
        logger.error(f"✗ Unexpected error: {e}")
        if args.verbose:
            traceback.print_exc()
        return 2


def cmd_run(args) -> int:
    """Execute a pfig configuration."""
    # Check for bundle mode vs config mode
    if getattr(args, "bundle", None):
        return cmd_run_bundle(args)

    if not args.config:
        logger.error("✗ Either config file or --bundle is required")
        return 1

    logger.info(f"Running configuration: {args.config}")

    try:
        _ensure_cwd_on_syspath()

        config_path = normalize_path_display(args.config)
        config_content = load_config_file(args.config)
        ast = parse_pfig_source(config_content, filename=config_path)

        # Determine if snapshots are enabled
        snapshot_enabled = not getattr(args, "no_snapshot", False)

        # Step 1: Validate WITHOUT materializing any objects
        # This gives us metadata for snapshotting before anything is constructed
        validation = validate_pfig(
            ast,
            entrypoint=args.entrypoint,
            filename=config_path,
            externals=args.set_values,
            profile_paths=args.profiles,
        )

        materializer = _prepare_snapshot_materializer(
            validation,
            enabled=snapshot_enabled,
            snapshot_dir=getattr(args, "snapshot_dir", None),
        )

        # Step 2: Create snapshot BEFORE execution
        # This ensures preempted/crashed jobs still have their snapshot saved
        entry_path = args.config if _fs.is_uri(args.config) else Path(args.config)
        _maybe_create_snapshot(
            args=args,
            metadata=validation,
            entry_path=entry_path,
            snapshot_enabled=snapshot_enabled,
            profile_paths=args.profiles,
        )

        # Step 3: Materialize and execute
        _execute_validated(validation, materializer)
        metadata = validation

        logger.info("✓ Configuration executed successfully")

        if args.verbose:
            logger.debug(f"Result: {metadata.result}")

        return 0

    except (ParseError, LexerError) as e:
        logger.error(f"✗ Execution failed: {e}")
        if args.verbose:
            traceback.print_exc()
        return 1
    except ValidationError as e:
        # ValidationError has a pre-formatted multi-line message
        logger.error(f"✗ {e}")
        if args.verbose:
            traceback.print_exc()
        return 1
    except ExecutionError as e:
        enhanced_msg = enhance_execution_error(e)
        logger.error(f"✗ Execution failed: {enhanced_msg}")
        if args.verbose:
            traceback.print_exc()
        return 1
    except Exception as e:
        logger.error(f"✗ Unexpected error: {e}")
        if args.verbose:
            traceback.print_exc()
        return 3


def _resolve_snapshot_dir(args, metadata: "ExecutionMetadata") -> Path | str | None:
    """
    Resolve the snapshot output directory from CLI args and config.

    Priority:
    1. --snapshot (CLI override)
    2. __snapshot__ directive in config
    3. Default: ./snapshots

    Note: A timestamped subdirectory is always created inside the resolved path.
    Returns a str for URI paths, Path for local paths.
    """
    # 1. --snapshot CLI override
    if getattr(args, "snapshot_dir", None):
        val = args.snapshot_dir
        if _fs.is_uri(val):
            return val
        return Path(val)

    # 2. __snapshot__ directive in config
    snapshot_path = _resolve_snapshot_directive(metadata)
    if snapshot_path:
        return snapshot_path

    # 3. Default
    return Path("./snapshots")


def _ensure_cwd_on_syspath() -> None:
    """Ensure the current working directory is importable."""
    current_dir = str(Path.cwd())
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)


def _maybe_create_snapshot(
    *,
    args,
    metadata: "ExecutionMetadata",
    entry_path: Path,
    snapshot_enabled: bool,
    profile_paths: Sequence[str | Path] | None = None,
) -> str | Path | None:
    """
    Create a snapshot (if enabled) for a run.

    Kept as a helper so config runs and bundle runs share identical snapshot behavior.
    """
    if not snapshot_enabled:
        return None

    snapshot_dir = _resolve_snapshot_dir(args, metadata)
    if not snapshot_dir:
        return None

    profile_paths_resolved = None
    profile_file_origins = None
    if profile_paths:
        from pfig.bundle import collect_profile_bundle_info

        profile_paths_resolved, profile_file_origins = collect_profile_bundle_info(profile_paths)

    pfig_files = [entry_path]
    pfig_files.extend(Path(p) for p in metadata.included_files)

    snapshot_path = create_snapshot(
        compiled=metadata.compiled,
        entrypoint=metadata.entrypoint_name,
        output_dir=snapshot_dir,
        pfig_files=pfig_files,
        externals=metadata.compiled["runtime_inputs"],
        external_sources=metadata.compiled["runtime_sources"],
        reachable_names=metadata.reachable_names,
        file_origins=metadata.file_origins,
        entry_path=entry_path,
        profile_paths=profile_paths_resolved,
        profile_file_origins=profile_file_origins,
        base_commit=getattr(args, "base_commit", None),
    )

    # Log reproducibility checklist if there are warnings
    snapshot_data = load_snapshot(snapshot_path)
    warnings = snapshot_data.get("warnings", [])

    if warnings:
        log_reproducibility_checklist(warnings, snapshot_path)
    else:
        logger.info("✓ Snapshot captured successfully")

    return snapshot_path


def _resolve_snapshot_directive(metadata: "ExecutionMetadata") -> Path | str | None:
    """
    Resolve __snapshot__ directive from compiled config.

    Handles:
    - LiteralValue: __snapshot__ = "./snapshots"
    - ProviderRef: __snapshot__ = @output_dir (resolved via externals)
    - Provider: __snapshot__ = os.path.join(...) (resolved via materialized)

    Returns a str for URI paths, Path for local paths, or None.
    """

    def _as_path_or_uri(val: str) -> Path | str:
        return val if _fs.is_uri(val) else Path(val)

    # First check if __snapshot__ was materialized (handles all cases including providers)
    if "__snapshot__" in metadata.materialized:
        resolved = metadata.materialized["__snapshot__"]
        if isinstance(resolved, str):
            return _as_path_or_uri(resolved)

    # Check compiled values for un-materialized cases
    values = metadata.compiled.get("values", {})

    if "__snapshot__" not in values:
        return None

    snapshot_value = values["__snapshot__"]

    # Handle LiteralValue (direct string path)
    if isinstance(snapshot_value, LiteralValue):
        return _as_path_or_uri(snapshot_value.value)

    # Handle plain string
    if isinstance(snapshot_value, str):
        return _as_path_or_uri(snapshot_value)

    # Handle ProviderRef (reference to external or other value)
    if isinstance(snapshot_value, ProviderRef):
        ref_name = snapshot_value.name
        # Check if it's in externals
        if ref_name in metadata.externals:
            external_value = metadata.externals[ref_name]
            if isinstance(external_value, str):
                return _as_path_or_uri(external_value)
        # Check if it's been materialized
        if ref_name in metadata.materialized:
            resolved = metadata.materialized[ref_name]
            if isinstance(resolved, str):
                return _as_path_or_uri(resolved)
        # Check in compiled values
        if ref_name in values:
            ref_value = values[ref_name]
            if isinstance(ref_value, LiteralValue):
                return _as_path_or_uri(ref_value.value)

    return None


# ===== Reproducibility Checklist =====


def log_reproducibility_checklist(warnings: list[dict], snapshot_path: str | Path) -> None:
    """
    Log reproducibility checklist.

    This transforms warning codes into an actionable checklist format,
    helping users understand what may be missing for full reproducibility.

    Args:
        warnings: List of warning dicts from snapshot log
        snapshot_path: Path to the snapshot directory
    """
    # Map warning codes to checklist items with status and remediation
    checklist_map = {
        "no_git": ("Not in git repository", None),
        "no_upstream": (
            "No upstream branch configured",
            "Run 'git push -u origin <branch>'",
        ),
        "unpushed_commits": (
            "Unpushed commits (no patch created)",
            "Push commits or check patch creation",
        ),
        "dirty_working_tree": (
            "Uncommitted changes (no patch created)",
            "Commit changes or check patch creation",
        ),
        "uses_lfs": (
            "LFS pointers only",
            "Run 'git lfs pull' after applying patches",
        ),
        "has_submodules": (
            "Submodules not captured",
            "Submodules must be manually restored",
        ),
        "incomplete_introspection": (
            "Incomplete introspection for some providers",
            "See IR for details; defaults may be missing",
        ),
    }

    # Build checklist items
    warning_codes = {w["code"] for w in warnings}

    logger.info(f"Snapshot written to {snapshot_path}/")
    logger.info("Reproducibility checklist:")

    # Standard checks that are OK if not in warnings
    standard_checks = [
        ("no_git", "In git repository"),
        ("no_upstream", "Upstream branch configured"),
        ("unpushed_commits", "All commits pushed (or patches created)"),
        ("dirty_working_tree", "Working tree clean (or patch created)"),
    ]

    for code, ok_message in standard_checks:
        if code in warning_codes:
            message, remediation = checklist_map[code]
            line = f"  [✗] {message}"
            if remediation:
                line += f" - {remediation}"
            logger.warning(line)
        else:
            # Only show OK status for checks we actually performed
            if code == "no_git" or (code != "no_git" and "no_git" not in warning_codes):
                logger.info(f"  [✓] {ok_message}")

    # Additional warnings that are always issues when present
    additional_codes = ["uses_lfs", "has_submodules", "incomplete_introspection"]
    for code in additional_codes:
        if code in warning_codes:
            message, remediation = checklist_map[code]
            line = f"  [✗] {message}"
            if remediation:
                line += f" - {remediation}"
            logger.warning(line)


def cmd_bundle(args) -> int:
    """Create a portable config bundle."""
    logger.info(f"Creating bundle from: {args.config}")

    try:
        _ensure_cwd_on_syspath()

        config_content = load_config_file(args.config)
        ast = parse_pfig_source(config_content, filename=args.config)

        # Compile to get file_origins
        # Use compile_only=True since bundling only needs to discover files,
        # not run the config. Externals will be provided when running from the bundle.
        metadata = execute_pfig_with_metadata(
            ast,
            entrypoint=args.entrypoint,
            filename=args.config,
            externals=args.set_values,
            profile_paths=args.profiles,
            compile_only=True,
            allow_missing_externals=True,
        )

        # Collect profile bundle info (profile files and their includes)
        profile_paths_resolved = None
        profile_file_origins = None
        if args.profiles:
            from pfig.bundle import collect_profile_bundle_info

            profile_paths_resolved, profile_file_origins = collect_profile_bundle_info(
                args.profiles
            )

        # Determine output path
        output_path = Path(args.output) if args.output else Path("bundle.tar.gz")
        as_tarball = str(output_path).endswith(".tar.gz") or not args.directory

        entry_path = args.config if _fs.is_uri(args.config) else Path(args.config).resolve()

        result_path = create_bundle(
            file_origins=metadata.file_origins,
            entry_path=entry_path,
            output_path=output_path,
            as_tarball=as_tarball,
            profile_paths=profile_paths_resolved,
            profile_file_origins=profile_file_origins,
        )

        logger.info(f"✓ Bundle created: {result_path}")
        return 0

    except (ParseError, LexerError) as e:
        logger.error(f"✗ Bundle creation failed: {e}")
        if args.verbose:
            traceback.print_exc()
        return 1
    except ExecutionError as e:
        enhanced_msg = enhance_execution_error(e)
        logger.error(f"✗ Bundle creation failed: {enhanced_msg}")
        if args.verbose:
            traceback.print_exc()
        return 1
    except Exception as e:
        logger.error(f"✗ Unexpected error: {e}")
        if args.verbose:
            traceback.print_exc()
        return 2


def cmd_run_bundle(args) -> int:
    """Execute a configuration from a bundle."""
    import tempfile
    from contextlib import ExitStack

    logger.info(f"Running from bundle: {args.bundle}")

    try:
        _ensure_cwd_on_syspath()

        is_remote = _fs.is_uri(args.bundle)
        bundle_path = args.bundle if is_remote else Path(args.bundle)

        with ExitStack() as stack:
            if is_remote or (
                isinstance(bundle_path, Path)
                and bundle_path.is_file()
                and str(bundle_path).endswith(".tar.gz")
            ):
                temp_dir = stack.enter_context(tempfile.TemporaryDirectory(prefix="pfig_bundle_"))
                bundle_dir, manifest = load_bundle(bundle_path, extract_dir=Path(temp_dir))
            else:
                bundle_dir, manifest = load_bundle(bundle_path)

            # Resolve entry path
            entry_path = bundle_dir / manifest.entry

            # Determine profile paths: CLI --profile replaces bundled profiles
            if args.profiles:
                final_profile_paths = args.profiles
            elif manifest.profiles:
                final_profile_paths = [str(bundle_dir / p) for p in manifest.profiles]
            else:
                final_profile_paths = []

            # Load and parse entry config
            config_content = _fs.read_text(entry_path)
            ast = parse_pfig_source(config_content, filename=str(entry_path))

            # Determine if snapshots are enabled
            snapshot_enabled = not getattr(args, "no_snapshot", False)

            # Step 1: Validate WITHOUT materializing any objects
            validation = validate_pfig(
                ast,
                entrypoint=args.entrypoint,
                filename=str(entry_path),
                externals=args.set_values,
                profile_paths=final_profile_paths or None,
            )

            materializer = _prepare_snapshot_materializer(
                validation,
                enabled=snapshot_enabled,
                snapshot_dir=getattr(args, "snapshot_dir", None),
            )

            # Step 2: Create snapshot BEFORE execution
            # This ensures preempted/crashed jobs still have their snapshot saved
            _maybe_create_snapshot(
                args=args,
                metadata=validation,
                entry_path=entry_path,
                snapshot_enabled=snapshot_enabled,
                profile_paths=final_profile_paths or None,
            )

            # Step 3: Materialize and execute
            _execute_validated(validation, materializer)
            metadata = validation

            logger.info("✓ Execution completed successfully")

            if metadata.result is not None:
                print(metadata.result)

            return 0

    except (ParseError, LexerError) as e:
        logger.error(f"✗ Execution failed: {e}")
        if args.verbose:
            traceback.print_exc()
        return 1
    except ExecutionError as e:
        enhanced_msg = enhance_execution_error(e)
        logger.error(f"✗ Execution failed: {enhanced_msg}")
        if args.verbose:
            traceback.print_exc()
        return 1
    except Exception as e:
        logger.error(f"✗ Unexpected error: {e}")
        if args.verbose:
            traceback.print_exc()
        return 2


def _cursor_frontmatter(meta: dict) -> str:
    return (
        "---\n"
        f"description: {meta['description']}\n"
        f'globs: "{meta["pattern"]}"\n'
        "alwaysApply: false\n"
        "---\n"
    )


def _claude_frontmatter(meta: dict) -> str:
    return f'---\ndescription: {meta["description"]}\npaths: ["{meta["pattern"]}"]\n---\n'


_RULES_AGENTS = {
    "cursor": {
        "mode": "per-file",
        "ext": ".mdc",
        "target": Path(".cursor") / "rules",
        "rules_dir": ".cursor/rules",
        "label": "Cursor",
        "render_frontmatter": _cursor_frontmatter,
    },
    "claude": {
        "mode": "per-file",
        "ext": ".md",
        "target": Path(".claude") / "rules",
        "rules_dir": ".claude/rules",
        "label": "Claude Code",
        "render_frontmatter": _claude_frontmatter,
    },
    "codex": {
        "mode": "single-file",
        "target": Path("AGENTS.md"),
        "label": "Codex",
    },
}


def _parse_canonical_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """Parse trivial `key: value` frontmatter at the top of a rule source file."""
    if not text.startswith("---\n"):
        raise ValueError("rule source missing frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("rule source frontmatter not terminated")
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, text[end + 5 :]


def _render_per_file_rule(source_text: str, agent_spec: dict) -> str:
    meta, body = _parse_canonical_frontmatter(source_text)
    body = body.replace("__RULES_DIR__", agent_spec["rules_dir"]).replace(
        "__RULES_EXT__", agent_spec["ext"]
    )
    return agent_spec["render_frontmatter"](meta) + body


def _render_single_file_bundle(source_dir: Path) -> str:
    """Concatenate canonical rule sources into one document.

    Strips per-source frontmatter and the cross-reference "See Also" sections
    (the only place placeholders live; they don't make sense in a single doc).
    """
    sections = []
    for src_file in sorted(source_dir.glob("*.md")):
        _, body = _parse_canonical_frontmatter(src_file.read_text())
        idx = body.find("\n## See Also\n")
        if idx >= 0:
            body = body[:idx]
        sections.append(body.strip())
    return "\n\n---\n\n".join(sections) + "\n"


def _install_per_file(spec: dict, source_dir: Path, force: bool) -> int:
    target_dir = Path.cwd() / spec["target"]
    target_dir.mkdir(parents=True, exist_ok=True)

    installed = []
    skipped = []
    for src_file in sorted(source_dir.glob("*.md")):
        dest_name = src_file.stem + spec["ext"]
        dest = target_dir / dest_name
        if dest.exists() and not force:
            skipped.append(dest_name)
            continue
        dest.write_text(_render_per_file_rule(src_file.read_text(), spec))
        installed.append(dest_name)

    if installed:
        for name in installed:
            logger.info(f"  ✓ {name}")
        logger.info(f"Installed {len(installed)} rule(s) to {target_dir}/")
    if skipped:
        for name in skipped:
            logger.info(f"  · {name} (already exists, use --force to overwrite)")
    if not installed and not skipped:
        logger.info("No rules to install")
    return 0


_MANAGED_BEGIN = "<!-- BEGIN pfig install-rules -->"
_MANAGED_END = "<!-- END pfig install-rules -->"


def _wrap_managed_region(body: str) -> str:
    return f"{_MANAGED_BEGIN}\n{body.rstrip()}\n{_MANAGED_END}\n"


def _merge_managed_region(existing: str, region: str) -> Tuple[str, str]:
    """Return (new_text, action) where action is 'replace' or 'append'."""
    begin = existing.find(_MANAGED_BEGIN)
    if begin < 0:
        sep = "" if existing.endswith("\n") else "\n"
        return existing + sep + "\n" + region, "append"
    end = existing.find(_MANAGED_END, begin)
    if end < 0:
        raise ValueError(f"found '{_MANAGED_BEGIN}' without matching '{_MANAGED_END}'")
    end += len(_MANAGED_END)
    # Consume a trailing newline so we don't accumulate blank lines on each run.
    if end < len(existing) and existing[end] == "\n":
        end += 1
    return existing[:begin] + region + existing[end:], "replace"


def _install_single_file(spec: dict, source_dir: Path, force: bool) -> int:
    target = Path.cwd() / spec["target"]
    region = _wrap_managed_region(_render_single_file_bundle(source_dir))
    target.parent.mkdir(parents=True, exist_ok=True)

    if not target.exists():
        target.write_text(region)
        logger.info(f"  ✓ {target.name} (created)")
        logger.info(f"Installed rules to {target}")
        return 0

    try:
        new_text, action = _merge_managed_region(target.read_text(), region)
    except ValueError as e:
        logger.error(f"✗ {target.name}: {e}")
        return 2

    target.write_text(new_text)
    if action == "replace":
        logger.info(f"  ✓ {target.name} (updated pfig section)")
    else:
        logger.info(f"  ✓ {target.name} (appended pfig section)")
    logger.info(f"Installed rules to {target}")
    return 0


def _install_rules(agent: str, force: bool) -> int:
    """Install pfig rules for the given agent into the current project."""
    spec = _RULES_AGENTS[agent]
    source_dir = Path(__file__).parent / "rules"
    if not source_dir.is_dir():
        logger.error("✗ Could not find bundled rule sources")
        return 2

    if spec["mode"] == "per-file":
        return _install_per_file(spec, source_dir, force)
    return _install_single_file(spec, source_dir, force)


def cmd_install_rules(args) -> int:
    """Install pfig rules for the selected agent."""
    agent = getattr(args, "agent", None)
    if agent is None:
        supported = ", ".join(sorted(_RULES_AGENTS))
        logger.error(f"✗ Specify an agent: pfig install-rules {{{supported}}}")
        return 2
    return _install_rules(agent, force=args.force)


# ===== Replay Commands =====


def cmd_replay_prepare(args) -> int:
    """Prepare a replay workspace from a snapshot."""
    from .replay import replay_prepare, write_report

    try:
        ws = replay_prepare(
            args.snapshot,
            workspace_path=getattr(args, "workspace", None),
            python=getattr(args, "python", None),
            no_deps=getattr(args, "no_deps", False),
            dry_run=getattr(args, "dry_run", False),
        )
        if ws.prepare_ok:
            write_report(ws)
            logger.info(f"Workspace ready: {ws.root}")
        return 0
    except (ValueError, FileNotFoundError) as e:
        logger.error(f"✗ Replay prepare failed: {e}")
        if args.verbose:
            traceback.print_exc()
        return 1
    except Exception as e:
        logger.error(f"✗ Unexpected error: {e}")
        if args.verbose:
            traceback.print_exc()
        return 2


def cmd_replay_validate(args) -> int:
    """Validate a prepared replay workspace."""
    from .replay import ReplayWorkspace, load_and_validate_snapshot, replay_validate

    try:
        workspace_path = args.workspace
        if not str(workspace_path).strip():
            logger.error("✗ --workspace must not be empty")
            return 1

        ws_root = Path(workspace_path)
        if not ws_root.exists():
            logger.error(f"✗ Workspace not found: {ws_root}")
            return 1

        snapshot = load_and_validate_snapshot(args.snapshot)
        ws = ReplayWorkspace(root=ws_root, snapshot=snapshot)
        ws.prepare_ok = True

        ok = replay_validate(
            ws,
            set_overrides=args.set_values,
            allow_baseline_mismatch=getattr(args, "allow_baseline_mismatch", False),
        )

        from .replay import write_report

        write_report(ws)
        return 0 if ok else 1
    except (ValueError, FileNotFoundError) as e:
        logger.error(f"✗ Replay validate failed: {e}")
        if args.verbose:
            traceback.print_exc()
        return 1
    except Exception as e:
        logger.error(f"✗ Unexpected error: {e}")
        if args.verbose:
            traceback.print_exc()
        return 2


def cmd_replay_run(args) -> int:
    """Full replay: prepare, validate, and execute."""
    from .replay import replay_prepare, replay_run

    try:
        ws = replay_prepare(
            args.snapshot,
            workspace_path=getattr(args, "workspace", None),
            python=getattr(args, "python", None),
            no_deps=getattr(args, "no_deps", False),
            dry_run=getattr(args, "dry_run", False),
        )

        if getattr(args, "dry_run", False):
            return 0

        if not ws.prepare_ok:
            logger.error("✗ Prepare phase failed")
            return 1

        return replay_run(
            ws,
            set_overrides=args.set_values,
            allow_baseline_mismatch=getattr(args, "allow_baseline_mismatch", False),
            verbose=args.verbose,
        )
    except (ValueError, FileNotFoundError) as e:
        logger.error(f"✗ Replay failed: {e}")
        if args.verbose:
            traceback.print_exc()
        return 1
    except Exception as e:
        logger.error(f"✗ Unexpected error: {e}")
        if args.verbose:
            traceback.print_exc()
        return 2


def cmd_fmt(args) -> int:
    """Format pfig source files in place, or check/diff without writing."""
    if args.check and args.diff:
        logger.error("✗ pfig fmt: --check and --diff are mutually exclusive")
        return 2

    options = FormatOptions(line_length=args.line_length)
    paths = args.paths or []

    # ``pfig fmt -`` is the explicit stdin form; the same as no arguments
    # plus a pipe. It's an error to mix ``-`` with file paths.
    if paths == ["-"]:
        paths = []
    elif "-" in paths:
        logger.error("✗ pfig fmt: '-' (stdin) cannot be combined with file paths")
        return 2

    if not paths:
        # No paths: read from stdin if it's piped; otherwise complain.
        if sys.stdin.isatty():
            logger.error("✗ pfig fmt: no input files (pass paths, '-' for stdin, or pipe a config)")
            return 1
        try:
            formatted = format_source(sys.stdin.read(), options=options)
        except FormatError as e:
            logger.error(f"✗ {e}")
            return 2
        sys.stdout.write(formatted)
        return 0

    files = _collect_pfig_files(paths)
    if not files:
        logger.error("✗ pfig fmt: no .pfig files found")
        return 1

    has_changes = False
    had_error = False
    for path in files:
        outcome = _format_one_file(
            path, options, check=args.check, diff=args.diff, stdout=args.stdout
        )
        if outcome == "error":
            had_error = True
        elif outcome == "changed":
            has_changes = True

    if had_error:
        return 2
    if (args.check or args.diff) and has_changes:
        return 1
    return 0


def _collect_pfig_files(paths: List[str]) -> List[Path]:
    """Expand the user's path list into a deduplicated list of .pfig files.

    Files are kept as-is. Directories are walked recursively for ``*.pfig``,
    skipping hidden directories (anything starting with ``.``). Non-existent
    paths log a warning but don't abort the run.
    """
    seen: set[Path] = set()
    result: List[Path] = []
    for raw in paths:
        p = Path(raw)
        if not p.exists():
            logger.warning(f"pfig fmt: path not found: {raw}")
            continue
        if p.is_file():
            resolved = p.resolve()
            if resolved not in seen:
                seen.add(resolved)
                result.append(p)
            continue
        # Directory: walk recursively.
        for child in sorted(p.rglob("*.pfig")):
            if any(part.startswith(".") for part in child.relative_to(p).parts):
                continue
            resolved = child.resolve()
            if resolved not in seen:
                seen.add(resolved)
                result.append(child)
    return result


def _format_one_file(
    path: Path,
    options: FormatOptions,
    *,
    check: bool,
    diff: bool,
    stdout: bool,
) -> str:
    """Format a single file. Returns one of ``"unchanged"``, ``"changed"``, ``"error"``."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.error(f"✗ pfig fmt: cannot read {path}: {e}")
        return "error"

    try:
        formatted = format_source(source, filename=str(path), options=options)
    except FormatError as e:
        logger.error(f"✗ pfig fmt: {path}: {e}")
        return "error"

    if formatted == source:
        if not (check or diff or stdout):
            logger.debug(f"pfig fmt: {path} unchanged")
        return "unchanged"

    if diff:
        diff_text = difflib.unified_diff(
            source.splitlines(keepends=True),
            formatted.splitlines(keepends=True),
            fromfile=f"{path} (original)",
            tofile=f"{path} (formatted)",
        )
        sys.stdout.writelines(_style_unified_diff(diff_text, color=supports_color(sys.stdout)))
        return "changed"

    if check:
        logger.info(f"would reformat {path}")
        return "changed"

    if stdout:
        sys.stdout.write(formatted)
        return "changed"

    try:
        path.write_text(formatted, encoding="utf-8")
    except OSError as e:
        logger.error(f"✗ pfig fmt: cannot write {path}: {e}")
        return "error"
    logger.info(f"reformatted {path}")
    return "changed"


def _style_unified_diff(lines, *, color: bool):
    """Style unified diff lines with terminal-palette colors when enabled."""
    if not color:
        yield from lines
        return

    for line in lines:
        if line.startswith("--- "):
            yield color_text(line, "removed")
        elif line.startswith("+++ "):
            yield color_text(line, "added")
        elif line.startswith("@@ "):
            yield color_text(line, "hunk")
        elif line.startswith("-"):
            yield color_text(line, "removed")
        elif line.startswith("+"):
            yield color_text(line, "added")
        else:
            yield line


def _namespace_alignment(spec: str | None) -> AlignmentMode:
    if spec == "auto" or spec is None:
        return spec
    return NamespaceAlignment.parse(spec)


def cmd_diff(args) -> int:
    """Compare two pfig configurations."""
    logger.info(f"Comparing configurations: {args.base} vs {args.proposed}")

    try:
        _ensure_cwd_on_syspath()

        # Load and parse both files
        # Note: We use parse_config_file from parser, which returns AST
        file_a = parse_config_file(args.base)
        file_b = parse_config_file(args.proposed)

        # Run comparison logic
        report = compare_configs(
            file_a,
            file_b,
            externals=args.set_values,
            base_path_a=args.base,
            base_path_b=args.proposed,
            alignment=_namespace_alignment(args.align_namespace),
        )

        if report.alignment is not None:
            logger.info(f"Aligned namespaces: {report.alignment}")
        if not report.has_changes:
            logger.info("✓ Configurations are semantically equivalent")
            return 0

        # Log diff summary and details
        log_diff_summary(report, args.base, args.proposed)
        log_diff_details(report)

        # Spec says exit code 3 for "configs differ"
        return 3

    except (ParseError, LexerError) as e:
        logger.error(f"✗ Diff failed to parse: {e}")
        if args.verbose:
            traceback.print_exc()
        return 2  # I/O or Parse error
    except Exception as e:
        logger.error(f"✗ Diff failed: {e}")
        if args.verbose:
            traceback.print_exc()
        return 2  # Unexpected error


def cmd_view(args) -> int:
    """Open the graph viewer for a pfig configuration."""
    target = args.uri or args.config
    if not target:
        logger.error("✗ Provide a .pfig file path or --uri")
        return 1
    return _serve_view(args, target)


def cmd_diff_view(args) -> int:
    """Open the proposed graph with changes from the base highlighted."""
    return _serve_view(args, args.proposed, base=args.base)


def _serve_view(args, target: str, *, base: str | None = None) -> int:
    try:
        import threading

        from pfig.vis import serve_graph

        _ensure_cwd_on_syspath()
        server = serve_graph(
            target,
            mode=args.mode,
            host=args.host,
            port=args.port,
            open_browser=not args.no_open,
            block=False,
            profiles=args.profiles or [],
            set_overrides=args.set_values,
            allow_missing_externals=args.allow_missing_externals,
            strict_signatures=args.strict,
            reveal_url=args.reveal_url,
            compare_to=base,
            alignment=_namespace_alignment(args.align_namespace) if base is not None else "auto",
        )
        print(server.url)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            return 130
        finally:
            server.shutdown()
        return 0
    except Exception as e:
        logger.error(f"✗ Could not open graph viewer: {e}")
        if args.verbose:
            traceback.print_exc()
        return 2


# ===== Argument Parsing =====


def parse_set_args(set_args: List[str]) -> dict:
    """Parse --set k=v arguments into a dictionary."""
    result = {}
    if not set_args:
        return result

    for arg in set_args:
        if "=" not in arg:
            raise ValueError(f"Invalid --set argument: {arg}. Expected format: key=value")

        key, value = arg.split("=", 1)
        key = key.strip()
        value = value.strip()

        parsed_value = _parse_cli_value(value)
        if key in result and not _runtime_values_equal(result[key], parsed_value):
            raise ValueError(f"Conflicting --set assignments for '{key}'")
        result[key] = parsed_value

    return result


def _validate_binding_path(raw: str) -> tuple:
    """Validate and split a pFig path (IDENT { '/' IDENT }*) into parts."""
    if not raw:
        raise ValueError("Path cannot be empty.")
    parts = raw.split("/")
    for part in parts:
        if not part:
            raise ValueError(
                f"Invalid path '{raw}': empty segment (double slash or leading/trailing slash)."
            )
        if not part.isidentifier():
            raise ValueError(f"Invalid path '{raw}': segment '{part}' is not a valid identifier.")
    return tuple(parts)


def parse_profile_arg(raw: str) -> ProfileSpec:
    """Parse a --profile argument into a ProfileSpec.

    Accepted forms:
        profiles/local.pfig              -> ProfileSpec(path=..., prefix=())
        train::profiles/local.pfig       -> ProfileSpec(path=..., prefix=("train",))
        train/model::profiles/local.pfig -> ProfileSpec(path=..., prefix=("train", "model"))
    """
    if "::" not in raw:
        return ProfileSpec(path=Path(raw), prefix=())

    prefix_raw, path_raw = raw.split("::", 1)

    if not prefix_raw:
        raise ValueError(
            "Profile prefix before '::' cannot be empty. "
            "Use the form '--profile <prefix>::<path>' or omit '::' for root application."
        )
    if not path_raw:
        raise ValueError("Profile path after '::' cannot be empty.")

    try:
        prefix = _validate_binding_path(prefix_raw)
    except ValueError as e:
        raise ValueError(f"Invalid profile prefix '{prefix_raw}': {e}") from e

    return ProfileSpec(path=Path(path_raw), prefix=prefix)


def _parse_cli_value(value: str) -> Any:
    """
    Parse CLI value using pfig syntax for complex types, falling back to strings.

    Strategy:
    1. Wrap the value in a dummy binding and try to parse it as pfig code.
    2. If parsing/compilation succeeds (e.g., lists, dicts, quoted strings, numbers), use that.
    3. If it fails (e.g., unquoted strings that look like refs), fallback to basic type inference.
    """
    value = value.strip()

    # Attempt strict DSL parsing first
    # We wrap in a dummy binding because the parser expects a full statement
    dummy_source = f"__cli_tmp__ = {value}"

    try:
        # 1. Parse
        # filename="<cli>" helps in error messages if needed
        ast = parse_pfig_source(dummy_source, filename="<cli>")

        # 2. Compile the expression from the dummy binding
        # The parser guarantees the first statement is our binding
        binding = ast.bindings()[0]
        compiler = ASTCompiler()
        compiled_val = compiler.compile_expr(binding.value)

        # 3. Materialize (turn IR into Python objects like list/dict)
        materializer = Materializer()

        # Note: This might raise ExecutionError if the value is a Reference (@ref or ident)
        # that cannot be resolved in an empty context. This is expected behavior,
        # which triggers the fallback below.
        return materializer.materialize_value(compiled_val)

    except (ParseError, LexerError, ExecutionError, AttributeError, KeyError):
        # Fallback: If DSL parsing fails (e.g. unquoted string treated as unknown identifier),
        # use the relaxed CLI inference (strings, simple numbers)
        return _fallback_parse_cli_value(value)


def _fallback_parse_cli_value(value: str) -> Any:
    """
    Legacy/Relaxed parsing for CLI values (unquoted strings, basic numbers).
    Used when strict DSL parsing fails.
    """
    # Boolean values
    if value.lower() in ("true", "false"):
        return value.lower() == "true"

    # None value
    if value.lower() == "none":
        return None

    # Integer values
    try:
        if "." not in value and "e" not in value.lower():
            return int(value)
    except ValueError:
        pass

    # Float values
    try:
        return float(value)
    except ValueError:
        pass

    # Default to string (handle unquoted identifiers like 'cuda')
    return value


def _add_namespace_alignment_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--align-namespace",
        metavar="BASE=PROPOSED",
        help="Align unmatched namespaces by path (use / for the root)",
    )
    group.add_argument(
        "--no-align-namespace",
        dest="align_namespace",
        action="store_const",
        const=None,
        help="Compare exact qualified paths only",
    )
    parser.set_defaults(align_namespace="auto")


def _add_comparison_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("base", metavar="BASE", help="Base .pfig configuration file")
    parser.add_argument("proposed", metavar="PROPOSED", help="Proposed .pfig configuration file")
    _add_namespace_alignment_flags(parser)


def _add_viewer_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mode",
        choices=["reachable", "all"],
        default="reachable",
        help="Graph scope (default: reachable)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Bind port, or 0 for an ephemeral port",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open a browser automatically",
    )
    parser.add_argument("--reveal-url", help="Editor callback URL for file/line jumps")
    parser.add_argument(
        "--set",
        nargs="+",
        dest="set_args",
        help="Set external or override values: --set key1=value1 key2=value2",
    )
    parser.add_argument(
        "--profile",
        nargs="+",
        action="extend",
        dest="profiles",
        default=[],
        help="Load external overrides from profile file(s)",
    )
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    parser.add_argument(
        "--allow-missing-externals",
        action="store_true",
        help="Allow graphing without providing all external values",
    )


def create_parser() -> argparse.ArgumentParser:
    """Create the main argument parser."""
    parser = argparse.ArgumentParser(
        prog="pfig",
        description="pfig - A tiny, strict DSL for declarative object configuration",
    )
    parser.add_argument("--version", action="version", version=f"pfig {get_version()}")
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v, -vv)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # pfig view
    view_parser = subparsers.add_parser("view", help="Open the graph viewer")
    view_parser.add_argument("config", nargs="?", help="Path to .pfig configuration file")
    view_parser.add_argument("--uri", help="file:// URI or path to .pfig configuration file")
    _add_viewer_options(view_parser)
    view_parser.set_defaults(func=cmd_view)

    diff_view_parser = subparsers.add_parser(
        "diff-view", help="View changes between two configurations"
    )
    _add_comparison_inputs(diff_view_parser)
    _add_viewer_options(diff_view_parser)
    diff_view_parser.set_defaults(func=cmd_diff_view)

    # pfig validate
    validate_parser = subparsers.add_parser(
        "validate", help="Validate configuration without execution"
    )
    validate_parser.add_argument("config", help="Path to .pfig configuration file")
    validate_parser.add_argument(
        "--entrypoint",
        help="Override entrypoint: binding name or provider.attr path "
        "(e.g. dataset_manager.upload, outer.data.copy)",
    )
    validate_parser.add_argument(
        "--set",
        nargs="+",
        dest="set_args",
        help="Set external or override values: --set key1=value1 key2=value2",
    )
    validate_parser.add_argument(
        "--profile",
        nargs="+",
        action="extend",
        dest="profiles",
        default=[],
        help="Load external overrides from profile file(s)",
    )
    validate_parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    validate_parser.add_argument(
        "--allow-missing-externals",
        action="store_true",
        help="Allow validation without providing all external values",
    )
    validate_parser.set_defaults(func=cmd_validate)

    # pfig run
    run_parser = subparsers.add_parser("run", help="Execute configuration")
    run_parser.add_argument("config", nargs="?", help="Path to .pfig configuration file")
    run_parser.add_argument(
        "--bundle",
        dest="bundle",
        help="Run from a bundle (.tar.gz or directory)",
    )
    run_parser.add_argument(
        "--entrypoint",
        help="Override entrypoint: binding name or provider.attr path "
        "(e.g. dataset_manager.upload, outer.data.copy)",
    )
    run_parser.add_argument(
        "--set",
        nargs="+",
        dest="set_args",
        help="Set external or override values: --set key1=value1 key2=value2",
    )
    run_parser.add_argument(
        "--profile",
        nargs="+",
        action="extend",
        dest="profiles",
        default=[],
        help="Load external overrides from profile file(s)",
    )
    run_parser.add_argument(
        "--snapshot",
        dest="snapshot_dir",
        help="Directory for snapshots; a timestamped subfolder is created (default: ./snapshots)",
    )
    run_parser.add_argument(
        "--no-snapshot",
        action="store_true",
        dest="no_snapshot",
        help="Disable snapshot writing",
    )
    run_parser.add_argument(
        "--base-commit",
        dest="base_commit",
        help="Explicit base commit for patch range (e.g. replay worktree)",
    )
    run_parser.set_defaults(func=cmd_run)

    # pfig bundle
    bundle_parser = subparsers.add_parser("bundle", help="Create portable config bundle")
    bundle_parser.add_argument("config", help="Path to .pfig configuration file")
    bundle_parser.add_argument(
        "-o",
        "--output",
        dest="output",
        help="Output path for bundle (default: bundle.tar.gz)",
    )
    bundle_parser.add_argument(
        "-d",
        "--directory",
        action="store_true",
        dest="directory",
        help="Create bundle as directory instead of tarball",
    )
    bundle_parser.add_argument(
        "--entrypoint",
        help="Override entrypoint: binding name or provider.attr path "
        "(e.g. dataset_manager.upload, outer.data.copy)",
    )
    bundle_parser.add_argument(
        "--set",
        nargs="+",
        dest="set_args",
        help="Set external or override values: --set key1=value1 key2=value2",
    )
    bundle_parser.add_argument(
        "--profile",
        nargs="+",
        action="extend",
        dest="profiles",
        default=[],
        help="Load external overrides from profile file(s)",
    )
    bundle_parser.set_defaults(func=cmd_bundle)

    # pfig install-rules <agent>
    install_rules_parser = subparsers.add_parser(
        "install-rules",
        help="Install pfig agent rules into the current project",
    )
    install_rules_subs = install_rules_parser.add_subparsers(dest="agent", help="Target agent")
    for agent_name, spec in _RULES_AGENTS.items():
        target_display = f"{spec['target']}/" if spec["mode"] == "per-file" else str(spec["target"])
        sub = install_rules_subs.add_parser(
            agent_name,
            help=f"Install pfig {spec['label']} rules into {target_display}",
        )
        sub.add_argument(
            "--force",
            action="store_true",
            help="Overwrite existing rule files",
        )
        sub.set_defaults(func=cmd_install_rules)

    # pfig replay (with sub-subcommands: prepare, validate, run)
    replay_parser = subparsers.add_parser("replay", help="Reproduce a run from a snapshot")
    replay_subs = replay_parser.add_subparsers(dest="replay_command", help="Replay subcommands")

    # pfig replay prepare
    rp_prepare = replay_subs.add_parser(
        "prepare", help="Set up replay workspace (code, venv, bundle)"
    )
    rp_prepare.add_argument("snapshot", help="Path to snapshot directory or log.json")
    rp_prepare.add_argument("--workspace", help="Override workspace directory path")
    rp_prepare.add_argument("--python", help="Python interpreter for the replay venv")
    rp_prepare.add_argument(
        "--no-deps", action="store_true", help="Skip venv creation and pip install"
    )
    rp_prepare.add_argument("--dry-run", action="store_true", help="Print plan without executing")
    rp_prepare.set_defaults(func=cmd_replay_prepare)

    # pfig replay validate
    rp_validate = replay_subs.add_parser(
        "validate", help="Validate baseline digest of a prepared workspace"
    )
    rp_validate.add_argument("snapshot", help="Path to snapshot directory or log.json")
    rp_validate.add_argument(
        "--workspace",
        required=True,
        help="Path to a prepared replay workspace",
    )
    rp_validate.add_argument(
        "--allow-baseline-mismatch",
        action="store_true",
        help="Continue even if baseline digest does not match",
    )
    rp_validate.add_argument(
        "--set",
        nargs="+",
        dest="set_args",
        help="Override external values: --set key=value",
    )
    rp_validate.set_defaults(func=cmd_replay_validate)

    # pfig replay run
    rp_run = replay_subs.add_parser("run", help="Full replay: prepare, validate, and execute")
    rp_run.add_argument("snapshot", help="Path to snapshot directory or log.json")
    rp_run.add_argument("--workspace", help="Override workspace directory path")
    rp_run.add_argument("--python", help="Python interpreter for the replay venv")
    rp_run.add_argument("--no-deps", action="store_true", help="Skip venv creation and pip install")
    rp_run.add_argument("--dry-run", action="store_true", help="Print plan without executing")
    rp_run.add_argument(
        "--allow-baseline-mismatch",
        action="store_true",
        help="Continue even if baseline digest does not match",
    )
    rp_run.add_argument(
        "--set",
        nargs="+",
        dest="set_args",
        help="Override external values: --set key=value",
    )
    rp_run.set_defaults(func=cmd_replay_run)

    # pfig diff
    diff_parser = subparsers.add_parser("diff", help="Compare two configurations")
    _add_comparison_inputs(diff_parser)
    diff_parser.add_argument(
        "--set",
        nargs="+",
        dest="set_args",
        help="Set external or override value for both configs: --set key=value",
    )
    diff_parser.set_defaults(func=cmd_diff)

    # pfig fmt
    fmt_parser = subparsers.add_parser("fmt", help="Format .pfig files in place (or check/diff)")
    fmt_parser.add_argument(
        "paths",
        nargs="*",
        help="Files or directories to format. With no paths, reads from stdin.",
    )
    fmt_parser.add_argument(
        "--check",
        action="store_true",
        help="Don't write; exit 1 if any file would change",
    )
    fmt_parser.add_argument(
        "--diff",
        action="store_true",
        help="Print a unified diff to stdout instead of writing",
    )
    fmt_parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write formatted output to stdout instead of in-place",
    )
    fmt_parser.add_argument(
        "--line-length",
        type=int,
        default=100,
        help="Maximum line length before wrapping (default: 100)",
    )
    fmt_parser.set_defaults(func=cmd_fmt)

    return parser


def main() -> int:
    """Main CLI entry point."""
    from .logging import setup_logging

    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    if not hasattr(args, "func"):
        # Sub-subcommand missing (e.g. "pfig replay" without prepare/validate/run)
        parser.parse_args([args.command, "--help"])
        return 1

    if args.verbose:
        setup_logging(level="DEBUG")

    if hasattr(args, "set_args"):
        try:
            args.set_values = parse_set_args(args.set_args or [])
        except ValueError as e:
            logger.error(f"✗ {e}")
            return 1
    else:
        args.set_values = {}

    if hasattr(args, "profiles") and args.profiles:
        try:
            args.profiles = [parse_profile_arg(p) for p in args.profiles]
        except ValueError as e:
            logger.error(f"✗ Invalid --profile argument: {e}")
            return 1

    try:
        return args.func(args)
    except KeyboardInterrupt:
        logger.error("\n✗ Interrupted")
        return 130
    except Exception as e:
        logger.error(f"✗ Unexpected error: {e}")
        if args.verbose:
            traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
