#!/usr/bin/env python3
"""Clean event-study style backtest for BTC weekend -> QQQ next session.

This runner avoids stale-price behavior by:
- computing BTC weekend return events directly
- matching each qualified weekend to the next available QQQ trading session
- evaluating one-session forward QQQ returns as an event series
- reporting simple strategy-style summary stats and perturbations

This is a governed exploration helper, not yet an official experiment-registry writer.
"""

from __future__ import annotations

import math

import polars as pl

from llm_quant.data.fetcher import fetch_ohlcv

LOOKBACK_DAYS = 5 * 365 + 30
SYMBOLS = ["BTC-USD", "QQQ"]


def _annualized_sharpe(returns: list[float], periods_per_year: int = 52) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return mean / std * math.sqrt(periods_per_year)


def _max_drawdown(returns: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        equity *= 1.0 + r
        peak = max(peak, equity)
        drawdown = 1.0 - (equity / peak)
        max_dd = max(max_dd, drawdown)
    return max_dd


def build_event_returns(
    prices_df: pl.DataFrame,
    threshold: float,
    min_weekend_days: int,
) -> list[float]:
    btc = (
        prices_df.filter(pl.col("symbol") == "BTC-USD")
        .sort("date")
        .select(["date", "open", "close"])
        .with_columns(pl.col("date").dt.weekday().alias("weekday"))
    )
    qqq = (
        prices_df.filter(pl.col("symbol") == "QQQ")
        .sort("date")
        .select(["date", "open", "close"])
    )

    qqq_rows = qqq.iter_rows(named=True)
    qqq_dates = [row["date"] for row in qqq_rows]
    qqq_open = {row["date"]: float(row["open"]) for row in qqq.iter_rows(named=True)}
    qqq_close = {row["date"]: float(row["close"]) for row in qqq.iter_rows(named=True)}

    btc_rows = list(btc.iter_rows(named=True))
    event_returns: list[float] = []

    for i in range(1, len(btc_rows)):
        row = btc_rows[i]
        prev = btc_rows[i - 1]
        weekday = int(row["weekday"])
        prev_weekday = int(prev["weekday"])

        is_monday = weekday == 1
        crossed_weekend = prev_weekday == 7 and is_monday
        if not crossed_weekend:
            continue

        weekend_slice: list[dict[str, object]] = [prev]
        j = i - 2
        while j >= 0:
            candidate = btc_rows[j]
            candidate_weekday = int(candidate["weekday"])
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
        if weekend_open <= 0:
            continue
        weekend_return = weekend_close / weekend_open - 1.0
        if weekend_return < threshold:
            continue

        next_qqq_idx = None
        for idx, q_date in enumerate(qqq_dates):
            if q_date >= row["date"]:
                next_qqq_idx = idx
                break
        if next_qqq_idx is None or next_qqq_idx + 1 >= len(qqq_dates):
            continue

        entry_date = qqq_dates[next_qqq_idx]
        exit_date = qqq_dates[next_qqq_idx + 1]
        entry_open = qqq_open.get(entry_date)
        exit_close = qqq_close.get(exit_date)
        if entry_open is None or exit_close is None or entry_open <= 0:
            continue

        qqq_return = exit_close / entry_open - 1.0
        event_returns.append(qqq_return)

    return event_returns


def summarize(label: str, returns: list[float]) -> None:
    total_return = 1.0
    for r in returns:
        total_return *= 1.0 + r
    total_return -= 1.0

    sharpe = _annualized_sharpe(returns)
    max_dd = _max_drawdown(returns)
    print(
        f"{label}: sharpe={sharpe:.4f}, max_dd={max_dd:.4f}, "
        f"total_return={total_return:.4f}, trades={len(returns)}"
    )


def main() -> None:
    prices_df = fetch_ohlcv(SYMBOLS, lookback_days=LOOKBACK_DAYS)

    print("BTC weekend -> QQQ next-session clean event test")

    base_returns = build_event_returns(
        prices_df=prices_df,
        threshold=0.01,
        min_weekend_days=2,
    )
    summarize("base", base_returns)

    variants = [
        ("threshold=0.00", 0.00, 2),
        ("threshold=0.02", 0.02, 2),
        ("min_weekend_days=1", 0.01, 1),
        ("min_weekend_days=3", 0.01, 3),
    ]
    for label, threshold, min_days in variants:
        variant_returns = build_event_returns(
            prices_df=prices_df,
            threshold=threshold,
            min_weekend_days=min_days,
        )
        summarize(label, variant_returns)


if __name__ == "__main__":
    main()
