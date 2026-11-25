"""
Unit tests for the pfig snapshot module.
"""

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pfig.execution import (
    ASTCompiler,
    FreshProviderCall,
    LiteralValue,
    ProviderRef,
    ProviderSpec,
    execute_pfig_with_metadata,
)
from pfig.parser import parse_pfig_source
from pfig.snapshot import (
    SNAPSHOT_VERSION,
    GitInfo,
    IntrospectionInfo,
    IntrospectionStatus,
    OpaqueReasonCode,
    SnapshotMeta,
    build_ir_graph,
    check_introspection_warnings,
    check_reproducibility_warnings,
    collect_environment,
    collect_git_info,
    compute_graph_digest,
    copy_config_files,
    create_patch_series,
    create_requirements_lock,
    create_snapshot,
    generate_run_id,
    generate_snapshot_dirname,
    get_pfig_version,
    load_snapshot,
)


class TestGitInfo:
    """Test git information collection."""

    def test_git_info_to_dict(self):
        """Test GitInfo serialization."""
        git_info = GitInfo(
            commit="abc123def456",
            is_dirty=True,
            remote_origin_url="git@github.com:org/repo.git",
        )
        result = git_info.to_dict()

        assert result["commit"] == "abc123def456"
        assert result["is_dirty"] is True
        assert result["remote_origin_url"] == "git@github.com:org/repo.git"

    def test_git_info_none_returns_none(self):
        """Test that GitInfo with no commit returns None."""
        git_info = GitInfo(commit=None)
        assert git_info.to_dict() is None

    @patch("pfig.snapshot._run_git_command")
    def test_collect_git_info_success(self, mock_run):
        """Test collecting git info from a repository with synced upstream."""
        mock_run.side_effect = [
            "abc123def456",  # rev-parse HEAD
            "",  # status --porcelain (clean)
            "git@github.com:org/repo.git",  # remote get-url origin
            "0",  # rev-list --count (synced with upstream)
            "abc123def456",  # rev-parse @{u} (same as HEAD when synced)
            "",  # lfs ls-files (no LFS)
        ]

        result = collect_git_info()

        assert result is not None
        assert result.commit == "abc123def456"
        assert result.is_dirty is False
        assert result.remote_origin_url == "git@github.com:org/repo.git"
        assert result.commits_ahead == 0
        assert result.upstream_commit == "abc123def456"
        assert result.uses_lfs is False
        # Check serialization
        result_dict = result.to_dict()
        assert result_dict["has_upstream"] is True
        assert "commits_ahead" not in result_dict  # Omitted when 0

    @patch("pfig.snapshot._run_git_command")
    def test_collect_git_info_dirty(self, mock_run):
        """Test detecting dirty git status."""
        mock_run.side_effect = [
            "abc123def456",
            "M pfig/snapshot.py",  # Modified file
            None,  # no remote
            "0",  # rev-list --count
            "abc123def456",  # rev-parse @{u}
            "",  # lfs ls-files (no LFS)
        ]

        result = collect_git_info()

        assert result is not None
        assert result.is_dirty is True

    @patch("pfig.snapshot._run_git_command")
    def test_collect_git_info_commits_ahead(self, mock_run):
        """Test detecting unpushed commits."""
        mock_run.side_effect = [
            "abc123def456",  # rev-parse HEAD
            "",  # status --porcelain (clean)
            "git@github.com:org/repo.git",  # remote get-url origin
            "3",  # rev-list --count (3 unpushed commits)
            "upstream789abc",  # rev-parse @{u} (upstream commit)
            "",  # lfs ls-files (no LFS)
        ]

        result = collect_git_info()

        assert result is not None
        assert result.commit == "abc123def456"
        assert result.commits_ahead == 3
        assert result.upstream_commit == "upstream789abc"
        # Check serialization: both has_upstream, commits_ahead, and upstream_commit present
        result_dict = result.to_dict()
        assert result_dict["has_upstream"] is True
        assert result_dict["commits_ahead"] == 3
        assert result_dict["upstream_commit"] == "upstream789abc"

    @patch("pfig.snapshot._run_git_command")
    def test_collect_git_info_no_upstream(self, mock_run):
        """Test behavior when there's no upstream branch (local-only branch)."""
        mock_run.side_effect = [
            "abc123def456",  # rev-parse HEAD
            "",  # status --porcelain (clean)
            "git@github.com:org/repo.git",  # remote get-url origin
            None,  # rev-list fails (no upstream configured)
            "",  # lfs ls-files (no LFS)
        ]

        result = collect_git_info()

        assert result is not None
        assert result.commits_ahead is None  # No upstream = None
        assert result.upstream_commit is None  # No upstream commit
        # Check serialization: has_upstream should be False
        result_dict = result.to_dict()
        assert result_dict["has_upstream"] is False
        assert "commits_ahead" not in result_dict
        assert "upstream_commit" not in result_dict

    @patch("pfig.snapshot._run_git_command")
    def test_collect_git_info_not_repo(self, mock_run):
        """Test behavior when not in a git repository."""
        mock_run.return_value = None

        result = collect_git_info()

        assert result is None

    @patch("pfig.snapshot._run_git_command")
    def test_collect_git_info_with_lfs(self, mock_run):
        """Test detecting Git LFS usage."""
        mock_run.side_effect = [
            "abc123def456",  # rev-parse HEAD
            "",  # status --porcelain (clean)
            "git@github.com:org/repo.git",  # remote get-url origin
            "0",  # rev-list --count (synced)
            "abc123def456",  # rev-parse @{u}
            "large-file.bin\nmodel.weights",  # lfs ls-files (has LFS files)
        ]

        result = collect_git_info()

        assert result is not None
        assert result.uses_lfs is True

    @patch("pfig.snapshot._run_git_command")
    def test_collect_git_info_without_lfs(self, mock_run):
        """Test no LFS when lfs ls-files returns empty."""
        mock_run.side_effect = [
            "abc123def456",  # rev-parse HEAD
            "",  # status --porcelain (clean)
            "git@github.com:org/repo.git",  # remote get-url origin
            "0",  # rev-list --count (synced)
            "abc123def456",  # rev-parse @{u}
            "",  # lfs ls-files (no LFS files)
        ]

        result = collect_git_info()

        assert result is not None
        assert result.uses_lfs is False

    @patch("pfig.snapshot._run_git_command")
    def test_collect_git_info_with_submodules(self, mock_run, tmp_path):
        """Test has_submodules is True when .gitmodules exists."""
        mock_run.side_effect = [
            "abc123def456",  # rev-parse HEAD
            "",  # status --porcelain (clean)
            "git@github.com:org/repo.git",  # remote get-url origin
            "0",  # rev-list --count (synced)
            "abc123def456",  # rev-parse @{u}
            "",  # lfs ls-files (no LFS files)
        ]
        # Create .gitmodules file
        (tmp_path / ".gitmodules").write_text(
            '[submodule "lib"]\n\tpath = lib\n\turl = https://github.com/example/lib.git\n'
        )

        result = collect_git_info(tmp_path)

        assert result is not None
        assert result.has_submodules is True

    @patch("pfig.snapshot._run_git_command")
    def test_collect_git_info_with_base_commit(self, mock_run):
        """Test collect_git_info with explicit base_commit (e.g. replay worktree)."""
        mock_run.side_effect = [
            "abc123def456",  # rev-parse HEAD
            "",  # status (clean)
            "git@github.com:org/repo.git",  # remote
            "2",  # rev-list --count base..HEAD
            "",  # lfs ls-files
        ]

        result = collect_git_info(base_commit="base789")

        assert result is not None
        assert result.commit == "abc123def456"
        assert result.commits_ahead == 2
        assert result.upstream_commit == "base789"
        # Should have used base_commit..HEAD, not @{u}
        rev_list_call = [c for c in mock_run.call_args_list if "rev-list" in str(c)]
        assert len(rev_list_call) == 1
        assert "base789..HEAD" in str(rev_list_call[0])

    @patch("pfig.snapshot._run_git_command")
    def test_collect_git_info_without_submodules(self, mock_run, tmp_path):
        """Test has_submodules is False when .gitmodules does not exist."""
        mock_run.side_effect = [
            "abc123def456",  # rev-parse HEAD
            "",  # status --porcelain (clean)
            "git@github.com:org/repo.git",  # remote get-url origin
            "0",  # rev-list --count (synced)
            "abc123def456",  # rev-parse @{u}
            "",  # lfs ls-files (no LFS files)
        ]
        # No .gitmodules file in tmp_path

        result = collect_git_info(tmp_path)

        assert result is not None
        assert result.has_submodules is False


