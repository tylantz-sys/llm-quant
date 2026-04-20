"""Intraday profit-taking + position state management."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import duckdb

from llm_quant.brain.models import Action, TradeSignal
from llm_quant.trading.portfolio import Portfolio

logger = logging.getLogger(__name__)


@dataclass
class IntradayPositionState:
    symbol: str
    entry_batch: int = 0
    entry_price: float = 0.0
    peak_price: float = 0.0
    partial_exit_taken: bool = False
    last_entry_ts: datetime | None = None
    last_exit_ts: datetime | None = None
    cooldown_until_ts: datetime | None = None


def load_position_states(
    conn: duckdb.DuckDBPyConnection,
    pod_id: str,
) -> dict[str, IntradayPositionState]:
    """Load intraday state keyed by symbol."""
    rows = conn.execute(
        """
        SELECT symbol, entry_batch, entry_price, peak_price,
               partial_exit_taken, last_entry_ts, last_exit_ts, cooldown_until_ts
        FROM intraday_position_state
        WHERE pod_id = ?
        """,
        [pod_id],
    ).fetchall()
    states: dict[str, IntradayPositionState] = {}
    for row in rows:
        (
            symbol,
            entry_batch,
            entry_price,
            peak_price,
            partial_exit_taken,
            last_entry_ts,
            last_exit_ts,
            cooldown_until_ts,
        ) = row
        states[symbol] = IntradayPositionState(
            symbol=symbol,
            entry_batch=int(entry_batch or 0),
            entry_price=float(entry_price or 0.0),
            peak_price=float(peak_price or 0.0),
            partial_exit_taken=bool(partial_exit_taken),
            last_entry_ts=last_entry_ts,
            last_exit_ts=last_exit_ts,
            cooldown_until_ts=cooldown_until_ts,
        )
    return states


def upsert_position_states(
    conn: duckdb.DuckDBPyConnection,
    pod_id: str,
    states: dict[str, IntradayPositionState],
) -> None:
    """Persist intraday position states."""
    if not states:
        return

    rows: list[list[Any]] = [
        [
            pod_id,
            state.symbol,
            state.entry_batch,
            state.entry_price,
            state.peak_price,
            state.partial_exit_taken,
            state.last_entry_ts,
            state.last_exit_ts,
            state.cooldown_until_ts,
        ]
        for state in states.values()
    ]

    conn.executemany(
        """
        INSERT OR REPLACE INTO intraday_position_state (
            pod_id, symbol, entry_batch, entry_price, peak_price,
            partial_exit_taken, last_entry_ts, last_exit_ts, cooldown_until_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def apply_scale_in(
    signals: list[TradeSignal],
    _portfolio: Portfolio,
    states: dict[str, IntradayPositionState],
    scale_in_tranches: int,
) -> list[TradeSignal]:
    """Adjust BUY signals to scale into positions over multiple tranches."""
    if scale_in_tranches <= 1:
        return signals

    adjusted: list[TradeSignal] = []
    for signal in signals:
        if signal.action != Action.BUY:
            adjusted.append(signal)
            continue

        state = states.get(signal.symbol)
        current_batch = state.entry_batch if state else 0
        if current_batch >= scale_in_tranches:
            logger.debug(
                "Scale-in cap reached for %s (%d/%d); skipping BUY.",
                signal.symbol,
                current_batch,
                scale_in_tranches,
            )
            continue

        tranche_weight = signal.target_weight * (
            (current_batch + 1) / scale_in_tranches
        )
        adjusted.append(
            TradeSignal(
                symbol=signal.symbol,
                action=signal.action,
                conviction=signal.conviction,
                target_weight=round(tranche_weight, 4),
                stop_loss=signal.stop_loss,
                reasoning=signal.reasoning,
                take_profit=signal.take_profit,
                strategy_id=signal.strategy_id,
                entry_batch=current_batch + 1,
                exit_reason=signal.exit_reason,
                metadata=signal.metadata,
            )
        )

    return adjusted


def apply_reentry_cooldown(
    signals: list[TradeSignal],
    states: dict[str, IntradayPositionState],
    now_ts: datetime,
    timeframe_minutes: int,
    cooldown_bars: int,
) -> list[TradeSignal]:
    """Drop BUY signals that violate the cooldown window."""
    if cooldown_bars <= 0:
        return signals

    cooldown_delta = timedelta(minutes=timeframe_minutes * cooldown_bars)
    filtered: list[TradeSignal] = []
    for signal in signals:
        if signal.action != Action.BUY:
            filtered.append(signal)
            continue

        state = states.get(signal.symbol)
        if not state or not state.last_exit_ts:
            filtered.append(signal)
            continue

        if now_ts < state.last_exit_ts + cooldown_delta:
            logger.info(
                "Cooldown active for %s (last exit %s); skipping BUY.",
                signal.symbol,
                state.last_exit_ts,
            )
            continue

        filtered.append(signal)

    return filtered


def merge_intraday_signals(
    entry_signals: list[TradeSignal],
    other_signals: list[TradeSignal],
    profit_signals: list[TradeSignal],
) -> list[TradeSignal]:
    """Merge intraday signals, prioritizing profit-taking exits."""
    exit_symbols = {
        s.symbol
        for s in profit_signals
        if s.action in (Action.SELL, Action.COVER, Action.CLOSE)
    }
    filtered_entries = [s for s in entry_signals if s.symbol not in exit_symbols]
    return other_signals + profit_signals + filtered_entries


def update_peak_prices(
    portfolio: Portfolio,
    prices: dict[str, float],
    states: dict[str, IntradayPositionState],
) -> None:
    """Update peak prices for open positions."""
    for symbol, pos in portfolio.positions.items():
        price = prices.get(symbol, pos.current_price)
        state = states.get(symbol)
        if state is None:
            state = IntradayPositionState(
                symbol=symbol,
                entry_batch=1,
                entry_price=pos.avg_cost,
                peak_price=price,
            )
            states[symbol] = state
        state.peak_price = max(state.peak_price, price)


def generate_profit_taking_signals(
    portfolio: Portfolio,
    prices: dict[str, float],
    states: dict[str, IntradayPositionState],
    now_ts: datetime,
    partial_tp_pct: float,
    partial_tp_size: float,
    trailing_stop_pct: float,
) -> list[TradeSignal]:
    """Backward-compatible wrapper around the canonical exit engine."""
    del now_ts

    from llm_quant.config import ExecutionConfig, RiskLimits
    from llm_quant.trading.exits import (
        SyntheticExitContext,
        build_exit_policy,
        evaluate_synthetic_exit,
    )

    policy = build_exit_policy(
        RiskLimits(
            partial_take_profit_enabled=partial_tp_pct > 0 and partial_tp_size > 0,
            partial_take_profit_pct=partial_tp_pct,
            partial_take_profit_size=partial_tp_size,
            trailing_stop_enabled=trailing_stop_pct > 0,
            trailing_stop_pct=trailing_stop_pct,
        ),
        ExecutionConfig(),
    )

    signals: list[TradeSignal] = []
    nav = portfolio.nav
    for symbol, pos in portfolio.positions.items():
        price = prices.get(symbol, pos.current_price)
        state = states.get(symbol)
        if state is None:
            state = IntradayPositionState(
                symbol=symbol,
                entry_batch=1,
                entry_price=pos.avg_cost,
                peak_price=price,
            )
            states[symbol] = state

        signal = evaluate_synthetic_exit(
            SyntheticExitContext(
                position=pos,
                price=price,
                nav=nav,
                state=state,
            ),
            policy,
        )
        if signal is not None:
            signals.append(signal)

    return signals


def update_state_from_trades(
    states: dict[str, IntradayPositionState],
    trades: list[Any],
    now_ts: datetime,
    partial_exit_reason: str = "tp_partial",
) -> None:
    """Update intraday position state based on executed trades."""
    for trade in trades:
        symbol = trade.symbol
        state = states.get(symbol)
        if state is None:
            state = IntradayPositionState(symbol=symbol)
            states[symbol] = state

        if trade.action in ("buy", "short"):
            state.entry_batch = max(state.entry_batch, trade.entry_batch)
            state.entry_price = trade.price
            state.last_entry_ts = now_ts
            state.partial_exit_taken = False
            if trade.action == "short":
                if state.peak_price <= 0.0:
                    state.peak_price = trade.price
                else:
                    state.peak_price = min(state.peak_price, trade.price)
            else:
                state.peak_price = max(state.peak_price, trade.price)
        elif trade.action in ("sell", "cover", "close"):
            state.last_exit_ts = now_ts
            state.cooldown_until_ts = None
            if trade.exit_reason == partial_exit_reason:
                state.partial_exit_taken = True
            else:
                state.partial_exit_taken = False
                if trade.action == "close":
                    state.entry_batch = 0
                    state.entry_price = 0.0
                    state.peak_price = 0.0


def log_intraday_context(
    conn: duckdb.DuckDBPyConnection,
    pod_id: str,
    timestamp: datetime,
    context: dict[str, Any],
) -> int:
    """Persist intraday context snapshot for audit."""
    row = conn.execute("SELECT nextval('seq_intraday_snapshot_id')").fetchone()
    snapshot_id = int(row[0]) if row else 0
    payload = json.dumps(context, default=str)
    conn.execute(
        """
        INSERT INTO intraday_context_snapshots (
            snapshot_id, timestamp, pod_id, context_json
        ) VALUES (?, ?, ?, ?)
        """,
        [snapshot_id, timestamp, pod_id, payload],
    )
    conn.commit()
    return snapshot_id
