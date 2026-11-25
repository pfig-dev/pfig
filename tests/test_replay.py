"""Tests for the pfig replay subsystem."""

import contextlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, call, patch

import pytest

from pfig.cli import parse_set_args
from pfig.diff import DiffChangeType, DiffDetail, DiffReport
from pfig.replay.git_restore import (
    _pick_base,
    _safe_child,
    normalize_git_url,
    restore_code,
    urls_match,
)
from pfig.replay.report import _build_report_data, _format_text, write_report
from pfig.replay.runner import _format_value, replay_run
from pfig.replay.snapshot_loader import SnapshotData, _join, load_and_validate_snapshot
from pfig.replay.validate import _format_diff_text, replay_validate
from pfig.replay.venv import (
    _filter_editables,
    _is_editable_line,
    _pkg_count,
    _split_lock_file,
)
from pfig.replay.workspace import (
    ReplayWorkspace,
    _build_plan,
    _extract_bundle,
    _resolve_workspace_root,
    _select_repo,
    replay_prepare,
)
from pfig.snapshot import GitInfo

# ======================================================================
# GitInfo.from_dict round-trip
# ======================================================================


class TestGitInfoFromDict:
    def test_full_round_trip(self):
        gi = GitInfo(
            commit="abc123",
            is_dirty=True,
            remote_origin_url="git@github.com:org/repo.git",
            commits_ahead=2,
            upstream_commit="def456",
            commit_patches=["0001-a.patch", "0002-b.patch"],
            dirty_patches=["dirty.patch"],
            uses_lfs=True,
            has_submodules=True,
        )
        d = gi.to_dict()
        gi2 = GitInfo.from_dict(d)

        assert gi2.commit == gi.commit
        assert gi2.is_dirty == gi.is_dirty
        assert gi2.remote_origin_url == gi.remote_origin_url
        assert gi2.commits_ahead == gi.commits_ahead
        assert gi2.upstream_commit == gi.upstream_commit
        assert gi2.commit_patches == gi.commit_patches
        assert gi2.dirty_patches == gi.dirty_patches
        assert gi2.uses_lfs == gi.uses_lfs
        assert gi2.has_submodules == gi.has_submodules

    def test_minimal_round_trip(self):
        gi = GitInfo(commit="abc123")
        d = gi.to_dict()
        gi2 = GitInfo.from_dict(d)

        assert gi2.commit == "abc123"
        assert gi2.is_dirty is False
        assert gi2.commits_ahead is None
        assert gi2.commit_patches == []
        assert gi2.dirty_patches == []

    def test_no_upstream(self):
        d = {
            "commit": "abc",
            "is_dirty": False,
            "remote_origin_url": None,
            "has_upstream": False,
        }
        gi = GitInfo.from_dict(d)
        assert gi.commits_ahead is None
        assert gi.upstream_commit is None

    def test_synced_upstream(self):
        d = {
            "commit": "abc",
            "is_dirty": False,
            "remote_origin_url": "https://github.com/org/repo",
            "has_upstream": True,
        }
        gi = GitInfo.from_dict(d)
        assert gi.commits_ahead == 0


# ======================================================================
# URL normalisation
# ======================================================================


class TestUrlNormalize:
    def test_ssh_url(self):
        assert normalize_git_url("git@github.com:org/repo.git") == "github.com/org/repo"

    def test_https_url(self):
        assert normalize_git_url("https://github.com/org/repo.git") == "github.com/org/repo"

    def test_https_no_suffix(self):
        assert normalize_git_url("https://github.com/org/repo") == "github.com/org/repo"

    def test_https_trailing_slash(self):
        assert normalize_git_url("https://github.com/org/repo/") == "github.com/org/repo"

    def test_ssh_no_suffix(self):
        assert normalize_git_url("git@gitlab.com:group/project") == "gitlab.com/group/project"

    def test_case_insensitive(self):
        assert normalize_git_url("git@GitHub.COM:Org/Repo.git") == "github.com/org/repo"

    def test_ssh_url_form(self):
        assert normalize_git_url("ssh://git@github.com/org/repo.git") == "github.com/org/repo"

    def test_ssh_url_no_suffix(self):
        assert normalize_git_url("ssh://git@github.com/org/repo") == "github.com/org/repo"

    def test_urls_match_ssh_vs_https(self):
        assert urls_match(
            "git@github.com:org/repo.git",
            "https://github.com/org/repo",
        )

    def test_urls_match_both_none(self):
        assert not urls_match(None, None)

    def test_urls_match_one_none(self):
        assert not urls_match("git@github.com:org/repo.git", None)

    def test_urls_mismatch(self):
        assert not urls_match(
            "git@github.com:org/repo.git",
            "git@gitlab.com:org/repo.git",
        )


# ======================================================================
# Repo selection
# ======================================================================


class TestRepoSelection:
    def test_no_repo_no_url_fails(self, tmp_path, monkeypatch):
        """If not in a repo and snapshot has no URL, selection fails."""
        monkeypatch.chdir(tmp_path)
        snap = _make_snap_data(tmp_path, remote_url=None)
        with pytest.raises(RuntimeError, match="Cannot determine repo source"):
            _select_repo(snap)

    def test_clone_url_from_snapshot(self, tmp_path, monkeypatch):
        """If not in a matching repo, clone_url is taken from snapshot."""
        monkeypatch.chdir(tmp_path)
        snap = _make_snap_data(tmp_path, remote_url="https://github.com/org/repo")
        choice = _select_repo(snap)
        assert choice.clone_url == "https://github.com/org/repo"
        assert choice.repo_root is None

    def test_local_repo_match(self, tmp_path, monkeypatch):
        """If CWD is in a matching repo, use local repo."""
        monkeypatch.chdir(tmp_path)
        snap = _make_snap_data(tmp_path, remote_url="https://github.com/org/repo")
        with (
            patch("pfig.replay.workspace.git_toplevel", return_value=tmp_path),
            patch(
                "pfig.replay.workspace.current_origin_url",
                return_value="git@github.com:org/repo.git",
            ),
        ):
            choice = _select_repo(snap)
        assert choice.repo_root == tmp_path
        assert choice.clone_url is None
        assert "local repo" in choice.label

    def test_local_repo_mismatch_falls_through_to_clone(self, tmp_path, monkeypatch):
        """If CWD repo doesn't match, fall through to clone."""
        monkeypatch.chdir(tmp_path)
        snap = _make_snap_data(tmp_path, remote_url="https://github.com/org/repo")
        with (
            patch("pfig.replay.workspace.git_toplevel", return_value=tmp_path),
            patch(
                "pfig.replay.workspace.current_origin_url",
                return_value="https://github.com/other/thing",
            ),
        ):
            choice = _select_repo(snap)
        assert choice.clone_url == "https://github.com/org/repo"
        assert choice.repo_root is None


