import importlib.util
from datetime import date, timedelta
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "run_walk_forward_non_ml.py"
    spec = importlib.util.spec_from_file_location(
        "run_walk_forward_non_ml",
        script_path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_windows_is_deterministic():
    mod = _load_module()
    start = date(2020, 1, 1)
    trading_dates = [start + timedelta(days=i) for i in range(760)]

    windows = mod.build_windows(
        trading_dates,
        train_days=24 * 21,
        test_days=3 * 21,
        step_days=3 * 21,
        purge_days=5,
    )

    assert len(windows) == 3
    assert windows[0]["train_start"] == trading_dates[0]
    assert windows[0]["train_end"] == trading_dates[(24 * 21) - 1]
    assert windows[1]["train_start"] == trading_dates[3 * 21]
    expected_last_idx = (2 * (3 * 21)) + (24 * 21) + 5 + (3 * 21) - 1
    assert windows[2]["test_end"] == trading_dates[expected_last_idx]
