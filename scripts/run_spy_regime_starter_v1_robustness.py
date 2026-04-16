#!/usr/bin/env python3
"""Run deterministic robustness validation for spy-regime-starter-v1.

This runner keeps the frozen strategy specification immutable on disk while
executing a governed robustness matrix across:
- baseline and alternate walk-forward windows
- shifted fold boundaries
- cost stress
- mild parameter perturbations

Outputs:
- data/strategies/spy-regime-starter-v1/robustness.yaml
- artifacts/spy-regime-starter-v1/robustness/*.yaml
"""

from __future__ import annotations

import argparse
import copy
import os
import socket
import statistics
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from llm_quant.backtest.artifacts import ensure_frozen_spec, strategy_dir
from llm_quant.backtest.engine import BacktestEngine, CostModel
from llm_quant.backtest.metrics import compute_max_drawdown, compute_sharpe
from llm_quant.backtest.strategies import create_strategy
from llm_quant.backtest.strategy import StrategyConfig
from llm_quant.data.fetcher import fetch_ohlcv
from llm_quant.data.indicators import compute_indicators

SLUG = "spy-regime-starter-v1"
DEFAULT_INITIAL_CAPITAL = 100_000.0
DEFAULT_MAXDD_THRESHOLD = 0.15


def _now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


def _resolve_symbols(spec: dict[str, Any]) -> list[str]:
    params = spec.get("parameters", {}) or {}
    backtest_spec = spec.get("backtest_spec", {}) or {}

    symbols: list[str] = []
    configured = backtest_spec.get("symbols", [])
    if isinstance(configured, list):
        symbols.extend(str(s) for s in configured)

    signal_symbols = backtest_spec.get("signal_symbols", [])
    if isinstance(signal_symbols, list):
        symbols.extend(str(s) for s in signal_symbols)

    for key in (
        "symbol",
        "trade_symbol",
        "vix_symbol",
        "leader_symbol",
        "follower_symbol",
        "symbol_a",
        "symbol_b",
    ):
        value = params.get(key)
        if value:
            symbols.append(str(value))

    raw_symbols = params.get("symbols")
    if isinstance(raw_symbols, list):
        symbols.extend(str(s) for s in raw_symbols)

    return sorted({s for s in symbols if s})


def _spec_fill_delay(spec: dict[str, Any]) -> int:
    params = spec.get("parameters", {}) or {}
    execution = spec.get("execution", {}) or {}
    return int(params.get("execution_lag_days", execution.get("fill_delay", 1)) or 1)


def _spec_warmup_days(spec: dict[str, Any]) -> int:
    execution = spec.get("execution", {}) or {}
    backtest_spec = spec.get("backtest_spec", {}) or {}
    return int(backtest_spec.get("warmup_days", execution.get("warmup_days", 30)) or 30)


def _spec_rebalance_frequency_days(spec: dict[str, Any]) -> int:
    params = spec.get("parameters", {}) or {}
    execution = spec.get("execution", {}) or {}
    return int(
        params.get(
            "rebalance_frequency_days",
            execution.get("rebalance_frequency_days", 1),
        )
        or 1
    )


def _build_strategy_config(spec: dict[str, Any], strategy_name: str) -> StrategyConfig:
    params = dict(spec.get("parameters", {}) or {})
    rebalance_frequency_days = _spec_rebalance_frequency_days(spec)
    if "rebalance_frequency" not in params:
        params["rebalance_frequency"] = rebalance_frequency_days
    if "rebalance_frequency_days" not in params:
        params["rebalance_frequency_days"] = rebalance_frequency_days
    return StrategyConfig(
        name=strategy_name,
        rebalance_frequency_days=rebalance_frequency_days,
        max_positions=10,
        target_position_weight=float(params.get("target_weight", 0.25)),
        stop_loss_pct=0.10,
        parameters=params,
    )


def _build_windows(
    trading_dates: list[date],
    *,
    train_days: int,
    test_days: int,
    step_days: int,
    purge_days: int,
    start_offset: int = 0,
) -> list[dict[str, date]]:
    windows: list[dict[str, date]] = []
    if start_offset < 0:
        raise ValueError("start_offset must be non-negative")
    if len(trading_dates) < start_offset + train_days + purge_days + test_days:
        return windows

    start_idx = start_offset
    while True:
        train_end_idx = start_idx + train_days - 1
        test_start_idx = train_end_idx + purge_days + 1
        test_end_idx = test_start_idx + test_days - 1
        if test_end_idx >= len(trading_dates):
            break
        windows.append(
            {
                "train_start": trading_dates[start_idx],
                "train_end": trading_dates[train_end_idx],
                "test_start": trading_dates[test_start_idx],
                "test_end": trading_dates[test_end_idx],
            }
        )
        start_idx += step_days
    return windows


