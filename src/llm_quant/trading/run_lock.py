"""Intraday run de-duplication lock."""

from __future__ import annotations

import fcntl
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

logger = logging.getLogger(__name__)


@dataclass
class RunLock:
    path: Path
    handle: TextIO
    slot: str

    def release(self) -> None:
        try:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
        finally:
            self.handle.close()

    def __enter__(self) -> "RunLock":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def slot_for_time(now: datetime, timeframe_minutes: int) -> str:
    if timeframe_minutes <= 0:
        timeframe_minutes = 5
    minute = (now.minute // timeframe_minutes) * timeframe_minutes
    slot_dt = now.replace(minute=minute, second=0, microsecond=0)
    return slot_dt.astimezone(UTC).isoformat()


def acquire_run_lock(
    pod_id: str,
    slot: str,
    lock_dir: Path | None = None,
) -> RunLock | None:
    locks_path = lock_dir or Path("data") / "locks"
    locks_path.mkdir(parents=True, exist_ok=True)
    lock_path = locks_path / f"intraday_{pod_id}.lock"

    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None

    handle.seek(0)
    existing = handle.read().strip()
    if existing == slot:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
        return None

    handle.seek(0)
    handle.truncate()
    handle.write(slot)
    handle.flush()

    logger.info("Acquired intraday lock %s for slot %s", lock_path, slot)
    return RunLock(path=lock_path, handle=handle, slot=slot)
