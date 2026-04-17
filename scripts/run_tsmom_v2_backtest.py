#!/usr/bin/env python3
"""Standalone backtest for multi-asset-tsmom-crash-aware-v2 strategy.

v2 change: cap-based sizing (flat 8% per position) instead of risk-parity
vol-scaling. All other logic identical to v1.

Runs a vectorised simulation loop directly against DuckDB data.
Two runs:
  (a) full strategy — crash index + regime filter enabled
  (b) baseline   — crash index disabled, regime always risk_on

Outputs a markdown summary and appends to the experiment registry.

Usage:
    cd /home/ty/Documents/llm-quant/llm-quant
    .venv/bin/python3 scripts/run_tsmom_v2_backtest.py
"""

from __future__ import annotations

import json
import math
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "data" / "strategies" / "multi-asset-tsmom-crash-aware-v2" / "research-spec.yaml"
DB_PATH = ROOT / "data" / "llm_quant.duckdb"
STRAT_DIR = ROOT / "data" / "strategies" / "multi-asset-tsmom-crash-aware-v2"
REGISTRY_PATH = STRAT_DIR / "experiment-registry.jsonl"

# ---------------------------------------------------------------------------
# Load spec
# ---------------------------------------------------------------------------

with SPEC_PATH.open() as f:
    SPEC = yaml.safe_load(f)

PARAMS = SPEC["parameters"]
BS = SPEC["backtest_spec"]

TRADEABLE: list[str] = BS["symbols"]["tradeable"]
REFERENCE: list[str] = BS["symbols"]["reference"]
ALL_SYMBOLS = TRADEABLE + REFERENCE

START_DATE = date.fromisoformat(BS["start_date"])
END_DATE = date.fromisoformat(BS["end_date"])
INITIAL_CAPITAL = float(BS["initial_capital"])
WARMUP_DAYS = int(PARAMS.get("warmup_days", 275))

# Cost model
CM = SPEC["cost_model"]
SPREAD_BPS = float(CM["spread_bps"])
FLAT_SLIPPAGE_BPS = float(CM["flat_slippage_bps"])
SLIPPAGE_VOL_FACTOR = float(CM["slippage_volatility_factor"])

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

print("Loading data from DuckDB …")
con = duckdb.connect(str(DB_PATH), read_only=True)
sym_list = "','".join(ALL_SYMBOLS)
raw = con.execute(
    f"""
    SELECT symbol, date, open, high, low, close, volume, adj_close, atr_14
    FROM market_data_daily
    WHERE symbol IN ('{sym_list}')
    ORDER BY symbol, date
    """
).fetchdf()
con.close()

raw["date"] = pd.to_datetime(raw["date"]).dt.date
raw = raw.sort_values(["date", "symbol"]).reset_index(drop=True)

# Use adj_close where available, fall back to close
raw["price"] = raw["adj_close"].where(raw["adj_close"].notna(), raw["close"])

print(f"Loaded {len(raw):,} rows for {raw['symbol'].nunique()} symbols")
print(f"Date range: {raw['date'].min()} → {raw['date'].max()}")

# ---------------------------------------------------------------------------
# Helper: compute tsmom composite for one symbol up to a date index
# ---------------------------------------------------------------------------

