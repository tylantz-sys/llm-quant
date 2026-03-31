"""File locks for DB write operations."""

from __future__ import annotations

import fcntl
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

logger = logging.getLogger(__name__)


@dataclass
class FileLock:
    path: Path
    handle: TextIO

    def release(self) -> None:
        try:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
        finally:
            self.handle.close()

    def __enter__(self) -> "FileLock":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def acquire_file_lock(
    path: Path,
    *,
    timeout_seconds: float = 30.0,
    retry_seconds: float = 0.5,
) -> FileLock | None:
    """Acquire a non-blocking file lock with timeout."""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(timeout_seconds, 0.0)

    while True:
        handle = path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            logger.info("Acquired lock %s", path)
            return FileLock(path=path, handle=handle)
        except BlockingIOError:
            handle.close()
            if time.monotonic() >= deadline:
                return None
            time.sleep(max(retry_seconds, 0.0))
