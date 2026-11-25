"""
pfig Bundle Module

Creates portable config bundles with explicit filesystem includes,
preserving their relative directory structure.

Bundle structure:
    configs/                  # Filesystem files (relative structure preserved)
    __profiles__/            # Profile files and their includes
    bundle_manifest.json     # Entry point, profile list, file list
"""

import json
import os
import shutil
import tarfile
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import fs as _fs
from .includes import IncludeResolution
from .logging import get_logger

logger = get_logger("bundle")

BUNDLE_VERSION = 2
CONFIGS_DIR = "configs"
PROFILES_DIR = "__profiles__"
MANIFEST_FILE = "bundle_manifest.json"


def _is_safe_tar_member(member: tarfile.TarInfo, dest: Path) -> bool:
    """Return True if *member* can be extracted safely under *dest*."""
    if not member.name or member.name.startswith(("/", "\\")):
        return False
    if ".." in Path(member.name).parts:
        return False
    target = (dest / member.name).resolve()
    try:
        target.relative_to(dest.resolve())
    except ValueError:
        return False
    if (
        member.issym()
        or member.islnk()
        or member.isdev()
        or member.isfifo()
        or member.ischr()
        or member.isblk()
    ):
        return False
    return True


def _extractall_with_data_filter(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract archive members under *dest* with path-traversal and symlink guards."""
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    try:
        tar.extractall(dest, filter="data")
        return
    except TypeError:
        pass
    except tarfile.FilterError as exc:
        raise ValueError(f"Unsafe tar member rejected: {exc}") from exc

    for member in tar.getmembers():
        if not _is_safe_tar_member(member, dest):
            raise ValueError(f"Unsafe tar member rejected: {member.name!r}")
        tar.extract(member, dest, set_attrs=False)


@dataclass
class BundleManifest:
    """Manifest describing bundle contents and how to run it.

    Attributes:
        bundle_version: Version of the bundle format (currently 2)
        entry: Path to the entry config file within the bundle (relative to bundle root)
        files: List of all files in the bundle (relative to bundle root)
        profiles: Ordered list of top-level profile paths within the bundle
            (relative to bundle root). When running from a bundle, these profiles
            are applied in order unless overridden by CLI --profile.
    """

    bundle_version: int
    entry: str
    files: List[str]
    profiles: List[str] = field(default_factory=list)

    def to_json(self) -> str:
        """Serialize manifest to JSON string."""
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "BundleManifest":
        """Deserialize manifest from JSON string."""
        data = json.loads(json_str)
        # Accept filesystem-only v1 bundles, but never silently reinterpret old mounts.
        if data.pop("mounts", {}):
            raise ValueError(
                "Bundles with mounts are no longer supported. "
                "Replace protocol includes with filesystem paths and recreate the bundle."
            )
        data.setdefault("profiles", [])
        return cls(**data)

    @classmethod
    def from_file(cls, path: Path) -> "BundleManifest":
        """Load manifest from a file."""
        return cls.from_json(_fs.read_text(path))


def _common_ancestor(paths: List[Path]) -> Path:
    """Return the deepest common ancestor directory of *paths*.

    If paths share no common prefix (shouldn't happen on one filesystem),
    falls back to ``/``.  If the result is a file, its parent is returned.
    """
    if not paths:
        return Path.cwd()

    common_parts = list(paths[0].parts)
    for path in paths[1:]:
        path_parts = list(path.parts)
        new_common = []
        for a, b in zip(common_parts, path_parts):
            if a == b:
                new_common.append(a)
            else:
                break
        common_parts = new_common

    if not common_parts:
        logger.warning("No common ancestor found, using root")
        return Path("/")

    ancestor = Path(*common_parts)
    if ancestor.is_file():
        ancestor = ancestor.parent
    return ancestor


def compute_fs_root(file_origins: Dict[str, IncludeResolution], entry_path: Path) -> Path:
    """Compute the filesystem root for bundling.

    The fs_root is the common ancestor of all included .pfig files.
    """
    fs_paths: List[Path] = [entry_path.resolve()]
    for path_str in file_origins:
        fs_paths.append(Path(path_str).resolve())

    if not fs_paths:
        return entry_path.parent.resolve()

    result = _common_ancestor(fs_paths)
    logger.debug(f"Computed fs_root: {result}")
    return result


def _compute_profile_fs_root(
    profile_paths: List[Path],
    profile_file_origins: Dict[str, IncludeResolution],
) -> Path:
    """Compute the filesystem root for profile files."""
    fs_paths: List[Path] = [p.resolve() for p in profile_paths]
    for path_str in profile_file_origins:
        fs_paths.append(Path(path_str).resolve())

    if not fs_paths:
        return Path.cwd()

    result = _common_ancestor(fs_paths)
    logger.debug(f"Computed profile fs_root: {result}")
    return result


def create_bundle(
    file_origins: Dict[str, IncludeResolution],
    entry_path: Path,
    output_path: Path,
    as_tarball: bool = True,
    profile_paths: Optional[List[Path]] = None,
    profile_file_origins: Optional[Dict[str, IncludeResolution]] = None,
) -> Path:
    """Create a portable config bundle.

    Args:
        file_origins: Dictionary mapping absolute paths to their IncludeResolution
        entry_path: Absolute path to the entry config file
        output_path: Path where bundle should be created (directory or .tar.gz)
        as_tarball: If True, create a .tar.gz archive; if False, create a directory
        profile_paths: Optional list of resolved absolute paths to top-level profile files
        profile_file_origins: Optional file origins from profile includes (for bundling
            files included by profiles)

    Returns:
        Path to the created bundle (directory or tarball)
    """
    entry_path = entry_path.resolve()
    fs_root = compute_fs_root(file_origins, entry_path)

    logger.info(f"Creating bundle with fs_root: {fs_root}")

    # Create bundle in a temp directory first
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle_dir = Path(tmpdir) / "bundle"
        bundle_dir.mkdir()

        configs_dir = bundle_dir / CONFIGS_DIR
        profiles_dir = bundle_dir / PROFILES_DIR

        files: List[str] = []
        profile_bundle_paths: List[str] = []

        # Copy entry file to configs/
        entry_rel = entry_path.relative_to(fs_root)
        entry_dest = configs_dir / entry_rel
        entry_dest.parent.mkdir(parents=True, exist_ok=True)
        _fs.cp(entry_path, entry_dest)
        entry_bundle_path = f"{CONFIGS_DIR}/{entry_rel}"
        files.append(entry_bundle_path)
        logger.debug(f"Copied entry: {entry_path} -> {entry_bundle_path}")

        # Process all included files
        for path_str in file_origins:
            abs_path = Path(path_str).resolve()
            rel_path = abs_path.relative_to(fs_root)
            dest_path = configs_dir / rel_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            _fs.cp(abs_path, dest_path)

            bundle_path = f"{CONFIGS_DIR}/{rel_path}"
            files.append(bundle_path)
            logger.debug(f"Copied include: {abs_path} -> {bundle_path}")

        # Process profile files
        if profile_paths:
            profile_file_origins = profile_file_origins or {}
            profile_fs_root = _compute_profile_fs_root(profile_paths, profile_file_origins)

            # Copy top-level profile files into __profiles__/
            for prof_path in profile_paths:
                abs_prof = prof_path.resolve()
                try:
                    rel_path = abs_prof.relative_to(profile_fs_root)
                except ValueError:
                    logger.warning(
                        f"Profile {abs_prof} is outside profile fs_root "
                        f"{profile_fs_root}, using filename only"
                    )
                    rel_path = Path(abs_prof.name)

                dest_path = profiles_dir / rel_path
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                _fs.cp(abs_prof, dest_path)

                bundle_path = f"{PROFILES_DIR}/{rel_path}"
                files.append(bundle_path)
                profile_bundle_paths.append(bundle_path)
                logger.debug(f"Copied profile: {abs_prof} -> {bundle_path}")

            # Copy profile-included files
            for path_str in profile_file_origins:
                abs_path = Path(path_str).resolve()
                rel_path = abs_path.relative_to(profile_fs_root)
                dest_path = profiles_dir / rel_path
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                _fs.cp(abs_path, dest_path)

                bundle_path = f"{PROFILES_DIR}/{rel_path}"
                files.append(bundle_path)
                logger.debug(f"Copied profile include: {abs_path} -> {bundle_path}")

        # Create manifest
        manifest = BundleManifest(
            bundle_version=BUNDLE_VERSION,
            entry=entry_bundle_path,
            files=sorted(set(files)),  # Deduplicate and sort
            profiles=profile_bundle_paths,
        )

        manifest_path = bundle_dir / MANIFEST_FILE
        _fs.write_text(manifest_path, manifest.to_json())
        logger.debug(f"Created manifest with {len(files)} files")

        # Create output
        if as_tarball:
            if not str(output_path).endswith(".tar.gz"):
                output_path = Path(str(output_path) + ".tar.gz")

            # Build tarball locally, then copy to final destination
            local_tar = Path(tmpdir) / "bundle.tar.gz"
            with tarfile.open(local_tar, "w:gz") as tar:
                for item in bundle_dir.iterdir():
                    tar.add(item, arcname=item.name)

            _fs.cp(local_tar, output_path)
            logger.info(f"Created bundle tarball: {output_path}")
            return output_path
        else:
            if _fs.is_uri(str(output_path)):
                _fs.cp(str(bundle_dir), str(output_path), recursive=True)
            else:
                if output_path.exists():
                    shutil.rmtree(output_path)
                shutil.copytree(bundle_dir, output_path)
            logger.info(f"Created bundle directory: {output_path}")
            return output_path


def collect_profile_bundle_info(
    profile_paths: Sequence[Any],
) -> Tuple[List[Path], Dict[str, IncludeResolution]]:
    """Collect resolved profile paths and their file_origins for bundling.

    Loads each profile file and aggregates the file origins from all profiles
    (including any files they include via include statements).

    Args:
        profile_paths: List of paths to profile files (str, Path, or ProfileSpec)

    Returns:
        Tuple of (resolved_top_level_paths, combined_file_origins) where:
        - resolved_top_level_paths: Absolute paths to top-level profile files
        - combined_file_origins: Merged file_origins from all profile compilations
    """
    from .execution import ProfileSpec, load_profile

    resolved_paths: List[Path] = []
    combined_origins: Dict[str, IncludeResolution] = {}

    for entry in profile_paths:
        profile_path = entry.path if isinstance(entry, ProfileSpec) else entry
        resolved = Path(profile_path).resolve()
        resolved_paths.append(resolved)

        _bindings, file_origins = load_profile(path=profile_path)
        combined_origins.update(file_origins)

    logger.debug(
        f"Collected {len(resolved_paths)} profile(s) with {len(combined_origins)} included file(s)"
    )
    return resolved_paths, combined_origins


def load_bundle(
    bundle_path: str | Path, extract_dir: Optional[Path] = None
) -> tuple[Path, BundleManifest]:
    """Load a bundle from a tarball or directory (local or GCS).

    Args:
        bundle_path: Path to bundle tarball (.tar.gz) or directory.
            May be a string URI (e.g. ``gs://...``) or a local Path.
        extract_dir: Optional directory to extract tarball into. If provided,
            the tarball will be extracted here (caller manages cleanup).
            If not provided, a temp directory is created (caller must clean up).

    Returns:
        Tuple of (extracted_path, manifest) where extracted_path is the
        path to the bundle contents (may be a temp directory for tarballs)

    Raises:
        FileNotFoundError: If bundle or manifest not found
        ValueError: If bundle format is invalid
    """
    bundle_path_str = str(bundle_path)
    is_remote = _fs.is_uri(bundle_path_str)

    if not is_remote:
        bundle_path = Path(bundle_path).resolve()
        bundle_path_str = str(bundle_path)
    bundle_location = bundle_path_str if is_remote else Path(bundle_path)

    if not _fs.exists(bundle_location):
        raise FileNotFoundError(f"Bundle not found: {bundle_path_str}")

    if is_remote or _fs.is_file(bundle_location):
        # Tarball - extract to directory
        if not bundle_path_str.endswith(".tar.gz"):
            raise ValueError(f"Bundle file must be .tar.gz: {bundle_path_str}")

        if extract_dir is not None:
            target_dir = Path(extract_dir)
            target_dir.mkdir(parents=True, exist_ok=True)
        else:
            target_dir = Path(tempfile.mkdtemp(prefix="pfig_bundle_"))

        logger.debug(f"Extracting bundle to: {target_dir}")

        # If remote, download to local temp file first
        if is_remote:
            local_tar = target_dir / "bundle.tar.gz"
            _fs.cp(bundle_location, local_tar)
            tar_to_open = local_tar
        else:
            tar_to_open = bundle_location

        with tarfile.open(tar_to_open, "r:gz") as tar:
            _extractall_with_data_filter(tar, target_dir)

        # Clean up the downloaded tarball if we fetched it
        if is_remote and local_tar.exists():
            local_tar.unlink()

        bundle_dir = target_dir
    else:
        # Directory - use directly
        bundle_dir = Path(bundle_location)

    # Load manifest
    manifest_path = bundle_dir / MANIFEST_FILE
    if not _fs.exists(manifest_path):
        raise ValueError(f"Bundle missing manifest: {manifest_path}")

    manifest = BundleManifest.from_file(manifest_path)

    if manifest.bundle_version not in (1, BUNDLE_VERSION):
        raise ValueError(f"Unsupported bundle version: {manifest.bundle_version}")

    logger.info(f"Loaded bundle: {len(manifest.files)} files")
    return bundle_dir, manifest