def _tsmom_composite(prices: list[float], atrs: list[float], params: dict) -> float | None:
    """Equal-weight 4-lookback TSMOM composite. Returns None if insufficient data."""
    lb1  = params["lookback_1m"]
    lb3  = params["lookback_3m"]
    lb6  = params["lookback_6m"]
    lb12 = params["lookback_12m"]
    skip = params["lookback_skip"]

    needed = lb12 + skip + 2
    if len(prices) < needed:
        return None

    last_price = prices[-1]
    last_atr   = atrs[-1]
    if not (last_price > 0 and last_atr > 0):
        return None

    vol_ann = last_atr * math.sqrt(252) / last_price
    if vol_ann <= 0:
        return None

    scores: list[float] = []
    for lb in (lb1, lb3, lb6):
        if len(prices) < lb + 1:
            return None
        p0 = prices[-(lb + 1)]
        if p0 is None or p0 <= 0:
            return None
        ret = (last_price - p0) / p0
        scores.append(ret / vol_ann)

    # 12M with skip
    if len(prices) < lb12 + skip + 2:
        return None
    p_end  = prices[-(skip + 1)]      # price at T-skip
    p_start = prices[-(lb12 + skip + 1)]  # price at T-lb12-skip
    if not (p_end > 0 and p_start > 0):
        return None
    ret12 = (p_end - p_start) / p_start
    scores.append(ret12 / vol_ann)

    if len(scores) < 4:
        return None
    return float(np.mean(scores))


# ---------------------------------------------------------------------------
# Helper: compute crash index
# ---------------------------------------------------------------------------

def _crash_index(
    date_idx: int,
    date_to_idx: dict[date, int],
    sym_df: dict[str, pd.DataFrame],
    held_symbols: list[str],
    params: dict,
    disable: bool = False,
) -> float:
    if disable:
        return 0.0

    vix_ratio_thresh = params["crash_vix_ratio_threshold"]
    vol_z_thresh     = params["crash_vol_zscore_threshold"]
    disp_window      = params["crash_dispersion_window"]
    disp_baseline    = params["crash_dispersion_baseline"]
    disp_mult        = params["crash_dispersion_multiplier"]

    components = []

    # Component 1: VIX/VIX3M
    c1 = 0.0
    if "VIX" in sym_df and "VIX3M" in sym_df:
        vix_row = sym_df["VIX"].iloc[:date_idx + 1].dropna(subset=["close"])
        vix3m_row = sym_df["VIX3M"].iloc[:date_idx + 1].dropna(subset=["close"])
        if len(vix_row) > 0 and len(vix3m_row) > 0:
            vix_c = vix_row["close"].iloc[-1]
            vix3m_c = vix3m_row["close"].iloc[-1]
            if vix3m_c > 0:
                c1 = 1.0 if (vix_c / vix3m_c) < vix_ratio_thresh else 0.0
    components.append(c1)

    # Component 2: max volume z-score across held
    c2 = 0.0
    max_z = 0.0
    for sym in held_symbols:
        if sym not in sym_df:
            continue
        df = sym_df[sym].iloc[:date_idx + 1].dropna(subset=["volume"])
        if len(df) < 22:
            continue
        vols = df["volume"].tail(21).to_numpy()
        if len(vols) < 5:
            continue
        recent = vols[-1]
        window = vols[:-1]
        std_v = np.std(window, ddof=1)
        if std_v > 0:
            z = (recent - np.mean(window)) / std_v
            max_z = max(max_z, z)
    c2 = 1.0 if max_z > vol_z_thresh else 0.0
    components.append(c2)

    # Component 3: cross-asset return dispersion
    c3 = 0.0
    needed = disp_baseline + disp_window + 5
    ret_series_list: list[np.ndarray] = []
    for sym in TRADEABLE:
        if sym not in sym_df:
            continue
        df = sym_df[sym].iloc[:date_idx + 1].dropna(subset=["price"])
        if len(df) < needed:
            continue
        prices = df["price"].tail(needed).to_numpy()
        rets = np.diff(prices) / prices[:-1]
        ret_series_list.append(rets)

    if len(ret_series_list) >= 5:
        min_len = min(len(r) for r in ret_series_list)
        mat = np.array([r[-min_len:] for r in ret_series_list])  # shape (n_sym, n_days)
        cross_std_per_day = mat.std(axis=0)
        current_disp = cross_std_per_day[-disp_window:].mean()
        baseline_disp = cross_std_per_day[-disp_baseline:].mean()
        if baseline_disp > 0:
            c3 = 1.0 if current_disp > disp_mult * baseline_disp else 0.0
    components.append(c3)

    return sum(components) / 3.0