class TestEnvironmentCollection:
    """Test environment information collection."""

    def test_collect_environment_basic(self):
        """Test basic environment collection."""
        result = collect_environment()

        assert result.python_version is not None
        assert result.platform is not None
        assert result.working_dir is not None
        assert isinstance(result.command, list)

    def test_collect_environment_with_externals(self):
        """Test environment collection with externals."""
        externals = {"output_dir": "./runs", "num_gpus": 2}
        external_sources = {"output_dir": "cli", "num_gpus": "profile"}

        result = collect_environment(
            externals=externals,
            external_sources=external_sources,
        )

        assert "output_dir" in result.externals
        assert result.externals["output_dir"].value == "./runs"
        assert result.externals["output_dir"].source == "cli"
        assert result.externals["num_gpus"].source == "profile"

    def test_environment_has_no_mount_table(self):
        assert "mounts" not in collect_environment().to_dict()

    def test_environment_to_dict(self):
        """Test EnvironmentInfo serialization."""
        result = collect_environment(
            externals={"test": "value"},
            requirements_lock_path="payload/requirements.lock",
        )
        env_dict = result.to_dict()

        assert "python_version" in env_dict
        assert "platform" in env_dict
        assert "command" in env_dict
        assert "working_dir" in env_dict
        assert "requirements_lock_path" in env_dict


class TestIRGraphBuilding:
    """Test IR graph construction from compiled data."""

    def test_build_ir_graph_simple_values(self):
        """Test IR graph with simple values."""
        source = """
batch_size = 64
learning_rate = 0.001
model_name = "transformer"
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        compiler = ASTCompiler()
        compiled = compiler.compile_file(ast)

        graph = build_ir_graph(compiled)

        assert "batch_size" in graph
        assert graph["batch_size"]["type"] == "value"
        assert graph["batch_size"]["value"] == 64

        assert "learning_rate" in graph
        assert graph["learning_rate"]["value"] == 0.001

        assert "model_name" in graph
        assert graph["model_name"]["value"] == "transformer"

    def test_build_ir_graph_provider(self):
        """Test IR graph with provider definitions."""
        source = """
import json

config = json.dumps(obj={"test": True})
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        compiler = ASTCompiler()
        compiled = compiler.compile_file(ast)

        graph = build_ir_graph(compiled)

        assert "config" in graph
        assert graph["config"]["type"] == "provider"
        assert "json.dumps" in graph["config"]["target"]
        assert graph["config"]["is_shared"] is True
        assert "kwargs" in graph["config"]

    def test_build_ir_graph_with_references(self):
        """Test IR graph with provider references."""
        source = """
import json

base_config = {"batch": 64}
config_str = json.dumps(obj=@base_config)
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        compiler = ASTCompiler()
        compiled = compiler.compile_file(ast)

        graph = build_ir_graph(compiled)

        assert "config_str" in graph
        # The reference should be serialized as $ref
        kwargs = graph["config_str"]["kwargs"]
        assert "obj" in kwargs
        assert kwargs["obj"] == {"$ref": "base_config"}

    def test_build_ir_graph_with_reachable_filter(self):
        """Test IR graph with reachable names filter."""
        source = """
