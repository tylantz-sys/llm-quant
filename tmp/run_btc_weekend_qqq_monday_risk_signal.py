#!/usr/bin/env python3
"""Cheap backtest for btc-weekend-qqq-monday-risk-signal.

This is an exploratory runner aligned to the pre-backtest research spec:
- signal source: completed BTC weekend return
- follower: QQQ
- hold only for the first tradable Monday session after the measured weekend
- no same-bar execution assumptions
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, "src")

from llm_quant.backtest.engine import BacktestEngine, CostModel
from llm_quant.backtest.strategies import create_strategy
from llm_quant.backtest.strategy import StrategyConfig
from llm_quant.data.fetcher import fetch_ohlcv
from llm_quant.data.indicators import compute_indicators

SLUG = "btc-weekend-qqq-monday-risk-signal"
STRATEGY = "lead_lag"
SYMBOLS = ["BTC-USD", "QQQ"]
BASE_PARAMS = {
    "leader_symbol": "BTC-USD",
    "follower_symbol": "QQQ",
    "lag_days": 1,
    "signal_window": 3,
    "entry_threshold": 0.01,
    "exit_threshold": -999.0,
    "target_weight": 1.0,
    "rebalance_frequency_days": 1,
}
LOOKBACK_DAYS = 5 * 365 + 30

prices_df = fetch_ohlcv(SYMBOLS, lookback_days=LOOKBACK_DAYS)
indicators_df = compute_indicators(prices_df)


def _filter_to_mondays(df: pl.DataFrame) -> pl.DataFrame:
    if "date" not in df.columns:
        return df
    return df.with_columns(pl.col("date").dt.weekday().alias("weekday")).filter(
        pl.col("weekday") == 1
    ).drop("weekday")


monday_prices_df = _filter_to_mondays(prices_df)
monday_indicators_df = _filter_to_mondays(indicators_df)


def run_single(
    params: dict[str, float | int | str],
) -> tuple[dict[str, float], list[float]]:
    config = StrategyConfig(
        name=STRATEGY,
        rebalance_frequency_days=1,
        max_positions=1,
        target_position_weight=float(params.get("target_weight", 1.0)),
        stop_loss_pct=0.20,
        parameters=dict(params),
    )
    strategy = create_strategy(STRATEGY, config)
    engine = BacktestEngine(strategy, initial_capital=100000.0)
    result = engine.run(
        prices_df=monday_prices_df,
        indicators_df=monday_indicators_df,
        slug=SLUG,
        cost_model=CostModel(),
        warmup_days=10,
        fill_delay=1,
        cost_multiplier=1.0,
    )
    m = result.metrics.get("1.0x")
    daily_returns = result.daily_returns or []
    return (
        {
            "sharpe": float(m.sharpe_ratio if m else 0.0),
            "max_dd": float(m.max_drawdown if m else 0.0),
            "total_return": float(m.total_return if m else 0.0),
            "trades": float(m.total_trades if m else 0.0),
        },
        daily_returns,
    )


def cpcv_sharpe(returns: list[float], n_groups: int = 6, k: int = 2, purge: int = 1) -> tuple[float, float]:
    from itertools import combinations

    n = len(returns)
    if n < n_groups:
        return 0.0, 0.0
    group_size = n // n_groups
    oos_sharpes: list[float] = []
    for test_idx in combinations(range(n_groups), k):
        test_rets: list[float] = []
        for i in test_idx:
            s, e = i * group_size + purge, (i + 1) * group_size - purge
            if s < e:
                test_rets.extend(returns[s:e])
        if len(test_rets) < 8:
            continue
        mean = sum(test_rets) / len(test_rets)
        std = (sum((r - mean) ** 2 for r in test_rets) / len(test_rets)) ** 0.5
        if std > 0:
            oos_sharpes.append(mean / std * math.sqrt(52))
    if not oos_sharpes:
        return 0.0, 0.0
    m = sum(oos_sharpes) / len(oos_sharpes)
    s = (sum((x - m) ** 2 for x in oos_sharpes) / len(oos_sharpes)) ** 0.5
    return m, s


base, base_returns = run_single(BASE_PARAMS)
cpcv_mean, cpcv_std = cpcv_sharpe(base_returns)

print("BTC weekend -> QQQ Monday cheap test")
print(
    f"base: sharpe={base['sharpe']:.4f}, max_dd={base['max_dd']:.4f}, "
    f"total_return={base['total_return']:.4f}, trades={int(base['trades'])}"
)
print(f"cpcv: mean={cpcv_mean:.4f} std={cpcv_std:.4f}")

perturbations = [
    ("entry_threshold=0.00", {**BASE_PARAMS, "entry_threshold": 0.00}),
    ("entry_threshold=0.02", {**BASE_PARAMS, "entry_threshold": 0.02}),
    ("signal_window=2", {**BASE_PARAMS, "signal_window": 2}),
    ("signal_window=4", {**BASE_PARAMS, "signal_window": 4}),
]

print("perturbations:")
for name, params in perturbations:
    r, _ = run_single(params)
    pct = (r["sharpe"] - base["sharpe"]) / (abs(base["sharpe"]) + 1e-8) * 100.0
    stable = "STABLE" if abs(pct) <= 50 else "UNSTABLE"
    print(
        f"  {name}: sharpe={r['sharpe']:.4f}, max_dd={r['max_dd']:.4f}, "
        f"trades={int(r['trades'])}, delta_vs_base={pct:+.1f}% {stable}"
    )