# ======================================================================
# Snapshot loader
# ======================================================================


class TestSnapshotLoader:
    def test_legacy_resolution_version_rejected(self, tmp_path):
        log = _minimal_log()
        log["version"] = "1.0"
        _write_snapshot(tmp_path, log, with_bundle=True)
        with pytest.raises(ValueError, match="recorded pfig version"):
            load_and_validate_snapshot(tmp_path)

    def test_missing_meta_raises(self, tmp_path):
        log = {"environment": {"python_version": "3.10.0"}}
        _write_snapshot(tmp_path, log)
        with pytest.raises(ValueError, match="meta"):
            load_and_validate_snapshot(tmp_path)

    def test_missing_bundle_raises(self, tmp_path):
        log = _minimal_log()
        (tmp_path / "log.json").write_text(json.dumps(log))
        with pytest.raises(ValueError, match="config_bundle"):
            load_and_validate_snapshot(tmp_path)

    def test_valid_snapshot_loads(self, tmp_path):
        log = _minimal_log()
        _write_snapshot(tmp_path, log, with_bundle=True)
        snap = load_and_validate_snapshot(tmp_path)
        assert snap.run_id == "abc123"
        assert snap.digest == "sha256:test"
        assert snap.entrypoint == "__entrypoint__"

    def test_missing_run_id_raises(self, tmp_path):
        log = _minimal_log()
        del log["meta"]["run_id"]
        _write_snapshot(tmp_path, log, with_bundle=True)
        with pytest.raises(ValueError, match="run_id"):
            load_and_validate_snapshot(tmp_path)

    def test_missing_digest_raises(self, tmp_path):
        log = _minimal_log()
        del log["meta"]["digest"]
        _write_snapshot(tmp_path, log, with_bundle=True)
        with pytest.raises(ValueError, match="digest"):
            load_and_validate_snapshot(tmp_path)

    def test_missing_entrypoint_raises(self, tmp_path):
        log = _minimal_log()
        del log["meta"]["entrypoint"]
        _write_snapshot(tmp_path, log, with_bundle=True)
        with pytest.raises(ValueError, match="entrypoint"):
            load_and_validate_snapshot(tmp_path)

    def test_missing_environment_raises(self, tmp_path):
        log = _minimal_log()
        del log["environment"]
        _write_snapshot(tmp_path, log, with_bundle=True)
        with pytest.raises(ValueError, match="environment"):
            load_and_validate_snapshot(tmp_path)


class TestSnapshotDataProperties:
    def test_bundle_tarball_local(self, tmp_path):
        snap = _load_snap(tmp_path)
        expected = str(Path(str(tmp_path / "snap")) / "payload" / "config_bundle.tar.gz")
        assert snap.bundle_tarball == expected

    def test_requirements_lock_local(self, tmp_path):
        snap = _load_snap(tmp_path)
        assert snap.requirements_lock.endswith("payload/requirements.lock")

    def test_patches_dir_local(self, tmp_path):
        snap = _load_snap(tmp_path)
        assert snap.patches_dir.endswith("payload/patches")

    def test_has_bundle_true(self, tmp_path):
        snap = _load_snap(tmp_path)
        assert snap.has_bundle()

    def test_has_requirements_false(self, tmp_path):
        snap = _load_snap(tmp_path)
        assert not snap.has_requirements()

    def test_has_patches_false(self, tmp_path):
        snap = _load_snap(tmp_path)
        assert not snap.has_patches()

    def test_externals_parsed(self, tmp_path):
        log = _minimal_log()
        log["environment"]["externals"] = {
            "lr": {"value": 0.01, "type": "float"},
            "name": {"value": "test", "type": "str"},
        }
        _write_snapshot(tmp_path / "snap", log, with_bundle=True)
        snap = load_and_validate_snapshot(tmp_path / "snap")
        assert snap.externals == {"lr": 0.01, "name": "test"}

    def test_graph_loaded(self, tmp_path):
        log = _minimal_log()
        log["graph"] = {"model": {"type": "provider", "callable": "Model"}}
        _write_snapshot(tmp_path / "snap", log, with_bundle=True)
        snap = load_and_validate_snapshot(tmp_path / "snap")
        assert "model" in snap.graph

    def test_git_info_loaded(self, tmp_path):
        log = _minimal_log(with_git=True)
        _write_snapshot(tmp_path / "snap", log, with_bundle=True)
        snap = load_and_validate_snapshot(tmp_path / "snap")
        assert snap.git is not None
        assert snap.git.commit == "aaa111bbb222ccc333ddd444eee555fff666777"

    def test_no_git_info(self, tmp_path):
        snap = _load_snap(tmp_path)
        assert snap.git is None

    def test_warnings_loaded(self, tmp_path):
        log = _minimal_log()
        log["warnings"] = [{"code": "W001", "message": "test warning"}]
        _write_snapshot(tmp_path / "snap", log, with_bundle=True)
        snap = load_and_validate_snapshot(tmp_path / "snap")
        assert len(snap.warnings) == 1


class TestJoinHelper:
    def test_local_path(self):
        result = _join("/tmp/snap", "payload/bundle.tar.gz")
        assert result == str(Path("/tmp/snap") / "payload" / "bundle.tar.gz")

    def test_uri(self):
        result = _join("gs://bucket/snap", "payload/bundle.tar.gz")
        assert result == "gs://bucket/snap/payload/bundle.tar.gz"

    def test_uri_trailing_slash(self):
        result = _join("gs://bucket/snap/", "payload/bundle.tar.gz")
        assert result == "gs://bucket/snap/payload/bundle.tar.gz"


# ======================================================================
# Replay report
# ======================================================================


