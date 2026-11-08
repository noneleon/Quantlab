"""OS-level exclusive file locks (``fcntl.flock`` / ``msvcrt.locking`` on byte 0) — CORE-011.

The operating system releases the lock when the holder exits or crashes, so a lock is never broken by age.
Used by the paper broker's state file (DEC-0027) and the single-instance trading supervisor (LIVE-001).
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any


def lock_file(fh: Any, timeout_s: float) -> bool:
    """Take an exclusive lock on an open file handle; False if another holder kept it for ``timeout_s``."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.02)


def unlock_file(fh: Any) -> None:
    if sys.platform == "win32":
        import msvcrt

        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class LockHeld(RuntimeError):
    pass


@contextmanager
def exclusive(path: Path, timeout_s: float = 0.0) -> Iterator[IO[bytes]]:
    """Hold ``path`` (created if missing, never deleted) exclusively for the duration of the block."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as fh:
        if not lock_file(fh, timeout_s):
            raise LockHeld(f"{path} is locked by another process")
        try:
            yield fh
        finally:
            unlock_file(fh)
