"""Intraday profit-taking + position state management."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import duckdb

from llm_quant.brain.models import Action, Conviction, TradeSignal
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

    rows: list[list[Any]] = []
    for state in states.values():
        rows.append(
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
        )

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
    portfolio: Portfolio,
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
        if price > state.peak_price:
            state.peak_price = price


def generate_profit_taking_signals(
    portfolio: Portfolio,
    prices: dict[str, float],
    states: dict[str, IntradayPositionState],
    now_ts: datetime,
    partial_tp_pct: float,
    partial_tp_size: float,
    trailing_stop_pct: float,
) -> list[TradeSignal]:
    """Generate SELL/CLOSE signals based on intraday profit-taking rules."""
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

        entry_price = state.entry_price or pos.avg_cost
        if entry_price <= 0:
            entry_price = pos.avg_cost

        # Stop-loss check
        if pos.stop_loss and price <= pos.stop_loss:
            signals.append(
                TradeSignal(
                    symbol=symbol,
                    action=Action.CLOSE,
                    conviction=Conviction.HIGH,
                    target_weight=0.0,
                    stop_loss=0.0,
                    reasoning="Intraday stop-loss triggered.",
                    exit_reason="stop_loss",
                    entry_batch=state.entry_batch or 1,
                )
            )
            continue

        partial_target = entry_price * (1.0 + partial_tp_pct)

        if (not state.partial_exit_taken) and price >= partial_target:
            current_weight = pos.market_value / nav if nav else 0.0
            target_weight = max(current_weight * (1.0 - partial_tp_size), 0.0)
            signals.append(
                TradeSignal(
                    symbol=symbol,
                    action=Action.SELL,
                    conviction=Conviction.HIGH,
                    target_weight=round(target_weight, 4),
                    stop_loss=pos.stop_loss,
                    reasoning=(
                        f"Partial TP: +{partial_tp_pct:.1%} reached."
                    ),
                    exit_reason="tp_partial",
                    entry_batch=state.entry_batch or 1,
                )
            )
            continue

        if state.partial_exit_taken and trailing_stop_pct > 0:
            trail_price = state.peak_price * (1.0 - trailing_stop_pct)
            if price <= trail_price:
                signals.append(
                    TradeSignal(
                        symbol=symbol,
                        action=Action.CLOSE,
                        conviction=Conviction.HIGH,
                        target_weight=0.0,
                        stop_loss=0.0,
                        reasoning=(
                            f"Trailing stop hit ({trailing_stop_pct:.2%})."
                        ),
                        exit_reason="trailing_stop",
                        entry_batch=state.entry_batch or 1,
                    )
                )

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

        if trade.action == "buy":
            state.entry_batch = max(state.entry_batch, trade.entry_batch)
            state.entry_price = trade.price
            state.last_entry_ts = now_ts
            state.partial_exit_taken = False
            state.peak_price = max(state.peak_price, trade.price)
        elif trade.action in ("sell", "close"):
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