class TestReplayReport:
    def test_report_files_created(self, tmp_path):
        log = _minimal_log()
        _write_snapshot(tmp_path / "snap", log, with_bundle=True)
        snap = load_and_validate_snapshot(tmp_path / "snap")

        ws_root = tmp_path / "ws"
        ws_root.mkdir()
        (ws_root / "artifacts").mkdir()
        ws = ReplayWorkspace(root=ws_root, snapshot=snap)
        ws.prepare_ok = True
        ws.method = "worktree"
        ws.base_commit = "abc123"

        write_report(ws)

        assert (ws_root / "replay_report.json").exists()
        assert (ws_root / "replay_report.txt").exists()

        data = json.loads((ws_root / "replay_report.json").read_text())
        assert data["snapshot"]["run_id"] == "abc123"
        assert data["phases"]["prepare_ok"] is True

        txt = (ws_root / "replay_report.txt").read_text()
        assert "Prepare: OK" in txt

    def test_report_prepare_failed(self, tmp_path):
        ws = _make_workspace(tmp_path)
        ws.prepare_ok = False
        write_report(ws)

        txt = (ws.root / "replay_report.txt").read_text()
        assert "Prepare: FAILED" in txt

    def test_report_validate_ok(self, tmp_path):
        ws = _make_workspace(tmp_path)
        ws.validate_ok = True
        write_report(ws)

        txt = (ws.root / "replay_report.txt").read_text()
        assert "Validate: OK" in txt

    def test_report_validate_failed(self, tmp_path):
        ws = _make_workspace(tmp_path)
        ws.validate_ok = False
        write_report(ws)

        txt = (ws.root / "replay_report.txt").read_text()
        assert "Validate: FAILED" in txt

    def test_report_validate_not_run(self, tmp_path):
        ws = _make_workspace(tmp_path)
        ws.validate_ok = None
        write_report(ws)

        txt = (ws.root / "replay_report.txt").read_text()
        assert "Validate: not run" in txt

    def test_report_run_ok(self, tmp_path):
        ws = _make_workspace(tmp_path)
        ws.run_exit_code = 0
        write_report(ws)

        txt = (ws.root / "replay_report.txt").read_text()
        assert "exit 0" in txt
        assert "OK" in txt

    def test_report_run_failed(self, tmp_path):
        ws = _make_workspace(tmp_path)
        ws.run_exit_code = 1
        write_report(ws)

        txt = (ws.root / "replay_report.txt").read_text()
        assert "exit 1" in txt
        assert "FAILED" in txt

    def test_report_run_not_run(self, tmp_path):
        ws = _make_workspace(tmp_path)
        ws.run_exit_code = None
        write_report(ws)

        txt = (ws.root / "replay_report.txt").read_text()
        assert "Run:     not run" in txt

    def test_report_with_warnings(self, tmp_path):
        ws = _make_workspace(tmp_path)
        ws.warnings = ["Python mismatch", "LFS not pulled"]
        write_report(ws)

        txt = (ws.root / "replay_report.txt").read_text()
        assert "--- Warnings ---" in txt
        assert "Python mismatch" in txt
        assert "LFS not pulled" in txt

    def test_report_json_structure(self, tmp_path):
        ws = _make_workspace(tmp_path)
        ws.method = "clone"
        ws.base_commit = "abc123"
        ws.patches_applied = 3
        ws.dirty_applied = True
        ws.venv_python = "/path/to/python"

        data = _build_report_data(ws)

        assert data["git_restore"]["method"] == "clone"
        assert data["git_restore"]["base_commit"] == "abc123"
        assert data["git_restore"]["patches_applied"] == 3
        assert data["git_restore"]["dirty_applied"] is True
        assert data["python"]["replay_venv_python"] == "/path/to/python"
        assert "generated_at" in data


# ======================================================================
# Dry-run
# ======================================================================


class TestDryRun:
    def test_dry_run_does_not_create_files(self, tmp_path):
        log = _minimal_log(with_git=True)
        _write_snapshot(tmp_path / "snap", log, with_bundle=True)

        ws_root = tmp_path / "ws"

        with (
            patch("pfig.replay.workspace.git_toplevel", return_value=None),
            patch("pfig.replay.workspace.current_origin_url", return_value=None),
        ):
            ws = replay_prepare(
                str(tmp_path / "snap"),
                workspace_path=str(ws_root),
                dry_run=True,
                no_deps=True,
            )

        assert not ws.prepare_ok
        assert not ws_root.exists()


# ======================================================================
# ReplayWorkspace
# ======================================================================


class TestReplayWorkspace:
    def test_directory_properties(self, tmp_path):
        snap = _load_snap(tmp_path)
        ws = ReplayWorkspace(root=tmp_path / "ws", snapshot=snap)
        assert ws.src_dir == tmp_path / "ws" / "src"
        assert ws.bundle_dir == tmp_path / "ws" / "bundle"
        assert ws.venv_dir == tmp_path / "ws" / "venv"
        assert ws.artifacts_dir == tmp_path / "ws" / "artifacts"

    def test_create_dirs(self, tmp_path):
        snap = _load_snap(tmp_path)
        ws = ReplayWorkspace(root=tmp_path / "ws", snapshot=snap)
        ws.create_dirs()
        assert ws.root.exists()
        assert ws.bundle_dir.exists()
        assert ws.artifacts_dir.exists()

    def test_default_state(self, tmp_path):
        snap = _load_snap(tmp_path)
        ws = ReplayWorkspace(root=tmp_path / "ws", snapshot=snap)
        assert ws.method == "unknown"
        assert ws.base_commit == ""
        assert ws.patches_applied == 0
        assert ws.dirty_applied is False
        assert ws.venv_python == ""
        assert ws.warnings == []
        assert ws.prepare_ok is False
        assert ws.validate_ok is None
        assert ws.run_exit_code is None


# ======================================================================
# Bundle extraction
# ======================================================================


class TestExtractBundle:
    def test_extract_local_tarball(self, tmp_path):
        log = _minimal_log()
        _write_snapshot(tmp_path / "snap", log, with_bundle=True)
        snap = load_and_validate_snapshot(tmp_path / "snap")

        dest = tmp_path / "extracted"
        _extract_bundle(snap, dest)

        assert dest.exists()
        assert (dest / "bundle_manifest.json").exists()
        assert (dest / "configs" / "test.pfig").exists()


# ======================================================================
# Workspace root resolution
# ======================================================================


class TestResolveWorkspaceRoot:
    def test_override_path(self, tmp_path):
        from pfig.replay.workspace import _RepoChoice

        snap = _load_snap(tmp_path)
        choice = _RepoChoice(repo_root=tmp_path)
        result = _resolve_workspace_root(snap, choice, str(tmp_path / "my_ws"))
        assert result == (tmp_path / "my_ws").resolve()

    def test_with_repo_root(self, tmp_path):
        from pfig.replay.workspace import _RepoChoice

        snap = _load_snap(tmp_path)
        choice = _RepoChoice(repo_root=tmp_path / "repo")
        result = _resolve_workspace_root(snap, choice, None)
        assert ".pfig/replays" in str(result)
        assert snap.run_id in str(result)

    def test_without_repo_root(self, tmp_path, monkeypatch):
        from pfig.replay.workspace import _RepoChoice

        monkeypatch.chdir(tmp_path)
        snap = _load_snap(tmp_path)
        choice = _RepoChoice()
        result = _resolve_workspace_root(snap, choice, None)
        assert ".pfig/replays" in str(result)


