"""Tests for pfig._remote_fs — exercises fsspec-backed operations via memory://."""

from unittest import mock

import pytest

from pfig import _remote_fs as rfs

ROOT = "memory://pfig-test"


@pytest.fixture(autouse=True)
def _clean_state():
    """Clear the fs cache and memory store between tests."""
    import fsspec

    rfs._fs_cache.clear()
    fsspec.filesystem("memory").store.clear()
    yield
    rfs._fs_cache.clear()
    fsspec.filesystem("memory").store.clear()


# ---------------------------------------------------------------------------
# _split_uri
# ---------------------------------------------------------------------------


class TestSplitUri:
    def test_gs_uri(self):
        assert rfs._split_uri("gs://bucket/key") == ("gs", "bucket/key")

    def test_s3_uri(self):
        assert rfs._split_uri("s3://bucket/key") == ("s3", "bucket/key")

    def test_memory_uri(self):
        proto, path = rfs._split_uri("memory://root/path")
        assert proto == "memory"
        assert path == "root/path"

    def test_file_uri(self):
        proto, path = rfs._split_uri("file:///tmp/foo")
        assert proto == "file"
        assert path == "/tmp/foo"

    def test_local_absolute_path(self, tmp_path):
        proto, path = rfs._split_uri(str(tmp_path))
        assert proto == "file"
        assert path == str(tmp_path)

    def test_unsupported_protocol_raises(self):
        with pytest.raises(ValueError, match="Unsupported protocol"):
            rfs._split_uri("ftp://host/path")


# ---------------------------------------------------------------------------
# _make_uri
# ---------------------------------------------------------------------------


class TestMakeUri:
    def test_gs(self):
        assert rfs._make_uri("gs", "b/k") == "gs://b/k"

    def test_s3(self):
        assert rfs._make_uri("s3", "b/k") == "s3://b/k"

    def test_memory(self):
        assert rfs._make_uri("memory", "b/k") == "memory://b/k"

    def test_local(self):
        assert rfs._make_uri("file", "/tmp/x") == "/tmp/x"


# ---------------------------------------------------------------------------
# Read / write — memory://
# ---------------------------------------------------------------------------


class TestReadWrite:
    def test_write_read_text(self):
        uri = f"{ROOT}/file.txt"
        rfs.write_text(uri, "hello")
        assert rfs.read_text(uri) == "hello"

    def test_write_read_bytes(self):
        uri = f"{ROOT}/data.bin"
        rfs.write_bytes(uri, b"\x00\x01\x02")
        assert rfs.read_bytes(uri) == b"\x00\x01\x02"

    def test_write_text_encoding(self):
        uri = f"{ROOT}/encoded.txt"
        rfs.write_text(uri, "café", encoding="utf-8")
        assert rfs.read_text(uri, encoding="utf-8") == "café"

    def test_write_bytes_no_overwrite_raises(self):
        uri = f"{ROOT}/once.bin"
        rfs.write_bytes(uri, b"first")
        with pytest.raises(FileExistsError):
            rfs.write_bytes(uri, b"second", overwrite=False)

    def test_write_text_no_overwrite_raises(self):
        uri = f"{ROOT}/once.txt"
        rfs.write_text(uri, "first")
        with pytest.raises(FileExistsError):
            rfs.write_text(uri, "second", overwrite=False)

    def test_write_bytes_non_atomic(self):
        uri = f"{ROOT}/direct.bin"
        rfs.write_bytes(uri, b"data", atomic=False)
        assert rfs.read_bytes(uri) == b"data"

    def test_write_bytes_non_atomic_no_overwrite_raises(self):
        uri = f"{ROOT}/direct.bin"
        rfs.write_bytes(uri, b"first", atomic=False)
        with pytest.raises(FileExistsError):
            rfs.write_bytes(uri, b"second", atomic=False, overwrite=False)


# ---------------------------------------------------------------------------
# Atomic write — local paths
# ---------------------------------------------------------------------------


class TestAtomicWriteLocal:
    def test_round_trip(self, tmp_path):
        path = str(tmp_path / "out.bin")
        rfs.write_bytes(path, b"local data")
        assert (tmp_path / "out.bin").read_bytes() == b"local data"

    def test_creates_parent_dirs(self, tmp_path):
        path = str(tmp_path / "a" / "b" / "c.txt")
        rfs.write_bytes(path, b"nested")
        assert (tmp_path / "a" / "b" / "c.txt").read_bytes() == b"nested"

    def test_no_overwrite_raises(self, tmp_path):
        p = tmp_path / "out.bin"
        p.write_bytes(b"existing")
        with pytest.raises(FileExistsError):
            rfs.write_bytes(str(p), b"new", overwrite=False)


