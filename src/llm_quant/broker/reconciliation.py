"""Broker-authoritative order reconciliation helpers."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

import duckdb

from llm_quant.broker.alpaca import AlpacaClient, AlpacaError
from llm_quant.broker.event_ledger import (
    BrokerEventType,
    BrokerLedgerEvent,
    append_event,
    rebuild_position_state_from_events,
)
from llm_quant.broker.exceptions import (
    CausalIntegrityError,
    OCOConflictError,
    OrderingError,
    PositionInvariantError,
    ReconciliationError,
)
from llm_quant.broker.state_machine import (
    BrokerLifecycleSnapshot,
    snapshot_from_broker_state,
)

logger = logging.getLogger(__name__)


_TERMINAL_ORDER_STATUSES = {
    "filled",
    "canceled",
    "cancelled",
    "expired",
    "rejected",
    "done_for_day",
    "replaced",
}

_ACTIVE_ORDER_STATUSES = {
    "new",
    "accepted",
    "accepted_for_bidding",
    "pending_new",
    "partially_filled",
    "pending_replace",
    "pending_cancel",
    "held",
    "stopped",
    "calculated",
}

_REQUIRED_TELEMETRY_SNAPSHOT_FIELDS = (
    "intraday_position_state",
    "order_state",
    "lifecycle_state",
    "exit_policy_state",
)

_EXIT_INTENT_TYPES = {
    "take_profit_1",
    "take_profit_2",
    "stop_loss",
    "trailing_stop",
    "forced_exit",
}

_PROTECTION_INTENT_TYPES = {
    "take_profit_1",
    "take_profit_2",
    "stop_loss",
    "trailing_stop",
}
_BRACKET_EXIT_GROUPS = (
    {"take_profit_1", "take_profit_2", "stop_loss", "trailing_stop"},
    {"take_profit_2", "stop_loss", "trailing_stop"},
    {"take_profit_2", "stop_loss"},
    {"take_profit_1"},
)


class ReconciliationStatus(StrEnum):
    SUCCESS = "SUCCESS"
    RECOVERABLE = "RECOVERABLE"
    FATAL = "FATAL"


@dataclass(slots=True)
class BrokerOrderStatus:
    order_id: str
    symbol: str
    side: str
    status: str
    qty: float
    filled_qty: float
    remaining_qty: float
    filled_avg_price: float | None
    submitted_at: datetime | None = None
    updated_at: datetime | None = None
    intent_type: str | None = None
    parent_order_id: str | None = None
    exit_reason: str | None = None
    replaced_by_order_id: str | None = None
    rejection_reason: str | None = None
    fill_events: list["BrokerFillEvent"] | None = None


@dataclass(slots=True)
class BrokerFillEvent:
    order_id: str
    symbol: str
    side: str
    fill_qty: float
    fill_price: float
    fill_time: datetime
    intent_type: str | None = None
    parent_order_id: str | None = None
    exit_reason: str | None = None
    lifecycle_state: str | None = None
    is_forced_liquidation: bool = False
    commission: float = 0.0
    execution_id: str | None = None
    execution_ref: str | None = None
    execution_action: str | None = None
    corrected_execution_id: str | None = None
    reversal_execution_id: str | None = None
    broker_fill_key: str | None = None
    is_correction: bool = False
    is_reversal: bool = False


@dataclass(slots=True, frozen=True)
class ReconciledFillDecision:
    fill: BrokerFillEvent
    execution_identity: str
    resolution: str
    signed_fill_qty: float


@dataclass(slots=True)
class ReconciliationResult:
    statuses: list[BrokerOrderStatus]
    fills: list[BrokerFillEvent]
    open_order_ids: list[str]
    terminal_order_ids: list[str]
    lifecycle: dict[str, BrokerLifecycleSnapshot]
    status: ReconciliationStatus = ReconciliationStatus.SUCCESS
    status_reason: str | None = None
    applied_fill_count: int = 0
    persisted_fill_count: int = 0
    snapshot: dict[str, Any] | None = None


def _parse_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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


def _validate_telemetry_snapshot(snapshot: object | None) -> dict[str, Any]:
    normalized = _normalize_snapshot_mapping(snapshot)
    missing = [
        field
        for field in _REQUIRED_TELEMETRY_SNAPSHOT_FIELDS
        if not any(normalized.get(key) is not None for key in _snapshot_key_variants(field))
    ]
    if missing:
        raise ReconciliationError("INCOMPLETE TELEMETRY SNAPSHOT")
    return normalized


def _ensure_reconciliation_tables(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS broker_submitted_orders (
            order_id VARCHAR PRIMARY KEY,
            pod_id VARCHAR NOT NULL DEFAULT 'default',
            symbol VARCHAR NOT NULL,
            side VARCHAR NOT NULL,
            qty DOUBLE,
            order_type VARCHAR,
            time_in_force VARCHAR,
            intent_type VARCHAR,
            parent_order_id VARCHAR,
            exit_reason VARCHAR,
            client_order_id VARCHAR,
            status VARCHAR,
            submitted_at TIMESTAMP,
            updated_at TIMESTAMP,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS broker_fill_events (
            order_id VARCHAR NOT NULL,
            symbol VARCHAR NOT NULL,
            side VARCHAR NOT NULL,
            fill_qty DOUBLE NOT NULL,
            fill_price DOUBLE NOT NULL,
            fill_time TIMESTAMP NOT NULL,
            intent_type VARCHAR,
            parent_order_id VARCHAR,
            exit_reason VARCHAR,
            lifecycle_state VARCHAR,
            is_forced_liquidation BOOLEAN NOT NULL DEFAULT FALSE,
            commission DOUBLE NOT NULL DEFAULT 0.0,
            execution_id VARCHAR,
            execution_ref VARCHAR,
            execution_action VARCHAR,
            corrected_execution_id VARCHAR,
            reversal_execution_id VARCHAR,
            broker_fill_key VARCHAR,
            is_correction BOOLEAN NOT NULL DEFAULT FALSE,
            is_reversal BOOLEAN NOT NULL DEFAULT FALSE,
            pod_id VARCHAR NOT NULL DEFAULT 'default',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (order_id, fill_time, fill_qty, fill_price)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS broker_position_lifecycle (
            pod_id VARCHAR NOT NULL DEFAULT 'default',
            symbol VARCHAR NOT NULL,
            state VARCHAR NOT NULL,
            entry_order_id VARCHAR,
            exit_order_id VARCHAR,
            position_qty DOUBLE NOT NULL DEFAULT 0,
            has_entry_fill BOOLEAN NOT NULL DEFAULT FALSE,
            has_exit_orders BOOLEAN NOT NULL DEFAULT FALSE,
            has_open_position BOOLEAN NOT NULL DEFAULT FALSE,
            is_flat BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (pod_id, symbol)
        )
        """
    )
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info('broker_fill_events')").fetchall()
    }
    if "execution_id" not in columns:
        conn.execute("ALTER TABLE broker_fill_events ADD COLUMN execution_id VARCHAR")
    if "execution_ref" not in columns:
        conn.execute("ALTER TABLE broker_fill_events ADD COLUMN execution_ref VARCHAR")
    if "execution_action" not in columns:
        conn.execute("ALTER TABLE broker_fill_events ADD COLUMN execution_action VARCHAR")
    if "corrected_execution_id" not in columns:
        conn.execute("ALTER TABLE broker_fill_events ADD COLUMN corrected_execution_id VARCHAR")
    if "reversal_execution_id" not in columns:
        conn.execute("ALTER TABLE broker_fill_events ADD COLUMN reversal_execution_id VARCHAR")
    if "broker_fill_key" not in columns:
        conn.execute("ALTER TABLE broker_fill_events ADD COLUMN broker_fill_key VARCHAR")
    if "is_correction" not in columns:
        conn.execute("ALTER TABLE broker_fill_events ADD COLUMN is_correction BOOLEAN NOT NULL DEFAULT FALSE")
    if "is_reversal" not in columns:
        conn.execute("ALTER TABLE broker_fill_events ADD COLUMN is_reversal BOOLEAN NOT NULL DEFAULT FALSE")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_broker_submitted_orders_pod_status
            ON broker_submitted_orders (pod_id, status, symbol)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_broker_fill_events_pod_time
            ON broker_fill_events (pod_id, fill_time DESC, symbol)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_broker_fill_events_identity
            ON broker_fill_events (pod_id, order_id, execution_id, execution_ref, broker_fill_key)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_broker_position_lifecycle_pod_state
            ON broker_position_lifecycle (pod_id, state, symbol)
        """
    )
    conn.commit()


def _coerce_mapping(item: Any) -> dict[str, Any]:
    if item is None:
        return {}
    if isinstance(item, dict):
        return item
    if hasattr(item, "__dict__"):
        return dict(item.__dict__)
    try:
        return asdict(item)
    except TypeError:
        return {}


def _metadata_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _normalized_execution_action(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text or None


def _execution_identity(fill: BrokerFillEvent) -> str:
    for candidate in (
        fill.execution_id,
        fill.execution_ref,
        fill.corrected_execution_id,
        fill.reversal_execution_id,
        fill.broker_fill_key,
    ):
        if candidate:
            return str(candidate)
    normalized_time = fill.fill_time.astimezone(UTC).isoformat()
    return "|".join(
        [
            fill.order_id,
            fill.symbol,
            fill.side.lower(),
            f"{fill.fill_qty:.12f}",
            f"{fill.fill_price:.12f}",
            normalized_time,
        ]
    )


def _canonical_fill_economic_key(fill: BrokerFillEvent) -> tuple[str, str, str, float, float, str]:
    return (
        fill.order_id,
        fill.symbol,
        fill.side.lower(),
        round(fill.fill_qty, 12),
        round(fill.fill_price, 12),
        fill.fill_time.astimezone(UTC).isoformat(),
    )


def _canonical_lineage_identity(status: BrokerOrderStatus) -> tuple[str, str, str | None]:
    return (
        status.symbol,
        status.side.lower(),
        status.parent_order_id,
    )


def _validate_submitted_order_identity(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str,
    record: dict[str, Any],
) -> None:
    order_id = str(record.get("order_id") or record.get("id") or "")
    if not order_id:
        return
    existing = conn.execute(
        """
        SELECT symbol, side, parent_order_id
        FROM broker_submitted_orders
        WHERE pod_id = ? AND order_id = ?
        """,
        [pod_id, order_id],
    ).fetchone()
    if existing is None:
        return
    existing_identity = (
        str(existing[0] or ""),
        str(existing[1] or "").lower(),
        str(existing[2]) if existing[2] is not None else None,
    )
    candidate_identity = (
        str(record.get("symbol") or ""),
        str(record.get("side") or "").lower(),
        str(record.get("parent_order_id")) if record.get("parent_order_id") is not None else None,
    )
    if existing_identity != candidate_identity:
        raise OrderingError("UNKNOWN_SUBMITTED_ORDER_LINEAGE")


def _signed_fill_qty(fill: BrokerFillEvent) -> float:
    base_qty = _parse_float(fill.fill_qty)
    if fill.side.lower() == "sell":
        base_qty = -base_qty
    if fill.is_reversal:
        return -base_qty
    return base_qty


def _status_from_order_record(
    order: dict[str, Any],
    fallback: dict[str, Any] | None = None,
) -> BrokerOrderStatus:
    record = dict(fallback or {})
    record.update(order)
    return BrokerOrderStatus(
        order_id=str(record.get("id") or record.get("order_id") or ""),
        symbol=str(record.get("symbol") or ""),
        side=str(record.get("side") or ""),
        status=str(record.get("status") or "unknown").lower(),
        qty=_parse_float(record.get("qty")),
        filled_qty=_parse_float(record.get("filled_qty")),
        remaining_qty=_parse_float(record.get("remaining_qty"))
        or max(_parse_float(record.get("qty")) - _parse_float(record.get("filled_qty")), 0.0),
        filled_avg_price=(
            _parse_float(record.get("filled_avg_price"))
            if record.get("filled_avg_price") not in (None, "")
            else None
        ),
        submitted_at=_parse_dt(record.get("submitted_at")),
        updated_at=_parse_dt(
            record.get("updated_at")
            or record.get("filled_at")
            or record.get("canceled_at")
            or record.get("expired_at")
            or record.get("replaced_at")
        ),
        intent_type=record.get("intent_type"),
        parent_order_id=record.get("parent_order_id"),
        exit_reason=record.get("exit_reason"),
        replaced_by_order_id=record.get("replaced_by") or record.get("replaced_by_order_id"),
        rejection_reason=record.get("rejection_reason"),
        fill_events=[
            BrokerFillEvent(
                order_id=str(fill.get("order_id") or record.get("id") or record.get("order_id") or ""),
                symbol=str(fill.get("symbol") or record.get("symbol") or ""),
                side=str(fill.get("side") or record.get("side") or ""),
                fill_qty=_parse_float(fill.get("fill_qty") or fill.get("qty")),
                fill_price=_parse_float(fill.get("fill_price") or fill.get("price")),
                fill_time=_parse_dt(fill.get("fill_time") or fill.get("timestamp")) or datetime.now(tz=UTC),
                intent_type=fill.get("intent_type") or record.get("intent_type"),
                parent_order_id=fill.get("parent_order_id") or record.get("parent_order_id"),
                exit_reason=fill.get("exit_reason") or record.get("exit_reason"),
                lifecycle_state=fill.get("lifecycle_state"),
                is_forced_liquidation=bool(fill.get("is_forced_liquidation", False)),
                commission=_parse_float(fill.get("commission")),
                execution_id=(
                    str(fill.get("execution_id"))
                    if fill.get("execution_id") not in (None, "")
                    else None
                ),
                execution_ref=(
                    str(fill.get("execution_ref") or fill.get("trade_id") or fill.get("id"))
                    if fill.get("execution_ref") not in (None, "") or fill.get("trade_id") not in (None, "") or fill.get("id") not in (None, "")
                    else None
                ),
                execution_action=_normalized_execution_action(fill.get("execution_action") or fill.get("event")),
                corrected_execution_id=(
                    str(fill.get("corrected_execution_id"))
                    if fill.get("corrected_execution_id") not in (None, "")
                    else None
                ),
                reversal_execution_id=(
                    str(fill.get("reversal_execution_id"))
                    if fill.get("reversal_execution_id") not in (None, "")
                    else None
                ),
                broker_fill_key=(
                    str(fill.get("broker_fill_key") or fill.get("fill_id"))
                    if fill.get("broker_fill_key") not in (None, "") or fill.get("fill_id") not in (None, "")
                    else None
                ),
                is_correction=bool(fill.get("is_correction", False)),
                is_reversal=bool(fill.get("is_reversal", False)),
            )
            for fill in (record.get("fill_events") or [])
        ],
    )


def _fills_from_status(status: BrokerOrderStatus) -> list[BrokerFillEvent]:
    if status.fill_events:
        return list(status.fill_events)

    if status.filled_qty <= 0:
        return []
    if status.filled_avg_price is None or status.filled_avg_price <= 0:
        return []

    fill_time = status.updated_at or status.submitted_at or datetime.now(tz=UTC)
    return [
        BrokerFillEvent(
            order_id=status.order_id,
            symbol=status.symbol,
            side=status.side,
            fill_qty=status.filled_qty,
            fill_price=status.filled_avg_price,
            fill_time=fill_time,
            intent_type=status.intent_type,
            parent_order_id=status.parent_order_id,
            exit_reason=status.exit_reason,
            execution_ref=f"{status.order_id}:{fill_time.astimezone(UTC).isoformat()}:{status.filled_qty}:{status.filled_avg_price}",
        )
    ]


def _event_type_for_submission(intent_type: str | None) -> BrokerEventType:
    return BrokerEventType.ORDER_SUBMITTED


def _event_type_for_fill(status: BrokerOrderStatus, fill: BrokerFillEvent) -> BrokerEventType:
    normalized_intent = (fill.intent_type or status.intent_type or "").strip().lower()
    if status.remaining_qty > 1e-9 or status.status == "partially_filled":
        return BrokerEventType.ORDER_PARTIALLY_FILLED
    if normalized_intent in {"stop_loss", "trailing_stop", "forced_exit"}:
        return BrokerEventType.STOP_TRIGGERED
    if normalized_intent == "take_profit_1":
        return BrokerEventType.TAKE_PROFIT_1_FILLED
    if normalized_intent == "take_profit_2":
        return BrokerEventType.TAKE_PROFIT_2_FILLED
    return BrokerEventType.ORDER_FILLED


def _event_type_for_terminal_status(status: BrokerOrderStatus) -> BrokerEventType | None:
    if status.status in {"canceled", "cancelled", "expired", "rejected", "done_for_day"}:
        return BrokerEventType.ORDER_CANCELED
    if status.status == "replaced":
        return BrokerEventType.ORDER_CANCELED
    return None


def _build_ledger_event_from_status(
    *,
    pod_id: str,
    status: BrokerOrderStatus,
    event_type: BrokerEventType,
    event_time: datetime,
    fill: BrokerFillEvent | None = None,
    resolution: str | None = None,
) -> BrokerLedgerEvent:
    price = None
    qty = status.qty
    metadata: dict[str, Any] | None = None
    if fill is not None:
        qty = fill.fill_qty
        price = fill.fill_price
        metadata = {
            "execution_identity": _execution_identity(fill),
            "execution_action": fill.execution_action,
            "resolution": resolution or "applied",
            "is_correction": fill.is_correction,
            "is_reversal": fill.is_reversal,
        }
    elif status.filled_avg_price is not None:
        price = status.filled_avg_price
        if status.filled_qty > 0:
            qty = status.filled_qty
    return BrokerLedgerEvent(
        order_id=status.order_id,
        event_type=event_type,
        symbol=status.symbol,
        side=status.side,
        qty=qty,
        price=price,
        event_time=event_time,
        parent_order_id=status.parent_order_id,
        intent_type=status.intent_type,
        exit_reason=status.exit_reason,
        pod_id=pod_id,
        metadata_json=_metadata_json(metadata) if metadata is not None else None,
        event_chain_id=status.parent_order_id or status.order_id,
        parent_event_order_id=status.parent_order_id,
    )


def _append_replacement_event(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str,
    status: BrokerOrderStatus,
) -> None:
    if status.status != "replaced":
        return
    metadata_json = None
    if status.replaced_by_order_id:
        metadata_json = _metadata_json({"replaced_by_order_id": status.replaced_by_order_id})
    append_event(
        conn,
        BrokerLedgerEvent(
            order_id=status.order_id,
            event_type=BrokerEventType.ORDER_CANCELED,
            symbol=status.symbol,
            side=status.side,
            qty=status.qty,
            price=status.filled_avg_price,
            event_time=status.updated_at or datetime.now(tz=UTC),
            parent_order_id=status.parent_order_id,
            intent_type=status.intent_type,
            exit_reason=status.exit_reason,
            pod_id=pod_id,
            metadata_json=metadata_json,
            event_chain_id=status.parent_order_id or status.order_id,
            parent_event_order_id=status.parent_order_id,
        ),
    )


def _position_was_closed_by_event(
    *,
    conn: duckdb.DuckDBPyConnection,
    pod_id: str,
    symbol: str,
) -> bool:
    rebuilt = rebuild_position_state_from_events(conn, pod_id=pod_id).get(symbol)
    if rebuilt is not None:
        if rebuilt.is_closed:
            return True
        return rebuilt.position_qty <= 0.0 and rebuilt.closed_at is not None
    row = conn.execute(
        """
        SELECT 1
        FROM broker_fill_events
        WHERE pod_id = ? AND symbol = ? AND COALESCE(exit_reason, '') <> ''
        ORDER BY fill_time DESC
        LIMIT 1
        """,
        [pod_id, symbol],
    ).fetchone()
    return row is not None


def _rebuild_portfolio_from_reconciliation(
    portfolio: Any,
    *,
    conn: duckdb.DuckDBPyConnection,
    pod_id: str,
) -> int:
    if portfolio is None or not hasattr(portfolio, "apply_broker_fill"):
        return 0
    if not hasattr(portfolio, "positions") or not hasattr(portfolio, "cash"):
        return 0

    initial_capital = float(getattr(portfolio, "initial_capital", float(getattr(portfolio, "cash", 0.0))))
    portfolio.positions.clear()
    portfolio.cash = initial_capital

    rows = conn.execute(
        """
        SELECT symbol, side, fill_qty, fill_price, fill_time, order_id, intent_type, is_reversal
        FROM broker_fill_events
        WHERE pod_id = ?
        ORDER BY fill_time ASC, created_at ASC, order_id ASC
        """,
        [pod_id],
    ).fetchall()

    applied = 0
    for row in rows:
        symbol, side, fill_qty, fill_price, fill_time, order_id, intent_type, is_reversal = row
        effective_side = str(side)
        effective_qty = _parse_float(fill_qty)
        if bool(is_reversal):
            effective_side = "buy" if effective_side.lower() == "sell" else "sell"
        portfolio.apply_broker_fill(
            str(symbol),
            effective_side,
            effective_qty,
            _parse_float(fill_price),
            0.0,
            _parse_dt(fill_time) or datetime.now(tz=UTC),
            str(order_id) if order_id is not None else None,
            str(intent_type) if intent_type is not None else None,
        )
        applied += 1

    rebuild_position_state_from_events(conn, pod_id=pod_id)
    return applied


def _persist_lifecycle_snapshot(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str,
    snapshot: BrokerLifecycleSnapshot,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO broker_position_lifecycle (
            pod_id,
            symbol,
            state,
            entry_order_id,
            exit_order_id,
            position_qty,
            has_entry_fill,
            has_exit_orders,
            has_open_position,
            is_flat,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            pod_id,
            snapshot.symbol,
            snapshot.state.value,
            snapshot.entry_order_id,
            snapshot.exit_order_id,
            snapshot.position_qty,
            snapshot.has_entry_fill,
            snapshot.has_exit_orders,
            snapshot.has_open_position,
            snapshot.is_flat,
            datetime.now(tz=UTC),
        ],
    )


def _portfolio_position_qtys(portfolio: Any) -> dict[str, float]:
    positions = getattr(portfolio, "positions", None)
    if not isinstance(positions, dict):
        return {}
    result: dict[str, float] = {}
    for symbol, position in positions.items():
        qty = 0.0
        if hasattr(position, "qty"):
            qty = _parse_float(getattr(position, "qty"))
        elif hasattr(position, "shares"):
            qty = _parse_float(getattr(position, "shares"))
        elif isinstance(position, dict):
            qty = _parse_float(position.get("qty", position.get("shares")))
        result[str(symbol)] = qty
    return result


def _broker_qtys_from_snapshot(
    broker_positions: list[dict[str, Any]] | None,
) -> dict[str, float]:
    return {
        str(item.get("symbol") or ""): _parse_float(item.get("qty"))
        for item in (broker_positions or [])
        if str(item.get("symbol") or "")
    }


def _effective_broker_qtys(
    *,
    conn: duckdb.DuckDBPyConnection,
    pod_id: str,
    broker_positions: list[dict[str, Any]] | None,
) -> dict[str, float]:
    broker_qtys = _broker_qtys_from_snapshot(broker_positions)
    if broker_qtys:
        return broker_qtys
    rebuilt = rebuild_position_state_from_events(conn, pod_id=pod_id)
    return {
        symbol: state.position_qty
        for symbol, state in rebuilt.items()
    }


def _validate_event_ledger_rebuild(
    *,
    conn: duckdb.DuckDBPyConnection,
    pod_id: str,
    lifecycle: dict[str, BrokerLifecycleSnapshot],
    broker_positions: list[dict[str, Any]] | None,
    portfolio: Any | None,
) -> None:
    rebuilt = rebuild_position_state_from_events(conn, pod_id=pod_id)
    broker_qtys = _broker_qtys_from_snapshot(broker_positions)
    portfolio_qtys = _portfolio_position_qtys(portfolio) if portfolio is not None else {}
    symbols = set(rebuilt) | set(lifecycle) | set(broker_qtys) | set(portfolio_qtys)

    for symbol in symbols:
        rebuilt_state = rebuilt.get(symbol)
        rebuilt_qty = rebuilt_state.position_qty if rebuilt_state is not None else 0.0
        lifecycle_snapshot = lifecycle.get(symbol)
        lifecycle_qty = lifecycle_snapshot.position_qty if lifecycle_snapshot is not None else 0.0
        portfolio_qty = portfolio_qtys.get(symbol, rebuilt_qty)

        if abs(rebuilt_qty - lifecycle_qty) > 1e-9:
            raise PositionInvariantError("EVENT LEDGER STATE DIVERGENCE")
        if portfolio is not None and abs(rebuilt_qty - portfolio_qty) > 1e-9:
            raise PositionInvariantError("EVENT LEDGER STATE DIVERGENCE")
        if symbol in broker_qtys and abs(rebuilt_qty - broker_qtys[symbol]) > 1e-9:
            raise PositionInvariantError("EVENT LEDGER STATE DIVERGENCE")


def _build_reconciliation_snapshot(
    *,
    statuses: list[BrokerOrderStatus],
    lifecycle: dict[str, BrokerLifecycleSnapshot],
    broker_positions: list[dict[str, Any]] | None,
    log_kwargs: dict[str, Any] | None,
) -> dict[str, Any]:
    order_state = {
        status.order_id: {
            "symbol": status.symbol,
            "side": status.side,
            "status": status.status,
            "qty": status.qty,
            "filled_qty": status.filled_qty,
            "intent_type": status.intent_type,
            "parent_order_id": status.parent_order_id,
            "exit_reason": status.exit_reason,
        }
        for status in statuses
    }
    intraday_position_state = {
        str(item.get("symbol") or ""): dict(item)
        for item in (broker_positions or [])
        if str(item.get("symbol") or "")
    }
    lifecycle_state = {
        symbol: snapshot.state.value
        for symbol, snapshot in lifecycle.items()
    }
    exit_policy_state = dict((log_kwargs or {}).get("exit_policy_state") or {})
    snapshot = {
        "intraday_position_state": intraday_position_state,
        "order_state": order_state,
        "lifecycle_state": lifecycle_state,
        "exit_policy_state": exit_policy_state,
    }
    return _validate_telemetry_snapshot(snapshot)


def _log_reconciliation_event(event: str, **fields: Any) -> None:
    logger.info("reconciliation_%s %s", event, fields)


def _require_submitted_order_lineage(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str,
    order_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT order_id, symbol, side, qty, intent_type, parent_order_id, exit_reason, status, submitted_at
        FROM broker_submitted_orders
        WHERE pod_id = ? AND order_id = ?
        """,
        [pod_id, order_id],
    ).fetchone()
    if row is None:
        raise OrderingError("UNKNOWN_SUBMITTED_ORDER_LINEAGE")
    return {
        "order_id": row[0],
        "symbol": row[1],
        "side": row[2],
        "qty": row[3],
        "intent_type": row[4],
        "parent_order_id": row[5],
        "exit_reason": row[6],
        "status": row[7],
        "submitted_at": row[8],
    }