# ======================================================================
# Build plan
# ======================================================================


class TestBuildPlan:
    def test_plan_with_git_and_worktree(self, tmp_path):
        from pfig.replay.workspace import _RepoChoice

        snap = _load_snap_with_git(tmp_path)
        ws = ReplayWorkspace(root=tmp_path / "ws", snapshot=snap)
        choice = _RepoChoice(repo_root=tmp_path)

        steps = _build_plan(
            ws,
            choice,
            commit="abc123",
            upstream="def456",
            commit_names=["0001.patch"],
            dirty_names=["dirty.patch"],
            patches_dir=tmp_path,
            snapshot=snap,
            python=None,
            no_deps=False,
        )

        assert any("workspace" in s.lower() for s in steps)
        assert any("worktree" in s.lower() for s in steps)
        assert any("patch" in s.lower() for s in steps)
        assert any("dirty" in s.lower() for s in steps)
        assert any("bundle" in s.lower() for s in steps)
        assert any("venv" in s.lower() for s in steps)

    def test_plan_with_clone(self, tmp_path):
        from pfig.replay.workspace import _RepoChoice

        snap = _load_snap_with_git(tmp_path)
        ws = ReplayWorkspace(root=tmp_path / "ws", snapshot=snap)
        choice = _RepoChoice(clone_url="https://github.com/org/repo")

        steps = _build_plan(
            ws,
            choice,
            commit="abc123",
            upstream=None,
            commit_names=[],
            dirty_names=[],
            patches_dir=None,
            snapshot=snap,
            python=None,
            no_deps=False,
        )

        assert any("clone" in s.lower() for s in steps)

    def test_plan_no_git(self, tmp_path):
        from pfig.replay.workspace import _RepoChoice

        snap = _load_snap(tmp_path)
        ws = ReplayWorkspace(root=tmp_path / "ws", snapshot=snap)
        choice = _RepoChoice()

        steps = _build_plan(
            ws,
            choice,
            commit=None,
            upstream=None,
            commit_names=[],
            dirty_names=[],
            patches_dir=None,
            snapshot=snap,
            python=None,
            no_deps=False,
        )

        assert any("no git" in s.lower() for s in steps)

    def test_plan_no_deps(self, tmp_path):
        from pfig.replay.workspace import _RepoChoice

        snap = _load_snap(tmp_path)
        ws = ReplayWorkspace(root=tmp_path / "ws", snapshot=snap)
        choice = _RepoChoice()

        steps = _build_plan(
            ws,
            choice,
            commit=None,
            upstream=None,
            commit_names=[],
            dirty_names=[],
            patches_dir=None,
            snapshot=snap,
            python=None,
            no_deps=True,
        )

        assert any("skip" in s.lower() for s in steps)

    def test_plan_custom_python(self, tmp_path):
        from pfig.replay.workspace import _RepoChoice

        snap = _load_snap(tmp_path)
        ws = ReplayWorkspace(root=tmp_path / "ws", snapshot=snap)
        choice = _RepoChoice()

        steps = _build_plan(
            ws,
            choice,
            commit=None,
            upstream=None,
            commit_names=[],
            dirty_names=[],
            patches_dir=None,
            snapshot=snap,
            python="/usr/bin/python3.11",
            no_deps=False,
        )

        assert any("python3.11" in s.lower() for s in steps)


# ======================================================================
# replay_prepare full paths
# ======================================================================


class TestReplayPrepare:
    def _patch_select_repo(self, **kwargs):
        from pfig.replay.workspace import _RepoChoice

        return patch(
            "pfig.replay.workspace._select_repo",
            return_value=_RepoChoice(**kwargs),
        )

    def test_prepare_bundle_only_no_git(self, tmp_path):
        """prepare without git info should set method to bundle-only."""
        log = _minimal_log(with_git=False)
        _write_snapshot(tmp_path / "snap", log, with_bundle=True)

        ws_root = tmp_path / "ws"

        with self._patch_select_repo(label="test"):
            ws = replay_prepare(
                str(tmp_path / "snap"),
                workspace_path=str(ws_root),
                no_deps=True,
            )

        assert ws.prepare_ok
        assert ws.method == "bundle-only"
        assert any("bundle only" in w.lower() for w in ws.warnings)
        assert ws.bundle_dir.exists()

    def test_prepare_lfs_warning(self, tmp_path):
        log = _minimal_log(with_git=True)
        log["environment"]["git"]["uses_lfs"] = True
        _write_snapshot(tmp_path / "snap", log, with_bundle=True)

        ws_root = tmp_path / "ws"

        with (
            self._patch_select_repo(clone_url="https://github.com/org/repo", label="test"),
            patch("pfig.replay.workspace.restore_code", return_value="clone"),
        ):
            ws = replay_prepare(
                str(tmp_path / "snap"),
                workspace_path=str(ws_root),
                no_deps=True,
            )

        assert ws.prepare_ok
        assert any("LFS" in w for w in ws.warnings)

    def test_prepare_submodule_warning(self, tmp_path):
        log = _minimal_log(with_git=True)
        log["environment"]["git"]["has_submodules"] = True
        _write_snapshot(tmp_path / "snap", log, with_bundle=True)

        ws_root = tmp_path / "ws"

        with (
            self._patch_select_repo(clone_url="https://github.com/org/repo", label="test"),
            patch("pfig.replay.workspace.restore_code", return_value="clone"),
        ):
            ws = replay_prepare(
                str(tmp_path / "snap"),
                workspace_path=str(ws_root),
                no_deps=True,
            )

        assert ws.prepare_ok
        assert any("submodule" in w.lower() for w in ws.warnings)

    def test_prepare_no_deps_warning(self, tmp_path):
        log = _minimal_log()
        _write_snapshot(tmp_path / "snap", log, with_bundle=True)

        ws_root = tmp_path / "ws"

        with self._patch_select_repo(label="test"):
            ws = replay_prepare(
                str(tmp_path / "snap"),
                workspace_path=str(ws_root),
                no_deps=True,
            )

        assert any("--no-deps" in w for w in ws.warnings)


# ======================================================================
# replay_validate
# ======================================================================


def _mock_validation():
    return MagicMock(
        compiled={
            "values": {},
            "binding_origins": {"lr": ("config", "lr")},
            "declared_externals": {"lr"},
            "runtime_inputs": {},
        },
        reachable_names=set(),
    )