def _compute_test_nav_series(snapshots: list[Any], test_start: date, test_end: date) -> list[float]:
    navs: list[float] = []
    for snap in snapshots:
        snap_date = snap.date
        if test_start <= snap_date <= test_end:
            navs.append(float(snap.nav))
    return navs


def _make_cost_model(spec: dict[str, Any], mode: str) -> CostModel:
    execution = dict(spec.get("execution", {}) or {})
    base = dict(execution.get("cost_model", {}) or {})
    spread_bps = float(base.get("spread_bps", 5.0))
    flat_slippage_bps = float(base.get("flat_slippage_bps", 2.0))
    slippage_volatility_factor = float(base.get("slippage_volatility_factor", 0.1))

    if mode == "baseline":
        pass
    elif mode == "1.5x":
        spread_bps *= 1.5
        flat_slippage_bps *= 1.5
        slippage_volatility_factor *= 1.5
    elif mode == "2.0x":
        spread_bps *= 2.0
        flat_slippage_bps *= 2.0
        slippage_volatility_factor *= 2.0
    elif mode == "spread_only":
        spread_bps *= 2.0
    elif mode == "slippage_heavy":
        flat_slippage_bps *= 2.0
        slippage_volatility_factor *= 2.0
    else:
        raise ValueError(f"Unsupported cost mode: {mode}")

    return CostModel(
        spread_bps=spread_bps,
        flat_slippage_bps=flat_slippage_bps,
        slippage_volatility_factor=slippage_volatility_factor,
    )


