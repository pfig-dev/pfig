"""
Filesystem abstraction layer with optional remote storage support.

When ``pfig[remote]`` is installed, all operations transparently support
``gs://`` and ``s3://`` URIs alongside local paths.  Without it, only local
paths work and attempting a remote URI raises a clear error.
"""

from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Union

_remote_fs: Any = None
_remote_fs_checked = False


def _get_remote_fs():
    """Return the ``pfig._remote_fs`` module, or *None* if not installed."""
    global _remote_fs, _remote_fs_checked
    if not _remote_fs_checked:
        try:
            import pfig._remote_fs as _mod

            _remote_fs = _mod
        except ImportError:
            _remote_fs = None
        _remote_fs_checked = True
    return _remote_fs


def _require_remote_fs(uri: str):
    """Raise a helpful error when a URI needs remote fs support but it is missing."""
    mod = _get_remote_fs()
    if mod is None:
        raise ImportError(
            f"Cannot access '{uri}': remote storage support requires extra dependencies. "
            "Install them with:  pip install pfig[remote]"
        )
    return mod


def is_uri(path: Union[str, Path]) -> bool:
    """Return *True* if *path* looks like a URI (contains ``://``)."""
    return "://" in str(path)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def read_text(path: Union[str, Path], encoding: str = "utf-8") -> str:
    s = str(path)
    if is_uri(s):
        return _require_remote_fs(s).read_text(s, encoding=encoding)
    return Path(s).read_text(encoding=encoding)


def read_bytes(path: Union[str, Path]) -> bytes:
    s = str(path)
    if is_uri(s):
        return _require_remote_fs(s).read_bytes(s)
    return Path(s).read_bytes()


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_text(
    path: Union[str, Path],
    text: str,
    encoding: str = "utf-8",
    *,
    overwrite: bool = True,
) -> None:
    s = str(path)
    if is_uri(s):
        _require_remote_fs(s).write_text(s, text, encoding=encoding, overwrite=overwrite)
        return
    p = Path(s)
    if not overwrite and p.exists():
        raise FileExistsError(f"File already exists and overwrite=False: {s}")
    p.write_text(text, encoding=encoding)


def write_bytes(
    path: Union[str, Path],
    data: bytes,
    *,
    overwrite: bool = True,
) -> None:
    s = str(path)
    if is_uri(s):
        _require_remote_fs(s).write_bytes(s, data, overwrite=overwrite)
        return
    p = Path(s)
    if not overwrite and p.exists():
        raise FileExistsError(f"File already exists and overwrite=False: {s}")
    p.write_bytes(data)


# ---------------------------------------------------------------------------
# Existence / metadata
# ---------------------------------------------------------------------------


def exists(path: Union[str, Path]) -> bool:
    s = str(path)
    if is_uri(s):
        return _require_remote_fs(s).exists(s)
    return Path(s).exists()


def is_file(path: Union[str, Path]) -> bool:
    s = str(path)
    if is_uri(s):
        return _require_remote_fs(s).is_file(s)
    return Path(s).is_file()


def is_dir(path: Union[str, Path]) -> bool:
    s = str(path)
    if is_uri(s):
        return _require_remote_fs(s).is_dir(s)
    return Path(s).is_dir()


# ---------------------------------------------------------------------------
# Directory ops
# ---------------------------------------------------------------------------


def mkdirs(path: Union[str, Path], exist_ok: bool = True) -> None:
    s = str(path)
    if is_uri(s):
        _require_remote_fs(s).mkdirs(s, exist_ok=exist_ok)
        return
    Path(s).mkdir(parents=True, exist_ok=exist_ok)


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------


def cp(
    src: Union[str, Path],
    dst: Union[str, Path],
    *,
    recursive: bool = False,
) -> None:
    s_src, s_dst = str(src), str(dst)
    if is_uri(s_src) or is_uri(s_dst):
        mod = _require_remote_fs(s_src if is_uri(s_src) else s_dst)
        mod.cp(s_src, s_dst, recursive=recursive)
        return
    if recursive:
        shutil.copytree(s_src, s_dst, dirs_exist_ok=True)
    else:
        shutil.copy2(s_src, s_dst)


# ---------------------------------------------------------------------------
# Context-manager open
# ---------------------------------------------------------------------------


@contextmanager
def open_file(path: Union[str, Path], mode: str = "r", **kwargs: Any) -> Iterator[Any]:
    s = str(path)
    if is_uri(s):
        with _require_remote_fs(s).open_file(s, mode=mode, **kwargs) as fh:
            yield fh
        return
    with open(s, mode, **kwargs) as fh:
        yield fh


# ---------------------------------------------------------------------------
# JSON helpers (used by snapshot)
# ---------------------------------------------------------------------------


def write_json(
    path: Union[str, Path],
    obj: Any,
    *,
    indent: int = 2,
    sort_keys: bool = True,
) -> None:
    text = json.dumps(obj, indent=indent, sort_keys=sort_keys)
    write_text(path, text)


def read_json(path: Union[str, Path]) -> Any:
    return json.loads(read_text(path))