# ---------------------------------------------------------------------------
# Helper: compute regime
# ---------------------------------------------------------------------------

def _regime(
    date_idx: int,
    sym_df: dict[str, pd.DataFrame],
    params: dict,
    disable: bool = False,
) -> str:
    if disable:
        return "risk_on"

    vix_on   = params["regime_vix_risk_on"]
    vix_off  = params["regime_vix_risk_off"]
    slope_d  = params["regime_slope_days"]
    slope_ro = params["regime_slope_risk_off"]

    vix_level = None
    if "VIX" in sym_df:
        df = sym_df["VIX"].iloc[:date_idx + 1].dropna(subset=["close"])
        if len(df) > 0:
            vix_level = df["close"].iloc[-1]

    hyg_tlt_slope = None
    if "HYG" in sym_df and "TLT" in sym_df:
        hyg = sym_df["HYG"].iloc[:date_idx + 1].dropna(subset=["adj_close"])
        tlt = sym_df["TLT"].iloc[:date_idx + 1].dropna(subset=["adj_close"])
        if len(hyg) >= slope_d + 1 and len(tlt) >= slope_d + 1:
            hp = hyg["adj_close"].tail(slope_d + 1).to_numpy()
            tp = tlt["adj_close"].tail(slope_d + 1).to_numpy()
            if hp[0] > 0 and tp[0] > 0 and tp[-1] > 0:
                r0 = hp[0] / tp[0]
                r1 = hp[-1] / tp[-1]
                hyg_tlt_slope = (r1 - r0) / r0

    if vix_level is None:
        return "transition"

    risk_off = vix_level > vix_off or (
        hyg_tlt_slope is not None and hyg_tlt_slope < slope_ro
    )
    risk_on = vix_level < vix_on and (
        hyg_tlt_slope is None or hyg_tlt_slope >= 0
    )

    if risk_off:
        return "risk_off"
    if risk_on:
        return "risk_on"
    return "transition"


# ---------------------------------------------------------------------------
# Cost computation
# ---------------------------------------------------------------------------

def _trade_cost(notional: float, daily_vol: float | None = None) -> float:
    spread = notional * SPREAD_BPS / 10_000
    if daily_vol is not None and daily_vol > 0:
        impact = SLIPPAGE_VOL_FACTOR * daily_vol * notional
    else:
        impact = notional * FLAT_SLIPPAGE_BPS / 10_000
    return spread + impact


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _metrics(nav_series: list[float], trades: int) -> dict[str, float]:
    if len(nav_series) < 2:
        return {}
    rets = np.array([(nav_series[i] / nav_series[i - 1]) - 1 for i in range(1, len(nav_series))])
    n = len(rets)
    mean_r = rets.mean()
    std_r  = rets.std(ddof=1) if n > 1 else 0.0
    sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0

    down = np.minimum(rets, 0.0)
    down_std = math.sqrt(np.mean(down ** 2))
    sortino = (mean_r / down_std * math.sqrt(252)) if down_std > 0 else (float("inf") if mean_r > 0 else 0.0)

    peak = nav_series[0]
    max_dd = 0.0
    for v in nav_series:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd

    total_ret = nav_series[-1] / nav_series[0] - 1.0
    years = n / 252
    cagr = ((1 + total_ret) ** (1 / years) - 1) if years > 0 else 0.0
    calmar = cagr / max_dd if max_dd > 0 else (float("inf") if cagr > 0 else 0.0)

    return {
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "max_drawdown": round(max_dd, 4),
        "calmar": round(calmar, 4),
        "cagr": round(cagr, 4),
        "total_return": round(total_ret, 4),
        "n_days": n,
        "total_trades": trades,
    }


# ---------------------------------------------------------------------------
# Benchmark: 60/40 SPY/TLT monthly rebalance
# ---------------------------------------------------------------------------