class TestReplayValidate:
    def _validate_patches(self, ws):
        """Common patches for replay_validate tests."""
        return [
            patch(
                "pfig.replay.validate.load_bundle",
                return_value=(ws.bundle_dir, _mock_manifest()),
            ),
            patch("pfig.replay.validate._fs.read_text", return_value="x = 1\n"),
            patch("pfig.replay.validate.parse_pfig_source", return_value=MagicMock()),
            patch(
                "pfig.replay.validate.validate_pfig",
                return_value=_mock_validation(),
            ),
            patch("pfig.replay.validate.build_ir_graph", return_value={"k": "v"}),
            patch("pfig.replay.validate._write_json"),
        ]

    def test_validate_digest_match(self, tmp_path):
        """When the recompiled digest matches, validate returns True."""
        ws = _make_validatable_workspace(tmp_path, digest="sha256:MATCH")

        patches = self._validate_patches(ws) + [
            patch("pfig.replay.validate.compute_graph_digest", return_value="sha256:MATCH"),
        ]
        with _apply_patches(patches):
            result = replay_validate(ws)

        assert result is True
        assert ws.validate_ok is True

    def test_validate_digest_mismatch_rejects(self, tmp_path):
        """When digest doesn't match and allow_baseline_mismatch is False, validation fails."""
        ws = _make_validatable_workspace(tmp_path, digest="sha256:EXPECTED")

        patches = self._validate_patches(ws) + [
            patch(
                "pfig.replay.validate.compute_graph_digest",
                return_value="sha256:ACTUAL",
            ),
            patch("pfig.replay.validate.compare_ir_graphs", return_value=DiffReport()),
        ]
        with _apply_patches(patches):
            result = replay_validate(ws)

        assert result is False
        assert ws.validate_ok is False
        assert any("MISMATCH" in w for w in ws.warnings)

    def test_validate_mismatch_allowed(self, tmp_path):
        """With allow_baseline_mismatch, validation passes despite mismatch."""
        ws = _make_validatable_workspace(tmp_path, digest="sha256:EXPECTED")

        patches = self._validate_patches(ws) + [
            patch(
                "pfig.replay.validate.compute_graph_digest",
                return_value="sha256:ACTUAL",
            ),
            patch("pfig.replay.validate.compare_ir_graphs", return_value=DiffReport()),
        ]
        with _apply_patches(patches):
            result = replay_validate(ws, allow_baseline_mismatch=True)

        assert result is True
        assert ws.validate_ok is True

    def test_validate_with_overrides(self, tmp_path):
        """Override pass should run when set_overrides is provided."""
        ws = _make_validatable_workspace(tmp_path, digest="sha256:OK")

        call_count = {"validate": 0}

        def fake_validate(*args, **kwargs):
            call_count["validate"] += 1
            return _mock_validation()

        patches = [
            patch(
                "pfig.replay.validate.load_bundle",
                return_value=(ws.bundle_dir, _mock_manifest()),
            ),
            patch("pfig.replay.validate._fs.read_text", return_value="x = 1\n"),
            patch("pfig.replay.validate.parse_pfig_source", return_value=MagicMock()),
            patch("pfig.replay.validate.validate_pfig", side_effect=fake_validate),
            patch("pfig.replay.validate.build_ir_graph", return_value={}),
            patch("pfig.replay.validate.compute_graph_digest", return_value="sha256:OK"),
            patch("pfig.replay.validate._write_json"),
            patch("pfig.replay.validate.compare_ir_graphs", return_value=DiffReport()),
        ]
        with _apply_patches(patches):
            result = replay_validate(ws, set_overrides={"lr": 0.1})

        assert result is True
        assert call_count["validate"] == 2  # baseline + override

    def test_validate_prepends_replay_src_to_syspath(self, tmp_path):
        """Imports during digest validation must see workspace/src like ``pfig run`` does."""
        ws = _make_validatable_workspace(tmp_path, digest="sha256:MATCH")
        src = ws.src_dir
        src.mkdir(parents=True)
        seen: dict[str, str | None] = {}

        def capture_validate(*args, **kwargs):
            seen["head"] = sys.path[0] if sys.path else None
            return _mock_validation()

        before_head = sys.path[0] if sys.path else None
        patches = [
            patch(
                "pfig.replay.validate.load_bundle",
                return_value=(ws.bundle_dir, _mock_manifest()),
            ),
            patch("pfig.replay.validate._fs.read_text", return_value="x = 1\n"),
            patch("pfig.replay.validate.parse_pfig_source", return_value=MagicMock()),
            patch("pfig.replay.validate.validate_pfig", side_effect=capture_validate),
            patch("pfig.replay.validate.build_ir_graph", return_value={"k": "v"}),
            patch("pfig.replay.validate.compute_graph_digest", return_value="sha256:MATCH"),
            patch("pfig.replay.validate._write_json"),
        ]
        with _apply_patches(patches):
            replay_validate(ws)

        assert seen["head"] == str(src.resolve())
        assert sys.path[0] == before_head

    def test_validate_prepends_venv_site_packages(self, tmp_path):
        """When a replay venv exists, its site-packages must be on ``sys.path`` for validate."""
        ws = _make_validatable_workspace(tmp_path, digest="sha256:MATCH")
        src = ws.src_dir
        src.mkdir(parents=True)
        sp = (
            ws.venv_dir
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages"
        )
        sp.mkdir(parents=True)
        ws.venv_python = str(ws.venv_dir / "bin" / "python")

        seen: list[str] = []

        def capture_validate(*args, **kwargs):
            seen.extend(sys.path[:2])
            return _mock_validation()

        patches = [
            patch(
                "pfig.replay.validate.load_bundle",
                return_value=(ws.bundle_dir, _mock_manifest()),
            ),
            patch("pfig.replay.validate._fs.read_text", return_value="x = 1\n"),
            patch("pfig.replay.validate.parse_pfig_source", return_value=MagicMock()),
            patch("pfig.replay.validate.validate_pfig", side_effect=capture_validate),
            patch("pfig.replay.validate.build_ir_graph", return_value={"k": "v"}),
            patch("pfig.replay.validate.compute_graph_digest", return_value="sha256:MATCH"),
            patch("pfig.replay.validate._write_json"),
        ]
        with _apply_patches(patches):
            replay_validate(ws)

        assert seen[0] == str(src.resolve())
        assert seen[1] == str(sp.resolve())


# ======================================================================
# Diff text formatting
# ======================================================================