# ---------------------------------------------------------------------------
# Atomic write — remote (mocked fs)
# ---------------------------------------------------------------------------


class TestAtomicWriteRemote:
    def test_no_overwrite_raises(self):
        mock_fs = mock.MagicMock()
        mock_fs.exists.return_value = True
        with pytest.raises(FileExistsError):
            rfs._atomic_write_remote(mock_fs, "bucket/key", b"data", False, "gs://bucket/key")

    def test_writes_then_copies(self):
        mock_fs = mock.MagicMock()
        mock_fs.exists.return_value = False
        rfs._atomic_write_remote(mock_fs, "bucket/key", b"data", True, "gs://bucket/key")
        mock_fs.copy.assert_called_once()
        mock_fs.rm.assert_called_once()


# ---------------------------------------------------------------------------
# Existence
# ---------------------------------------------------------------------------


class TestExistence:
    def test_exists_missing(self):
        assert rfs.exists(f"{ROOT}/missing.txt") is False

    def test_exists_present(self):
        uri = f"{ROOT}/present.txt"
        rfs.write_text(uri, "x")
        assert rfs.exists(uri) is True

    def test_is_file_missing(self):
        assert rfs.is_file(f"{ROOT}/nope.txt") is False

    def test_is_file_present(self):
        uri = f"{ROOT}/yes.txt"
        rfs.write_text(uri, "x")
        assert rfs.is_file(uri) is True

    def test_is_dir_missing(self):
        assert rfs.is_dir(f"{ROOT}/nodir") is False


# ---------------------------------------------------------------------------
# mkdirs
# ---------------------------------------------------------------------------


class TestMkdirs:
    def test_mkdirs_gs_is_noop(self):
        mock_fs = mock.MagicMock()
        with mock.patch.object(rfs, "_url_to_fs", return_value=(mock_fs, "b/d", "gs")):
            rfs.mkdirs("gs://b/d")
        mock_fs.makedirs.assert_not_called()

    def test_mkdirs_s3_is_noop(self):
        mock_fs = mock.MagicMock()
        with mock.patch.object(rfs, "_url_to_fs", return_value=(mock_fs, "b/d", "s3")):
            rfs.mkdirs("s3://b/d")
        mock_fs.makedirs.assert_not_called()

    def test_mkdirs_local(self, tmp_path):
        d = str(tmp_path / "a" / "b")
        rfs.mkdirs(d)
        assert (tmp_path / "a" / "b").is_dir()


# ---------------------------------------------------------------------------
# open_file
# ---------------------------------------------------------------------------


class TestOpenFile:
    def test_write_then_read(self):
        uri = f"{ROOT}/open.bin"
        with rfs.open_file(uri, "wb") as f:
            f.write(b"written")
        with rfs.open_file(uri, "rb") as f:
            assert f.read() == b"written"


# ---------------------------------------------------------------------------
# cp
# ---------------------------------------------------------------------------


class TestCp:
    def test_cp_same_backend(self):
        src, dst = f"{ROOT}/src.txt", f"{ROOT}/dst.txt"
        rfs.write_text(src, "content")
        rfs.cp(src, dst)
        assert rfs.read_text(dst) == "content"

    def test_cp_missing_src_raises(self):
        with pytest.raises(FileNotFoundError):
            rfs.cp(f"{ROOT}/gone.txt", f"{ROOT}/dst.txt")

    def test_cp_no_overwrite_raises(self):
        src, dst = f"{ROOT}/src.txt", f"{ROOT}/dst.txt"
        rfs.write_text(src, "a")
        rfs.write_text(dst, "b")
        with pytest.raises(FileExistsError):
            rfs.cp(src, dst, overwrite=False)

    def test_cp_dir_without_recursive_raises(self):
        rfs.write_text(f"{ROOT}/mydir/file.txt", "x")
        with pytest.raises(IsADirectoryError):
            rfs.cp(f"{ROOT}/mydir", f"{ROOT}/out")

    def test_cp_recursive(self):
        rfs.write_text(f"{ROOT}/src/a.txt", "aaa")
        rfs.write_text(f"{ROOT}/src/b.txt", "bbb")
        rfs.cp(f"{ROOT}/src", f"{ROOT}/dst", recursive=True)
        assert rfs.read_text(f"{ROOT}/dst/a.txt") == "aaa"
        assert rfs.read_text(f"{ROOT}/dst/b.txt") == "bbb"

    def test_cp_cross_protocol_streaming(self, tmp_path):
        src = f"{ROOT}/cross.txt"
        dst = str(tmp_path / "out.txt")
        rfs.write_text(src, "streamed")
        rfs.cp(src, dst)
        assert (tmp_path / "out.txt").read_text() == "streamed"