a = 1
b = 2
c = 3
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        compiler = ASTCompiler()
        compiled = compiler.compile_file(ast)

        # Only include 'a' and 'b'
        graph = build_ir_graph(compiled, reachable_names={"a", "b"})

        assert "a" in graph
        assert "b" in graph
        assert "c" not in graph

    def test_build_ir_graph_fresh_provider(self):
        """Test IR graph with fresh provider calls."""
        source = """
import json

base_encoder = json.JSONEncoder(indent=2)
custom_encoder = @base_encoder(sort_keys=True)
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        compiler = ASTCompiler()
        compiled = compiler.compile_file(ast)

        graph = build_ir_graph(compiled)

        assert "custom_encoder" in graph
        assert graph["custom_encoder"]["type"] == "fresh_provider"
        assert graph["custom_encoder"]["base_provider"] == "base_encoder"
        assert graph["custom_encoder"]["is_shared"] is False


class TestGraphDigest:
    """Test graph digest computation."""

    def test_compute_graph_digest_deterministic(self):
        """Test that digest is deterministic for same graph."""
        graph = {
            "model": {"type": "provider", "target": "pkg.Model", "kwargs": {}},
            "batch": {"type": "value", "value": 64},
        }

        digest1 = compute_graph_digest(graph)
        digest2 = compute_graph_digest(graph)

        assert digest1 == digest2
        assert digest1.startswith("sha256:")

    def test_compute_graph_digest_different_for_different_graphs(self):
        """Test that different graphs produce different digests."""
        graph1 = {"batch": {"type": "value", "value": 64}}
        graph2 = {"batch": {"type": "value", "value": 128}}

        digest1 = compute_graph_digest(graph1)
        digest2 = compute_graph_digest(graph2)

        assert digest1 != digest2

    def test_compute_graph_digest_order_independent(self):
        """Test that key order doesn't affect digest (sorted keys)."""
        graph1 = {
            "a": {"type": "value", "value": 1},
            "b": {"type": "value", "value": 2},
        }
        graph2 = {
            "b": {"type": "value", "value": 2},
            "a": {"type": "value", "value": 1},
        }

        digest1 = compute_graph_digest(graph1)
        digest2 = compute_graph_digest(graph2)

        assert digest1 == digest2


