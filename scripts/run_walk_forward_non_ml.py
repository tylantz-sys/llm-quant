"""Run fixed-split walk-forward validation for non-ML strategies.

Default split policy (pre-registered):
- train: 24 months (~504 trading days)
- test: 3 months (~63 trading days)
- step: 3 months (~63 trading days)
- purge: 5 trading days
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl
import yaml

# Ensure src/ is importable when run as a script from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from llm_quant.backtest.artifacts import ensure_frozen_spec, strategy_dir
from llm_quant.backtest.engine import BacktestEngine, CostModel
from llm_quant.backtest.metrics import compute_max_drawdown, compute_sharpe
from llm_quant.backtest.strategies import create_strategy
from llm_quant.backtest.strategy import StrategyConfig
from llm_quant.data.fetcher import fetch_ohlcv
from llm_quant.data.indicators import compute_indicators


def _resolve_symbols(spec: dict[str, Any]) -> list[str]:
    params = spec.get("parameters", {}) or {}
    configured = spec.get("backtest_spec", {}).get("symbols", [])
    if isinstance(configured, list) and configured:
        return [str(s) for s in configured]
    symbols: list[str] = []
    for key in ("symbol", "leader_symbol", "follower_symbol", "symbol_a", "symbol_b"):
        value = params.get(key)
        if value:
            symbols.append(str(value))
    raw_symbols = params.get("symbols")
    if isinstance(raw_symbols, list):
        symbols.extend(str(s) for s in raw_symbols)
    return sorted({s for s in symbols if s})


def _build_strategy_config(spec: dict[str, Any], strategy_name: str) -> StrategyConfig:
    params = dict(spec.get("parameters", {}) or {})
    if "rebalance_frequency_days" in params and "rebalance_frequency" not in params:
        params["rebalance_frequency"] = params["rebalance_frequency_days"]
    return StrategyConfig(
        name=spec.get("strategy_slug", strategy_name),
        rebalance_frequency_days=int(params.get("rebalance_frequency_days", 1)),
        max_positions=10,
        target_position_weight=float(params.get("target_weight", 0.25)),
        stop_loss_pct=0.10,
        parameters=params,
    )


def build_windows(
    trading_dates: list[date],
    *,
    train_days: int = 24 * 21,
    test_days: int = 3 * 21,
    step_days: int = 3 * 21,
    purge_days: int = 5,
) -> list[dict[str, date]]:
    """Build deterministic rolling windows from a sorted trading-date list."""
    windows: list[dict[str, date]] = []
    if len(trading_dates) < train_days + purge_days + test_days:
        return windows

    start_idx = 0
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


def _compute_test_nav_series(
    snapshots: list[Any], test_start: date, test_end: date
) -> list[float]:
    navs: list[float] = []
    for snap in snapshots:
        snap_date = snap.date
        if test_start <= snap_date <= test_end:
            navs.append(float(snap.nav))
    return navs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run rolling walk-forward validation for a frozen non-ML strategy."
    )
    parser.add_argument("--slug", required=True, help="Strategy slug")
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Data directory containing strategies/<slug> artifacts",
    )
    parser.add_argument(
        "--train-days",
        type=int,
        default=24 * 21,
        help="Training window in trading days (default: 504)",
    )
    parser.add_argument(
        "--test-days",
        type=int,
        default=3 * 21,
        help="Test window in trading days (default: 63)",
    )
    parser.add_argument(
        "--step-days",
        type=int,
        default=3 * 21,
        help="Step size in trading days (default: 63)",
    )
    parser.add_argument(
        "--purge-days",
        type=int,
        default=5,
        help="Purge buffer between train/test in trading days",
    )
    parser.add_argument(
        "--maxdd-threshold",
        type=float,
        default=0.25,
        help="Pass threshold for worst fold max drawdown",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    strat_dir = strategy_dir(data_dir, args.slug)
    spec = ensure_frozen_spec(strat_dir)
    strategy_name = str(spec.get("strategy_type", "pairs_ratio"))
    symbols = _resolve_symbols(spec)
    if not symbols:
        msg = "No symbols resolved from strategy spec."
        raise SystemExit(msg)

    years = int(spec.get("backtest_spec", {}).get("years", 5))
    lookback_days = max(years * 365, 365)
    warmup_days = int(spec.get("backtest_spec", {}).get("warmup_days", 30))

    print(
        f"Fetching data for {args.slug}: symbols={symbols}, lookback_days={lookback_days}"
    )
    prices_df = fetch_ohlcv(symbols, lookback_days=lookback_days)
    if prices_df.is_empty():
        msg = "No data fetched for walk-forward run."
        raise SystemExit(msg)

    indicators_df = compute_indicators(prices_df)
    trading_dates = sorted(prices_df.select("date").unique().to_series().to_list())
    windows = build_windows(
        trading_dates,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
        purge_days=args.purge_days,
    )
    if not windows:
        msg = "Not enough data to construct walk-forward splits."
        raise SystemExit(msg)

    config = _build_strategy_config(spec, strategy_name)
    strategy = create_strategy(strategy_name, config)
    engine = BacktestEngine(strategy, initial_capital=100_000.0)
    cost_model = CostModel.from_spec(spec)

    fold_results: list[dict[str, Any]] = []
    for i, window in enumerate(windows, start=1):
        train_start = window["train_start"]
        test_end = window["test_end"]
        test_start = window["test_start"]

        fold_prices = prices_df.filter(
            (pl.col("date") >= train_start) & (pl.col("date") <= test_end)
        )
        fold_indicators = indicators_df.filter(
            (pl.col("date") >= train_start) & (pl.col("date") <= test_end)
        )
        result = engine.run(
            prices_df=fold_prices,
            indicators_df=fold_indicators,
            slug=f"{args.slug}-wf-fold-{i}",
            cost_model=cost_model,
            fill_delay=1,
            warmup_days=warmup_days,
            cost_multiplier=1.0,
            trial_count=1,
        )

        nav_series = _compute_test_nav_series(result.snapshots, test_start, test_end)
        if len(nav_series) < 2:
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
                "train_start": str(window["train_start"]),
                "train_end": str(window["train_end"]),
                "test_start": str(window["test_start"]),
                "test_end": str(window["test_end"]),
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
    passed = (
        len(fold_results) > 0
        and mean_sharpe > 0.0
        and median_sharpe > 0.0
        and worst_maxdd <= float(args.maxdd_threshold)
    )

    payload = {
        "strategy_slug": args.slug,
        "created_at": datetime.now(tz=UTC).replace(microsecond=0).isoformat(),
        "runner": "run_walk_forward_non_ml.py",
        "policy": {
            "train_days": int(args.train_days),
            "test_days": int(args.test_days),
            "step_days": int(args.step_days),
            "purge_days": int(args.purge_days),
            "pass_criteria": {
                "mean_oos_sharpe_gt": 0.0,
                "median_oos_sharpe_gt": 0.0,
                "max_drawdown_lte": float(args.maxdd_threshold),
            },
        },
        "summary": {
            "fold_count": len(fold_results),
            "mean_oos_sharpe": round(float(mean_sharpe), 6),
            "median_oos_sharpe": round(float(median_sharpe), 6),
            "max_oos_drawdown": round(float(worst_maxdd), 6),
        },
        "folds": fold_results,
        "passed": bool(passed),
    }

    out_path = strat_dir / "walk-forward.yaml"
    with out_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False)

    print(
        "Walk-forward complete: "
        f"folds={len(fold_results)} mean={mean_sharpe:.3f} "
        f"median={median_sharpe:.3f} maxdd={worst_maxdd:.3f} passed={passed}"
    )
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
