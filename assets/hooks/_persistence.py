"""Small persistence primitives for standalone OKS hooks.

Hooks are copied into user instances and run as standalone scripts, so they
cannot assume the ``knowledge_studio`` package is importable. Keep this module
stdlib-only and limit it to atomic snapshots and locked, fsynced JSONL appends.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None


@contextmanager
def file_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if msvcrt is not None:
        _ensure_windows_lock_file(lock_path)
    handle = lock_path.open("r+b" if msvcrt is not None else "a+b")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        if fcntl is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        elif msvcrt is not None:
            handle.seek(0)
            with contextlib.suppress(OSError):
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def _ensure_windows_lock_file(lock_path: Path) -> None:
    """Create the byte used by ``msvcrt.locking`` without a first-use race."""
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_BINARY", 0)
    for _ in range(100):
        try:
            fd = os.open(str(lock_path), flags)
        except FileExistsError:
            try:
                if lock_path.stat().st_size >= 1:
                    return
            except FileNotFoundError:
                continue
            time.sleep(0.01)
        else:
            try:
                os.write(fd, b"\0")
            finally:
                os.close(fd)
            return
    with lock_path.open("r+b") as seed:
        if seed.seek(0, os.SEEK_END) == 0:
            seed.write(b"\0")
            seed.flush()


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=path.stem)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, str(path))
        with contextlib.suppress(OSError):
            directory = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def append_jsonl(path: Path, record: dict, *, lock_path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(lock_path):
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
