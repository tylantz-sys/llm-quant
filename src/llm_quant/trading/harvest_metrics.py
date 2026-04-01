"""Harvest metrics derived from profit-taking telemetry."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time
from typing import Any

import duckdb


def _coerce_to_datetime_boundary(
    value: datetime | date | None,
    *,
    end_of_day: bool,
) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    boundary_time = time.max if end_of_day else time.min
    return datetime.combine(value, boundary_time)


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _sum_numeric(events: list[dict[str, Any]], field: str) -> float:
    return sum(float(event.get(field) or 0.0) for event in events)


def _avg_optional_numeric(events: list[dict[str, Any]], field: str) -> float | None:
    values = [
        float(event.get(field))
        for event in events
        if event.get(field) is not None
    ]
    if not values:
        return None
    return sum(values) / len(values)


def fetch_profit_take_events(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str | None = None,
    start: datetime | date | None = None,
    end: datetime | date | None = None,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch normalized profit-take events for Phase 5b metric computation."""
    conditions: list[str] = []
    params: list[Any] = []

    if pod_id is not None:
        conditions.append("pod_id = ?")
        params.append(pod_id)
    if symbol is not None:
        conditions.append("symbol = ?")
        params.append(symbol)

    start_dt = _coerce_to_datetime_boundary(start, end_of_day=False)
    if start_dt is not None:
        conditions.append("timestamp >= ?")
        params.append(start_dt)

    end_dt = _coerce_to_datetime_boundary(end, end_of_day=True)
    if end_dt is not None:
        conditions.append("timestamp <= ?")
        params.append(end_dt)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"""
        SELECT
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
            reason
        FROM profit_take_events
        {where_clause}
        ORDER BY timestamp ASC, event_id ASC
        """,
        params,
    ).fetchall()

    keys = [
        "event_id",
        "timestamp",
        "pod_id",
        "symbol",
        "event_type",
        "decision_source",
        "sleeve",
        "source_decision_id",
        "decision_id",
        "trade_id",
        "entry_batch",
        "reduction_sequence",
        "position_fraction",
        "action",
        "shares",
        "price",
        "notional",
        "trigger_price",
        "peak_price",
        "drawdown_pct",
        "pre_reduction_peak_unrealized_pnl",
        "pre_reduction_peak_return_pct",
        "trailing_stop_activated_at",
        "peak_to_reduction_drawdown_pct",
        "realized_pnl",
        "return_pct",
        "rule_name",
        "reason",
    ]
    return [dict(zip(keys, row, strict=True)) for row in rows]


def compute_harvest_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate harvest metrics from profit-take telemetry events."""
    executed = [event for event in events if event.get("event_type") == "executed"]

    positive_pnl = [
        event
        for event in executed
        if (event.get("pre_reduction_peak_unrealized_pnl") or 0.0) > 0
        and event.get("realized_pnl") is not None
    ]
    positive_return = [
        event
        for event in executed
        if (event.get("pre_reduction_peak_return_pct") or 0.0) > 0
        and event.get("return_pct") is not None
    ]
    tp1_pnl = [event for event in positive_pnl if event.get("reduction_sequence") == 1]
    tp1_return = [
        event for event in positive_return if event.get("reduction_sequence") == 1
    ]
    trailing_pnl = [
        event
        for event in positive_pnl
        if event.get("reason") == "trailing_stop"
        and event.get("trailing_stop_activated_at") is not None
    ]
    trailing_return = [
        event
        for event in positive_return
        if event.get("reason") == "trailing_stop"
        and event.get("trailing_stop_activated_at") is not None
    ]
    runner_events = [
        event
        for event in executed
        if event.get("reduction_sequence") == 1
        and event.get("position_fraction") is not None
    ]

    realized_harvest_pnl = _sum_numeric(executed, "realized_pnl")
    peak_unrealized_pnl = _sum_numeric(positive_pnl, "pre_reduction_peak_unrealized_pnl")
    realized_return_sum = _sum_numeric(positive_return, "return_pct")
    peak_return_sum = _sum_numeric(positive_return, "pre_reduction_peak_return_pct")

    tp1_realized_pnl = _sum_numeric(tp1_pnl, "realized_pnl")
    tp1_peak_pnl = _sum_numeric(tp1_pnl, "pre_reduction_peak_unrealized_pnl")
    tp1_realized_return = _sum_numeric(tp1_return, "return_pct")
    tp1_peak_return = _sum_numeric(tp1_return, "pre_reduction_peak_return_pct")

    trailing_realized_pnl = _sum_numeric(trailing_pnl, "realized_pnl")
    trailing_peak_pnl = _sum_numeric(trailing_pnl, "pre_reduction_peak_unrealized_pnl")
    trailing_realized_return = _sum_numeric(trailing_return, "return_pct")
    trailing_peak_return = _sum_numeric(
        trailing_return, "pre_reduction_peak_return_pct"
    )

    avg_position_fraction = _avg_optional_numeric(runner_events, "position_fraction")

    reason_counts = Counter(
        str(event.get("reason")) for event in executed if event.get("reason")
    )

    return {
        "profit_take_events": len(events),
        "executed_profit_take_events": len(executed),
        "symbols_harvested": len(
            {str(event["symbol"]) for event in executed if event.get("symbol")}
        ),
        "realized_harvest_pnl": realized_harvest_pnl,
        "peak_unrealized_pnl_reference": peak_unrealized_pnl,
        "capture_ratio": _safe_ratio(realized_harvest_pnl, peak_unrealized_pnl),
        "capture_ratio_return_pct": _safe_ratio(realized_return_sum, peak_return_sum),
        "giveback_ratio": _safe_ratio(
            peak_unrealized_pnl - realized_harvest_pnl,
            peak_unrealized_pnl,
        ),
        "giveback_ratio_return_pct": _safe_ratio(
            peak_return_sum - realized_return_sum,
            peak_return_sum,
        ),
        "tp1_effectiveness": _safe_ratio(tp1_realized_pnl, tp1_peak_pnl),
        "tp1_effectiveness_return_pct": _safe_ratio(
            tp1_realized_return,
            tp1_peak_return,
        ),
        "runner_retention_proxy": (
            max(0.0, 1.0 - avg_position_fraction)
            if avg_position_fraction is not None
            else None
        ),
        "trailing_salvage_proxy": _safe_ratio(
            trailing_realized_pnl,
            trailing_peak_pnl,
        ),
        "trailing_salvage_proxy_return_pct": _safe_ratio(
            trailing_realized_return,
            trailing_peak_return,
        ),
        "realized_to_peak_ratio": _safe_ratio(
            realized_harvest_pnl,
            peak_unrealized_pnl,
        ),
        "realized_to_peak_ratio_return_pct": _safe_ratio(
            realized_return_sum,
            peak_return_sum,
        ),
        "avg_peak_to_reduction_drawdown_pct": _avg_optional_numeric(
            executed,
            "peak_to_reduction_drawdown_pct",
        ),
        "exit_reason_breakdown": dict(reason_counts),
    }


def compute_harvest_metrics_from_db(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str | None = None,
    start: datetime | date | None = None,
    end: datetime | date | None = None,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Fetch and compute harvest metrics in one call."""
    events = fetch_profit_take_events(
        conn,
        pod_id=pod_id,
        start=start,
        end=end,
        symbol=symbol,
    )
    metrics = compute_harvest_metrics(events)
    metrics["events"] = events
    return metrics


__all__ = [
    "compute_harvest_metrics",
    "compute_harvest_metrics_from_db",
    "fetch_profit_take_events",
]
