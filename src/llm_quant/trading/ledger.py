"""Append-only trade ledger and portfolio snapshot persistence.

All writes go to DuckDB via the connection obtained from
``llm_quant.db.schema.get_connection``.  The module never deletes or
updates existing rows – every call *appends* new records, preserving a
full audit trail.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

import duckdb

from llm_quant.db.integrity import compute_trade_hash, get_latest_hash
from llm_quant.trading.executor import ExecutedTrade
from llm_quant.trading.portfolio import Portfolio
from llm_quant.trading.telemetry import (
    is_profit_take_reason,
    normalize_profit_take_reason,
)

logger = logging.getLogger(__name__)


_REQUIRED_TELEMETRY_SNAPSHOT_FIELDS = (
    "intraday_position_state",
    "order_state",
    "lifecycle_state",
    "exit_policy_state",
)


def _fill_attr(fill: object, name: str, default: object = None) -> object:
    if isinstance(fill, dict):
        return fill.get(name, default)
    return getattr(fill, name, default)


def _fill_float(fill: object, name: str, default: float = 0.0) -> float:
    value = _fill_attr(fill, name, default)
    if value is None:
        return float(default)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    return float(default)


def _normalize_snapshot_mapping(snapshot: object | None) -> dict[str, Any]:
    if snapshot is None:
        return {}
    if isinstance(snapshot, dict):
        return dict(snapshot)
    if hasattr(snapshot, "__dict__"):
        return dict(vars(snapshot))
    return {}


def _snapshot_key_variants(field: str) -> tuple[str, ...]:
    if field == "order_state":
        return ("order_state", "intraday_order_state")
    return (field,)


def _require_complete_telemetry_snapshot(snapshot: object | None) -> dict[str, Any]:
    normalized = _normalize_snapshot_mapping(snapshot)
    missing = [
        field
        for field in _REQUIRED_TELEMETRY_SNAPSHOT_FIELDS
        if not any(
            normalized.get(key) is not None for key in _snapshot_key_variants(field)
        )
    ]
    if missing:
        msg = "INCOMPLETE TELEMETRY SNAPSHOT"
        raise RuntimeError(msg)
    return normalized


def _snapshot_reasoning_suffix(snapshot: dict[str, Any]) -> str:
    payload = {
        "intraday_position_state": snapshot.get("intraday_position_state"),
        "order_state": snapshot.get(
            "order_state", snapshot.get("intraday_order_state")
        ),
        "lifecycle_state": snapshot.get("lifecycle_state"),
        "exit_policy_state": snapshot.get("exit_policy_state"),
    }
    return json.dumps(payload, sort_keys=True, default=str)


def _normalize_broker_fill_semantic_action(
    side: str,
    intent_type: object | None,
    lifecycle_state: object | None,
) -> str:
    """Return a semantic action label that preserves short lifecycle intent."""
    normalized_side = (side or "").strip().lower()
    intent = str(intent_type).strip().lower() if intent_type is not None else ""
    lifecycle = (
        str(lifecycle_state).strip().lower() if lifecycle_state is not None else ""
    )

    if normalized_side == "sell_short":
        return "short_entry"
    if normalized_side == "buy_to_cover":
        return "short_cover"
    if normalized_side == "buy":
        if "cover" in intent or "cover" in lifecycle:
            return "short_cover"
        return "long_entry"
    if normalized_side == "sell":
        if "entry" in intent or "open" in lifecycle:
            return "short_entry"
        return "long_exit"
    return "unknown"


def _normalize_local_trade_semantic_action(trade: ExecutedTrade) -> str:
    """Return a semantic action label for locally executed paper trades."""
    action = (trade.action or "").strip().lower()
    if action == "buy":
        return "long_entry"
    if action == "sell":
        return "long_exit"
    if action == "short":
        return "short_entry"
    if action == "cover":
        return "short_cover"
    if action == "close":
        return "short_cover" if trade.is_short_close else "long_exit"
    return "unknown"


# ---------------------------------------------------------------------------
# Trade logging
# ---------------------------------------------------------------------------


def log_trades(
    conn: duckdb.DuckDBPyConnection,
    trades: list[ExecutedTrade],
    trade_date: date,
    decision_id: int | None = None,
    pod_id: str = "default",
    decision_source: str | None = None,
    sleeve: str | None = None,
    source_decision_id: int | None = None,
) -> list[int]:
    """Persist executed trades to the ``trades`` table.

    Each trade is assigned a new ``trade_id`` from ``seq_trade_id``.

    Parameters
    ----------
    conn:
        Active DuckDB connection.
    trades:
        Trades to record.
    trade_date:
        Date on which the trades were executed (session date).
    decision_id:
        Optional FK linking back to the ``llm_decisions`` row that
        produced this batch.

    Returns
    -------
    list[int]
        The ``trade_id`` values assigned to the inserted rows, in the
        same order as *trades*.
    """
    trade_ids: list[int] = []
    prev_hash = get_latest_hash(conn)

    for trade in trades:
        row = conn.execute("SELECT nextval('seq_trade_id')").fetchone()
        assert row is not None
        trade_id: int = row[0]

        trade_cols = [c[0] for c in conn.execute("DESCRIBE trades").fetchall()]
        insert_cols = [
            "trade_id",
            "date",
            "symbol",
            "action",
            "shares",
            "price",
            "notional",
            "conviction",
            "reasoning",
            "llm_decision_id",
        ]
        insert_vals = [
            trade_id,
            trade_date,
            trade.symbol,
            trade.action,
            trade.shares,
            trade.price,
            trade.notional,
            trade.conviction,
            trade.reasoning,
            decision_id,
        ]

        if "pod_id" in trade_cols:
            insert_cols.insert(2, "pod_id")
            insert_vals.insert(2, pod_id)

        if "strategy_id" in trade_cols:
            insert_cols.append("strategy_id")
            insert_vals.append(trade.strategy_id or None)
        if "semantic_action" in trade_cols:
            insert_cols.append("semantic_action")
            insert_vals.append(_normalize_local_trade_semantic_action(trade))
        if "entry_batch" in trade_cols:
            insert_cols.append("entry_batch")
            insert_vals.append(trade.entry_batch)
        normalized_exit_reason = normalize_profit_take_reason(trade.exit_reason)
        if "exit_reason" in trade_cols:
            insert_cols.append("exit_reason")
            insert_vals.append(normalized_exit_reason)
        if "source_decision_id" in trade_cols:
            insert_cols.append("source_decision_id")
            insert_vals.append(
                source_decision_id if source_decision_id is not None else decision_id
            )
        if "decision_source" in trade_cols:
            insert_cols.append("decision_source")
            insert_vals.append(decision_source)
        if "sleeve" in trade_cols:
            insert_cols.append("sleeve")
            insert_vals.append(sleeve)
        if "is_profit_take" in trade_cols:
            insert_cols.append("is_profit_take")
            insert_vals.append(is_profit_take_reason(normalized_exit_reason))
        if "profit_take_reason" in trade_cols:
            insert_cols.append("profit_take_reason")
            insert_vals.append(
                normalized_exit_reason
                if is_profit_take_reason(normalized_exit_reason)
                else None
            )

        cols_sql = ", ".join(insert_cols)
        placeholders = ", ".join(["?"] * len(insert_cols))
        conn.execute(
            f"INSERT INTO trades ({cols_sql}) VALUES ({placeholders})",
            insert_vals,
        )

        # Retrieve the server-generated created_at, then compute hash
        created_row = conn.execute(
            "SELECT created_at FROM trades WHERE trade_id = ?", [trade_id]
        ).fetchone()
        assert created_row is not None
        created_at = created_row[0]

        row_hash = compute_trade_hash(
            prev_hash,
            trade_id,
            trade_date,
            trade.symbol,
            trade.action,
            trade.shares,
            trade.price,
            trade.notional,
            trade.conviction,
            trade.reasoning,
            decision_id,
            created_at,
        )

        conn.execute(
            "UPDATE trades SET prev_hash = ?, row_hash = ? WHERE trade_id = ?",
            [prev_hash, row_hash, trade_id],
        )

        prev_hash = row_hash
        trade_ids.append(trade_id)
        logger.debug(
            "Logged trade %d: %s %s %.4f shares @ %.4f",
            trade_id,
            trade.action,
            trade.symbol,
            trade.shares,
            trade.price,
        )

    if trade_ids:
        conn.commit()
        logger.info(
            "Persisted %d trade(s) for %s (ids=%s)",
            len(trade_ids),
            trade_date,
            trade_ids,
        )

    return trade_ids


def log_broker_fills(
    conn: duckdb.DuckDBPyConnection,
    fills: list[object],
    trade_date: date,
    pod_id: str,
    decision_id: int | None,
    decision_source: str | None,
    sleeve: str | None,
    source_decision_id: int | None,
    snapshot: object | None = None,
    exit_policy_state: (  # noqa: ARG001 — interface for callers via **kwargs
        dict[str, Any] | None
    ) = None,
) -> list[int]:
    """Persist broker-authoritative fills into the existing ``trades`` table.

    Parameters
    ----------
    fills:
        Iterable of fill-like objects exposing at least
        ``symbol``, ``side``, ``fill_qty``, ``fill_price``, ``order_id``,
        ``intent_type``, ``parent_order_id``, ``exit_reason``, and
        ``lifecycle_state`` either as attributes or dict keys.

    Returns
    -------
    list[int]
        Trade ids inserted into ``trades`` in the same order as ``fills``.
    """
    trade_ids: list[int] = []
    prev_hash = get_latest_hash(conn)
    trade_cols = [c[0] for c in conn.execute("DESCRIBE trades").fetchall()]
    telemetry_snapshot = _require_complete_telemetry_snapshot(snapshot)
    telemetry_suffix = _snapshot_reasoning_suffix(telemetry_snapshot)

    for fill in fills:
        symbol = str(_fill_attr(fill, "symbol", ""))
        side = str(_fill_attr(fill, "side", "")).lower()
        shares = _fill_float(fill, "fill_qty", 0.0)
        price = _fill_float(fill, "fill_price", 0.0)
        order_id = _fill_attr(fill, "order_id")
        intent_type = _fill_attr(fill, "intent_type")
        parent_order_id = _fill_attr(fill, "parent_order_id")
        exit_reason = _fill_attr(fill, "exit_reason")
        lifecycle_state = _fill_attr(fill, "lifecycle_state")
        fill_time = _fill_attr(fill, "fill_time")

        if shares <= 0.0 or price <= 0.0 or not symbol:
            logger.warning(
                "Skipping invalid broker fill: symbol=%s side=%s "
                "qty=%.6f price=%.4f order_id=%s",
                symbol,
                side,
                shares,
                price,
                order_id,
            )
            continue

        row = conn.execute("SELECT nextval('seq_trade_id')").fetchone()
        assert row is not None
        trade_id: int = row[0]

        action = "buy" if side in {"buy", "buy_to_cover"} else "sell"
        semantic_action = _normalize_broker_fill_semantic_action(
            side,
            intent_type,
            lifecycle_state,
        )
        normalized_exit_reason = normalize_profit_take_reason(
            str(exit_reason) if exit_reason is not None else None
        )
        broker_reason_bits = []
        if order_id:
            broker_reason_bits.append(f"order_id={order_id}")
        if parent_order_id:
            broker_reason_bits.append(f"parent_order_id={parent_order_id}")
        if intent_type:
            broker_reason_bits.append(f"intent_type={intent_type}")
        if normalized_exit_reason:
            broker_reason_bits.append(f"exit_reason={normalized_exit_reason}")
        if lifecycle_state:
            broker_reason_bits.append(f"lifecycle_state={lifecycle_state}")
        if fill_time is not None:
            broker_reason_bits.append(f"fill_time={fill_time}")

        reasoning = "Broker fill reconciliation"
        if broker_reason_bits:
            reasoning = f"{reasoning} ({', '.join(broker_reason_bits)})"
        reasoning = f"{reasoning} telemetry={telemetry_suffix}"

        insert_cols = [
            "trade_id",
            "date",
            "symbol",
            "action",
            "shares",
            "price",
            "notional",
            "conviction",
            "reasoning",
            "llm_decision_id",
        ]
        insert_vals = [
            trade_id,
            trade_date,
            symbol,
            action,
            shares,
            price,
            shares * price,
            None,
            reasoning,
            decision_id,
        ]

        if "pod_id" in trade_cols:
            insert_cols.insert(2, "pod_id")
            insert_vals.insert(2, pod_id)

        if "strategy_id" in trade_cols:
            insert_cols.append("strategy_id")
            insert_vals.append(None)
        if "semantic_action" in trade_cols:
            insert_cols.append("semantic_action")
            insert_vals.append(semantic_action)
        if "broker_side" in trade_cols:
            insert_cols.append("broker_side")
            insert_vals.append(side)
        if "intent_type" in trade_cols:
            insert_cols.append("intent_type")
            insert_vals.append(str(intent_type) if intent_type is not None else None)
        if "lifecycle_state" in trade_cols:
            insert_cols.append("lifecycle_state")
            insert_vals.append(
                str(lifecycle_state) if lifecycle_state is not None else None
            )
        if "order_id" in trade_cols:
            insert_cols.append("order_id")
            insert_vals.append(str(order_id) if order_id is not None else None)
        if "parent_order_id" in trade_cols:
            insert_cols.append("parent_order_id")
            insert_vals.append(
                str(parent_order_id) if parent_order_id is not None else None
            )
        if "entry_batch" in trade_cols:
            insert_cols.append("entry_batch")
            insert_vals.append(None)
        if "exit_reason" in trade_cols:
            insert_cols.append("exit_reason")
            insert_vals.append(normalized_exit_reason)
        if "source_decision_id" in trade_cols:
            insert_cols.append("source_decision_id")
            insert_vals.append(
                source_decision_id if source_decision_id is not None else decision_id
            )
        if "decision_source" in trade_cols:
            insert_cols.append("decision_source")
            insert_vals.append(decision_source)
        if "sleeve" in trade_cols:
            insert_cols.append("sleeve")
            insert_vals.append(sleeve)
        if "is_profit_take" in trade_cols:
            insert_cols.append("is_profit_take")
            insert_vals.append(is_profit_take_reason(normalized_exit_reason))
        if "profit_take_reason" in trade_cols:
            insert_cols.append("profit_take_reason")
            insert_vals.append(
                normalized_exit_reason
                if is_profit_take_reason(normalized_exit_reason)
                else None
            )

        cols_sql = ", ".join(insert_cols)
        placeholders = ", ".join(["?"] * len(insert_cols))
        conn.execute(
            f"INSERT INTO trades ({cols_sql}) VALUES ({placeholders})",
            insert_vals,
        )

        created_row = conn.execute(
            "SELECT created_at FROM trades WHERE trade_id = ?", [trade_id]
        ).fetchone()
        assert created_row is not None
        created_at = created_row[0]

        row_hash = compute_trade_hash(
            prev_hash,
            trade_id,
            trade_date,
            symbol,
            action,
            shares,
            price,
            shares * price,
            None,
            reasoning,
            decision_id,
            created_at,
        )

        conn.execute(
            "UPDATE trades SET prev_hash = ?, row_hash = ? WHERE trade_id = ?",
            [prev_hash, row_hash, trade_id],
        )

        prev_hash = row_hash
        trade_ids.append(trade_id)
        logger.debug(
            "Logged broker fill %d: %s %s %.4f shares @ %.4f "
            "order_id=%s intent_type=%s lifecycle_state=%s",
            trade_id,
            action,
            symbol,
            shares,
            price,
            order_id,
            intent_type,
            lifecycle_state,
        )

    if trade_ids:
        conn.commit()
        logger.info(
            "Persisted %d broker fill trade row(s) for %s (ids=%s)",
            len(trade_ids),
            trade_date,
            trade_ids,
        )

    return trade_ids


# ---------------------------------------------------------------------------
# Portfolio snapshots
# ---------------------------------------------------------------------------


def persist_reconciliation_snapshot(
    _conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str,
    snapshot_date: date,
    snapshot: object | None,
) -> dict[str, Any]:
    """Validate and normalize the broker reconciliation telemetry snapshot."""
    normalized = _require_complete_telemetry_snapshot(snapshot)
    return {
        "pod_id": pod_id,
        "snapshot_date": snapshot_date,
        "snapshot": normalized,
    }


def save_portfolio_snapshot(
    conn: duckdb.DuckDBPyConnection,
    portfolio: Portfolio,
    trade_date: date,
    daily_pnl: float | None = None,
    pod_id: str = "default",
) -> int:
    """Save the current portfolio state to ``portfolio_snapshots`` and
    ``positions``.

    Parameters
    ----------
    conn:
        Active DuckDB connection.
    portfolio:
        Portfolio whose state should be persisted.
    trade_date:
        The trading date for the snapshot.
    daily_pnl:
        Optional daily P&L figure.  If *None*, ``NULL`` is stored.

    Returns
    -------
    int
        The assigned ``snapshot_id``.
    """
    row = conn.execute("SELECT nextval('seq_snapshot_id')").fetchone()
    assert row is not None
    snapshot_id: int = row[0]

    nav = portfolio.nav
    long_exposure = sum(
        pos.market_value for pos in portfolio.positions.values() if pos.market_value > 0
    )
    short_exposure = sum(
        abs(pos.market_value)
        for pos in portfolio.positions.values()
        if pos.market_value < 0
    )

    snap_cols = [c[0] for c in conn.execute("DESCRIBE portfolio_snapshots").fetchall()]
    snap_insert_cols = [
        "snapshot_id",
        "date",
        "nav",
        "cash",
        "gross_exposure",
        "net_exposure",
        "total_pnl",
        "daily_pnl",
    ]
    snap_insert_vals: list[Any] = [
        snapshot_id,
        trade_date,
        nav,
        portfolio.cash,
        portfolio.gross_exposure,
        portfolio.net_exposure,
        portfolio.total_pnl,
        daily_pnl,
    ]
    if "pod_id" in snap_cols:
        snap_insert_cols.insert(2, "pod_id")
        snap_insert_vals.insert(2, pod_id)
    if "long_exposure" in snap_cols:
        snap_insert_cols.append("long_exposure")
        snap_insert_vals.append(long_exposure)
    if "short_exposure" in snap_cols:
        snap_insert_cols.append("short_exposure")
        snap_insert_vals.append(short_exposure)

    snap_cols_sql = ", ".join(snap_insert_cols)
    snap_placeholders = ", ".join(["?"] * len(snap_insert_cols))
    conn.execute(
        f"INSERT INTO portfolio_snapshots ({snap_cols_sql}) "
        f"VALUES ({snap_placeholders})",
        snap_insert_vals,
    )

    # Persist individual positions
    position_cols = [c[0] for c in conn.execute("DESCRIBE positions").fetchall()]
    for pos in portfolio.positions.values():
        weight = (pos.market_value / nav) if nav else 0.0
        position_insert_cols = [
            "snapshot_id",
            "symbol",
            "shares",
            "avg_cost",
            "current_price",
            "market_value",
            "unrealized_pnl",
            "weight",
            "stop_loss",
        ]
        position_insert_vals: list[Any] = [
            snapshot_id,
            pos.symbol,
            pos.shares,
            pos.avg_cost,
            pos.current_price,
            pos.market_value,
            pos.unrealized_pnl,
            weight,
            pos.stop_loss,
        ]
        if "is_short" in position_cols:
            position_insert_cols.append("is_short")
            position_insert_vals.append(pos.is_short)
        if "short_proceeds" in position_cols:
            position_insert_cols.append("short_proceeds")
            position_insert_vals.append(pos.short_proceeds)

        position_cols_sql = ", ".join(position_insert_cols)
        position_placeholders = ", ".join(["?"] * len(position_insert_cols))
        conn.execute(
            f"INSERT INTO positions ({position_cols_sql}) "
            f"VALUES ({position_placeholders})",
            position_insert_vals,
        )

    conn.commit()
    logger.info(
        "Saved snapshot %d for %s: NAV=%.2f, cash=%.2f, %d position(s)",
        snapshot_id,
        trade_date,
        nav,
        portfolio.cash,
        len(portfolio.positions),
    )
    return snapshot_id


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def get_recent_trades(
    conn: duckdb.DuckDBPyConnection,
    limit: int = 20,
    pod_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return the most recent trades as a list of dicts.

    Parameters
    ----------
    conn:
        Active DuckDB connection.
    limit:
        Maximum number of rows to return (most recent first).
    pod_id:
        If provided, only return trades for this pod. If *None*, return
        all trades (backward compatible).

    Returns
    -------
    list[dict]
        Each dict mirrors a row in the ``trades`` table.
    """
    trade_cols = [c[0] for c in conn.execute("DESCRIBE trades").fetchall()]
    has_pod_id = "pod_id" in trade_cols

    if has_pod_id and pod_id is not None:
        result = conn.execute(
            """
            SELECT
                trade_id,
                date,
                pod_id,
                symbol,
                action,
                shares,
                price,
                notional,
                conviction,
                reasoning,
                llm_decision_id,
                created_at
            FROM trades
            WHERE pod_id = ?
            ORDER BY date DESC, trade_id DESC
            LIMIT ?
            """,
            [pod_id, limit],
        ).fetchall()
        columns = [
            "trade_id",
            "date",
            "pod_id",
            "symbol",
            "action",
            "shares",
            "price",
            "notional",
            "conviction",
            "reasoning",
            "llm_decision_id",
            "created_at",
        ]
    elif has_pod_id:
        result = conn.execute(
            """
            SELECT
                trade_id,
                date,
                pod_id,
                symbol,
                action,
                shares,
                price,
                notional,
                conviction,
                reasoning,
                llm_decision_id,
                created_at
            FROM trades
            ORDER BY date DESC, trade_id DESC
            LIMIT ?
            """,
            [limit],
        ).fetchall()
        columns = [
            "trade_id",
            "date",
            "pod_id",
            "symbol",
            "action",
            "shares",
            "price",
            "notional",
            "conviction",
            "reasoning",
            "llm_decision_id",
            "created_at",
        ]
    else:
        result = conn.execute(
            """
            SELECT
                trade_id,
                date,
                symbol,
                action,
                shares,
                price,
                notional,
                conviction,
                reasoning,
                llm_decision_id,
                created_at
            FROM trades
            ORDER BY date DESC, trade_id DESC
            LIMIT ?
            """,
            [limit],
        ).fetchall()
        columns = [
            "trade_id",
            "date",
            "symbol",
            "action",
            "shares",
            "price",
            "notional",
            "conviction",
            "reasoning",
            "llm_decision_id",
            "created_at",
        ]

    trades = [dict(zip(columns, row, strict=True)) for row in result]

    logger.debug("Fetched %d recent trade(s).", len(trades))
    return trades