class TestFormatDiffText:
    def test_empty_report(self):
        report = DiffReport()
        assert not report.has_changes

    def test_added_keys(self):
        report = DiffReport(added=["new_key"])
        text = _format_diff_text(report)
        assert "+ new_key" in text

    def test_removed_keys(self):
        report = DiffReport(removed=["old_key"])
        text = _format_diff_text(report)
        assert "- old_key" in text

    def test_modified_value(self):
        report = DiffReport(
            modified={"lr": DiffDetail(DiffChangeType.MODIFIED, old_value=0.01, new_value=0.1)}
        )
        text = _format_diff_text(report)
        assert "~ lr:" in text
        assert "0.01" in text
        assert "0.1" in text

    def test_type_changed(self):
        report = DiffReport(
            modified={"x": DiffDetail(DiffChangeType.TYPE_CHANGED, old_value=42, new_value="hello")}
        )
        text = _format_diff_text(report)
        assert "~ x:" in text

    def test_sub_diffs(self):
        report = DiffReport(
            modified={
                "model": DiffDetail(
                    DiffChangeType.MODIFIED,
                    sub_diffs={
                        "layers": DiffDetail(DiffChangeType.MODIFIED, old_value=2, new_value=4),
                        "dropout": DiffDetail(DiffChangeType.ADDED, new_value=0.5),
                        "bias": DiffDetail(DiffChangeType.REMOVED, old_value=True),
                    },
                )
            }
        )
        text = _format_diff_text(report)
        assert "~ model:" in text
        assert "+ dropout" in text
        assert "- bias" in text
        assert "~ layers" in text


# ======================================================================
# Runner
# ======================================================================


