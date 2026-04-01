"""Decision telemetry logging helpers."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import duckdb

from llm_quant.brain.models import MarketContext

CANONICAL_PROFIT_TAKE_REASONS = {
    "take_profit_partial",
    "trailing_stop",
}

LEGACY_PROFIT_TAKE_REASON_MAP = {
    "tp_partial": "take_profit_partial",
}


def log_decision_context(
    conn: duckdb.DuckDBPyConnection,
    decision_id: int,
    pod_id: str,
    context: MarketContext,
    extra: dict[str, Any] | None = None,
) -> None:
    payload_dict: dict[str, Any] = asdict(context)
    if extra:
        payload_dict["runtime"] = extra
    payload = json.dumps(payload_dict, default=str)
    conn.execute(
        """
        INSERT INTO decision_contexts (
            decision_id, pod_id, timestamp, context_json
        ) VALUES (?, ?, ?, ?)
        """,
        [decision_id, pod_id, datetime.now(tz=UTC), payload],
    )
    conn.commit()


def normalize_profit_take_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    normalized = LEGACY_PROFIT_TAKE_REASON_MAP.get(reason, reason)
    return normalized


def is_profit_take_reason(reason: str | None) -> bool:
    normalized = normalize_profit_take_reason(reason)
    return normalized in CANONICAL_PROFIT_TAKE_REASONS


def log_profit_take_event(
    conn: duckdb.DuckDBPyConnection,
    *,
    timestamp: datetime | None = None,
    pod_id: str = "default",
    symbol: str,
    event_type: str,
    decision_source: str | None = None,
    sleeve: str | None = None,
    source_decision_id: int | None = None,
    decision_id: int | None = None,
    trade_id: int | None = None,
    entry_batch: int | None = None,
    reduction_sequence: int | None = None,
    position_fraction: float | None = None,
    action: str | None = None,
    shares: float | None = None,
    price: float | None = None,
    notional: float | None = None,
    trigger_price: float | None = None,
    peak_price: float | None = None,
    drawdown_pct: float | None = None,
    pre_reduction_peak_unrealized_pnl: float | None = None,
    pre_reduction_peak_return_pct: float | None = None,
    trailing_stop_activated_at: datetime | None = None,
    peak_to_reduction_drawdown_pct: float | None = None,
    realized_pnl: float | None = None,
    return_pct: float | None = None,
    rule_name: str | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    normalized_reason = normalize_profit_take_reason(reason)
    payload = json.dumps(metadata or {}, default=str)
    row = conn.execute("SELECT nextval('seq_profit_take_event_id')").fetchone()
    assert row is not None
    event_id: int = row[0]

    conn.execute(
        """
        INSERT INTO profit_take_events (
            event_id,
            timestamp,
            pod_id,
            symbol,
            event_type,
            decision_source,
            sleeve,
            source_decision_id,
            decision_id,
            trade_id,
            entry_batch,
            reduction_sequence,
            position_fraction,
            action,
            shares,
            price,
            notional,
            trigger_price,
            peak_price,
            drawdown_pct,
            pre_reduction_peak_unrealized_pnl,
            pre_reduction_peak_return_pct,
            trailing_stop_activated_at,
            peak_to_reduction_drawdown_pct,
            realized_pnl,
            return_pct,
            rule_name,
            reason,
            metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            event_id,
            timestamp or datetime.now(tz=UTC),
            pod_id,
            symbol,
            event_type,
            decision_source,
            sleeve,
            source_decision_id,
            decision_id,
            trade_id,
            entry_batch,
            reduction_sequence,
            position_fraction,
            action,
            shares,
            price,
            notional,
            trigger_price,
            peak_price,
            drawdown_pct,
            pre_reduction_peak_unrealized_pnl,
            pre_reduction_peak_return_pct,
            trailing_stop_activated_at,
            peak_to_reduction_drawdown_pct,
            realized_pnl,
            return_pct,
            rule_name,
            normalized_reason,
            payload,
        ],
    )
    conn.commit()
    return event_id


__all__ = [
    "CANONICAL_PROFIT_TAKE_REASONS",
    "LEGACY_PROFIT_TAKE_REASON_MAP",
    "is_profit_take_reason",
    "log_decision_context",
    "log_profit_take_event",
    "normalize_profit_take_reason",
]