def get_portfolio_history(
    conn: duckdb.DuckDBPyConnection,
    days: int = 30,
    pod_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return portfolio snapshots for the last *days* calendar days.

    Parameters
    ----------
    conn:
        Active DuckDB connection.
    days:
        Look-back window in calendar days.
    pod_id:
        If provided, only return snapshots for this pod. If *None*, return
        all snapshots (backward compatible).

    Returns
    -------
    list[dict]
        Each dict mirrors a row in ``portfolio_snapshots``, ordered by
        date ascending.
    """
    snap_cols = [c[0] for c in conn.execute("DESCRIBE portfolio_snapshots").fetchall()]
    has_pod_id = "pod_id" in snap_cols
    has_long_exposure = "long_exposure" in snap_cols
    has_short_exposure = "short_exposure" in snap_cols

    select_cols = [
        "snapshot_id",
        "date",
        "nav",
        "cash",
        "gross_exposure",
        "net_exposure",
        "total_pnl",
        "daily_pnl",
    ]
    if has_long_exposure:
        select_cols.append("long_exposure")
    if has_short_exposure:
        select_cols.append("short_exposure")
    select_cols.append("created_at")

    prefixed_select_cols = list(select_cols)
    if has_pod_id:
        prefixed_select_cols.insert(2, "pod_id")
    select_clause = ",\n                ".join(prefixed_select_cols)

    if has_pod_id and pod_id is not None:
        result = conn.execute(
            f"""
            SELECT
                {select_clause}
            FROM portfolio_snapshots
            WHERE date >= CURRENT_DATE - INTERVAL {int(days)} DAY
              AND pod_id = ?
            ORDER BY date ASC, snapshot_id ASC
            """,
            [pod_id],
        ).fetchall()
        columns = prefixed_select_cols
    elif has_pod_id:
        result = conn.execute(
            f"""
            SELECT
                {select_clause}
            FROM portfolio_snapshots
            WHERE date >= CURRENT_DATE - INTERVAL {int(days)} DAY
            ORDER BY date ASC, snapshot_id ASC
            """,
        ).fetchall()
        columns = prefixed_select_cols
    else:
        result = conn.execute(
            f"""
            SELECT
                {",\n                ".join(select_cols)}
            FROM portfolio_snapshots
            WHERE date >= CURRENT_DATE - INTERVAL {int(days)} DAY
            ORDER BY date ASC, snapshot_id ASC
            """,
        ).fetchall()
        columns = select_cols

    history = [dict(zip(columns, row, strict=True)) for row in result]

    logger.debug("Fetched %d snapshot(s) over last %d day(s).", len(history), days)
    return history