def _lane_status(results: list[dict[str, Any]], *, baseline_required: bool = True) -> str:
    if not results:
        return "fail"
    baseline_ok = True
    if baseline_required:
        baseline_candidates = [r for r in results if r.get("is_baseline")]
        baseline_ok = bool(baseline_candidates) and all(r.get("passed", False) for r in baseline_candidates)
    passed_count = sum(1 for r in results if r.get("passed", False))
    if baseline_ok and passed_count == len(results):
        return "pass"
    if baseline_ok and passed_count >= max(1, (len(results) + 1) // 2):
        return "conditional_pass"
    return "fail"


def _run_case(
    *,
    case_id: str,
    lane: str,
    spec: dict[str, Any],
    strategy_name: str,
    prices_df: pl.DataFrame,
    indicators_df: pl.DataFrame,
    train_days: int,
    test_days: int,
    step_days: int,
    purge_days: int,
    start_offset: int,
    cost_mode: str,
    parameter_overrides: dict[str, Any] | None,
    initial_capital: float,
    maxdd_threshold: float,
) -> dict[str, Any]:
    case_spec = copy.deepcopy(spec)
    params = dict(case_spec.get("parameters", {}) or {})
    if parameter_overrides:
        params.update(parameter_overrides)
    case_spec["parameters"] = params

    symbols = _resolve_symbols(case_spec)
    warmup_days = _spec_warmup_days(case_spec)
    fill_delay = _spec_fill_delay(case_spec)
    rebalance_frequency_days = _spec_rebalance_frequency_days(case_spec)
    cost_model = _make_cost_model(case_spec, cost_mode)

    trading_dates = sorted(prices_df.select("date").unique().to_series().to_list())
    windows = _build_windows(
        trading_dates,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        purge_days=purge_days,
        start_offset=start_offset,
    )
    if not windows:
        raise ValueError(f"No walk-forward windows produced for case {case_id}")

    config = _build_strategy_config(case_spec, strategy_name)
    strategy = create_strategy(strategy_name, config)
    engine = BacktestEngine(strategy, initial_capital=initial_capital)

    fold_results: list[dict[str, Any]] = []
    for i, window in enumerate(windows, start=1):
        train_start = window["train_start"]
        test_end = window["test_end"]
        test_start = window["test_start"]

        fold_prices = prices_df.filter((pl.col("date") >= train_start) & (pl.col("date") <= test_end))
        fold_indicators = indicators_df.filter((pl.col("date") >= train_start) & (pl.col("date") <= test_end))

        result = engine.run(
            prices_df=fold_prices,
            indicators_df=fold_indicators,
            slug=f"{SLUG}-{case_id.lower()}-fold-{i}",
            cost_model=cost_model,
            fill_delay=fill_delay,
            warmup_days=warmup_days,
            cost_multiplier=1.0,
            trial_count=1,
        )

        nav_series = _compute_test_nav_series(result.snapshots, test_start, test_end)
        if len(nav_series) < 2:
            returns: list[float] = []
            fold_sharpe = 0.0
            fold_maxdd = 0.0
            test_days_used = 0
        else:
            returns = [
                nav_series[idx] / nav_series[idx - 1] - 1.0
                for idx in range(1, len(nav_series))
                if nav_series[idx - 1] != 0
            ]
            fold_sharpe = compute_sharpe(returns, annualize=True) if returns else 0.0
            fold_maxdd = compute_max_drawdown(nav_series)[0]
            test_days_used = len(nav_series)

        fold_results.append(
            {
                "fold": i,
                "train_start": str(train_start),
                "train_end": str(window["train_end"]),
                "test_start": str(test_start),
                "test_end": str(test_end),
                "test_days_used": test_days_used,
                "oos_sharpe": round(float(fold_sharpe), 6),
                "oos_max_drawdown": round(float(fold_maxdd), 6),
            }
        )

    sharpes = [float(f["oos_sharpe"]) for f in fold_results]
    maxdds = [float(f["oos_max_drawdown"]) for f in fold_results]
    mean_sharpe = statistics.fmean(sharpes) if sharpes else 0.0
    median_sharpe = statistics.median(sharpes) if sharpes else 0.0
    worst_maxdd = max(maxdds) if maxdds else 0.0
    passed = bool(fold_results) and mean_sharpe > 0.0 and median_sharpe > 0.0 and worst_maxdd <= maxdd_threshold

    return {
        "case_id": case_id,
        "lane": lane,
        "created_at": _now_iso(),
        "strategy_slug": SLUG,
        "symbols": symbols,
        "policy_inputs": {
            "train_days": train_days,
            "test_days": test_days,
            "step_days": step_days,
            "purge_days": purge_days,
            "start_offset": start_offset,
            "warmup_days": warmup_days,
            "fill_delay": fill_delay,
            "rebalance_frequency_days": rebalance_frequency_days,
            "cost_mode": cost_mode,
            "cost_model": {
                "spread_bps": cost_model.spread_bps,
                "flat_slippage_bps": cost_model.flat_slippage_bps,
                "slippage_volatility_factor": cost_model.slippage_volatility_factor,
            },
            "parameter_overrides": parameter_overrides or {},
        },
        "summary": {
            "fold_count": len(fold_results),
            "mean_oos_sharpe": round(float(mean_sharpe), 6),
            "median_oos_sharpe": round(float(median_sharpe), 6),
            "max_oos_drawdown": round(float(worst_maxdd), 6),
        },
        "folds": fold_results,
        "passed": passed,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic robustness validation for spy-regime-starter-v1.")
    parser.add_argument("--slug", default=SLUG, help="Strategy slug; only spy-regime-starter-v1 is supported.")
    parser.add_argument("--data-dir", default="data", help="Base data directory")
    parser.add_argument("--artifacts-dir", default="artifacts/spy-regime-starter-v1/robustness", help="Directory for case artifacts")
    parser.add_argument("--initial-capital", type=float, default=DEFAULT_INITIAL_CAPITAL, help="Initial capital per fold run")
    parser.add_argument("--maxdd-threshold", type=float, default=DEFAULT_MAXDD_THRESHOLD, help="Worst-fold max drawdown threshold")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.slug != SLUG:
        raise SystemExit(f"This runner only supports slug={SLUG}")

    data_dir = Path(args.data_dir)
    strat_dir = strategy_dir(data_dir, args.slug)
    spec = ensure_frozen_spec(strat_dir)
    strategy_name = str(spec.get("strategy_class", spec.get("strategy_name", spec.get("strategy_type", ""))))
    if not strategy_name:
        raise SystemExit("Unable to resolve strategy name from frozen spec.")

    symbols = _resolve_symbols(spec)
    if not symbols:
        raise SystemExit("No symbols resolved from strategy spec.")

    years = int(spec.get("backtest_spec", {}).get("years", 5))
    lookback_days = max(years * 365, 365)

    print(f"Fetching data for {args.slug}: symbols={symbols}, lookback_days={lookback_days}")
    prices_df = fetch_ohlcv(symbols, lookback_days=lookback_days)
    if prices_df.is_empty():
        raise SystemExit("No data fetched for robustness run.")
    indicators_df = compute_indicators(prices_df)

    case_defs = [
        {
            "case_id": "A1",
            "lane": "baseline_reproducibility",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {},
            "is_baseline": True,
        },
        {
            "case_id": "B1",
            "lane": "window_variation",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {},
            "is_baseline": True,
        },
        {
            "case_id": "B2",
            "lane": "window_variation",
            "train_days": 378,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {},
            "is_baseline": False,
        },
        {
            "case_id": "B3",
            "lane": "window_variation",
            "train_days": 756,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {},
            "is_baseline": False,
        },
        {
            "case_id": "B4",
            "lane": "window_variation",
            "train_days": 504,
            "test_days": 42,
            "step_days": 42,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {},
            "is_baseline": False,
        },
        {
            "case_id": "B5",
            "lane": "window_variation",
            "train_days": 504,
            "test_days": 84,
            "step_days": 84,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {},
            "is_baseline": False,
        },
        {
            "case_id": "C1",
            "lane": "boundary_shift",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {},
            "is_baseline": True,
        },
        {
            "case_id": "C2",
            "lane": "boundary_shift",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 21,
            "cost_mode": "baseline",
            "parameter_overrides": {},
            "is_baseline": False,
        },
        {
            "case_id": "C3",
            "lane": "boundary_shift",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 42,
            "cost_mode": "baseline",
            "parameter_overrides": {},
            "is_baseline": False,
        },
        {
            "case_id": "C4",
            "lane": "boundary_shift",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 63,
            "cost_mode": "baseline",
            "parameter_overrides": {},
            "is_baseline": False,
        },
        {
            "case_id": "D1",
            "lane": "cost_stress",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {},
            "is_baseline": True,
        },
        {
            "case_id": "D2",
            "lane": "cost_stress",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "1.5x",
            "parameter_overrides": {},
            "is_baseline": False,
        },
        {
            "case_id": "D3",
            "lane": "cost_stress",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "2.0x",
            "parameter_overrides": {},
            "is_baseline": False,
        },
        {
            "case_id": "D4",
            "lane": "cost_stress",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "spread_only",
            "parameter_overrides": {},
            "is_baseline": False,
        },
        {
            "case_id": "D5",
            "lane": "cost_stress",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "slippage_heavy",
            "parameter_overrides": {},
            "is_baseline": False,
        },
        {
            "case_id": "E1",
            "lane": "parameter_perturbation",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {"rsi_entry_threshold": 54.0},
            "is_baseline": False,
        },
        {
            "case_id": "E2",
            "lane": "parameter_perturbation",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {"rsi_entry_threshold": 56.0},
            "is_baseline": False,
        },
        {
            "case_id": "E3",
            "lane": "parameter_perturbation",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {"rsi_exit_threshold": 39.0},
            "is_baseline": False,
        },
        {
            "case_id": "E4",
            "lane": "parameter_perturbation",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {"rsi_exit_threshold": 41.0},
            "is_baseline": False,
        },
        {
            "case_id": "E5",
            "lane": "parameter_perturbation",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {"vix_entry_max": 18.7},
            "is_baseline": False,
        },
        {
            "case_id": "E6",
            "lane": "parameter_perturbation",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {"vix_entry_max": 19.7},
            "is_baseline": False,
        },
        {
            "case_id": "E7",
            "lane": "parameter_perturbation",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {"vix_add_max": 15.9},
            "is_baseline": False,
        },
        {
            "case_id": "E8",
            "lane": "parameter_perturbation",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {"vix_add_max": 16.9},
            "is_baseline": False,
        },
        {
            "case_id": "E9",
            "lane": "parameter_perturbation",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {"vix_exit_min": 24.5},
            "is_baseline": False,
        },
        {
            "case_id": "E10",
            "lane": "parameter_perturbation",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {"vix_exit_min": 25.5},
            "is_baseline": False,
        },
        {
            "case_id": "E11",
            "lane": "parameter_perturbation",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {"macd_exit_max": -0.25},
            "is_baseline": False,
        },
        {
            "case_id": "E12",
            "lane": "parameter_perturbation",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {"macd_exit_max": -0.15},
            "is_baseline": False,
        },
        {
            "case_id": "E13",
            "lane": "parameter_perturbation",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {"atr_stop_multiple": 1.50},
            "is_baseline": False,
        },
        {
            "case_id": "E14",
            "lane": "parameter_perturbation",
            "train_days": 504,
            "test_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "start_offset": 0,
            "cost_mode": "baseline",
            "parameter_overrides": {"atr_stop_multiple": 2.00},
            "is_baseline": False,
        },
    ]

    artifacts_dir = Path(args.artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    case_results: list[dict[str, Any]] = []
    for case in case_defs:
        result = _run_case(
            case_id=case["case_id"],
            lane=case["lane"],
            spec=spec,
            strategy_name=strategy_name,
            prices_df=prices_df,
            indicators_df=indicators_df,
            train_days=case["train_days"],
            test_days=case["test_days"],
            step_days=case["step_days"],
            purge_days=case["purge_days"],
            start_offset=case["start_offset"],
            cost_mode=case["cost_mode"],
            parameter_overrides=case["parameter_overrides"],
            initial_capital=float(args.initial_capital),
            maxdd_threshold=float(args.maxdd_threshold),
        )
        result["is_baseline"] = bool(case["is_baseline"])
        case_results.append(result)

        case_path = artifacts_dir / f"{case['case_id'].lower()}.yaml"
        with case_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(result, f, sort_keys=False)
        print(
            f"{case['case_id']} {case['lane']}: "
            f"mean={result['summary']['mean_oos_sharpe']:.3f} "
            f"median={result['summary']['median_oos_sharpe']:.3f} "
            f"maxdd={result['summary']['max_oos_drawdown']:.3f} "
            f"passed={result['passed']}"
        )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in case_results:
        grouped.setdefault(result["lane"], []).append(result)

    lane_summaries: dict[str, Any] = {}
    for lane_name, lane_results in grouped.items():
        lane_summaries[lane_name] = {
            "status": _lane_status(lane_results),
            "case_count": len(lane_results),
            "passed_cases": sum(1 for r in lane_results if r["passed"]),
            "failed_cases": sum(1 for r in lane_results if not r["passed"]),
            "cases": [
                {
                    "case_id": r["case_id"],
                    "passed": r["passed"],
                    "mean_oos_sharpe": r["summary"]["mean_oos_sharpe"],
                    "median_oos_sharpe": r["summary"]["median_oos_sharpe"],
                    "max_oos_drawdown": r["summary"]["max_oos_drawdown"],
                }
                for r in lane_results
            ],
        }

    perturbation_cases = grouped.get("parameter_perturbation", [])
    parameter_stability = (
        sum(1 for r in perturbation_cases if r["passed"]) / len(perturbation_cases)
        if perturbation_cases
        else 0.0
    )

    cost_cases = {r["case_id"]: r for r in grouped.get("cost_stress", [])}
    overall_passed = (
        lane_summaries.get("baseline_reproducibility", {}).get("status") == "pass"
        and lane_summaries.get("window_variation", {}).get("status") != "fail"
        and lane_summaries.get("boundary_shift", {}).get("status") != "fail"
        and cost_cases.get("D3", {}).get("passed", False)
        and parameter_stability > 0.5
    )

    output = {
        "strategy_slug": SLUG,
        "created_at": _now_iso(),
        "runner": "scripts/run_spy_regime_starter_v1_robustness.py",
        "runner_identity": {
            "script": "scripts/run_spy_regime_starter_v1_robustness.py",
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "python_executable": sys.executable,
        },
        "frozen_spec_hash": spec.get("frozen_hash", ""),
        "provenance": {
            "created_at": _now_iso(),
            "strategy_slug": SLUG,
            "strategy_name": strategy_name,
            "spec_frozen": bool(spec.get("frozen", False)),
            "spec_frozen_at": spec.get("frozen_at"),
            "frozen_spec_hash": spec.get("frozen_hash", ""),
            "data_dir": str(data_dir),
            "symbols": symbols,
            "lookback_days": lookback_days,
            "artifact_dir": str(artifacts_dir),
        },
        "matrix_reference": "artifacts/spy-regime-starter-v1-robustness-matrix.md",
        "promotion_checklist_reference": "artifacts/spy-regime-starter-v1-promotion-checklist.md",
        "lane_summaries": lane_summaries,
        "parameter_stability": {
            "stable_fraction": round(float(parameter_stability), 6),
            "threshold": "> 0.50",
            "result": "PASS" if parameter_stability > 0.5 else "FAIL",
        },
        "two_x_cost_survival": {
            "case_id": "D3",
            "passed": bool(cost_cases.get("D3", {}).get("passed", False)),
        },
        "overall_result": "PASS" if overall_passed else "HOLD",
        "overall_passed": overall_passed,
        "case_count": len(case_results),
        "cases": [
            {
                "case_id": r["case_id"],
                "lane": r["lane"],
                "artifact_path": str(artifacts_dir / f"{r['case_id'].lower()}.yaml"),
                "passed": r["passed"],
            }
            for r in case_results
        ],
        "promotion_notes": (
            "PASS: robustness lanes acceptable for research-to-paper review."
            if overall_passed
            else "HOLD: robustness evidence remains insufficient for shadow/paper promotion."
        ),
    }

    output_path = strat_dir / "robustness.yaml"
    with output_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(output, f, sort_keys=False)

    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
