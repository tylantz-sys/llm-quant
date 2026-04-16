#!/usr/bin/env python3
"""Deterministic BTC weekend follow-through event study.

Research scope
--------------
This script studies whether BTC weekend direction carries information for the next
available session in equity index ETFs. It is intentionally implemented as an
event study rather than a standard daily-marked backtest because BTC trades on
weekends while the followers do not. That avoids stale-price artifacts from
forcing non-trading follower assets through weekend portfolio marks.

Documented assumptions
----------------------
- Source signal asset is ``BTC-USD`` daily OHLCV from Yahoo Finance.
- A weekend event is a Saturday+Sunday BTC slice ending before the next Monday.
- Weekend return is measured from the first weekend open to the final weekend close.
- Directional signal is deterministic:
  - ``+1`` when weekend return >= ``entry_threshold``
  - ``-1`` when weekend return <= ``-entry_threshold``
  - otherwise no event
- For each follower, entry occurs at the next available follower session open
  on or after the BTC Monday date, and exit occurs at the close of the following
  follower session.
- Event returns are analyzed independently and reported additively in basis points.
- Cost sensitivity is modeled as a fixed round-trip cost in basis points per event.
- Shuffled-signal falsification preserves the realized follower event windows while
  randomly permuting the signal direction with a fixed seed.

This is a deterministic research helper. It does not write experiment registries,
place trades, or make promotion claims by itself.
"""

from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import cast

sys.path.insert(0, "src")

import polars as pl

from llm_quant.data.fetcher import fetch_ohlcv

LOOKBACK_DAYS = 5 * 365 + 30
SOURCE_SYMBOL = "BTC-USD"
FOLLOWER_SYMBOLS = ["QQQ", "SPY", "IWM", "DIA"]
ENTRY_THRESHOLD = 0.01
MIN_WEEKEND_DAYS = 2
SHUFFLE_SEED = 42
N_SHUFFLES = 1000
COST_GRID_BPS = [0, 5, 10, 25, 50]


@dataclass(frozen=True)
class EventObservation:
    """Single BTC weekend event mapped to a follower trade window."""

    monday_date: date
    follower: str
    signal: int
    weekend_return: float
    entry_date: date
    exit_date: date
    follower_raw_return: float

    @property
    def gross_return(self) -> float:
        return self.signal * self.follower_raw_return