def _lookup_submitted_order_lineage(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str,
    order_id: str | None,
) -> dict[str, Any] | None:
    if not order_id:
        return None
    row = conn.execute(
        """
        SELECT order_id, symbol, side, qty, intent_type, parent_order_id, exit_reason, status, submitted_at
        FROM broker_submitted_orders
        WHERE pod_id = ? AND order_id = ?
        """,
        [pod_id, order_id],
    ).fetchone()
    if row is None:
        return None
    return {
        "order_id": row[0],
        "symbol": row[1],
        "side": row[2],
        "qty": row[3],
        "intent_type": row[4],
        "parent_order_id": row[5],
        "exit_reason": row[6],
        "status": row[7],
        "submitted_at": row[8],
    }


def _is_open_status(status: str) -> bool:
    return status in _ACTIVE_ORDER_STATUSES


def _symbol_open_exit_statuses(
    statuses: list[BrokerOrderStatus],
    *,
    symbol: str,
) -> list[BrokerOrderStatus]:
    return [
        status
        for status in statuses
        if status.symbol == symbol
        and status.intent_type in _PROTECTION_INTENT_TYPES
        and _is_open_status(status.status)
    ]


def _symbol_position_qty_from_fills(
    *,
    conn: duckdb.DuckDBPyConnection,
    pod_id: str,
    symbol: str,
) -> float:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN side = 'buy' THEN fill_qty ELSE -fill_qty END), 0.0)
        FROM broker_fill_events
        WHERE pod_id = ? AND symbol = ? AND is_reversal = FALSE
        """,
        [pod_id, symbol],
    ).fetchone()
    return _parse_float(row[0] if row is not None else 0.0)


def _classify_status(
    statuses: list[BrokerOrderStatus],
    fills: list[BrokerFillEvent],
) -> tuple[ReconciliationStatus, str | None]:
    fatal_reasons: list[str] = []

    symbol_entry_orders = {
        status.symbol: status.order_id
        for status in statuses
        if (status.intent_type or "") == "entry" and status.order_id
    }

    for fill in fills:
        if fill.intent_type in _EXIT_INTENT_TYPES and not fill.parent_order_id:
            entry_order_id = symbol_entry_orders.get(fill.symbol)
            if entry_order_id is None:
                fatal_reasons.append(f"EXIT_FILL_WITHOUT_PARENT:{fill.symbol}:{fill.order_id}")

    if fatal_reasons:
        return ReconciliationStatus.FATAL, ";".join(sorted(set(fatal_reasons)))
    return ReconciliationStatus.SUCCESS, None


def _validate_bracket_invariants(
    *,
    conn: duckdb.DuckDBPyConnection,
    pod_id: str,
    statuses: list[BrokerOrderStatus],
    broker_positions: list[dict[str, Any]] | None,
) -> None:
    broker_qtys = {
        str(item.get("symbol") or ""): _parse_float(item.get("qty"))
        for item in (broker_positions or [])
        if str(item.get("symbol") or "")
    }
    symbols = set(broker_qtys) | {status.symbol for status in statuses if status.symbol}

    for symbol in symbols:
        open_exit_statuses = _symbol_open_exit_statuses(statuses, symbol=symbol)
        open_exit_qty = sum(status.remaining_qty for status in open_exit_statuses)
        broker_qty = broker_qtys.get(
            symbol,
            _symbol_position_qty_from_fills(conn=conn, pod_id=pod_id, symbol=symbol),
        )
        open_exit_intents = {str(status.intent_type or "") for status in open_exit_statuses}
        if broker_qty <= 1e-9:
            if open_exit_statuses:
                raise ReconciliationError("OPEN_PROTECTION_WITH_FLAT_POSITION")
            continue
        if not open_exit_statuses:
            continue
        if open_exit_intents == {"take_profit_2"}:
            raise ReconciliationError("MISSING_TP1_LEG")
        if not any(open_exit_intents == allowed for allowed in _BRACKET_EXIT_GROUPS):
            raise ReconciliationError("INVALID_BRACKET_CONFIGURATION")
        if "take_profit_1" in open_exit_intents and len(open_exit_intents) == 1:
            only_tp1_status = open_exit_statuses[0]
            if only_tp1_status.filled_qty <= 1e-9 and open_exit_qty < broker_qty - 1e-9:
                raise ReconciliationError("MISSING_TP1_LEG")
            if open_exit_qty > broker_qty + 1e-9:
                raise ReconciliationError("PROTECTION_QTY_MISMATCH")
            continue
        if open_exit_intents == {"take_profit_2", "stop_loss"}:
            if abs(open_exit_qty - (2.0 * broker_qty)) > 1e-9:
                raise ReconciliationError("PROTECTION_QTY_MISMATCH")
            continue
        if abs(open_exit_qty - broker_qty) > 1e-9:
            raise ReconciliationError("PROTECTION_QTY_MISMATCH")


def _resolve_fill_decisions(status: BrokerOrderStatus) -> list[ReconciledFillDecision]:
    raw_fills = sorted(
        _fills_from_status(status),
        key=lambda fill: (
            fill.fill_time,
            _execution_identity(fill),
            fill.order_id,
        ),
    )
    decisions_by_identity: dict[str, ReconciledFillDecision] = {}
    applied_identity_by_economic_key: dict[tuple[str, str, str, float, float, str], str] = {}
    for fill in raw_fills:
        identity = _execution_identity(fill)
        action = _normalized_execution_action(fill.execution_action) or "apply"
        if fill.is_reversal or action == "reverse":
            decisions_by_identity[identity] = ReconciledFillDecision(
                fill=fill,
                execution_identity=identity,
                resolution="reversal",
                signed_fill_qty=_signed_fill_qty(fill),
            )
            continue
        if fill.is_correction or action == "correct":
            decisions_by_identity[identity] = ReconciledFillDecision(
                fill=fill,
                execution_identity=identity,
                resolution="correction",
                signed_fill_qty=_signed_fill_qty(fill),
            )
            continue
        economic_key = _canonical_fill_economic_key(fill)
        prior_identity = applied_identity_by_economic_key.get(economic_key)
        if prior_identity is not None and prior_identity != identity:
            continue
        if identity in decisions_by_identity:
            continue
        applied_identity_by_economic_key[economic_key] = identity
        decisions_by_identity[identity] = ReconciledFillDecision(
            fill=fill,
            execution_identity=identity,
            resolution="applied",
            signed_fill_qty=_signed_fill_qty(fill),
        )
    return [decisions_by_identity[key] for key in sorted(decisions_by_identity)]


def _resolved_fill_qty_total(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str,
    order_id: str,
    decisions: list[ReconciledFillDecision],
) -> float:
    persisted_row = conn.execute(
        """
        SELECT
            COALESCE(
                SUM(
                    CASE
                        WHEN is_reversal THEN -ABS(fill_qty)
                        ELSE ABS(fill_qty)
                    END
                ),
                0.0
            )
        FROM broker_fill_events
        WHERE pod_id = ? AND order_id = ?
        """,
        [pod_id, order_id],
    ).fetchone()
    persisted_qty = _parse_float(persisted_row[0] if persisted_row is not None else 0.0)
    if not decisions:
        return persisted_qty
    decision_qty = sum(
        abs(decision.fill.fill_qty)
        for decision in decisions
        if decision.resolution != "reversal"
    ) - sum(
        abs(decision.fill.fill_qty)
        for decision in decisions
        if decision.resolution == "reversal"
    )
    return decision_qty


def _validate_partial_fill_consistency(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str,
    status: BrokerOrderStatus,
    decisions: list[ReconciledFillDecision],
) -> None:
    effective_qty = _resolved_fill_qty_total(
        conn,
        pod_id=pod_id,
        order_id=status.order_id,
        decisions=decisions,
    )
    if status.filled_qty > 0 and abs(effective_qty - status.filled_qty) > 1e-9:
        raise ReconciliationError(
            f"PARTIAL_FILL_RECONCILIATION_MISMATCH: order_id={status.order_id} status_filled_qty={status.filled_qty} resolved_fill_qty={effective_qty}"
        )


def _advisory_canceled_oco_fills(statuses: list[BrokerOrderStatus]) -> dict[str, list[BrokerOrderStatus]]:
    by_parent: dict[str, list[BrokerOrderStatus]] = {}
    for status in statuses:
        if status.intent_type not in _PROTECTION_INTENT_TYPES:
            continue
        if not status.parent_order_id:
            continue
        by_parent.setdefault(status.parent_order_id, []).append(status)
    return by_parent


def _post_facto_oco_arbitration(
    *,
    statuses: list[BrokerOrderStatus],
    fills: list[BrokerFillEvent],
) -> None:
    fills_by_parent: dict[str, list[BrokerFillEvent]] = {}
    for fill in fills:
        if fill.intent_type not in _PROTECTION_INTENT_TYPES or not fill.parent_order_id:
            continue
        fills_by_parent.setdefault(fill.parent_order_id, []).append(fill)

    for parent_order_id, parent_fills in sorted(fills_by_parent.items()):
        intents = {str(fill.intent_type or "") for fill in parent_fills}
        if len(intents) <= 1:
            continue
        if "stop_loss" in intents and ("take_profit_1" in intents or "take_profit_2" in intents):
            earliest = min(parent_fills, key=lambda fill: (fill.fill_time, fill.order_id))
            latest = max(parent_fills, key=lambda fill: (fill.fill_time, fill.order_id))
            if earliest.order_id == latest.order_id and earliest.intent_type == latest.intent_type:
                continue
            raise OCOConflictError(
                f"OCO_POST_FACTO_ARBITRATION_REQUIRED: parent_order_id={parent_order_id} winning_order_id={earliest.order_id} losing_order_id={latest.order_id}"
            )

    grouped_statuses = _advisory_canceled_oco_fills(statuses)
    for parent_order_id, child_statuses in sorted(grouped_statuses.items()):
        filled_children = [
            status for status in child_statuses if status.filled_qty > 0 or status.status == "filled"
        ]
        if len(filled_children) <= 1:
            continue
        ordered = sorted(
            filled_children,
            key=lambda status: (
                status.updated_at or status.submitted_at or datetime.min.replace(tzinfo=UTC),
                status.order_id,
            ),
        )
        winner = ordered[0]
        losers = ordered[1:]
        raise OCOConflictError(
            f"OCO_POST_FACTO_ARBITRATION_REQUIRED: parent_order_id={parent_order_id} winning_order_id={winner.order_id} losing_order_ids={','.join(status.order_id for status in losers)}"
        )


def _validate_reconciliation_invariants(
    *,
    conn: duckdb.DuckDBPyConnection,
    pod_id: str,
    statuses: list[BrokerOrderStatus],
    fills: list[BrokerFillEvent],
    lifecycle: dict[str, BrokerLifecycleSnapshot],
    broker_positions: list[dict[str, Any]] | None,
    portfolio: Any | None,
) -> None:
    fill_rows = conn.execute(
        """
        SELECT order_id, symbol, side, fill_qty, intent_type, parent_order_id, is_reversal
        FROM broker_fill_events
        WHERE pod_id = ?
        ORDER BY fill_time ASC, created_at ASC, order_id ASC
        """,
        [pod_id],
    ).fetchall()

    fill_position_qtys: dict[str, float] = {}
    known_submitted_orders: set[str] = set()
    for row in conn.execute(
        """
        SELECT order_id
        FROM broker_submitted_orders
        WHERE pod_id = ?
        """,
        [pod_id],
    ).fetchall():
        known_submitted_orders.add(str(row[0]))

    for order_id, symbol, side, fill_qty, intent_type, parent_order_id, is_reversal in fill_rows:
        if str(order_id) not in known_submitted_orders:
            raise OrderingError("FILL_WITHOUT_KNOWN_SUBMITTED_ORDER")
        normalized_intent = str(intent_type or "")
        normalized_parent = str(parent_order_id) if parent_order_id is not None else None
        if normalized_intent in _EXIT_INTENT_TYPES and not normalized_parent:
            raise OrderingError("FILL_WITHOUT_PARENT_ORDER")
        if normalized_parent is not None and normalized_parent not in known_submitted_orders:
            raise OrderingError("FILL_WITH_UNKNOWN_PARENT_ORDER")
        signed_qty = _parse_float(fill_qty) if str(side).lower() == "buy" else -_parse_float(fill_qty)
        if bool(is_reversal):
            signed_qty = -signed_qty
        fill_position_qtys[str(symbol)] = fill_position_qtys.get(str(symbol), 0.0) + signed_qty

    broker_qtys = _broker_qtys_from_snapshot(broker_positions)
    rebuilt = rebuild_position_state_from_events(conn, pod_id=pod_id)
    portfolio_qtys = _portfolio_position_qtys(portfolio) if portfolio is not None else {}
    symbols = set(fill_position_qtys) | set(broker_qtys) | set(rebuilt) | set(lifecycle) | set(portfolio_qtys)

    for symbol in symbols:
        fill_qty = fill_position_qtys.get(symbol, 0.0)
        rebuilt_state = rebuilt.get(symbol)
        rebuilt_qty = rebuilt_state.position_qty if rebuilt_state is not None else 0.0
        lifecycle_state = lifecycle.get(symbol)
        lifecycle_qty = lifecycle_state.position_qty if lifecycle_state is not None else 0.0
        portfolio_qty = portfolio_qtys.get(symbol, rebuilt_qty)

        if abs(fill_qty - rebuilt_qty) > 1e-9:
            raise PositionInvariantError("POSITION_SUM_MISMATCH")
        if abs(fill_qty - lifecycle_qty) > 1e-9:
            raise PositionInvariantError("POSITION_SUM_MISMATCH")
        if portfolio is not None and abs(fill_qty - portfolio_qty) > 1e-9:
            raise PositionInvariantError("POSITION_SUM_MISMATCH")
        if symbol in broker_qtys and abs(fill_qty - broker_qtys[symbol]) > 1e-9:
            raise PositionInvariantError("POSITION_SUM_MISMATCH")

    for status in statuses:
        if not _is_open_status(status.status):
            continue
        if status.order_id not in known_submitted_orders:
            raise OrderingError("OPEN_ORDER_WITHOUT_VALID_ORIGIN_INTENT")
        lineage = _require_submitted_order_lineage(conn, pod_id=pod_id, order_id=status.order_id)
        if not lineage.get("intent_type"):
            raise OrderingError("OPEN_ORDER_WITHOUT_VALID_ORIGIN_INTENT")
        if status.intent_type in _EXIT_INTENT_TYPES and not (
            status.parent_order_id or lineage.get("parent_order_id")
        ):
            raise OrderingError("OPEN_EXIT_ORDER_WITHOUT_PARENT_INTENT")
        if status.intent_type in _EXIT_INTENT_TYPES:
            parent_order_id = str(status.parent_order_id or lineage.get("parent_order_id") or "")
            if parent_order_id not in known_submitted_orders:
                raise OrderingError("OPEN_EXIT_ORDER_WITH_UNKNOWN_PARENT_INTENT")

    _validate_bracket_invariants(
        conn=conn,
        pod_id=pod_id,
        statuses=statuses,
        broker_positions=broker_positions,
    )
    _post_facto_oco_arbitration(statuses=statuses, fills=fills)

    for fill in fills:
        if fill.is_forced_liquidation:
            _log_reconciliation_event(
                "forced_liquidation_observed",
                pod_id=pod_id,
                symbol=fill.symbol,
                order_id=fill.order_id,
                fill_qty=fill.fill_qty,
                fill_price=fill.fill_price,
            )


def persist_submitted_orders(
    conn: duckdb.DuckDBPyConnection,
    submitted_orders: list[Any],
    pod_id: str = "default",
) -> int:
    """Persist submitted broker orders for later reconciliation."""
    _ensure_reconciliation_tables(conn)
    persisted = 0
    for item in submitted_orders:
        record = _coerce_mapping(item)
        order_id = str(record.get("order_id") or record.get("id") or "")
        if not order_id:
            continue
        raw_json = str(
            record.get("broker_raw")
            or record.get("raw_response")
            or record.get("raw_json")
            or record
        )
        submitted_at = _parse_dt(record.get("submitted_at")) or datetime.now(tz=UTC)
        updated_at = _parse_dt(record.get("updated_at")) or datetime.now(tz=UTC)
        stored_submitted_at = submitted_at.astimezone(UTC).replace(tzinfo=None)
        stored_updated_at = updated_at.astimezone(UTC).replace(tzinfo=None)
        parent_order_id = record.get("parent_order_id")
        _validate_submitted_order_identity(conn, pod_id=pod_id, record=record)
        conn.execute(
            """
            INSERT OR REPLACE INTO broker_submitted_orders (
                order_id,
                pod_id,
                symbol,
                side,
                qty,
                order_type,
                time_in_force,
                intent_type,
                parent_order_id,
                exit_reason,
                client_order_id,
                status,
                submitted_at,
                updated_at,
                raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                order_id,
                pod_id,
                str(record.get("symbol") or ""),
                str(record.get("side") or ""),
                _parse_float(record.get("qty")),
                record.get("order_type") or record.get("type"),
                record.get("time_in_force"),
                record.get("intent_type"),
                parent_order_id,
                record.get("exit_reason"),
                record.get("client_order_id"),
                str(record.get("status") or "submitted").lower(),
                stored_submitted_at,
                stored_updated_at,
                raw_json,
            ],
        )
        append_event(
            conn,
            BrokerLedgerEvent(
                order_id=order_id,
                event_type=_event_type_for_submission(record.get("intent_type")),
                symbol=str(record.get("symbol") or ""),
                side=str(record.get("side") or "") or None,
                qty=_parse_float(record.get("qty")),
                price=None,
                event_time=submitted_at,
                parent_order_id=parent_order_id,
                intent_type=record.get("intent_type"),
                exit_reason=record.get("exit_reason"),
                pod_id=pod_id,
                event_chain_id=parent_order_id or order_id,
                parent_event_order_id=parent_order_id,
            ),
        )
        persisted += 1
    conn.commit()
    return persisted