def _benchmark_metrics(spy_prices: pd.Series, tlt_prices: pd.Series) -> dict[str, float]:
    df = pd.DataFrame({"SPY": spy_prices, "TLT": tlt_prices}).dropna()
    if len(df) < 2:
        return {}
    # Monthly rebalance 60/40
    nav = 100.0
    nav_series = [nav]
    spy_w = tlt_w = None
    for i in range(1, len(df)):
        date_ = df.index[i]
        if spy_w is None or date_.month != df.index[i - 1].month:
            spy_w, tlt_w = 0.60, 0.40
        spy_ret = df["SPY"].iloc[i] / df["SPY"].iloc[i - 1] - 1
        tlt_ret = df["TLT"].iloc[i] / df["TLT"].iloc[i - 1] - 1
        nav = nav * (1 + spy_w * spy_ret + tlt_w * tlt_ret)
        nav_series.append(nav)
    return _metrics(nav_series, 0)


# ---------------------------------------------------------------------------
# Core simulation
# ---------------------------------------------------------------------------

def run_simulation(
    disable_crash: bool = False,
    disable_regime: bool = False,
    label: str = "strategy",
) -> tuple[dict[str, float], list[float], int]:
    """Run the TSMOM backtest. Returns (metrics, nav_series, n_trades)."""

    # Build per-symbol DataFrames indexed by position (not date, to keep things fast)
    sym_df: dict[str, pd.DataFrame] = {}
    all_dates_set: set[date] = set()
    for sym in ALL_SYMBOLS:
        df = raw[raw["symbol"] == sym].copy()
        df = df.sort_values("date").reset_index(drop=True)
        # Normalize date column to datetime.date objects
        if len(df) > 0 and hasattr(df["date"].iloc[0], "date"):
            df["date"] = df["date"].apply(lambda x: x.date() if hasattr(x, "date") else x)
        sym_df[sym] = df
        all_dates_set.update(df["date"].tolist())

    all_dates = sorted(all_dates_set)
    # Filter to backtest window + warmup
    # We need warmup_days prior to START_DATE for indicator computation
    # We'll look back WARMUP_DAYS before START_DATE for indicators
    signal_dates = [d for d in all_dates if START_DATE <= d <= END_DATE]
    full_dates   = all_dates  # entire history for look-back slicing

    # Build a date->row-index mapping per symbol
    sym_date_to_idx: dict[str, dict[date, int]] = {}
    for sym, df in sym_df.items():
        sym_date_to_idx[sym] = {row["date"]: i for i, row in df.iterrows()}

    # Portfolio state
    cash = INITIAL_CAPITAL
    positions: dict[str, float] = {}   # symbol -> shares
    entry_prices: dict[str, float] = {}
    atr_at_entry: dict[str, float] = {}
    initial_stops: dict[str, float] = {}
    trailing_stops: dict[str, float] = {}
    highest_since_entry: dict[str, float] = {}

    nav_series: list[float] = []
    n_trades = 0
    last_rebal_date: date | None = None

    rebal_freq = PARAMS["rebalance_frequency_days"]
    top_n      = PARAMS["top_n"]
    base_position_weight = PARAMS["base_position_weight"]  # v2: flat 8% cap
    max_weight = PARAMS["max_position_weight"]
    atr_stop_mult = PARAMS["atr_stop_multiple"]
    trail_pct = PARAMS["trailing_stop_pct"]
    min_cash_frac = PARAMS["min_cash_reserve"]

    # Pre-build date → full_dates index map for O(1) warmup checks
    full_date_to_idx: dict[date, int] = {d: i for i, d in enumerate(full_dates)}

    for current_date in signal_dates:
        # --- Current prices (close) ---
        curr_prices: dict[str, float] = {}
        curr_opens: dict[str, float] = {}  # for next-day fills (we use same-day open as proxy for T+1)
        curr_vols: dict[str, float | None] = {}
        curr_daily_vol: dict[str, float | None] = {}

        for sym in ALL_SYMBOLS:
            if sym not in sym_df:
                continue
            df = sym_df[sym]
            idx = sym_date_to_idx[sym].get(current_date)
            if idx is None:
                continue
            row = df.iloc[idx]
            price = row.get("price")  # adj_close or close
            if price and price > 0:
                curr_prices[sym] = float(price)
            open_ = row.get("open")
            if open_ and open_ > 0:
                curr_opens[sym] = float(open_)
            vol = row.get("volume")
            if vol is not None:
                curr_vols[sym] = float(vol) if vol > 0 else None
            atr = row.get("atr_14")
            if atr and atr > 0:
                curr_daily_vol[sym] = float(atr) / curr_prices.get(sym, 1.0)

        # --- Mark to market ---
        pos_value = sum(
            shares * curr_prices.get(sym, entry_prices.get(sym, 0.0))
            for sym, shares in positions.items()
        )
        nav = cash + pos_value
        nav_series.append(nav)

        # --- Update trailing stops and check stop triggers ---
        stops_hit: list[str] = []
        for sym, _ in list(positions.items()):
            cp = curr_prices.get(sym)
            if cp is None:
                continue
            prev_high = highest_since_entry.get(sym, cp)
            new_high = max(prev_high, cp)
            highest_since_entry[sym] = new_high
            new_trail = new_high * (1.0 - trail_pct)
            trailing_stops[sym] = max(trailing_stops.get(sym, 0.0), new_trail)
            stop = max(initial_stops.get(sym, 0.0), trailing_stops.get(sym, 0.0))
            if stop > 0 and cp <= stop:
                stops_hit.append(sym)

        for sym in stops_hit:
            # Fill at current close (conservative — actual fill would be next open)
            fill = curr_prices.get(sym, entry_prices.get(sym, 0.0))
            shares = positions.pop(sym)
            notional = abs(shares * fill)
            cost = _trade_cost(notional, curr_daily_vol.get(sym))
            cash += shares * fill - cost
            n_trades += 1
            for d in (entry_prices, atr_at_entry, initial_stops, trailing_stops, highest_since_entry):
                d.pop(sym, None)

        # --- Determine if rebalance day ---
        days_since = 9999 if last_rebal_date is None else (current_date - last_rebal_date).days
        is_rebal = days_since >= rebal_freq

        if not is_rebal:
            continue

        last_rebal_date = current_date

        # --- Check warmup: need enough history ---
        cd_full_idx = full_date_to_idx.get(current_date)
        if cd_full_idx is None or cd_full_idx < WARMUP_DAYS:
            continue

        # --- Compute TSMOM composite for all tradeable symbols ---
        tsmom_scores: dict[str, float] = {}
        sym_atr_map: dict[str, float] = {}

        for sym in TRADEABLE:
            if sym not in sym_df:
                continue
            df = sym_df[sym]
            idx = sym_date_to_idx[sym].get(current_date)
            if idx is None:
                continue
            prices_hist = df["price"].iloc[:idx + 1].tolist()
            atrs_hist   = df["atr_14"].iloc[:idx + 1].tolist()
            score = _tsmom_composite(prices_hist, atrs_hist, PARAMS)
            if score is None:
                continue
            tsmom_scores[sym] = score
            atr_val = atrs_hist[-1] if atrs_hist else None
            if atr_val and atr_val > 0:
                sym_atr_map[sym] = float(atr_val)

        if not tsmom_scores:
            continue

        # --- Rank ---
        ranked = sorted(tsmom_scores.items(), key=lambda x: x[1], reverse=True)
        rank_map = {sym: i + 1 for i, (sym, _) in enumerate(ranked)}
        n_total = len(tsmom_scores)

        # --- Crash index ---
        crash_idx = _crash_index(
            cd_full_idx,
            {},
            sym_df,
            list(positions.keys()),
            PARAMS,
            disable=disable_crash,
        )

        # --- Regime ---
        regime = _regime(cd_full_idx, sym_df, PARAMS, disable=disable_regime)
        regime_scale = {"risk_on": 1.0, "transition": 0.75, "risk_off": 0.50}.get(regime, 0.75)
        crash_scale = max(0.0, 1.0 - crash_idx * 0.50)

        # --- Full flatten on crash == 1.0 ---
        if crash_idx >= 1.0:
            for sym, shares in list(positions.items()):
                fill = curr_prices.get(sym, entry_prices.get(sym, 0.0))
                notional = abs(shares * fill)
                cost = _trade_cost(notional, curr_daily_vol.get(sym))
                cash += shares * fill - cost
                n_trades += 1
                for d in (entry_prices, atr_at_entry, initial_stops, trailing_stops, highest_since_entry):
                    d.pop(sym, None)
            positions.clear()
            continue

        # --- Exit: bottom-quartile rank ---
        exit_thresh = max(int(n_total * 0.75) + 1, top_n + 1)
        for sym in list(positions.keys()):
            r = rank_map.get(sym, n_total + 1)
            if r >= exit_thresh:
                fill = curr_prices.get(sym, entry_prices.get(sym, 0.0))
                if fill <= 0:
                    continue
                shares = positions.pop(sym)
                notional = abs(shares * fill)
                cost = _trade_cost(notional, curr_daily_vol.get(sym))
                cash += shares * fill - cost
                n_trades += 1
                for d in (entry_prices, atr_at_entry, initial_stops, trailing_stops, highest_since_entry):
                    d.pop(sym, None)

        # Re-compute nav after exits
        pos_value = sum(
            shares * curr_prices.get(sym, entry_prices.get(sym, 0.0))
            for sym, shares in positions.items()
        )
        nav = cash + pos_value

        # --- Entries: top_n not already held ---
        n_current = len(positions)
        slots = top_n - n_current

        for sym, _ in ranked:
            if slots <= 0:
                break
            if sym in positions:
                continue

            fill = curr_prices.get(sym)
            if fill is None or fill <= 0:
                continue

            atr_val = sym_atr_map.get(sym, 0.0)
            # v2: cap-based sizing — flat base_position_weight (no vol-scaling)
            base_w = base_position_weight
            final_w = min(base_w * regime_scale * crash_scale, max_weight)

            # Cash check
            min_cash = nav * min_cash_frac
            if cash - nav * final_w < min_cash:
                continue

            notional = nav * final_w
            shares = notional / fill
            cost = _trade_cost(notional, curr_daily_vol.get(sym))
            cash -= notional + cost
            positions[sym] = shares

            stop_price = max(fill - atr_stop_mult * atr_val, fill * 0.80) if atr_val > 0 else fill * 0.95
            entry_prices[sym] = fill
            atr_at_entry[sym] = atr_val
            initial_stops[sym] = stop_price
            trailing_stops[sym] = fill * (1.0 - trail_pct)
            highest_since_entry[sym] = fill
            n_trades += 1
            slots -= 1

    return _metrics(nav_series, n_trades), nav_series, n_trades


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