class TestPayloadBuilding:
    """Test payload creation (config files, requirements.lock)."""

    def test_copy_config_files(self):
        """Test copying pfig config files to payload directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create some pfig files
            config1 = tmpdir / "config1.pfig"
            config2 = tmpdir / "config2.pfig"
            config1.write_text("batch = 64")
            config2.write_text("lr = 0.001")

            # Copy to output dir
            output_dir = tmpdir / "output"
            output_dir.mkdir()
            copied = copy_config_files([config1, config2], output_dir)

            # Verify files were copied
            assert (output_dir / "config1.pfig").exists()
            assert (output_dir / "config2.pfig").exists()
            assert (output_dir / "config1.pfig").read_text() == "batch = 64"
            assert (output_dir / "config2.pfig").read_text() == "lr = 0.001"
            assert len(copied) == 2

    def test_copy_config_files_handles_missing_files(self):
        """Test that copy_config_files skips non-existent files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create one file, reference another that doesn't exist
            config1 = tmpdir / "config1.pfig"
            config1.write_text("batch = 64")
            missing = tmpdir / "missing.pfig"

            # Should not raise
            output_dir = tmpdir / "output"
            output_dir.mkdir()
            copied = copy_config_files([config1, missing], output_dir)

            assert (output_dir / "config1.pfig").exists()
            assert not (output_dir / "missing.pfig").exists()
            assert len(copied) == 1

    def test_create_requirements_lock(self):
        """Test creating requirements.lock file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "requirements.lock"

            result = create_requirements_lock(lock_path)

            # Should succeed (pip is available in test env)
            assert result is True
            assert lock_path.exists()
            content = lock_path.read_text()
            # Should contain some packages
            assert len(content) > 0

    @patch("subprocess.run")
    def test_create_patch_series_with_commits_and_dirty(self, mock_run):
        """Test creating patch series with both commit patches and dirty patch."""

        def mock_subprocess(cmd, **kwargs):
            if "format-patch" in cmd:
                # Simulate git format-patch output
                patches_dir = cmd[cmd.index("-o") + 1]
                patch1 = Path(patches_dir) / "0001-first-commit.patch"
                patch2 = Path(patches_dir) / "0002-second-commit.patch"
                patch1.write_text("From abc123\nSubject: first commit\n")
                patch2.write_text("From def456\nSubject: second commit\n")
                return MagicMock(
                    returncode=0,
                    stdout=f"{patch1}\n{patch2}\n",
                )
            elif "diff" in cmd:
                # Simulate git diff output
                return MagicMock(
                    returncode=0,
                    stdout="diff --git a/file.py b/file.py\n+uncommitted change\n",
                )
            return MagicMock(returncode=1, stdout="")

        mock_run.side_effect = mock_subprocess

        with tempfile.TemporaryDirectory() as tmpdir:
            patches_dir = Path(tmpdir) / "patches"
            commit_patches, dirty_patches = create_patch_series(patches_dir)

            assert len(commit_patches) == 2
            assert "0001-first-commit.patch" in commit_patches
            assert "0002-second-commit.patch" in commit_patches
            assert dirty_patches == ["dirty.patch"]
            assert (patches_dir / "dirty.patch").exists()

    @patch("subprocess.run")
    def test_create_patch_series_commits_only(self, mock_run):
        """Test creating patch series with only commit patches (clean working tree)."""

        def mock_subprocess(cmd, **kwargs):
            if "format-patch" in cmd:
                patches_dir = cmd[cmd.index("-o") + 1]
                patch1 = Path(patches_dir) / "0001-add-feature.patch"
                patch1.write_text("From abc123\nSubject: add feature\n")
                return MagicMock(returncode=0, stdout=f"{patch1}\n")
            elif "diff" in cmd:
                # No uncommitted changes
                return MagicMock(returncode=0, stdout="")
            return MagicMock(returncode=1, stdout="")

        mock_run.side_effect = mock_subprocess

        with tempfile.TemporaryDirectory() as tmpdir:
            patches_dir = Path(tmpdir) / "patches"
            commit_patches, dirty_patches = create_patch_series(patches_dir)

            assert len(commit_patches) == 1
            assert commit_patches[0] == "0001-add-feature.patch"
            assert dirty_patches == []

    @patch("subprocess.run")
    def test_create_patch_series_dirty_only(self, mock_run):
        """Test creating patch series with only dirty patch (synced with upstream)."""

        def mock_subprocess(cmd, **kwargs):
            if "format-patch" in cmd:
                # No commits ahead of upstream
                return MagicMock(returncode=0, stdout="")
            elif "diff" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout="diff --git a/file.py b/file.py\n+uncommitted\n",
                )
            return MagicMock(returncode=1, stdout="")

        mock_run.side_effect = mock_subprocess

        with tempfile.TemporaryDirectory() as tmpdir:
            patches_dir = Path(tmpdir) / "patches"
            commit_patches, dirty_patches = create_patch_series(patches_dir)

            assert len(commit_patches) == 0
            assert dirty_patches == ["dirty.patch"]
            assert (patches_dir / "dirty.patch").exists()

    @patch("subprocess.run")
    def test_create_patch_series_no_patches_needed(self, mock_run):
        """Test create_patch_series when repo is clean and synced."""

        def mock_subprocess(cmd, **kwargs):
            # No commits ahead, no uncommitted changes
            return MagicMock(returncode=0, stdout="")

        mock_run.side_effect = mock_subprocess

        with tempfile.TemporaryDirectory() as tmpdir:
            patches_dir = Path(tmpdir) / "patches"
            commit_patches, dirty_patches = create_patch_series(patches_dir)

            assert len(commit_patches) == 0
            assert dirty_patches == []

    @patch("subprocess.run")
    def test_create_patch_series_with_base_commit(self, mock_run):
        """Test create_patch_series uses base_commit when provided."""

        def mock_subprocess(cmd, **kwargs):
            if "format-patch" in cmd:
                # Verify format-patch uses base_commit..HEAD
                assert "baseabc..HEAD" in cmd
                patches_dir = cmd[cmd.index("-o") + 1]
                patch1 = Path(patches_dir) / "0001-feature.patch"
                patch1.write_text("From abc123\nSubject: feature\n")
                return MagicMock(returncode=0, stdout=f"{patch1}\n")
            elif "diff" in cmd:
                return MagicMock(returncode=0, stdout="")
            return MagicMock(returncode=1, stdout="")

        mock_run.side_effect = mock_subprocess

        with tempfile.TemporaryDirectory() as tmpdir:
            patches_dir = Path(tmpdir) / "patches"
            commit_patches, dirty_patches = create_patch_series(patches_dir, base_commit="baseabc")

            assert len(commit_patches) == 1
            assert commit_patches[0] == "0001-feature.patch"
            assert dirty_patches == []


class TestGitInfoPatches:
    """Test GitInfo patches field serialization."""

    def test_git_info_with_commit_patches_and_dirty(self):
        """Test GitInfo serialization with commit patches and dirty patch."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=True,
            commits_ahead=2,
            upstream_commit="upstream456",
            commit_patches=["0001-first-commit.patch", "0002-second-commit.patch"],
            dirty_patches=["dirty.patch"],
        )
        result = git_info.to_dict()

        assert result["patches"] == {
            "commits": ["0001-first-commit.patch", "0002-second-commit.patch"],
            "dirty": ["dirty.patch"],
        }
        assert result["upstream_commit"] == "upstream456"

    def test_git_info_with_only_commit_patches(self):
        """Test GitInfo serialization with only commit patches (no dirty)."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=False,
            commits_ahead=2,
            upstream_commit="upstream456",
            commit_patches=["0001-add-feature.patch"],
        )
        result = git_info.to_dict()

        assert result["patches"] == {"commits": ["0001-add-feature.patch"]}
        assert "dirty" not in result["patches"]

    def test_git_info_with_only_dirty_patches(self):
        """Test GitInfo serialization with only dirty patches (no commits ahead)."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=True,
            commits_ahead=0,
            upstream_commit="abc123",
            dirty_patches=["dirty.patch"],
        )
        result = git_info.to_dict()

        assert result["patches"] == {"dirty": ["dirty.patch"]}
        assert "commits" not in result["patches"]

    def test_git_info_no_patches(self):
        """Test GitInfo serialization with no patches."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=False,
            commits_ahead=0,
            upstream_commit="abc123",
        )
        result = git_info.to_dict()

        assert "patches" not in result  # Omitted when empty

    def test_git_info_with_lfs(self):
        """Test GitInfo serialization with LFS enabled."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=False,
            commits_ahead=0,
            upstream_commit="abc123",
            uses_lfs=True,
        )
        result = git_info.to_dict()

        assert result["uses_lfs"] is True

    def test_git_info_without_lfs(self):
        """Test GitInfo serialization omits uses_lfs when False."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=False,
            commits_ahead=0,
            upstream_commit="abc123",
            uses_lfs=False,
        )
        result = git_info.to_dict()

        assert "uses_lfs" not in result  # Omitted when False

    def test_git_info_with_submodules(self):
        """Test GitInfo serialization with submodules."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=False,
            commits_ahead=0,
            upstream_commit="abc123",
            has_submodules=True,
        )
        result = git_info.to_dict()

        assert result["has_submodules"] is True

    def test_git_info_without_submodules(self):
        """Test GitInfo serialization omits has_submodules when False."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=False,
            commits_ahead=0,
            upstream_commit="abc123",
            has_submodules=False,
        )
        result = git_info.to_dict()

        assert "has_submodules" not in result  # Omitted when False


class TestReproducibilityWarnings:
    """Test reproducibility warning generation."""

    def test_no_git_warning(self):
        """Test warning when not in a git repository."""
        warnings = check_reproducibility_warnings(None)

        assert len(warnings) == 1
        assert warnings[0]["code"] == "no_git"
        assert "Not a git repository" in warnings[0]["message"]

    def test_no_upstream_warning(self):
        """Test warning when no upstream branch is configured."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=False,
            commits_ahead=None,  # No upstream
        )
        warnings = check_reproducibility_warnings(git_info)

        assert len(warnings) == 1
        assert warnings[0]["code"] == "no_upstream"
        assert "git push -u origin" in warnings[0]["message"]

    def test_unpushed_commits_warning_without_patch_series(self):
        """Test warning when there are unpushed commits and no patch series."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=False,
            commits_ahead=3,
            upstream_commit="upstream456",
            commit_patches=[],  # No patches created
        )
        warnings = check_reproducibility_warnings(git_info)

        assert len(warnings) == 1
        assert warnings[0]["code"] == "unpushed_commits"
        assert "3 commit(s) not pushed" in warnings[0]["message"]

    def test_unpushed_commits_no_warning_with_patch_series(self):
        """Test NO warning when unpushed commits have patch series."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=False,
            commits_ahead=3,
            upstream_commit="upstream456",
            commit_patches=[
                "0001-first.patch",
                "0002-second.patch",
                "0003-third.patch",
            ],
        )
        warnings = check_reproducibility_warnings(git_info)

        assert len(warnings) == 0  # No warning - we can reproduce!

    def test_dirty_working_tree_warning_without_patch(self):
        """Test warning when working tree is dirty and no patch."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=True,
            commits_ahead=0,
            upstream_commit="abc123",
            dirty_patches=[],  # No patch created
        )
        warnings = check_reproducibility_warnings(git_info)

        assert len(warnings) == 1
        assert warnings[0]["code"] == "dirty_working_tree"
        assert "uncommitted changes" in warnings[0]["message"]

    def test_dirty_working_tree_no_warning_with_patch(self):
        """Test NO warning when dirty tree has a patch."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=True,
            commits_ahead=0,
            upstream_commit="abc123",
            dirty_patches=["dirty.patch"],  # Patch exists!
        )
        warnings = check_reproducibility_warnings(git_info)

        assert len(warnings) == 0  # No warning - we can reproduce!

    def test_multiple_issues_without_patches(self):
        """Test multiple warnings when both dirty and unpushed without patches."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=True,
            commits_ahead=2,
            upstream_commit="upstream456",
            commit_patches=[],  # No commit patches
            dirty_patches=[],  # No dirty patches
        )
        warnings = check_reproducibility_warnings(git_info)

        assert len(warnings) == 2
        codes = [w["code"] for w in warnings]
        assert "unpushed_commits" in codes
        assert "dirty_working_tree" in codes

    def test_multiple_issues_with_patches(self):
        """Test NO warnings when both dirty and unpushed WITH patches."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=True,
            commits_ahead=2,
            upstream_commit="upstream456",
            commit_patches=["0001-first.patch", "0002-second.patch"],
            dirty_patches=["dirty.patch"],
        )
        warnings = check_reproducibility_warnings(git_info)

        assert len(warnings) == 0  # No warnings - fully reproducible!

    def test_clean_repo_no_warnings(self):
        """Test no warnings when repo is clean and synced."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=False,
            commits_ahead=0,
            upstream_commit="abc123",
        )
        warnings = check_reproducibility_warnings(git_info)

        assert len(warnings) == 0

    def test_lfs_warning(self):
        """Test warning when repository uses Git LFS."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=False,
            commits_ahead=0,
            upstream_commit="abc123",
            uses_lfs=True,
        )
        warnings = check_reproducibility_warnings(git_info)

        assert len(warnings) == 1
        assert warnings[0]["code"] == "uses_lfs"
        assert "Git LFS" in warnings[0]["message"]
        assert "git lfs pull" in warnings[0]["message"]

    def test_lfs_with_other_issues(self):
        """Test LFS warning appears alongside other warnings."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=True,
            commits_ahead=2,
            upstream_commit="upstream456",
            commit_patches=[],
            dirty_patches=[],
            uses_lfs=True,
        )
        warnings = check_reproducibility_warnings(git_info)

        assert len(warnings) == 3
        codes = [w["code"] for w in warnings]
        assert "unpushed_commits" in codes
        assert "dirty_working_tree" in codes
        assert "uses_lfs" in codes

    def test_submodules_warning(self):
        """Test warning when repository has submodules."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=False,
            commits_ahead=0,
            upstream_commit="abc123",
            has_submodules=True,
        )
        warnings = check_reproducibility_warnings(git_info)

        assert len(warnings) == 1
        assert warnings[0]["code"] == "has_submodules"
        assert "submodules" in warnings[0]["message"]
        assert "not supported" in warnings[0]["message"]

    def test_submodules_with_lfs(self):
        """Test both submodules and LFS warnings appear together."""
        git_info = GitInfo(
            commit="abc123",
            is_dirty=False,
            commits_ahead=0,
            upstream_commit="abc123",
            uses_lfs=True,
            has_submodules=True,
        )
        warnings = check_reproducibility_warnings(git_info)

        assert len(warnings) == 2
        codes = [w["code"] for w in warnings]
        assert "uses_lfs" in codes
        assert "has_submodules" in codes