def wait_for_order_fills(
    client: AlpacaClient,
    order_ids: list[str],
    timeout_seconds: int = 30,
    poll_interval_seconds: float = 2.0,
) -> list[BrokerOrderStatus]:
    """Poll broker order endpoints until orders are terminal or timeout expires."""
    deadline = time.monotonic() + max(timeout_seconds, 0)
    remaining_ids = {order_id for order_id in order_ids if order_id}
    statuses: dict[str, BrokerOrderStatus] = {}

    while remaining_ids:
        for order_id in list(remaining_ids):
            try:
                order = client.get_order(order_id, nested=True)
            except AlpacaError as exc:
                logger.warning("Order status fetch failed for %s: %s", order_id, exc)
                continue
            status = _status_from_order_record(order)
            statuses[order_id] = status
            if status.status in _TERMINAL_ORDER_STATUSES:
                remaining_ids.discard(order_id)

        if not remaining_ids or time.monotonic() >= deadline:
            break
        time.sleep(max(poll_interval_seconds, 0.0))

    return [statuses[order_id] for order_id in order_ids if order_id in statuses]


def reconcile_broker_orders(
    conn: duckdb.DuckDBPyConnection,
    client: AlpacaClient,
    portfolio: Any | None = None,
    ledger_conn: duckdb.DuckDBPyConnection | None = None,
    pod_id: str = "default",
    order_ids: list[str] | None = None,
    broker_positions: list[dict[str, Any]] | None = None,
    log_fills_fn: Any | None = None,
    trade_date: Any | None = None,
    log_kwargs: dict[str, Any] | None = None,
) -> ReconciliationResult:
    """Reconcile broker-submitted orders, persist fills, and optionally apply them."""
    _ensure_reconciliation_tables(conn)
    if order_ids:
        rows = conn.execute(
            """
            SELECT order_id, symbol, side, qty, intent_type, parent_order_id, exit_reason
            FROM broker_submitted_orders
            WHERE pod_id = ? AND order_id IN ({})
            """.format(", ".join(["?"] * len(order_ids))),
            [pod_id, *order_ids],
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT order_id, symbol, side, qty, intent_type, parent_order_id, exit_reason
            FROM broker_submitted_orders
            WHERE pod_id = ?
              AND COALESCE(status, '') NOT IN ('filled', 'canceled', 'cancelled', 'expired', 'rejected', 'done_for_day', 'replaced')
            ORDER BY created_at ASC
            """,
            [pod_id],
        ).fetchall()

    fallback_records = {
        str(row[0]): {
            "order_id": row[0],
            "symbol": row[1],
            "side": row[2],
            "qty": row[3],
            "intent_type": row[4],
            "parent_order_id": row[5],
            "exit_reason": row[6],
        }
        for row in rows
    }

    statuses: list[BrokerOrderStatus] = []
    fills: list[BrokerFillEvent] = []

    try:
        for order_id, fallback in fallback_records.items():
            _require_submitted_order_lineage(conn, pod_id=pod_id, order_id=order_id)
            try:
                order = client.get_order(order_id, nested=True)
            except AlpacaError as exc:
                _log_reconciliation_event(
                    "rejected_fetch",
                    pod_id=pod_id,
                    order_id=order_id,
                    reason=str(exc),
                )
                logger.warning("Skipping reconciliation fetch failure for %s: %s", order_id, exc)
                continue
            status = _status_from_order_record(order, fallback)
            statuses.append(status)
            normalized_submitted_at = (
                status.submitted_at.astimezone(UTC).replace(tzinfo=None)
                if status.submitted_at is not None
                else None
            )
            if normalized_submitted_at is not None:
                conn.execute(
                    """
                    UPDATE broker_event_ledger
                    SET event_time = ?
                    WHERE pod_id = ?
                      AND order_id = ?
                      AND event_type = ?
                      AND event_time > ?
                    """,
                    [
                        normalized_submitted_at,
                        pod_id,
                        status.order_id,
                        BrokerEventType.ORDER_SUBMITTED.value,
                        normalized_submitted_at,
                    ],
                )
            conn.execute(
                """
                UPDATE broker_submitted_orders
                SET status = ?, updated_at = ?, submitted_at = COALESCE(submitted_at, ?)
                WHERE order_id = ? AND pod_id = ?
                """,
                [
                    status.status,
                    (
                        (status.updated_at or datetime.now(tz=UTC))
                        .astimezone(UTC)
                        .replace(tzinfo=None)
                    ),
                    (
                        status.submitted_at.astimezone(UTC).replace(tzinfo=None)
                        if status.submitted_at is not None
                        else None
                    ),
                    status.order_id,
                    pod_id,
                ],
            )

            if status.status == "replaced":
                _append_replacement_event(conn, pod_id=pod_id, status=status)
            else:
                terminal_event_type = _event_type_for_terminal_status(status)
                if terminal_event_type is not None:
                    append_event(
                        conn,
                        _build_ledger_event_from_status(
                            pod_id=pod_id,
                            status=status,
                            event_type=terminal_event_type,
                            event_time=status.updated_at or datetime.now(tz=UTC),
                            fill=None,
                        ),
                    )

            decisions = _resolve_fill_decisions(status)
            _validate_partial_fill_consistency(
                conn,
                pod_id=pod_id,
                status=status,
                decisions=decisions,
            )

            for decision in decisions:
                fill = decision.fill
                lineage = _require_submitted_order_lineage(conn, pod_id=pod_id, order_id=fill.order_id)
                if fill.fill_time is not None:
                    candidate_submitted_times = [
                        submitted_time
                        for submitted_time in (
                            _parse_dt(lineage.get("submitted_at")),
                            status.submitted_at,
                        )
                        if submitted_time is not None
                    ]
                    earliest_submitted_at = (
                        min(candidate_submitted_times) if candidate_submitted_times else None
                    )
                    if earliest_submitted_at is not None and fill.fill_time < earliest_submitted_at:
                        raise OrderingError("FILL_WITHOUT_KNOWN_SUBMITTED_ORDER")
                if _canonical_lineage_identity(status) != (
                    str(lineage.get("symbol") or ""),
                    str(lineage.get("side") or "").lower(),
                    str(lineage.get("parent_order_id")) if lineage.get("parent_order_id") is not None else None,
                ):
                    raise OrderingError("UNKNOWN_SUBMITTED_ORDER_LINEAGE")
                if fill.parent_order_id and fill.parent_order_id not in fallback_records:
                    parent_lineage = _require_submitted_order_lineage(conn, pod_id=pod_id, order_id=fill.parent_order_id)
                    if fill.fill_time is not None:
                        parent_candidate_submitted_times = [
                            submitted_time
                            for submitted_time in (
                                _parse_dt(parent_lineage.get("submitted_at")),
                                _parse_dt(fallback_records.get(fill.parent_order_id, {}).get("submitted_at")),
                            )
                            if submitted_time is not None
                        ]
                        parent_earliest_submitted_at = (
                            min(parent_candidate_submitted_times)
                            if parent_candidate_submitted_times
                            else None
                        )
                        if (
                            parent_earliest_submitted_at is not None
                            and fill.fill_time < parent_earliest_submitted_at
                        ):
                            raise OrderingError("FILL_WITH_UNKNOWN_PARENT_ORDER")
                before_row = conn.execute(
                    """
                    SELECT 1
                    FROM broker_fill_events
                    WHERE order_id = ?
                      AND fill_time = ?
                      AND fill_qty = ?
                      AND fill_price = ?
                      AND COALESCE(execution_id, '') = COALESCE(?, '')
                      AND COALESCE(execution_ref, '') = COALESCE(?, '')
                      AND COALESCE(broker_fill_key, '') = COALESCE(?, '')
                      AND pod_id = ?
                    """,
                    [
                        fill.order_id,
                        fill.fill_time,
                        fill.fill_qty,
                        fill.fill_price,
                        fill.execution_id,
                        fill.execution_ref,
                        fill.broker_fill_key,
                        pod_id,
                    ],
                ).fetchone()
                conn.execute(
                    """
                    INSERT OR IGNORE INTO broker_fill_events (
                        order_id,
                        symbol,
                        side,
                        fill_qty,
                        fill_price,
                        fill_time,
                        intent_type,
                        parent_order_id,
                        exit_reason,
                        lifecycle_state,
                        is_forced_liquidation,
                        commission,
                        execution_id,
                        execution_ref,
                        execution_action,
                        corrected_execution_id,
                        reversal_execution_id,
                        broker_fill_key,
                        is_correction,
                        is_reversal,
                        pod_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        fill.order_id,
                        fill.symbol,
                        fill.side,
                        fill.fill_qty,
                        fill.fill_price,
                        fill.fill_time,
                        fill.intent_type,
                        fill.parent_order_id,
                        fill.exit_reason,
                        None,
                        fill.is_forced_liquidation,
                        fill.commission,
                        fill.execution_id,
                        fill.execution_ref,
                        fill.execution_action,
                        fill.corrected_execution_id,
                        fill.reversal_execution_id,
                        fill.broker_fill_key,
                        fill.is_correction,
                        fill.is_reversal,
                        pod_id,
                    ],
                )
                if before_row is None:
                    fills.append(fill)
                    append_event(
                        conn,
                        _build_ledger_event_from_status(
                            pod_id=pod_id,
                            status=status,
                            event_type=_event_type_for_fill(status, fill),
                            event_time=fill.fill_time,
                            fill=fill,
                            resolution=decision.resolution,
                        ),
                    )

        lifecycle: dict[str, BrokerLifecycleSnapshot] = {}
        position_qty_by_symbol = _effective_broker_qtys(
            conn=conn,
            pod_id=pod_id,
            broker_positions=broker_positions,
        )
        rebuilt_positions = rebuild_position_state_from_events(conn, pod_id=pod_id)
        lifecycle_symbols = {
            status.symbol
            for status in statuses
            if status.symbol
        } | {
            str(item.get("symbol") or "")
            for item in (broker_positions or [])
            if str(item.get("symbol") or "")
        } | set(rebuilt_positions)
        for symbol in lifecycle_symbols:
            symbol_statuses = [status for status in statuses if status.symbol == symbol]
            entry_status = next(
                (status for status in symbol_statuses if (status.intent_type or "") == "entry"),
                None,
            )
            exit_statuses = [
                status
                for status in symbol_statuses
                if (status.intent_type or "") != "entry"
            ]
            rebuilt_position = rebuilt_positions.get(symbol)
            rebuilt_position_qty = (
                _parse_float(getattr(rebuilt_position, "position_qty", 0.0))
                if rebuilt_position is not None
                else 0.0
            )
            broker_position_qty = position_qty_by_symbol.get(
                symbol,
                rebuilt_position_qty,
            )
            current_position_qty = rebuilt_position_qty
            if abs(broker_position_qty - rebuilt_position_qty) > 1e-9:
                _log_reconciliation_event(
                    "broker_position_rebuild_mismatch",
                    pod_id=pod_id,
                    symbol=symbol,
                    broker_position_qty=broker_position_qty,
                    rebuilt_position_qty=rebuilt_position_qty,
                )
            closed_by_event = current_position_qty <= 0.0 and _position_was_closed_by_event(
                conn=conn,
                pod_id=pod_id,
                symbol=symbol,
            )
            snapshot = snapshot_from_broker_state(
                symbol=symbol,
                entry_order_id=entry_status.order_id if entry_status is not None else None,
                entry_status=entry_status.status if entry_status is not None else None,
                entry_filled_qty=entry_status.filled_qty if entry_status is not None else None,
                position_qty=0.0 if closed_by_event else current_position_qty,
                exit_order_id=exit_statuses[0].order_id if exit_statuses else None,
                has_exit_orders=bool(exit_statuses) and not closed_by_event,
                exit_statuses=[status.status for status in exit_statuses],
            )
            lifecycle[symbol] = snapshot
            _persist_lifecycle_snapshot(conn, pod_id=pod_id, snapshot=snapshot)

            for fill in fills:
                if fill.symbol == symbol:
                    fill.lifecycle_state = snapshot.state.value

            conn.execute(
                """
                UPDATE broker_fill_events
                SET lifecycle_state = ?
                WHERE pod_id = ? AND symbol = ? AND lifecycle_state IS NULL
                """,
                [snapshot.state.value, pod_id, symbol],
            )

            if (
                closed_by_event
                and rebuilt_position is not None
                and rebuilt_position.last_event_type is not BrokerEventType.POSITION_CLOSED
            ):
                closed_at = max(
                    [
                        fill.fill_time
                        for fill in fills
                        if fill.symbol == symbol and (fill.intent_type or "") != "entry"
                    ]
                    or [datetime.now(tz=UTC)]
                )
                position_closed_order_id = (
                    snapshot.exit_order_id
                    or getattr(rebuilt_position, "exit_order_id", None)
                    or snapshot.entry_order_id
                    or getattr(rebuilt_position, "entry_order_id", None)
                    or f"{symbol}:position_closed"
                )
                exit_lineage = _lookup_submitted_order_lineage(
                    conn,
                    pod_id=pod_id,
                    order_id=(
                        snapshot.exit_order_id
                        or getattr(rebuilt_position, "exit_order_id", None)
                    ),
                )
                entry_lineage = _lookup_submitted_order_lineage(
                    conn,
                    pod_id=pod_id,
                    order_id=(
                        snapshot.entry_order_id
                        or getattr(rebuilt_position, "entry_order_id", None)
                    ),
                )
                position_closed_side = None
                if exit_statuses:
                    position_closed_side = exit_statuses[0].side
                elif exit_lineage is not None:
                    position_closed_side = str(exit_lineage.get("side") or "") or None
                elif entry_status is not None:
                    position_closed_side = entry_status.side
                elif entry_lineage is not None:
                    position_closed_side = str(entry_lineage.get("side") or "") or None

                append_event(
                    conn,
                    BrokerLedgerEvent(
                        order_id=position_closed_order_id,
                        event_type=BrokerEventType.POSITION_CLOSED,
                        symbol=symbol,
                        side=position_closed_side,
                        qty=0.0,
                        price=None,
                        event_time=closed_at,
                        parent_order_id=(
                            snapshot.entry_order_id
                            or getattr(rebuilt_position, "entry_order_id", None)
                        ),
                        intent_type="position_closed",
                        exit_reason="position_closed",
                        pod_id=pod_id,
                        event_chain_id=(
                            snapshot.entry_order_id
                            or getattr(rebuilt_position, "entry_order_id", None)
                            or position_closed_order_id
                        ),
                        parent_event_order_id=(
                            snapshot.entry_order_id
                            or getattr(rebuilt_position, "entry_order_id", None)
                        ),
                    ),
                )

        conn.commit()

        applied_fill_count = _rebuild_portfolio_from_reconciliation(
            portfolio,
            conn=conn,
            pod_id=pod_id,
        )

        persisted_fill_count = len(fills)
        reconciliation_snapshot = _build_reconciliation_snapshot(
            statuses=statuses,
            lifecycle=lifecycle,
            broker_positions=broker_positions,
            log_kwargs=log_kwargs,
        )

        status_classification, status_reason = _classify_status(statuses, fills)
        _log_reconciliation_event(
            "classification",
            pod_id=pod_id,
            status=status_classification.value,
            reason=status_reason,
            persisted_fill_count=persisted_fill_count,
        )

        if log_fills_fn is not None and ledger_conn is not None and fills:
            kwargs = dict(log_kwargs or {})
            if trade_date is None:
                trade_date = date.today()
            log_fills_fn(
                ledger_conn,
                fills,
                trade_date=trade_date,
                pod_id=pod_id,
                snapshot=reconciliation_snapshot,
                **kwargs,
            )

        _validate_event_ledger_rebuild(
            conn=conn,
            pod_id=pod_id,
            lifecycle=lifecycle,
            broker_positions=broker_positions,
            portfolio=portfolio,
        )
        _validate_reconciliation_invariants(
            conn=conn,
            pod_id=pod_id,
            statuses=statuses,
            fills=fills,
            lifecycle=lifecycle,
            broker_positions=broker_positions,
            portfolio=portfolio,
        )

        if status_classification is ReconciliationStatus.FATAL:
            _log_reconciliation_event(
                "fatal_rejection",
                pod_id=pod_id,
                reason=status_reason,
            )
            raise ReconciliationError(status_reason or "FATAL_RECONCILIATION_STATE")
        if status_classification is ReconciliationStatus.RECOVERABLE:
            _log_reconciliation_event(
                "recoverable_rejection",
                pod_id=pod_id,
                reason=status_reason,
            )
            raise ReconciliationError(status_reason or "RECOVERABLE_RECONCILIATION_STATE")

        open_order_ids = [status.order_id for status in statuses if status.status in _ACTIVE_ORDER_STATUSES]
        terminal_order_ids = [status.order_id for status in statuses if status.status in _TERMINAL_ORDER_STATUSES]

        return ReconciliationResult(
            statuses=statuses,
            fills=fills,
            open_order_ids=open_order_ids,
            terminal_order_ids=terminal_order_ids,
            lifecycle=lifecycle,
            status=status_classification,
            status_reason=status_reason,
            applied_fill_count=applied_fill_count,
            persisted_fill_count=persisted_fill_count,
            snapshot=reconciliation_snapshot,
        )
    except (CausalIntegrityError, OCOConflictError, OrderingError, PositionInvariantError, ReconciliationError):
        raise
    except Exception as exc:  # pragma: no cover - defensive fatal conversion
        _log_reconciliation_event(
            "fatal_exception",
            pod_id=pod_id,
            reason=str(exc),
        )
        raise ReconciliationError(str(exc)) from exc


__all__ = [
    "BrokerOrderStatus",
    "BrokerFillEvent",
    "ReconciledFillDecision",
    "ReconciliationResult",
    "ReconciliationStatus",
    "persist_submitted_orders",
    "wait_for_order_fills",
    "reconcile_broker_orders",
]
