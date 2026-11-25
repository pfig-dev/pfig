"""Tests for pfig.fs -- filesystem abstraction with optional remote storage support."""

import json
import textwrap
from pathlib import Path
from unittest import mock

import pytest

from pfig import fs

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_remote_fs_cache():
    """Reset the lazy-import cache between tests."""
    fs._remote_fs = None
    fs._remote_fs_checked = False
    yield
    fs._remote_fs = None
    fs._remote_fs_checked = False


def _patch_no_remote_fs():
    """Simulate pfig[remote] not being installed."""
    return mock.patch.dict("sys.modules", {"pfig._remote_fs": None})


# ---------------------------------------------------------------------------
# is_uri
# ---------------------------------------------------------------------------


class TestIsUri:
    def test_gs_uri(self):
        assert fs.is_uri("gs://bucket/path") is True

    def test_s3_uri(self):
        assert fs.is_uri("s3://bucket/path") is True

    def test_memory_uri(self):
        assert fs.is_uri("memory://root/path") is True

    def test_file_uri(self):
        assert fs.is_uri("file:///tmp/foo") is True

    def test_local_path(self):
        assert fs.is_uri("/tmp/foo") is False

    def test_relative_path(self):
        assert fs.is_uri("./config.pfig") is False

    def test_path_object(self):
        assert fs.is_uri(Path("/tmp/foo")) is False


# ---------------------------------------------------------------------------
# Local fallback (pfig[remote] NOT installed)
# ---------------------------------------------------------------------------