def _safe_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _safe_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean_value = _safe_mean(values)
    variance = sum((value - mean_value) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(max(variance, 0.0))


def _t_stat(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean_value = _safe_mean(values)
    std_value = _safe_std(values)
    if std_value == 0.0:
        return 0.0
    return mean_value / (std_value / math.sqrt(len(values)))


def _quantile_sorted(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = q * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _basis_points(value: float) -> float:
    return value * 10_000.0


def summarize_returns(returns: list[float]) -> dict[str, float]:
    sorted_returns = sorted(returns)
    positive_count = sum(1 for value in returns if value > 0.0)
    return {
        "n_events": float(len(returns)),
        "mean_bp": _basis_points(_safe_mean(returns)),
        "median_bp": _basis_points(median(returns)) if returns else 0.0,
        "hit_rate": positive_count / len(returns) if returns else 0.0,
        "std_bp": _basis_points(_safe_std(returns)),
        "t_stat": _t_stat(returns),
        "cum_bp": _basis_points(sum(returns)),
        "p10_bp": _basis_points(_quantile_sorted(sorted_returns, 0.10)),
        "p90_bp": _basis_points(_quantile_sorted(sorted_returns, 0.90)),
        "min_bp": _basis_points(sorted_returns[0]) if sorted_returns else 0.0,
        "max_bp": _basis_points(sorted_returns[-1]) if sorted_returns else 0.0,
    }


def build_btc_weekend_events(
    prices_df: pl.DataFrame,
    entry_threshold: float,
    min_weekend_days: int,
) -> list[tuple[date, int, float]]:
    btc = (
        prices_df.filter(pl.col("symbol") == SOURCE_SYMBOL)
        .sort("date")
        .select(["date", "open", "close"])
        .with_columns(pl.col("date").dt.weekday().alias("weekday"))
    )

    btc_rows = list(btc.iter_rows(named=True))
    events: list[tuple[date, int, float]] = []

    for i in range(1, len(btc_rows)):
        row = btc_rows[i]
        prev = btc_rows[i - 1]
        row_date = row["date"]
        prev_date = prev["date"]
        weekday = row["weekday"]
        prev_weekday = prev["weekday"]

        if not isinstance(row_date, date) or not isinstance(prev_date, date):
            continue
        if not isinstance(weekday, int) or not isinstance(prev_weekday, int):
            continue

        is_monday = weekday == 1
        crossed_weekend = prev_weekday == 7 and is_monday
        if not crossed_weekend:
            continue

        weekend_slice: list[dict[str, object]] = [prev]
        j = i - 2
        while j >= 0:
            candidate = btc_rows[j]
            candidate_weekday = candidate["weekday"]
            if not isinstance(candidate_weekday, int):
                break
            if candidate_weekday in (6, 7):
                weekend_slice.insert(0, candidate)
                j -= 1
                continue
            break

        if len(weekend_slice) < min_weekend_days:
            continue

        weekend_open_obj = weekend_slice[0]["open"]
        weekend_close_obj = weekend_slice[-1]["close"]
        if not isinstance(weekend_open_obj, (int, float)) or not isinstance(
            weekend_close_obj, (int, float)
        ):
            continue

        weekend_open = float(weekend_open_obj)
        weekend_close = float(weekend_close_obj)
        if weekend_open <= 0.0:
            continue

        weekend_return = weekend_close / weekend_open - 1.0
        signal = 0
        if weekend_return >= entry_threshold:
            signal = 1
        elif weekend_return <= -entry_threshold:
            signal = -1

        if signal != 0:
            events.append((row_date, signal, weekend_return))

    return events


def map_events_to_follower(
    prices_df: pl.DataFrame,
    follower: str,
    btc_events: list[tuple[date, int, float]],
) -> list[EventObservation]:
    follower_df = (
        prices_df.filter(pl.col("symbol") == follower)
        .sort("date")
        .select(["date", "open", "close"])
    )

    follower_rows = list(follower_df.iter_rows(named=True))
    follower_dates: list[date] = []
    follower_open: dict[date, float] = {}
    follower_close: dict[date, float] = {}

    for row in follower_rows:
        row_date = row["date"]
        open_obj = row["open"]
        close_obj = row["close"]
        if not isinstance(row_date, date):
            continue
        if not isinstance(open_obj, (int, float)) or not isinstance(close_obj, (int, float)):
            continue
        follower_dates.append(row_date)
        follower_open[row_date] = float(open_obj)
        follower_close[row_date] = float(close_obj)

    observations: list[EventObservation] = []
    for monday_date, signal, weekend_return in btc_events:
        next_idx: int | None = None
        for idx, follower_date in enumerate(follower_dates):
            if follower_date >= monday_date:
                next_idx = idx
                break
        if next_idx is None or next_idx + 1 >= len(follower_dates):
            continue

        entry_date = follower_dates[next_idx]
        exit_date = follower_dates[next_idx + 1]
        entry_open = follower_open.get(entry_date)
        exit_close = follower_close.get(exit_date)
        if entry_open is None or exit_close is None or entry_open <= 0.0:
            continue

        follower_raw_return = exit_close / entry_open - 1.0
        observations.append(
            EventObservation(
                monday_date=monday_date,
                follower=follower,
                signal=signal,
                weekend_return=weekend_return,
                entry_date=entry_date,
                exit_date=exit_date,
                follower_raw_return=follower_raw_return,
            )
        )

    return observations


def net_returns_after_cost(observations: list[EventObservation], cost_bps: int) -> list[float]:
    cost = cost_bps / 10_000.0
    return [obs.gross_return - cost for obs in observations]


def shuffled_signal_test(
    observations: list[EventObservation],
    seed: int,
    n_shuffles: int,
) -> dict[str, float]:
    if not observations:
        return {
            "actual_mean_bp": 0.0,
            "shuffle_mean_bp": 0.0,
            "shuffle_std_bp": 0.0,
            "shuffle_p05_bp": 0.0,
            "shuffle_p50_bp": 0.0,
            "shuffle_p95_bp": 0.0,
            "empirical_p_value": 1.0,
        }

    signals = [obs.signal for obs in observations]
    raw_returns = [obs.follower_raw_return for obs in observations]
    actual_mean = _safe_mean([signal * raw for signal, raw in zip(signals, raw_returns, strict=True)])

    rng = random.Random(seed)
    shuffle_means: list[float] = []
    for _ in range(n_shuffles):
        shuffled_signals = list(signals)
        rng.shuffle(shuffled_signals)
        shuffle_returns = [
            signal * raw
            for signal, raw in zip(shuffled_signals, raw_returns, strict=True)
        ]
        shuffle_means.append(_safe_mean(shuffle_returns))

    sorted_means = sorted(shuffle_means)
    if actual_mean >= 0.0:
        p_value = sum(1 for value in shuffle_means if value >= actual_mean) / len(shuffle_means)
    else:
        p_value = sum(1 for value in shuffle_means if value <= actual_mean) / len(shuffle_means)

    return {
        "actual_mean_bp": _basis_points(actual_mean),
        "shuffle_mean_bp": _basis_points(_safe_mean(shuffle_means)),
        "shuffle_std_bp": _basis_points(_safe_std(shuffle_means)),
        "shuffle_p05_bp": _basis_points(_quantile_sorted(sorted_means, 0.05)),
        "shuffle_p50_bp": _basis_points(_quantile_sorted(sorted_means, 0.50)),
        "shuffle_p95_bp": _basis_points(_quantile_sorted(sorted_means, 0.95)),
        "empirical_p_value": p_value,
    }


def print_cost_table(observations: list[EventObservation]) -> None:
    print("Cost sensitivity (round-trip bps per event)")
    print("cost_bp | mean_bp | hit_% | t_stat | cum_bp")
    for cost_bps in COST_GRID_BPS:
        returns = net_returns_after_cost(observations, cost_bps)
        summary = summarize_returns(returns)
        print(
            f"{cost_bps:>7} | "
            f"{summary['mean_bp']:>7.2f} | "
            f"{summary['hit_rate'] * 100:>5.1f} | "
            f"{summary['t_stat']:>6.2f} | "
            f"{summary['cum_bp']:>7.2f}"
        )


def print_shuffle_block(shuffle_stats: dict[str, float]) -> None:
    print("Shuffled-signal falsification")
    print(
        "actual_bp={actual:.2f} shuffle_mean_bp={mean:.2f} "
        "p05/p50/p95=({p05:.2f}, {p50:.2f}, {p95:.2f}) empirical_p={p:.4f}".format(
            actual=shuffle_stats["actual_mean_bp"],
            mean=shuffle_stats["shuffle_mean_bp"],
            p05=shuffle_stats["shuffle_p05_bp"],
            p50=shuffle_stats["shuffle_p50_bp"],
            p95=shuffle_stats["shuffle_p95_bp"],
            p=shuffle_stats["empirical_p_value"],
        )
    )


def print_follower_table(
    follower_results: dict[str, dict[str, float]],
) -> None:
    print("Cross-follower comparison")
    print("symbol | events | mean_bp | hit_% | t_stat | mean_10bp_bp | shuffle_p")
    for symbol, result in follower_results.items():
        print(
            f"{symbol:>6} | "
            f"{int(result['n_events']):>6} | "
            f"{result['mean_bp']:>7.2f} | "
            f"{result['hit_rate'] * 100:>5.1f} | "
            f"{result['t_stat']:>6.2f} | "
            f"{result['mean_10bp_bp']:>11.2f} | "
            f"{result['shuffle_p']:>9.4f}"
        )


def main() -> None:
    symbols = [SOURCE_SYMBOL, *FOLLOWER_SYMBOLS]
    prices_df = fetch_ohlcv(symbols, lookback_days=LOOKBACK_DAYS)

    all_dates = sorted(
        cast(list[date], prices_df.select("date").unique().to_series().to_list())
    )
    if not all_dates:
        print("No data fetched.")
        return

    btc_events = build_btc_weekend_events(
        prices_df=prices_df,
        entry_threshold=ENTRY_THRESHOLD,
        min_weekend_days=MIN_WEEKEND_DAYS,
    )

    print("BTC weekend follow-through study")
    print(
        f"sample={all_dates[0]}..{all_dates[-1]} source={SOURCE_SYMBOL} "
        f"entry_threshold={ENTRY_THRESHOLD:.2%} min_weekend_days={MIN_WEEKEND_DAYS}"
    )
    print(
        f"followers={','.join(FOLLOWER_SYMBOLS)} shuffle_seed={SHUFFLE_SEED} "
        f"n_shuffles={N_SHUFFLES} cost_grid_bps={COST_GRID_BPS}"
    )
    print(f"btc_directional_events={len(btc_events)}")

    primary_symbol = "QQQ"
    primary_observations = map_events_to_follower(prices_df, primary_symbol, btc_events)
    primary_returns = [obs.gross_return for obs in primary_observations]
    primary_summary = summarize_returns(primary_returns)
    print(
        "Primary summary "
        f"{primary_symbol}: events={int(primary_summary['n_events'])} "
        f"mean_bp={primary_summary['mean_bp']:.2f} "
        f"median_bp={primary_summary['median_bp']:.2f} "
        f"hit%={primary_summary['hit_rate'] * 100:.1f} "
        f"t_stat={primary_summary['t_stat']:.2f} "
        f"cum_bp={primary_summary['cum_bp']:.2f} "
        f"p10/p90=({primary_summary['p10_bp']:.2f}, {primary_summary['p90_bp']:.2f})"
    )
    print()

    print_cost_table(primary_observations)
    print()

    primary_shuffle = shuffled_signal_test(
        primary_observations,
        seed=SHUFFLE_SEED,
        n_shuffles=N_SHUFFLES,
    )
    print_shuffle_block(primary_shuffle)
    print()

    follower_results: dict[str, dict[str, float]] = {}
    for follower in FOLLOWER_SYMBOLS:
        observations = map_events_to_follower(prices_df, follower, btc_events)
        gross_summary = summarize_returns([obs.gross_return for obs in observations])
        net_10bp_summary = summarize_returns(net_returns_after_cost(observations, 10))
        shuffle_stats = shuffled_signal_test(
            observations,
            seed=SHUFFLE_SEED,
            n_shuffles=N_SHUFFLES,
        )
        follower_results[follower] = {
            "n_events": gross_summary["n_events"],
            "mean_bp": gross_summary["mean_bp"],
            "hit_rate": gross_summary["hit_rate"],
            "t_stat": gross_summary["t_stat"],
            "mean_10bp_bp": net_10bp_summary["mean_bp"],
            "shuffle_p": shuffle_stats["empirical_p_value"],
        }

    print_follower_table(follower_results)
    print()

    robust_to_10bp = follower_results[primary_symbol]["mean_10bp_bp"] > 0.0
    beats_shuffle_5pct = primary_shuffle["empirical_p_value"] < 0.05

    consistent_candidates = ["QQQ", "SPY", "IWM", "DIA"]
    consistent_count = sum(
        1
        for symbol in consistent_candidates
        if follower_results[symbol]["mean_bp"] > 0.0
        and follower_results[symbol]["mean_10bp_bp"] > 0.0
    )
    consistent_across_followers = consistent_count >= 2

    print("Flags")
    print(f"ROBUST_TO_10BP={'yes' if robust_to_10bp else 'no'}")
    print(f"BEATS_SHUFFLE_5PCT={'yes' if beats_shuffle_5pct else 'no'}")
    print(
        "CONSISTENT_ACROSS_FOLLOWERS="
        f"{'yes' if consistent_across_followers else 'no'}"
    )


if __name__ == "__main__":
    main()
