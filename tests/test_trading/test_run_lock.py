from datetime import UTC, datetime, timedelta

from llm_quant.trading.run_lock import acquire_run_lock, slot_for_time


def test_run_lock_dedupes_same_slot(tmp_path):
    now = datetime(2026, 3, 30, 15, 5, tzinfo=UTC)
    slot = slot_for_time(now, timeframe_minutes=5)

    lock = acquire_run_lock("default", slot, lock_dir=tmp_path)
    assert lock is not None
    lock.release()

    # Same slot should be skipped even after release.
    lock2 = acquire_run_lock("default", slot, lock_dir=tmp_path)
    assert lock2 is None

    # Next slot should be allowed.
    next_slot = slot_for_time(now + timedelta(minutes=5), timeframe_minutes=5)
    lock3 = acquire_run_lock("default", next_slot, lock_dir=tmp_path)
    assert lock3 is not None
    lock3.release()
