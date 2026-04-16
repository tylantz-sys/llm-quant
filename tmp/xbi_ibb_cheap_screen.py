#!/usr/bin/env python3
from __future__ import annotations

import math
import sys

sys.path.insert(0, "src")

from llm_quant.backtest.engine import BacktestEngine, CostModel
from llm_quant.backtest.strategies import create_strategy
from llm_quant.backtest.strategy import StrategyConfig
from llm_quant.data.fetcher import fetch_ohlcv
from llm_quant.data.indicators import compute_indicators

SLUG = "xbi-ibb-lead-lag-cheap-screen"
SYMBOLS = ["XBI", "IBB"]
BASE_PARAMS = {
    "leader_symbol": "XBI",
    "follower_symbol": "IBB",
    "lag_days": 1,
    "signal_window": 1,
    "entry_threshold": 0.02,
    "exit_threshold": -0.005,
    "target_weight": 0.90,
    "rebalance_frequency_days": 1,
}

prices_df = fetch_ohlcv(SYMBOLS, lookback_days=5 * 365 + 30)
indicators_df = compute_indicators(prices_df)


def run_single(params: dict[str, float | int | str]) -> dict[str, float]:
    config = StrategyConfig(
        name="lead_lag",
        rebalance_frequency_days=1,
        max_positions=1,
        target_position_weight=0.90,
        stop_loss_pct=0.05,
        parameters=dict(params),
    )
    strategy = create_strategy("lead_lag", config)
    engine = BacktestEngine(strategy, initial_capital=100000.0)
    result = engine.run(
        prices_df=prices_df,
        indicators_df=indicators_df,
        slug=SLUG,
        cost_model=CostModel(),
        warmup_days=30,
        cost_multiplier=1.0,
    )
    m = result.metrics.get("1.0x")
    daily_returns = result.daily_returns or []
    time_in_market = sum(1 for r in daily_returns if abs(r) > 1e-12) / max(len(daily_returns), 1)
    return {
        "sharpe": float(m.sharpe_ratio if m else 0.0),
        "max_dd": float(m.max_drawdown if m else 0.0),
        "trades": float(m.total_trades if m else 0.0),
        "time_in_market": float(time_in_market),
    }


def fmt(name: str, r: dict[str, float], base_sharpe: float | None = None) -> str:
    delta = ""
    if base_sharpe is not None:
        pct = (r["sharpe"] - base_sharpe) / (abs(base_sharpe) + 1e-8) * 100.0
        delta = f", delta_vs_base={pct:+.1f}%"
    return (
        f"{name}: sharpe={r['sharpe']:.4f}, max_dd={r['max_dd']:.4f}, "
        f"trades={int(r['trades'])}, time_in_market={r['time_in_market']:.2%}{delta}"
    )


base = run_single(BASE_PARAMS)
print("XBI -> IBB cheap exploratory screen")
print(fmt("base", base))

perturbations = [
    ("lag_days=2", {**BASE_PARAMS, "lag_days": 2}),
    ("signal_window=2", {**BASE_PARAMS, "signal_window": 2}),
    ("entry_threshold=0.01", {**BASE_PARAMS, "entry_threshold": 0.01}),
]

print("perturbations:")
for name, params in perturbations:
    r = run_single(params)
    print(fmt(name, r, base["sharpe"]))

passes = (
    base["sharpe"] > 0.6
    and base["trades"] > 25
    and base["max_dd"] < 0.20
    and base["time_in_market"] < 0.80
)

stability_ok = True
for _, params in perturbations:
    r = run_single(params)
    if base["sharpe"] != 0.0:
        if abs((r["sharpe"] - base["sharpe"]) / base["sharpe"]) > 1.0:
            stability_ok = False

print(
    f"screen_result={'PASS' if passes and stability_ok else 'FAIL'} "
    f"(base_pass={passes}, stability_pass={stability_ok})"
)