class TestReplayRun:
    _VALIDATE_PATCH = "pfig.replay.validate.replay_validate"

    def test_unprepared_workspace_returns_2(self, tmp_path):
        ws = _make_workspace(tmp_path)
        ws.prepare_ok = False

        rc = replay_run(ws)
        assert rc == 2

    def test_validation_failure_returns_1(self, tmp_path):
        ws = _make_workspace(tmp_path)
        ws.prepare_ok = True

        with (
            patch(self._VALIDATE_PATCH, return_value=False),
            patch("pfig.replay.runner.write_report"),
        ):
            rc = replay_run(ws)

        assert rc == 1

    def test_dry_run_returns_0_after_validate(self, tmp_path):
        ws = _make_workspace(tmp_path)
        ws.prepare_ok = True

        with patch(self._VALIDATE_PATCH, return_value=True):
            rc = replay_run(ws, dry_run=True)

        assert rc == 0

    def test_successful_execution(self, tmp_path):
        ws = _make_workspace(tmp_path)
        ws.resolved_inputs = {}
        ws.prepare_ok = True
        ws.artifacts_dir.mkdir(parents=True, exist_ok=True)

        mock_proc = MagicMock()
        mock_proc.stdout = iter([])
        mock_proc.returncode = 0
        mock_proc.wait = MagicMock()

        with (
            patch(self._VALIDATE_PATCH, return_value=True),
            patch("pfig.replay.runner.write_report"),
            patch("pfig.replay.runner.subprocess.Popen", return_value=mock_proc),
        ):
            rc = replay_run(ws)

        assert rc == 0
        assert ws.run_exit_code == 0

    def test_failed_execution(self, tmp_path):
        ws = _make_workspace(tmp_path)
        ws.resolved_inputs = {}
        ws.prepare_ok = True
        ws.artifacts_dir.mkdir(parents=True, exist_ok=True)

        mock_proc = MagicMock()
        mock_proc.stdout = iter([])
        mock_proc.returncode = 1
        mock_proc.wait = MagicMock()

        with (
            patch(self._VALIDATE_PATCH, return_value=True),
            patch("pfig.replay.runner.write_report"),
            patch("pfig.replay.runner.subprocess.Popen", return_value=mock_proc),
        ):
            rc = replay_run(ws)

        assert rc == 1
        assert ws.run_exit_code == 1

    def test_run_with_set_overrides(self, tmp_path):
        ws = _make_workspace(tmp_path)
        ws.resolved_inputs = {"lr": 0.01}
        ws.prepare_ok = True
        ws.artifacts_dir.mkdir(parents=True, exist_ok=True)

        mock_proc = MagicMock()
        mock_proc.stdout = iter([])
        mock_proc.returncode = 0
        mock_proc.wait = MagicMock()

        with (
            patch(self._VALIDATE_PATCH, return_value=True),
            patch("pfig.replay.runner.write_report"),
            patch("pfig.replay.runner.subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            rc = replay_run(ws, set_overrides={"lr": 0.01})

        assert rc == 0
        cmd = mock_popen.call_args[0][0]
        assert "--set" in cmd

    def test_run_passes_base_commit_when_snapshot_has_upstream(self, tmp_path):
        """Replay passes --base-commit so snapshot can capture patch series (worktree has no @{u})."""
        snap = _load_snap_with_git(tmp_path)
        ws_root = tmp_path / "ws"
        ws_root.mkdir(parents=True, exist_ok=True)
        (ws_root / "artifacts").mkdir(exist_ok=True)
        (ws_root / "src").mkdir(exist_ok=True)
        ws = ReplayWorkspace(root=ws_root, snapshot=snap)
        ws.prepare_ok = True
        ws.venv_python = "/usr/bin/python3"
        ws.resolved_inputs = {}

        mock_proc = MagicMock()
        mock_proc.stdout = iter([])
        mock_proc.returncode = 0
        mock_proc.wait = MagicMock()

        with (
            patch(self._VALIDATE_PATCH, return_value=True),
            patch("pfig.replay.runner.write_report"),
            patch("pfig.replay.runner.subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            replay_run(ws)

        cmd = mock_popen.call_args[0][0]
        assert "--base-commit" in cmd
        upstream = snap.git.upstream_commit
        assert upstream is not None
        idx = cmd.index("--base-commit")
        assert cmd[idx + 1] == upstream


class TestFormatValue:
    def test_string_plain(self):
        assert _format_value("hello") == "'hello'"

    def test_string_with_spaces(self):
        assert _format_value("hello world") == "'hello world'"

    def test_bool_true(self):
        assert _format_value(True) == "True"

    def test_bool_false(self):
        assert _format_value(False) == "False"

    def test_none(self):
        assert _format_value(None) == "None"

    def test_int(self):
        assert _format_value(42) == "42"

    def test_float(self):
        assert _format_value(0.01) == "0.01"

    def test_round_trip_parse_set_args(self):
        samples = [
            "hello",
            "hello world",
            "a'b",
            42,
            0.01,
            True,
            False,
            None,
            [1, 2],
            {"a": 1},
        ]
        for v in samples:
            formatted = _format_value(v)
            parsed = parse_set_args([f"k={formatted}"])
            assert parsed["k"] == v, (v, formatted, parsed["k"])


# ======================================================================
# git_restore helpers
# ======================================================================


class TestPickBase:
    def test_prefer_upstream_when_patches(self):
        with patch("pfig.replay.git_restore.commit_exists", return_value=True):
            base = _pick_base("abc123", "upstream123", True, Path("/repo"))
        assert base == "upstream123"

    def test_fallback_to_commit_without_patches(self):
        with patch("pfig.replay.git_restore.commit_exists", return_value=True):
            base = _pick_base("abc123", "upstream123", False, Path("/repo"))
        assert base == "abc123"

    def test_fallback_to_commit_when_upstream_unreachable(self):
        def exists_side_effect(sha, cwd=None):
            return sha == "abc123"

        with patch("pfig.replay.git_restore.commit_exists", side_effect=exists_side_effect):
            base = _pick_base("abc123", "upstream123", True, Path("/repo"))
        assert base == "abc123"

    def test_fallback_to_upstream_when_commit_unreachable(self):
        def exists_side_effect(sha, cwd=None):
            return sha == "upstream123"

        with patch("pfig.replay.git_restore.commit_exists", side_effect=exists_side_effect):
            base = _pick_base("abc123", "upstream123", False, Path("/repo"))
        assert base == "upstream123"

    def test_raises_when_neither_reachable(self):
        with patch("pfig.replay.git_restore.commit_exists", return_value=False):
            with pytest.raises(RuntimeError, match="Neither commit"):
                _pick_base("abc123", "upstream123", False, Path("/repo"))

    def test_no_upstream(self):
        with patch("pfig.replay.git_restore.commit_exists", return_value=True):
            base = _pick_base("abc123", None, False, Path("/repo"))
        assert base == "abc123"

    def test_no_upstream_commit_unreachable(self):
        with patch("pfig.replay.git_restore.commit_exists", return_value=False):
            with pytest.raises(RuntimeError, match="Neither commit"):
                _pick_base("abc123", None, False, Path("/repo"))


class TestSafeChild:
    def test_valid_child(self, tmp_path):
        child = tmp_path / "patch.txt"
        child.write_text("content")
        result = _safe_child(tmp_path, "patch.txt")
        assert result is not None
        assert result == child.resolve()

    def test_nonexistent_child(self, tmp_path):
        result = _safe_child(tmp_path, "nonexistent.txt")
        assert result is None

    def test_path_traversal_blocked(self, tmp_path):
        (tmp_path / "inner").mkdir()
        (tmp_path / "secret.txt").write_text("secret")
        result = _safe_child(tmp_path / "inner", "../secret.txt")
        assert result is None


class TestRestoreCode:
    def test_no_repo_root_or_clone_url_raises(self):
        with pytest.raises(RuntimeError, match="No repo_root or clone_url"):
            restore_code(
                dest=Path("/tmp/dest"),
                commit="abc",
                upstream_commit=None,
                patches_dir=None,
                commit_patch_names=[],
                dirty_patch_names=[],
                repo_root=None,
                clone_url=None,
            )

    def test_worktree_method(self, tmp_path):
        with (
            patch("pfig.replay.git_restore._pick_base", return_value="abc123"),
            patch("pfig.replay.git_restore.create_worktree"),
            patch("pfig.replay.git_restore._apply_patches"),
        ):
            method = restore_code(
                dest=tmp_path / "src",
                commit="abc123",
                upstream_commit=None,
                patches_dir=None,
                commit_patch_names=[],
                dirty_patch_names=[],
                repo_root=tmp_path / "repo",
                clone_url=None,
            )
        assert method == "worktree"

    def test_clone_method(self, tmp_path):
        with (
            patch("pfig.replay.git_restore.clone_repo"),
            patch("pfig.replay.git_restore._pick_base", return_value="abc123"),
            patch("pfig.replay.git_restore.checkout"),
            patch("pfig.replay.git_restore._apply_patches"),
        ):
            method = restore_code(
                dest=tmp_path / "src",
                commit="abc123",
                upstream_commit=None,
                patches_dir=None,
                commit_patch_names=[],
                dirty_patch_names=[],
                repo_root=None,
                clone_url="https://github.com/org/repo",
            )
        assert method == "clone"


# ======================================================================
# venv helpers
# ======================================================================


class TestIsEditableLine:
    def test_editable_dash_e(self):
        assert _is_editable_line("-e git+https://github.com/org/repo@main")

    def test_editable_long_flag(self):
        assert _is_editable_line("--editable git+https://github.com/org/repo@main")

    def test_regular_package(self):
        assert not _is_editable_line("numpy==1.24.0")

    def test_comment_line(self):
        assert not _is_editable_line("# comment")

    def test_empty_line(self):
        assert not _is_editable_line("")

    def test_whitespace_prefix(self):
        assert _is_editable_line("  -e git+https://github.com/org/repo@main")


class TestSplitLockFile:
    def test_splits_correctly(self, tmp_path):
        lock = tmp_path / "requirements.lock"
        lock.write_text(
            "numpy==1.24.0\n"
            "-e git+https://github.com/org/repo@main#egg=mylib\n"
            "torch==2.0.0\n"
            "--editable git+https://github.com/org/other@v1\n"
        )
        editables, regulars = _split_lock_file(str(lock))
        assert len(editables) == 2
        assert len(regulars) == 2
        assert "numpy" in regulars[0]
        assert "torch" in regulars[1]

    def test_all_regular(self, tmp_path):
        lock = tmp_path / "requirements.lock"
        lock.write_text("numpy==1.24.0\ntorch==2.0.0\n")
        editables, regulars = _split_lock_file(str(lock))
        assert len(editables) == 0
        assert len(regulars) == 2

    def test_all_editable(self, tmp_path):
        lock = tmp_path / "requirements.lock"
        lock.write_text("-e git+https://github.com/org/repo@main\n")
        editables, regulars = _split_lock_file(str(lock))
        assert len(editables) == 1
        assert len(regulars) == 0

    def test_empty_file(self, tmp_path):
        lock = tmp_path / "requirements.lock"
        lock.write_text("")
        editables, regulars = _split_lock_file(str(lock))
        assert len(editables) == 0
        assert len(regulars) == 0


class TestFilterEditables:
    def test_drops_own_repo(self):
        lines = [
            "-e git+https://github.com/org/repo@abc123#egg=mylib\n",
            "-e git+https://github.com/other/lib@def456#egg=otherlib\n",
        ]
        result = _filter_editables(lines, "https://github.com/org/repo")
        assert len(result) == 1
        assert "other" in result[0]

    def test_keeps_all_without_snapshot_url(self):
        lines = ["-e git+https://github.com/org/repo@abc123#egg=mylib\n"]
        result = _filter_editables(lines, None)
        assert len(result) == 1

    def test_keeps_non_matching(self):
        lines = ["-e git+https://github.com/org/repo@abc123#egg=mylib\n"]
        result = _filter_editables(lines, "https://github.com/other/project")
        assert len(result) == 1

    def test_empty_list(self):
        result = _filter_editables([], "https://github.com/org/repo")
        assert result == []


class TestPkgCount:
    def test_counts_packages(self):
        lines = ["numpy==1.24.0\n", "torch==2.0.0\n", "# comment\n", "\n"]
        assert _pkg_count(lines) == 2

    def test_empty(self):
        assert _pkg_count([]) == 0

    def test_only_comments(self):
        assert _pkg_count(["# comment\n", "# another\n"]) == 0

    def test_only_blanks(self):
        assert _pkg_count(["\n", "  \n"]) == 0


# ======================================================================
# CLI integration
# ======================================================================


class TestCLIReplay:
    def test_replay_help(self):
        r = subprocess.run(
            ["python", "-m", "pfig", "replay", "--help"],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0
        assert "prepare" in r.stdout
        assert "validate" in r.stdout
        assert "run" in r.stdout

    def test_replay_run_help(self):
        r = subprocess.run(
            ["python", "-m", "pfig", "replay", "run", "--help"],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0
        assert "--dry-run" in r.stdout
        assert "--allow-baseline-mismatch" in r.stdout

    def test_replay_prepare_missing_snapshot(self):
        r = subprocess.run(
            ["python", "-m", "pfig", "replay", "prepare", "/nonexistent/path"],
            capture_output=True,
            text=True,
        )
        assert r.returncode != 0

    def test_replay_validate_help(self):
        r = subprocess.run(
            ["python", "-m", "pfig", "replay", "validate", "--help"],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0


# ======================================================================
# Helpers
# ======================================================================


def _make_snap_data(
    tmp_path: Path,
    remote_url: str | None = "https://github.com/org/repo",
) -> SnapshotData:
    log = _minimal_log(with_git=True, remote_url=remote_url)
    _write_snapshot(tmp_path / "snap", log, with_bundle=True)
    return load_and_validate_snapshot(tmp_path / "snap")


def _minimal_log(
    with_git: bool = False,
    remote_url: str | None = "https://github.com/org/repo",
) -> dict:
    log: dict = {
        "version": "2.0",
        "meta": {
            "pfig_version": "0.1.0",
            "generated_at": "2026-01-14T12:00:00Z",
            "entrypoint": "__entrypoint__",
            "digest": "sha256:test",
            "run_id": "abc123",
            "pfig_files": [],
        },
        "environment": {
            "python_version": "3.10.0",
            "platform": "linux-x86_64",
            "command": ["pfig", "run", "test.pfig"],
            "working_dir": "/tmp/test",
        },
        "graph": {},
    }
    if with_git:
        log["environment"]["git"] = {
            "commit": "aaa111bbb222ccc333ddd444eee555fff666777",
            "is_dirty": False,
            "remote_origin_url": remote_url,
            "has_upstream": True,
            "upstream_commit": "000111222333444555666777888999aaabbbccc",
        }
    return log


def _write_snapshot(
    snap_dir: Path,
    log: dict,
    with_bundle: bool = False,
) -> None:
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / "log.json").write_text(json.dumps(log, indent=2))

    if with_bundle:
        payload = snap_dir / "payload"
        payload.mkdir(parents=True, exist_ok=True)
        bundle_tar = payload / "config_bundle.tar.gz"
        with tarfile.open(bundle_tar, "w:gz") as tf:
            manifest = {
                "entry": "configs/test.pfig",
                "profiles": [],
                "files": ["configs/test.pfig"],
            }
            _add_str_to_tar(tf, "bundle_manifest.json", json.dumps(manifest))
            _add_str_to_tar(tf, "configs/test.pfig", '__entrypoint__ = "hello"\n')


def _add_str_to_tar(tf: tarfile.TarFile, name: str, content: str) -> None:
    data = content.encode()
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    tf.addfile(info, io.BytesIO(data))


def _load_snap(tmp_path: Path) -> SnapshotData:
    """Helper: create and load a minimal snapshot."""
    log = _minimal_log()
    _write_snapshot(tmp_path / "snap", log, with_bundle=True)
    return load_and_validate_snapshot(tmp_path / "snap")


def _load_snap_with_git(tmp_path: Path) -> SnapshotData:
    """Helper: create and load a snapshot with git info."""
    log = _minimal_log(with_git=True)
    _write_snapshot(tmp_path / "snap", log, with_bundle=True)
    return load_and_validate_snapshot(tmp_path / "snap")


def _make_workspace(tmp_path: Path) -> ReplayWorkspace:
    """Helper: create a workspace with standard dirs."""
    snap = _load_snap(tmp_path)
    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    (ws_root / "artifacts").mkdir(exist_ok=True)
    ws = ReplayWorkspace(root=ws_root, snapshot=snap)
    ws.prepare_ok = True
    ws.method = "worktree"
    return ws


def _make_validatable_workspace(tmp_path: Path, digest: str = "sha256:test") -> ReplayWorkspace:
    """Helper: workspace set up for validation tests."""
    log = _minimal_log()
    log["meta"]["digest"] = digest
    _write_snapshot(tmp_path / "snap", log, with_bundle=True)
    snap = load_and_validate_snapshot(tmp_path / "snap")

    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    (ws_root / "artifacts").mkdir(exist_ok=True)
    bundle_dir = ws_root / "bundle"
    bundle_dir.mkdir(exist_ok=True)

    ws = ReplayWorkspace(root=ws_root, snapshot=snap)
    ws.prepare_ok = True
    return ws


def _mock_manifest():
    """Create a mock BundleManifest."""
    m = MagicMock()
    m.entry = "configs/test.pfig"
    m.profiles = []
    return m


@contextlib.contextmanager
def _apply_patches(patches: List):
    """Enter a list of ``unittest.mock.patch`` context managers."""
    mocks = []
    try:
        for p in patches:
            mocks.append(p.__enter__())
        yield mocks
    finally:
        for p in reversed(patches):
            p.__exit__(None, None, None)