class TestSnapshotCreation:
    """Test full snapshot creation."""

    def test_create_snapshot_basic(self):
        """Test creating a basic snapshot."""
        source = """
batch_size = 64
learning_rate = 0.001
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        compiler = ASTCompiler()
        compiled = compiler.compile_file(ast)

        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_path = create_snapshot(
                compiled=compiled,
                entrypoint="batch_size",
                output_dir=Path(tmpdir),
                reachable_names={"batch_size", "learning_rate"},
                include_payload=False,  # Skip payload for faster test
            )

            # Check snapshot directory structure
            assert snapshot_path.exists()
            assert (snapshot_path / "log.json").exists()

            # Load and verify log.json
            log = load_snapshot(snapshot_path)

            assert log["version"] == SNAPSHOT_VERSION
            assert "meta" in log
            assert "environment" in log
            assert "graph" in log

            assert log["meta"]["entrypoint"] == "batch_size"
            assert "batch_size" in log["graph"]
            assert "learning_rate" in log["graph"]

    def test_create_snapshot_with_payload(self):
        """Test creating snapshot with full payload."""
        source = """
batch_size = 64
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        compiler = ASTCompiler()
        compiled = compiler.compile_file(ast)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create a pfig file to include
            config_file = tmpdir / "config.pfig"
            config_file.write_text(source)

            snapshot_path = create_snapshot(
                compiled=compiled,
                entrypoint="batch_size",
                output_dir=tmpdir,
                pfig_files=[config_file],
                reachable_names={"batch_size"},
                include_payload=True,
                file_origins={},  # Empty - entry file is the only file
                entry_path=config_file,
            )

            # Check payload
            payload_dir = snapshot_path / "payload"
            assert payload_dir.exists()
            assert (payload_dir / "config_bundle.tar.gz").exists()
            assert (payload_dir / "requirements.lock").exists()

    def test_create_snapshot_with_externals(self):
        """Test snapshot captures externals correctly."""
        source = """
external output_dir
batch_size = 64
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        compiler = ASTCompiler(externals={"output_dir": "./runs"})
        compiled = compiler.compile_file(ast)

        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_path = create_snapshot(
                compiled=compiled,
                entrypoint="batch_size",
                output_dir=Path(tmpdir),
                externals={"output_dir": "./runs"},
                external_sources={"output_dir": "cli"},
                reachable_names={"batch_size", "output_dir"},
                include_payload=False,
            )

            log = load_snapshot(snapshot_path)

            assert "externals" in log["environment"]
            assert "output_dir" in log["environment"]["externals"]
            assert log["environment"]["externals"]["output_dir"]["value"] == "./runs"
            assert log["environment"]["externals"]["output_dir"]["source"] == "cli"

    def test_create_snapshot_deterministic_digest(self):
        """Test that same config produces same digest."""
        source = """
