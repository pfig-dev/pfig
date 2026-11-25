"""Remote filesystem operations for pfig — optional, requires ``pfig[remote]``.

Provides read/write/copy/existence operations over local paths, GCS (``gs://``),
S3 (``s3://``), and in-memory (``memory://``) URIs via fsspec.  Import this
module only inside a try/except ImportError guard.
"""

from __future__ import annotations

import functools
import os
import random
import shutil
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import IO, Any, Callable, Iterator, TypeVar

import fsspec

# ---------------------------------------------------------------------------
# URI / filesystem helpers
# ---------------------------------------------------------------------------

SUPPORTED_PROTOCOLS = frozenset({"file", "gs", "s3", "memory"})

_SCHEME_TO_PROTO = {
    "gs://": "gs",
    "s3://": "s3",
    "file://": "file",
    "memory://": "memory",
}

F = TypeVar("F", bound=Callable[..., Any])

_MAX_RETRIES = 3
_BASE_DELAY = 0.5
_MAX_DELAY = 30.0
_RETRYABLE: tuple[type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
)

_fs_cache: dict[tuple[str, frozenset[tuple[str, Any]]], Any] = {}


def _split_uri(uri: str) -> tuple[str, str]:
    uri = uri.strip()
    for prefix, proto in _SCHEME_TO_PROTO.items():
        if uri.startswith(prefix):
            path = uri[len(prefix) :]
            if proto == "file":
                return (proto, str(Path(path).expanduser().resolve()))
            return (proto, path.lstrip("/"))
    if "://" in uri:
        scheme = uri.split("://")[0]
        raise ValueError(
            f"Unsupported protocol: {scheme!r}. Supported: {sorted(SUPPORTED_PROTOCOLS)}"
        )
    return ("file", str(Path(uri).expanduser().resolve()))


def _get_fs(protocol: str, **kwargs: Any) -> Any:
    if protocol not in SUPPORTED_PROTOCOLS:
        raise ValueError(f"Unsupported protocol: {protocol!r}")
    cache_key = (protocol, frozenset(sorted(kwargs.items())))
    if cache_key not in _fs_cache:
        _fs_cache[cache_key] = fsspec.filesystem(protocol, **kwargs)
    return _fs_cache[cache_key]


def _url_to_fs(uri: str, **kwargs: Any) -> tuple[Any, str, str]:
    protocol, path = _split_uri(uri)
    return (_get_fs(protocol, **kwargs), path, protocol)


def _make_uri(protocol: str, path: str) -> str:
    if protocol in ("gs", "s3", "memory"):
        return f"{protocol}://{path}"
    return path


def _retry(
    max_retries: int = _MAX_RETRIES,
    base_delay: float = _BASE_DELAY,
    max_delay: float = _MAX_DELAY,
    retryable: tuple[type[Exception], ...] = _RETRYABLE,
) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable as exc:
                    last = exc
                    if attempt == max_retries:
                        raise
                    delay = min(base_delay * (2**attempt), max_delay)
                    time.sleep(delay + random.uniform(0, delay * 0.1))
            if last:
                raise last

        return wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@contextmanager
def open_file(uri: str, mode: str = "rb", **kwargs: Any) -> Iterator[IO[Any]]:
    fs, path, _ = _url_to_fs(uri)
    with fs.open(path, mode, **kwargs) as fh:
        yield fh


@_retry()
def read_bytes(uri: str, **kwargs: Any) -> bytes:
    fs, path, _ = _url_to_fs(uri, **kwargs)
    return fs.cat_file(path)


