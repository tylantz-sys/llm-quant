import argparse
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


def test_backtest_spec_years_override_cli_years():
    from scripts.run_backtest import _build_policy_inputs, _spec_backtest_years

    spec = {"backtest_spec": {"years": 5}}
    args = argparse.Namespace(
        years=3,
        initial_capital=100_000.0,
        no_spec_check=False,
        volatility_target=None,
        vol_target_window=20,
        vol_target_max_scale=2.0,
        regime_filter=False,
        vix_threshold=25.0,
        signal_strength=False,
        signal_strength_scale=0.01,
        signal_strength_cap=2.0,
        ensemble_vote=False,
        ensemble_min_votes=2,
    )

    effective_years = _spec_backtest_years(spec, args.years)
    policy_inputs = _build_policy_inputs(
        args=args,
        spec=spec,
        symbols=["SOXX", "QQQ"],
        effective_years=effective_years,
        years_overridden_by_spec=True,
    )

    assert effective_years == 5
    assert policy_inputs["years_requested_cli"] == 3
    assert policy_inputs["years_effective"] == 5
    assert policy_inputs["years_overridden_by_spec"] is True
    assert policy_inputs["years"] == 5


def test_exploratory_mode_retains_cli_years_when_spec_check_disabled():
    from scripts.run_backtest import _build_policy_inputs

    spec = {}
    args = argparse.Namespace(
        years=3,
        initial_capital=100_000.0,
        no_spec_check=True,
        volatility_target=None,
        vol_target_window=20,
        vol_target_max_scale=2.0,
        regime_filter=False,
        vix_threshold=25.0,
        signal_strength=False,
        signal_strength_scale=0.01,
        signal_strength_cap=2.0,
        ensemble_vote=False,
        ensemble_min_votes=2,
    )

    policy_inputs = _build_policy_inputs(
        args=args,
        spec=spec,
        symbols=["SOXX", "QQQ"],
        effective_years=args.years,
        years_overridden_by_spec=False,
    )

    assert policy_inputs["years_requested_cli"] == 3
    assert policy_inputs["years_effective"] == 3
    assert policy_inputs["years_overridden_by_spec"] is False
    assert policy_inputs["years"] == 3