batch_size = 64
learning_rate = 0.001
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        compiler = ASTCompiler()
        compiled = compiler.compile_file(ast)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create two snapshots
            snapshot1 = create_snapshot(
                compiled=compiled,
                entrypoint="batch_size",
                output_dir=Path(tmpdir),
                reachable_names={"batch_size", "learning_rate"},
                run_id="test1",
                include_payload=False,
            )
            snapshot2 = create_snapshot(
                compiled=compiled,
                entrypoint="batch_size",
                output_dir=Path(tmpdir),
                reachable_names={"batch_size", "learning_rate"},
                run_id="test2",
                include_payload=False,
            )

            log1 = load_snapshot(snapshot1)
            log2 = load_snapshot(snapshot2)

            # Digests should be the same (same graph)
            assert log1["meta"]["digest"] == log2["meta"]["digest"]


class TestSnapshotLoading:
    """Test snapshot loading functionality."""

    def test_load_snapshot_from_directory(self):
        """Test loading snapshot from directory path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_dir = Path(tmpdir) / "test_snapshot"
            snapshot_dir.mkdir()

            log_data = {
                "version": "1.0",
                "meta": {"entrypoint": "main"},
                "environment": {},
                "graph": {},
            }
            (snapshot_dir / "log.json").write_text(json.dumps(log_data))

            loaded = load_snapshot(snapshot_dir)

            assert loaded["version"] == "1.0"
            assert loaded["meta"]["entrypoint"] == "main"

    def test_load_snapshot_from_file(self):
        """Test loading snapshot from log.json path directly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "log.json"
            log_data = {"version": "1.0", "meta": {}, "environment": {}, "graph": {}}
            log_path.write_text(json.dumps(log_data))

            loaded = load_snapshot(log_path)

            assert loaded["version"] == "1.0"

    def test_load_snapshot_not_found(self):
        """Test error when snapshot not found."""
        with pytest.raises(FileNotFoundError):
            load_snapshot(Path("/nonexistent/path"))


class TestUtilityFunctions:
    """Test utility functions."""

    def test_generate_run_id(self):
        """Test run ID generation."""
        id1 = generate_run_id()
        id2 = generate_run_id()

        assert len(id1) == 6  # Short ID for cleaner directory names
        assert id1 != id2  # Should be unique

    def test_generate_snapshot_dirname(self):
        """Test snapshot directory name generation."""
        dirname = generate_snapshot_dirname("abc123def456")

        # Should be format: YYYYMMDD_HHMMSS_runid
        parts = dirname.split("_")
        assert len(parts) == 3
        assert parts[2] == "abc123def456"

    def test_get_pfig_version(self):
        """Test getting pfig version."""
        version = get_pfig_version()

        # Should be a version string
        assert isinstance(version, str)
        # Should have at least one dot (x.y.z format)
        assert "." in version or version == "0.0.0"