@_retry()
def read_text(uri: str, encoding: str = "utf-8", **kwargs: Any) -> str:
    return read_bytes(uri, **kwargs).decode(encoding)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def _atomic_write_local(path: str, data: bytes, overwrite: bool) -> None:
    target = Path(path)
    if not overwrite and target.exists():
        raise FileExistsError(f"File already exists: {path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp_")
    try:
        os.write(fd, data)
        os.close(fd)
        os.replace(tmp, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _atomic_write_remote(fs: Any, path: str, data: bytes, overwrite: bool, uri: str) -> None:
    if not overwrite and fs.exists(path):
        raise FileExistsError(f"File already exists: {uri}")
    parts = path.split("/", 1)
    bucket = parts[0]
    key = parts[1] if len(parts) > 1 else ""
    parent = str(PurePosixPath(key).parent) if key else ""
    tmp_key = f"{parent}/.tmp_{uuid.uuid4().hex}" if parent else f".tmp_{uuid.uuid4().hex}"
    tmp_path = f"{bucket}/{tmp_key}"
    try:
        with fs.open(tmp_path, "wb") as f:
            f.write(data)
        fs.copy(tmp_path, path)
    finally:
        try:
            fs.rm(tmp_path)
        except Exception:
            pass


@_retry()
def write_bytes(
    uri: str,
    data: bytes,
    *,
    overwrite: bool = True,
    atomic: bool = True,
    **kwargs: Any,
) -> None:
    fs, path, protocol = _url_to_fs(uri, **kwargs)
    if atomic:
        if protocol == "file":
            _atomic_write_local(path, data, overwrite)
        elif protocol in ("gs", "s3"):
            _atomic_write_remote(fs, path, data, overwrite, uri)
        else:
            if not overwrite and fs.exists(path):
                raise FileExistsError(f"File already exists: {uri}")
            with fs.open(path, "wb") as f:
                f.write(data)
    else:
        if not overwrite and fs.exists(path):
            raise FileExistsError(f"File already exists: {uri}")
        if protocol == "file":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        with fs.open(path, "wb") as f:
            f.write(data)


@_retry()
def write_text(
    uri: str,
    text: str,
    encoding: str = "utf-8",
    *,
    overwrite: bool = True,
    atomic: bool = True,
    **kwargs: Any,
) -> None:
    write_bytes(uri, text.encode(encoding), overwrite=overwrite, atomic=atomic, **kwargs)


# ---------------------------------------------------------------------------
# Existence
# ---------------------------------------------------------------------------


@_retry()
def exists(uri: str, **kwargs: Any) -> bool:
    fs, path, _ = _url_to_fs(uri, **kwargs)
    return fs.exists(path)


@_retry()
def is_file(uri: str, **kwargs: Any) -> bool:
    fs, path, _ = _url_to_fs(uri, **kwargs)
    return fs.isfile(path)


@_retry()
def is_dir(uri: str, **kwargs: Any) -> bool:
    fs, path, _ = _url_to_fs(uri, **kwargs)
    return fs.isdir(path)


# ---------------------------------------------------------------------------
# Directory ops
# ---------------------------------------------------------------------------


def mkdirs(uri: str, exist_ok: bool = True, **kwargs: Any) -> None:
    fs, path, protocol = _url_to_fs(uri, **kwargs)
    if protocol in ("gs", "s3"):
        return  # directories are virtual on object stores
    fs.makedirs(path, exist_ok=exist_ok)


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------


def _copy_streaming(src_uri: str, dst_uri: str, **kwargs: Any) -> None:
    chunk = 8 * 1024 * 1024
    with open_file(src_uri, "rb", **kwargs) as src:
        with open_file(dst_uri, "wb", **kwargs) as dst:
            shutil.copyfileobj(src, dst, length=chunk)


def _copy_recursive(src_dir: str, dst_dir: str, *, overwrite: bool = True, **kwargs: Any) -> None:
    src_fs, src_path, src_proto = _url_to_fs(src_dir, **kwargs)
    src_path = src_path.rstrip("/")
    first_root: str | None = None
    for root, _dirs, files in src_fs.walk(src_path):
        if first_root is None:
            first_root = root
            rel_root = ""
        elif root == first_root:
            rel_root = ""
        else:
            rel_root = root[len(first_root) + 1 :]
        for filename in files:
            src_file = _make_uri(src_proto, f"{root}/{filename}")
            raw_dst = f"{dst_dir}/{rel_root}/{filename}" if rel_root else f"{dst_dir}/{filename}"
            dst_proto, dst_norm = _split_uri(raw_dst)
            cp(
                src_file,
                _make_uri(dst_proto, dst_norm),
                recursive=False,
                overwrite=overwrite,
                **kwargs,
            )


@_retry()
def cp(
    src: str,
    dst: str,
    *,
    recursive: bool = False,
    overwrite: bool = True,
    **kwargs: Any,
) -> None:
    src_fs, src_path, src_proto = _url_to_fs(src, **kwargs)
    dst_fs, dst_path, dst_proto = _url_to_fs(dst, **kwargs)

    if not src_fs.exists(src_path):
        raise FileNotFoundError(f"No such file or directory: {src}")
    if not overwrite and dst_fs.exists(dst_path):
        raise FileExistsError(f"Destination already exists: {dst}")

    if src_fs.isdir(src_path):
        if not recursive:
            raise IsADirectoryError(f"Source is a directory (use recursive=True): {src}")
        _copy_recursive(src, dst, overwrite=overwrite, **kwargs)
        return

    if src_proto == dst_proto and src_proto != "memory":
        if dst_proto == "file":
            Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
        src_fs.copy(src_path, dst_path)
    else:
        if dst_proto == "file":
            Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
        _copy_streaming(src, dst, **kwargs)