class TestLocalFallback:
    """All operations must work on local paths when pfig[remote] is absent."""

    def test_read_write_text(self, tmp_path):
        p = tmp_path / "test.txt"
        with _patch_no_remote_fs():
            fs.write_text(p, "hello world")
            assert fs.read_text(p) == "hello world"

    def test_read_write_bytes(self, tmp_path):
        p = tmp_path / "test.bin"
        with _patch_no_remote_fs():
            fs.write_bytes(p, b"\x00\x01\x02")
            assert fs.read_bytes(p) == b"\x00\x01\x02"

    def test_exists(self, tmp_path):
        p = tmp_path / "exists.txt"
        with _patch_no_remote_fs():
            assert fs.exists(p) is False
            p.write_text("x")
            assert fs.exists(p) is True

    def test_is_file(self, tmp_path):
        p = tmp_path / "f.txt"
        with _patch_no_remote_fs():
            assert fs.is_file(p) is False
            p.write_text("x")
            assert fs.is_file(p) is True

    def test_is_dir(self, tmp_path):
        d = tmp_path / "subdir"
        with _patch_no_remote_fs():
            assert fs.is_dir(d) is False
            d.mkdir()
            assert fs.is_dir(d) is True

    def test_mkdirs(self, tmp_path):
        d = tmp_path / "a" / "b" / "c"
        with _patch_no_remote_fs():
            fs.mkdirs(d)
            assert d.is_dir()

    def test_cp(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("copy me")
        with _patch_no_remote_fs():
            fs.cp(src, dst)
            assert dst.read_text() == "copy me"

    def test_open_file(self, tmp_path):
        p = tmp_path / "open.txt"
        p.write_text("contents")
        with _patch_no_remote_fs():
            with fs.open_file(p, "r") as fh:
                assert fh.read() == "contents"

    def test_write_json_read_json(self, tmp_path):
        p = tmp_path / "data.json"
        obj = {"key": [1, 2, 3]}
        with _patch_no_remote_fs():
            fs.write_json(p, obj)
            assert fs.read_json(p) == obj


# ---------------------------------------------------------------------------
# Remote URI without pfig[remote] -> ImportError
# ---------------------------------------------------------------------------


class TestRemoteWithoutDeps:
    """Attempting a remote URI without pfig[remote] must raise a clear ImportError."""

    GCS_PATH = "gs://bucket/path/to/file.pfig"
    S3_PATH = "s3://bucket/path/to/file.pfig"

    def test_read_text_gcs(self):
        with _patch_no_remote_fs():
            with pytest.raises(ImportError, match="pfig\\[remote\\]"):
                fs.read_text(self.GCS_PATH)

    def test_read_text_s3(self):
        with _patch_no_remote_fs():
            with pytest.raises(ImportError, match="pfig\\[remote\\]"):
                fs.read_text(self.S3_PATH)

    def test_write_text(self):
        with _patch_no_remote_fs():
            with pytest.raises(ImportError, match="pfig\\[remote\\]"):
                fs.write_text(self.GCS_PATH, "data")

    def test_exists(self):
        with _patch_no_remote_fs():
            with pytest.raises(ImportError, match="pfig\\[remote\\]"):
                fs.exists(self.GCS_PATH)

    def test_cp(self):
        with _patch_no_remote_fs():
            with pytest.raises(ImportError, match="pfig\\[remote\\]"):
                fs.cp(self.GCS_PATH, "/tmp/dst")

    def test_cp_dst_uri(self):
        with _patch_no_remote_fs():
            with pytest.raises(ImportError, match="pfig\\[remote\\]"):
                fs.cp("/tmp/src", self.GCS_PATH)

    def test_is_dir(self):
        with _patch_no_remote_fs():
            with pytest.raises(ImportError, match="pfig\\[remote\\]"):
                fs.is_dir(self.GCS_PATH)

    def test_mkdirs(self):
        with _patch_no_remote_fs():
            with pytest.raises(ImportError, match="pfig\\[remote\\]"):
                fs.mkdirs(self.GCS_PATH)

    def test_open_file(self):
        with _patch_no_remote_fs():
            with pytest.raises(ImportError, match="pfig\\[remote\\]"):
                with fs.open_file(self.GCS_PATH):
                    pass


# ---------------------------------------------------------------------------
# Delegation to remote fs when available
# ---------------------------------------------------------------------------


class TestRemoteFsDelegation:
    """When pfig[remote] is importable, URI operations delegate to it."""

    def _inject_mock_remote_fs(self):
        """Inject a mock _remote_fs module into the fs module's cache."""
        mod = mock.MagicMock()
        mod.read_text.return_value = "remote content"
        mod.read_bytes.return_value = b"remote bytes"
        mod.exists.return_value = True
        mod.is_file.return_value = True
        mod.is_dir.return_value = False
        fs._remote_fs = mod
        fs._remote_fs_checked = True
        return mod

    def test_read_text_delegates_for_uri(self):
        rfs = self._inject_mock_remote_fs()
        result = fs.read_text("gs://bucket/file.txt")
        assert result == "remote content"
        rfs.read_text.assert_called_once_with("gs://bucket/file.txt", encoding="utf-8")

    def test_read_text_delegates_for_s3_uri(self):
        rfs = self._inject_mock_remote_fs()
        result = fs.read_text("s3://bucket/file.txt")
        assert result == "remote content"
        rfs.read_text.assert_called_once_with("s3://bucket/file.txt", encoding="utf-8")

    def test_write_text_delegates_for_uri(self):
        rfs = self._inject_mock_remote_fs()
        fs.write_text("gs://bucket/file.txt", "data")
        rfs.write_text.assert_called_once_with(
            "gs://bucket/file.txt", "data", encoding="utf-8", overwrite=True
        )

    def test_exists_delegates_for_uri(self):
        rfs = self._inject_mock_remote_fs()
        result = fs.exists("gs://bucket/file.txt")
        assert result is True
        rfs.exists.assert_called_once_with("gs://bucket/file.txt")

    def test_cp_delegates_when_src_is_uri(self):
        rfs = self._inject_mock_remote_fs()
        fs.cp("gs://bucket/src.txt", "/tmp/dst.txt")
        rfs.cp.assert_called_once_with("gs://bucket/src.txt", "/tmp/dst.txt", recursive=False)

    def test_cp_delegates_when_dst_is_uri(self):
        rfs = self._inject_mock_remote_fs()
        fs.cp("/tmp/src.txt", "gs://bucket/dst.txt")
        rfs.cp.assert_called_once_with("/tmp/src.txt", "gs://bucket/dst.txt", recursive=False)

    def test_local_path_uses_local_io_when_remote_available(self, tmp_path):
        rfs = self._inject_mock_remote_fs()
        path = tmp_path / "local.txt"

        fs.write_text(path, "local content")

        assert fs.read_text(path) == "local content"
        rfs.write_text.assert_not_called()
        rfs.read_text.assert_not_called()