class TestSnapshotMeta:
    """Test SnapshotMeta dataclass."""

    def test_snapshot_meta_to_dict(self):
        """Test SnapshotMeta serialization."""
        meta = SnapshotMeta(
            pfig_version="1.0.0",
            generated_at="2024-01-01T00:00:00Z",
            entrypoint="trainer",
            digest="sha256:abc123",
            run_id="test123",
            pfig_files=["train.pfig", "consts.pfig"],
        )

        result = meta.to_dict()

        assert result["pfig_version"] == "1.0.0"
        assert result["entrypoint"] == "trainer"
        assert result["digest"] == "sha256:abc123"
        assert result["run_id"] == "test123"
        assert result["pfig_files"] == ["train.pfig", "consts.pfig"]


class TestCLISnapshotIntegration:
    """Test CLI integration with snapshots."""

    def test_execute_with_metadata(self):
        """Test execute_pfig_with_metadata returns proper metadata."""
        # Use a simple callable entrypoint (not a provider)
        source = """
batch = 64
config = {"batch": @batch}

# print is a callable that returns None
__entrypoint__ = print
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        metadata = execute_pfig_with_metadata(ast, filename="test.pfig")

        assert metadata.compiled is not None
        assert metadata.entrypoint_name == "__entrypoint__"
        assert metadata.reachable_names is not None
        assert metadata.materialized is not None
        # print() returns None
        assert metadata.result is None

    def test_validate_pfig_does_not_materialize(self):
        """Test validate_pfig validates without materializing objects."""
        from pfig.execution import validate_pfig

        source = """
batch = 64
config = {"batch": @batch}
__entrypoint__ = print
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        metadata = validate_pfig(ast, filename="test.pfig")

        # Should not have executed or materialized
        assert metadata.result is None
        assert metadata.materialized == {}
        # But should have compiled and planned
        assert metadata.compiled is not None
        assert metadata.entrypoint_name == "__entrypoint__"

    def test_execute_with_metadata_includes_files(self):
        """Test that execute_pfig_with_metadata returns included_files."""
        source = """
batch = 64
__entrypoint__ = print
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        metadata = execute_pfig_with_metadata(ast, filename="test.pfig")

        # included_files should be an empty list for config without includes
        assert metadata.included_files == []


class TestSnapshotWithIncludes:
    """Test snapshot creation with included files."""

    def test_snapshot_captures_included_files(self):
        """Test that snapshot payload includes all included pfig files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create base config
            base_config = tmpdir / "base.pfig"
            base_config.write_text(
                """
batch_size = 64
"""
            )

            # Create main config that includes base
            main_config = tmpdir / "main.pfig"
            main_config.write_text(
                """
include "./base.pfig" as base
config = @base/batch_size
__entrypoint__ = print
"""
            )

            ast = parse_pfig_source(main_config.read_text(), filename=str(main_config))
            metadata = execute_pfig_with_metadata(ast, filename=str(main_config))

            # Collect all pfig files (main + includes)
            pfig_files = [main_config]
            pfig_files.extend(Path(p) for p in metadata.included_files)

            # Create snapshot with bundle
            snapshot_path = create_snapshot(
                compiled=metadata.compiled,
                entrypoint=metadata.entrypoint_name,
                output_dir=tmpdir / "snapshots",
                pfig_files=pfig_files,
                reachable_names=metadata.reachable_names,
                include_payload=True,
                file_origins=metadata.file_origins,
                entry_path=main_config,
            )

            # Verify bundle exists in snapshot
            bundle_path = snapshot_path / "payload" / "config_bundle.tar.gz"
            assert bundle_path.exists()

            # Extract and verify bundle contains both files
            import tarfile

            with tarfile.open(bundle_path, "r:gz") as tar:
                names = tar.getnames()
                # Check for main.pfig and base.pfig in the bundle
                main_in_bundle = any("main.pfig" in n for n in names)
                base_in_bundle = any("base.pfig" in n for n in names)
                assert main_in_bundle, "main.pfig should be in bundle"
                assert base_in_bundle, "base.pfig should be in bundle"

            # Load and verify log.json has the pfig_files list
            log = load_snapshot(snapshot_path)
            assert len(log["meta"]["pfig_files"]) == 2


class TestIntrospectionInfo:
    """Test introspection info serialization."""

    def test_introspection_info_ok_status(self):
        """Test IntrospectionInfo with OK status."""
        info = IntrospectionInfo(
            status=IntrospectionStatus.OK,
            defaults_source="signature",
        )
        result = info.to_dict()

        assert result["status"] == "ok"
        assert result["defaults_source"] == "signature"
        assert "opaque_reason" not in result

    def test_introspection_info_partial_status(self):
        """Test IntrospectionInfo with partial status and opaque reason."""
        info = IntrospectionInfo(
            status=IntrospectionStatus.PARTIAL,
            defaults_source="none",
            opaque_reason={
                "code": "varargs_only",
                "detail": "Signature is *args, **kwargs only",
            },
        )
        result = info.to_dict()

        assert result["status"] == "partial"
        assert result["defaults_source"] == "none"
        assert result["opaque_reason"]["code"] == "varargs_only"

    def test_introspection_info_failed_status(self):
        """Test IntrospectionInfo with failed status."""
        info = IntrospectionInfo(
            status=IntrospectionStatus.FAILED,
            defaults_source="explicit",
            opaque_reason={
                "code": "c_extension",
                "detail": "torch._C._nn.Conv2d: ValueError from inspect.signature",
            },
        )
        result = info.to_dict()

        assert result["status"] == "failed"
        assert result["defaults_source"] == "explicit"
        assert result["opaque_reason"]["code"] == "c_extension"


