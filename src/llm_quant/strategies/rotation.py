"""Strategy rotation selector for promoted specs."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from math import sqrt
from typing import Any

import duckdb

from llm_quant.strategies.runtime import StrategySpec

logger = logging.getLogger(__name__)


@dataclass
class StrategyMetric:
    sharpe: float
    max_drawdown: float
    trades: int


def _compute_rotation_metrics(
    conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
    pod_id: str,
    initial_capital: float,
) -> dict[str, StrategyMetric]:
    rows = conn.execute(
        """
        SELECT trade_id, date, symbol, action, shares, price, strategy_id
        FROM trades
        WHERE date >= ? AND date <= ? AND pod_id = ?
        ORDER BY trade_id ASC
        """,
        [start_date, end_date, pod_id],
    ).fetchall()
    if not rows:
        return {}

    nav_rows = conn.execute(
        """
        SELECT date, nav
        FROM portfolio_snapshots
        WHERE date >= ? AND date <= ? AND pod_id = ?
        """,
        [start_date, end_date, pod_id],
    ).fetchall()
    nav_by_date = {row[0]: float(row[1]) for row in nav_rows}

    lots: dict[tuple[str, str], list[list[float]]] = {}
    pnl_by_date: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    trade_counts: dict[str, int] = defaultdict(int)

    for row in rows:
        _, trade_date, symbol, action, shares, price, strategy_id = row
        strategy = strategy_id or "unattributed"
        qty = float(shares)
        if qty <= 0:
            continue

        key = (strategy, symbol)
        if action == "buy":
            lots.setdefault(key, []).append([qty, float(price)])
            continue

        if action not in {"sell", "close"}:
            continue

        queue = lots.setdefault(key, [])
        if not queue:
            continue

        remaining = qty
        while remaining > 0 and queue:
            lot_qty, lot_price = queue[0]
            matched = min(remaining, lot_qty)
            pnl = (float(price) - lot_price) * matched
            pnl_by_date[strategy][trade_date] += pnl
            trade_counts[strategy] += 1

            lot_qty -= matched
            remaining -= matched
            if lot_qty <= 0:
                queue.pop(0)
            else:
                queue[0][0] = lot_qty

    metrics: dict[str, StrategyMetric] = {}
    for strategy, pnl_map in pnl_by_date.items():
        dates = sorted(pnl_map)
        if not dates:
            continue

        returns: list[float] = []
        for dt in dates:
            nav = nav_by_date.get(dt, initial_capital)
            nav = nav if nav > 0 else initial_capital
            returns.append(pnl_map[dt] / nav)

        sharpe = 0.0
        if len(returns) > 1:
            mean_ret = sum(returns) / len(returns)
            var = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
            std = sqrt(var) if var > 0 else 0.0
            if std > 0:
                sharpe = mean_ret / std * sqrt(252)

        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        for r in returns:
            equity *= 1.0 + r
            peak = max(peak, equity)
            drawdown = (peak - equity) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, drawdown)

        metrics[strategy] = StrategyMetric(
            sharpe=sharpe,
            max_drawdown=max_dd,
            trades=trade_counts.get(strategy, 0),
        )

    return metrics


def load_rotation_state(
    conn: duckdb.DuckDBPyConnection,
    pod_id: str,
) -> dict[str, date | None]:
    rows = conn.execute(
        """
        SELECT strategy_id, disabled_until
        FROM strategy_rotation_state
        WHERE pod_id = ?
        """
        ,
        [pod_id],
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def upsert_rotation_state(
    conn: duckdb.DuckDBPyConnection,
    pod_id: str,
    state: dict[str, date | None],
) -> None:
    if not state:
        return
    rows: list[list[Any]] = []
    for strategy_id, disabled_until in state.items():
        rows.append([strategy_id, disabled_until])
    conn.executemany(
        """
        INSERT OR REPLACE INTO strategy_rotation_state (
            pod_id, strategy_id, disabled_until, updated_at
        ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [[pod_id, row[0], row[1]] for row in rows],
    )
    conn.commit()


def select_rotated_specs(
    conn: duckdb.DuckDBPyConnection,
    specs: list[StrategySpec],
    *,
    as_of_date: date,
    pod_id: str,
    initial_capital: float,
    enabled: bool,
    window_days: int,
    top_n: int,
    min_trades: int,
    cooldown_days: int,
) -> tuple[list[StrategySpec], list[str]]:
    if not enabled:
        return specs, []

    start_date = as_of_date - timedelta(days=window_days)
    metrics = _compute_rotation_metrics(
        conn,
        start_date=start_date,
        end_date=as_of_date,
        pod_id=pod_id,
        initial_capital=initial_capital,
    )
    state = load_rotation_state(conn, pod_id=pod_id)

    eligible: list[tuple[StrategySpec, StrategyMetric]] = []
    for spec in specs:
        disabled_until = state.get(spec.slug)
        if disabled_until and disabled_until > as_of_date:
            continue
        metric = metrics.get(spec.slug)
        if metric is None or metric.trades < min_trades:
            continue
        eligible.append((spec, metric))

    if not eligible:
        logger.warning(
            "Rotation enabled but no eligible strategies found; using all specs."
        )
        return specs, []

    eligible.sort(key=lambda item: (-item[1].sharpe, item[1].max_drawdown))
    selected = [spec for spec, _metric in eligible[:top_n]]
    selected_ids = {spec.slug for spec in selected}

    rotation_state: dict[str, date | None] = {}
    for spec in specs:
        if spec.slug in selected_ids:
            rotation_state[spec.slug] = None
        else:
            rotation_state[spec.slug] = as_of_date + timedelta(days=cooldown_days)

    upsert_rotation_state(conn, pod_id=pod_id, state=rotation_state)
    return selected, sorted(selected_ids)


__all__ = ["select_rotated_specs"]