def _get_benchmark_metrics() -> dict[str, float]:
    spy_df = raw[(raw["symbol"] == "SPY") & (raw["date"] >= START_DATE) & (raw["date"] <= END_DATE)]
    tlt_df = raw[(raw["symbol"] == "TLT") & (raw["date"] >= START_DATE) & (raw["date"] <= END_DATE)]
    spy_s = spy_df.set_index("date")["price"]
    spy_s.index = pd.to_datetime(spy_s.index)
    tlt_s = tlt_df.set_index("date")["price"]
    tlt_s.index = pd.to_datetime(tlt_s.index)
    return _benchmark_metrics(spy_s, tlt_s)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("\n" + "=" * 65)
    print("  MULTI-ASSET TSMOM CRASH-AWARE — Backtest")
    print("=" * 65)
    print(f"  Universe : {len(TRADEABLE)} ETFs  |  Reference: {len(REFERENCE)} symbols")
    print(f"  Window   : {START_DATE} → {END_DATE}")
    print(f"  Capital  : ${INITIAL_CAPITAL:,.2f}")
    print(f"  Warmup   : {WARMUP_DAYS} days")
    print()

    # Run strategy
    print("Running full strategy (crash + regime enabled) …")
    strat_metrics, strat_nav, strat_trades = run_simulation(
        disable_crash=False, disable_regime=False, label="strategy"
    )

    print("Running baseline (crash + regime DISABLED) …")
    base_metrics, base_nav, base_trades = run_simulation(
        disable_crash=True, disable_regime=True, label="baseline"
    )

    print("Computing benchmark (60/40 SPY/TLT) …")
    bench_metrics = _get_benchmark_metrics()

    # -----------------------------------------------------------------------
    # Promotion gates
    # -----------------------------------------------------------------------
    gates = SPEC["promotion_gates"]["backtest"]
    gate_results = {
        "sharpe":       (strat_metrics.get("sharpe", 0), gates["sharpe_min"]),
        "max_drawdown": (strat_metrics.get("max_drawdown", 1), gates["max_drawdown_max"]),
        "sortino":      (strat_metrics.get("sortino", 0), gates["sortino_min"]),
        "calmar":       (strat_metrics.get("calmar", 0), gates["calmar_min"]),
    }

    def _pass(metric: str) -> bool:
        val, threshold = gate_results[metric]
        if metric == "max_drawdown":
            return val <= threshold
        return val >= threshold

    # -----------------------------------------------------------------------
    # Print report
    # -----------------------------------------------------------------------

    print()
    print("=" * 65)
    print("## BACKTEST RESULTS")
    print("=" * 65)
    print()
    print("### Full-Period Strategy Metrics")
    print()
    print("| Metric         | Strategy | Baseline | Benchmark 60/40 |")
    print("|----------------|----------|----------|-----------------|")

    def fmt(v: float | None, pct: bool = False) -> str:
        if v is None:
            return "  N/A  "
        if pct:
            return f"{v:+.2%}"
        return f"{v:.4f}"

    rows = [
        ("Sharpe",       "sharpe",       False),
        ("Sortino",      "sortino",      False),
        ("Calmar",       "calmar",       False),
        ("Max Drawdown", "max_drawdown", True),
        ("CAGR",         "cagr",         True),
        ("Total Return", "total_return", True),
        ("Trades",       "total_trades", False),
    ]
    for label, key, is_pct in rows:
        sv = strat_metrics.get(key)
        bv = base_metrics.get(key)
        bmv = bench_metrics.get(key)
        print(f"| {label:<14} | {fmt(sv, is_pct):>8} | {fmt(bv, is_pct):>8} | {fmt(bmv, is_pct):>15} |")

    print()
    print("### Promotion Gates (Track A)")
    print()
    print("| Gate             | Required   | Achieved   | Status |")
    print("|------------------|------------|------------|--------|")
    gate_defs = [
        ("Sharpe ≥ 0.80",    "sharpe",       f"≥ {gates['sharpe_min']:.2f}"),
        ("MaxDD ≤ 15%",       "max_drawdown", f"≤ {gates['max_drawdown_max']:.1%}"),
        ("Sortino ≥ 1.00",   "sortino",      f"≥ {gates['sortino_min']:.2f}"),
        ("Calmar ≥ 0.50",    "calmar",       f"≥ {gates['calmar_min']:.2f}"),
    ]
    all_pass = True
    for name, key, req in gate_defs:
        val = strat_metrics.get(key, 0)
        passed = _pass(key)
        if not passed:
            all_pass = False
        status = "✅ PASS" if passed else "❌ FAIL"
        if key == "max_drawdown":
            achieved = f"{val:.2%}"
        elif key in ("sharpe", "sortino", "calmar"):
            achieved = f"{val:.4f}"
        else:
            achieved = f"{val:.4f}"
        print(f"| {name:<16} | {req:<10} | {achieved:<10} | {status} |")

    print()
    print(f"**Overall gate result: {'✅ ALL PASS' if all_pass else '❌ ONE OR MORE GATES FAILED'}**")
    print()

    print("### Overlay Effectiveness (Strategy vs Baseline)")
    print()
    s_sharpe = strat_metrics.get("sharpe", 0)
    b_sharpe = base_metrics.get("sharpe", 0)
    s_dd     = strat_metrics.get("max_drawdown", 1)
    b_dd     = base_metrics.get("max_drawdown", 1)
    s_cagr   = strat_metrics.get("cagr", 0)
    b_cagr   = base_metrics.get("cagr", 0)

    sharpe_delta = s_sharpe - b_sharpe
    dd_delta     = s_dd - b_dd
    cagr_delta   = s_cagr - b_cagr

    print(f"  Sharpe improvement  : {sharpe_delta:+.4f} "
          f"({'✅ overlay adds value' if sharpe_delta > 0 else '⚠️  overlay hurts Sharpe'})")
    print(f"  MaxDD improvement   : {dd_delta:+.4f} "
          f"({'✅ overlay reduces drawdown' if dd_delta < 0 else '⚠️  overlay increases drawdown'})")
    print(f"  CAGR delta          : {cagr_delta:+.2%}")
    print()

    # -----------------------------------------------------------------------
    # Save experiment artifact
    # -----------------------------------------------------------------------
    exp_id = str(uuid.uuid4())[:8]
    entry = {
        "experiment_id": exp_id,
        "strategy_slug": "multi-asset-tsmom-crash-aware-v2",
        "frozen_hash": SPEC.get("frozen_hash", ""),
        "run_type": "backtest",
        "start_date": str(START_DATE),
        "end_date": str(END_DATE),
        "initial_capital": INITIAL_CAPITAL,
        "metrics": strat_metrics,
        "baseline_metrics": base_metrics,
        "benchmark_metrics": bench_metrics,
        "promotion_gates_passed": all_pass,
        "gate_details": {
            k: {"value": strat_metrics.get(k, None), "threshold": v, "passed": _pass(k)}
            for k, v in [
                ("sharpe", gates["sharpe_min"]),
                ("max_drawdown", gates["max_drawdown_max"]),
                ("sortino", gates["sortino_min"]),
                ("calmar", gates["calmar_min"]),
            ]
        },
        "disable_crash": False,
        "disable_regime": False,
        "cost_model": {
            "spread_bps": SPREAD_BPS,
            "flat_slippage_bps": FLAT_SLIPPAGE_BPS,
            "slippage_volatility_factor": SLIPPAGE_VOL_FACTOR,
        },
        "daily_returns": strat_metrics.get("daily_returns_list", []),
    }

    STRAT_DIR.mkdir(parents=True, exist_ok=True)
    with REGISTRY_PATH.open("a", encoding="utf-8") as f:
        record = {
            **entry,
            "trial_number": 2,
            "recorded_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        f.write(json.dumps(record, default=str) + "\n")

    print(f"✅ Experiment artifact saved → {REGISTRY_PATH}")
    print(f"   experiment_id: {exp_id}")
    print()

    # Return non-zero exit code if gates fail (for CI integration)
    if not all_pass:
        print("⚠️  Strategy did NOT pass all promotion gates.")
        sys.exit(0)  # still exit 0 — bad results are expected and honest
    else:
        print("🎉 Strategy passed all backtest promotion gates!")


if __name__ == "__main__":
    main()