class TestIntrospectionStatusEnum:
    """Test IntrospectionStatus enum values."""

    def test_status_values(self):
        """Test that status enum has expected values."""
        assert IntrospectionStatus.OK.value == "ok"
        assert IntrospectionStatus.PARTIAL.value == "partial"
        assert IntrospectionStatus.FAILED.value == "failed"

    def test_status_is_string_enum(self):
        """Test that status enum values are strings."""
        assert isinstance(IntrospectionStatus.OK.value, str)
        assert str(IntrospectionStatus.OK) == "IntrospectionStatus.OK"


class TestOpaqueReasonCodeEnum:
    """Test OpaqueReasonCode enum values."""

    def test_reason_code_values(self):
        """Test that reason code enum has expected values."""
        assert OpaqueReasonCode.C_EXTENSION.value == "c_extension"
        assert OpaqueReasonCode.BUILTIN.value == "builtin"
        assert OpaqueReasonCode.NO_SIGNATURE.value == "no_signature"
        assert OpaqueReasonCode.VARARGS_ONLY.value == "varargs_only"


class TestProviderIntrospection:
    """Test provider introspection in IR graph."""

    def test_build_ir_graph_includes_introspection(self):
        """Test that IR graph includes introspection field for providers."""
        source = """
import json

config = json.dumps(obj={"test": True})
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        compiler = ASTCompiler()
        compiled = compiler.compile_file(ast)

        graph = build_ir_graph(compiled)

        assert "config" in graph
        assert "introspection" in graph["config"]
        assert graph["config"]["introspection"]["status"] == "ok"
        assert graph["config"]["introspection"]["defaults_source"] in [
            "signature",
            "explicit",
        ]

    def test_introspection_ok_for_inspectable_callable(self):
        """Test that introspection status is OK for normal Python callables."""
        source = """
import json

encoder = json.JSONEncoder(indent=2)
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        compiler = ASTCompiler()
        compiled = compiler.compile_file(ast)

        graph = build_ir_graph(compiled)

        assert "encoder" in graph
        intro = graph["encoder"]["introspection"]
        assert intro["status"] == "ok"
        # JSONEncoder has defaults that can be introspected
        assert intro["defaults_source"] == "signature"


class TestCheckIntrospectionWarnings:
    """Test introspection warning generation."""

    def test_no_warnings_for_ok_introspection(self):
        """Test no warnings when all providers have OK introspection."""
        graph = {
            "model": {
                "type": "provider",
                "target": "pkg.Model",
                "introspection": {"status": "ok", "defaults_source": "signature"},
                "kwargs": {},
            }
        }

        warnings = check_introspection_warnings(graph)
        assert len(warnings) == 0

    def test_warning_for_failed_introspection(self):
        """Test warning generated for failed introspection."""
        graph = {
            "model": {
                "type": "provider",
                "target": "pkg.Model",
                "introspection": {
                    "status": "failed",
                    "defaults_source": "none",
                    "opaque_reason": {"code": "c_extension"},
                },
                "kwargs": {},
            }
        }

        warnings = check_introspection_warnings(graph)
        assert len(warnings) == 1
        assert warnings[0]["code"] == "incomplete_introspection"
        assert "model" in warnings[0]["message"]
        assert warnings[0]["providers"] == ["model"]

    def test_warning_for_partial_introspection(self):
        """Test warning generated for partial introspection."""
        graph = {
            "wrapper": {
                "type": "provider",
                "target": "pkg.Wrapper",
                "introspection": {
                    "status": "partial",
                    "defaults_source": "none",
                    "opaque_reason": {"code": "varargs_only"},
                },
                "kwargs": {},
            }
        }

        warnings = check_introspection_warnings(graph)
        assert len(warnings) == 1
        assert warnings[0]["code"] == "incomplete_introspection"

    def test_warning_counts_multiple_providers(self):
        """Test warning message includes count of affected providers."""
        graph = {
            "model1": {
                "type": "provider",
                "introspection": {"status": "failed", "defaults_source": "none"},
                "kwargs": {},
            },
            "model2": {
                "type": "provider",
                "introspection": {"status": "failed", "defaults_source": "none"},
                "kwargs": {},
            },
            "model3": {
                "type": "provider",
                "introspection": {"status": "ok", "defaults_source": "signature"},
                "kwargs": {},
            },
        }

        warnings = check_introspection_warnings(graph)
        assert len(warnings) == 1
        assert "2 provider(s)" in warnings[0]["message"]

    def test_warning_truncates_long_provider_list(self):
        """Test that warning truncates when many providers affected."""
        graph = {
            f"model{i}": {
                "type": "provider",
                "introspection": {"status": "failed", "defaults_source": "none"},
                "kwargs": {},
            }
            for i in range(5)
        }

        warnings = check_introspection_warnings(graph)
        assert len(warnings) == 1
        assert "5 provider(s)" in warnings[0]["message"]
        assert "..." in warnings[0]["message"]  # Truncated
        assert len(warnings[0]["providers"]) == 5  # Full list still available

    def test_ignores_non_provider_nodes(self):
        """Test that value nodes are ignored."""
        graph = {
            "batch_size": {"type": "value", "value": 64},
            "model": {
                "type": "provider",
                "introspection": {"status": "ok", "defaults_source": "signature"},
                "kwargs": {},
            },
        }

        warnings = check_introspection_warnings(graph)
        assert len(warnings) == 0
