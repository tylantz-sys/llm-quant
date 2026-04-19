"""Immutable broker event ledger backed by DuckDB."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import duckdb

from llm_quant.broker.exceptions import CausalIntegrityError, OrderingError


class BrokerEventType(StrEnum):
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_PARTIALLY_FILLED = "ORDER_PARTIALLY_FILLED"
    STOP_TRIGGERED = "STOP_TRIGGERED"
    TAKE_PROFIT_1_FILLED = "TAKE_PROFIT_1_FILLED"
    TAKE_PROFIT_2_FILLED = "TAKE_PROFIT_2_FILLED"
    ORDER_CANCELED = "ORDER_CANCELED"
    POSITION_CLOSED = "POSITION_CLOSED"


_CAUSAL_EVENT_TYPES = {
    BrokerEventType.ORDER_FILLED,
    BrokerEventType.ORDER_PARTIALLY_FILLED,
    BrokerEventType.STOP_TRIGGERED,
    BrokerEventType.TAKE_PROFIT_1_FILLED,
    BrokerEventType.TAKE_PROFIT_2_FILLED,
    BrokerEventType.ORDER_CANCELED,
    BrokerEventType.POSITION_CLOSED,
}

_FILL_EVENT_TYPES = {
    BrokerEventType.ORDER_FILLED,
    BrokerEventType.ORDER_PARTIALLY_FILLED,
    BrokerEventType.STOP_TRIGGERED,
    BrokerEventType.TAKE_PROFIT_1_FILLED,
    BrokerEventType.TAKE_PROFIT_2_FILLED,
}


@dataclass(slots=True, frozen=True)
class BrokerLedgerEvent:
    order_id: str
    event_type: BrokerEventType
    symbol: str
    side: str | None = None
    qty: float = 0.0
    price: float | None = None
    event_time: datetime | None = None
    sequence_id: int | None = None
    parent_order_id: str | None = None
    intent_type: str | None = None
    exit_reason: str | None = None
    pod_id: str = "default"
    metadata_json: str | None = None
    event_chain_id: str | None = None
    parent_event_order_id: str | None = None


@dataclass(slots=True, frozen=True)
class RebuiltPositionState:
    symbol: str
    pod_id: str
    position_qty: float
    avg_cost: float
    current_price: float
    is_open: bool
    is_closed: bool
    last_event_type: BrokerEventType | None
    last_sequence_id: int | None
    entry_order_id: str | None
    exit_order_id: str | None
    closed_at: datetime | None


@dataclass(slots=True, frozen=True)
class LedgerOrderingDigest:
    event_id: int
    event_time: datetime
    sequence_id: int
    order_id: str
    event_type: BrokerEventType
    symbol: str
    side: str | None
    qty: float
    price: float | None
    parent_order_id: str | None
    intent_type: str | None
    exit_reason: str | None
    pod_id: str
    metadata_json: str | None
    event_chain_id: str | None
    parent_event_order_id: str | None


@dataclass(slots=True, frozen=True)
class LedgerCausalLink:
    order_id: str
    parent_order_id: str | None
    parent_event_order_id: str | None
    event_chain_id: str
    event_type: BrokerEventType
    sequence_id: int
    symbol: str
    intent_type: str | None


@dataclass(slots=True, frozen=True)
class LedgerCausalValidationResult:
    pod_id: str
    links: tuple[LedgerCausalLink, ...]
    root_order_ids: tuple[str, ...]
    leaf_order_ids: tuple[str, ...]


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)

    text = str(value).strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC)

    return parsed.replace(tzinfo=UTC)


def _parse_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_event(event: BrokerLedgerEvent | dict[str, Any]) -> BrokerLedgerEvent:
    if isinstance(event, BrokerLedgerEvent):
        return event
    if is_dataclass(event):
        payload = asdict(event)
    else:
        payload = dict(event)

    event_type = payload.get("event_type")
    if not isinstance(event_type, BrokerEventType):
        event_type = BrokerEventType(str(event_type))

    price = payload.get("price")
    if price in (None, ""):
        if payload.get("fill_price") not in (None, ""):
            price = payload.get("fill_price")
        elif payload.get("filled_avg_price") not in (None, ""):
            price = payload.get("filled_avg_price")

    qty = payload.get("qty")
    if qty in (None, ""):
        if payload.get("fill_qty") not in (None, ""):
            qty = payload.get("fill_qty")
        elif payload.get("filled_qty") not in (None, ""):
            qty = payload.get("filled_qty")

    order_id = str(payload.get("order_id") or "")
    parent_order_id = payload.get("parent_order_id")
    event_chain_id = payload.get("event_chain_id") or parent_order_id or order_id

    return BrokerLedgerEvent(
        order_id=order_id,
        event_type=event_type,
        symbol=str(payload.get("symbol") or ""),
        side=payload.get("side"),
        qty=_parse_float(qty),
        price=_parse_float(price) if price not in (None, "") else None,
        event_time=_parse_dt(payload.get("event_time")),
        sequence_id=_parse_int(payload.get("sequence_id")),
        parent_order_id=parent_order_id,
        intent_type=payload.get("intent_type"),
        exit_reason=payload.get("exit_reason"),
        pod_id=str(payload.get("pod_id") or "default"),
        metadata_json=payload.get("metadata_json"),
        event_chain_id=str(event_chain_id),
        parent_event_order_id=payload.get("parent_event_order_id"),
    )


def _ensure_event_ledger_table(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE SEQUENCE IF NOT EXISTS broker_event_ledger_seq START 1
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS broker_event_ledger (
            event_id BIGINT PRIMARY KEY DEFAULT nextval('broker_event_ledger_seq'),
            pod_id VARCHAR NOT NULL DEFAULT 'default',
            order_id VARCHAR NOT NULL,
            event_type VARCHAR NOT NULL,
            symbol VARCHAR NOT NULL,
            side VARCHAR,
            qty DOUBLE NOT NULL DEFAULT 0,
            price DOUBLE,
            event_time TIMESTAMP NOT NULL,
            sequence_id BIGINT,
            parent_order_id VARCHAR,
            intent_type VARCHAR,
            exit_reason VARCHAR,
            metadata_json TEXT,
            event_chain_id VARCHAR,
            parent_event_order_id VARCHAR,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info('broker_event_ledger')").fetchall()
    }
    if "sequence_id" not in columns:
        conn.execute("ALTER TABLE broker_event_ledger ADD COLUMN sequence_id BIGINT")
    if "event_chain_id" not in columns:
        conn.execute("ALTER TABLE broker_event_ledger ADD COLUMN event_chain_id VARCHAR")
    if "parent_event_order_id" not in columns:
        conn.execute("ALTER TABLE broker_event_ledger ADD COLUMN parent_event_order_id VARCHAR")
    conn.execute(
        """
        UPDATE broker_event_ledger
        SET sequence_id = event_id
        WHERE sequence_id IS NULL
        """
    )
    conn.execute(
        """
        UPDATE broker_event_ledger
        SET event_chain_id = COALESCE(NULLIF(event_chain_id, ''), NULLIF(parent_order_id, ''), order_id)
        WHERE event_chain_id IS NULL OR event_chain_id = ''
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_broker_event_ledger_pod_sequence
            ON broker_event_ledger (pod_id, sequence_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_broker_event_ledger_order_time
            ON broker_event_ledger (pod_id, order_id, event_time, sequence_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_broker_event_ledger_symbol_time
            ON broker_event_ledger (pod_id, symbol, event_time, sequence_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_broker_event_ledger_chain
            ON broker_event_ledger (pod_id, event_chain_id, event_time, sequence_id)
        """
    )
    conn.commit()


def _next_sequence_id(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str,
) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(MAX(sequence_id), 0)
        FROM broker_event_ledger
        WHERE pod_id = ?
        """,
        [pod_id],
    ).fetchone()
    max_sequence = int(row[0] or 0) if row is not None else 0
    return max_sequence + 1


def _validate_sequence_id(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str,
    sequence_id: int,
) -> None:
    row = conn.execute(
        """
        SELECT COALESCE(MAX(sequence_id), 0)
        FROM broker_event_ledger
        WHERE pod_id = ?
        """,
        [pod_id],
    ).fetchone()
    current_max = int(row[0] or 0) if row is not None else 0
    if sequence_id <= current_max:
        raise OrderingError(
            f"EVENT_SEQUENCE_REGRESSION: pod_id={pod_id} sequence_id={sequence_id} current_max={current_max}"
        )


def _validate_replay_sequence_order(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str,
) -> None:
    rows = conn.execute(
        """
        SELECT sequence_id
        FROM broker_event_ledger
        WHERE pod_id = ?
        ORDER BY sequence_id ASC
        """,
        [pod_id],
    ).fetchall()

    seen_sequence_ids: set[int] = set()
    last_sequence_id = 0
    for (value,) in rows:
        sequence_id = _parse_int(value)
        if sequence_id is None:
            raise OrderingError(
                f"EVENT_SEQUENCE_REPLAY_REGRESSION: pod_id={pod_id} sequence_id=NULL last_sequence_id={last_sequence_id}"
            )
        if sequence_id in seen_sequence_ids:
            raise OrderingError(
                f"EVENT_SEQUENCE_REPLAY_REGRESSION: pod_id={pod_id} sequence_id={sequence_id} duplicate_sequence_id=true"
            )
        if sequence_id < last_sequence_id:
            raise OrderingError(
                f"EVENT_SEQUENCE_REPLAY_REGRESSION: pod_id={pod_id} sequence_id={sequence_id} last_sequence_id={last_sequence_id}"
            )
        seen_sequence_ids.add(sequence_id)
        last_sequence_id = sequence_id


def _row_to_event(row: tuple[Any, ...]) -> BrokerLedgerEvent:
    return BrokerLedgerEvent(
        order_id=row[0],
        event_type=BrokerEventType(row[1]),
        symbol=row[2],
        side=row[3],
        qty=row[4],
        price=row[5],
        event_time=_parse_dt(row[6]),
        sequence_id=_parse_int(row[7]),
        parent_order_id=row[8],
        intent_type=row[9],
        exit_reason=row[10],
        pod_id=row[11],
        metadata_json=row[12],
        event_chain_id=row[13],
        parent_event_order_id=row[14],
    )


def _event_exists(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str,
    order_id: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM broker_event_ledger
        WHERE pod_id = ? AND order_id = ?
        LIMIT 1
        """,
        [pod_id, order_id],
    ).fetchone()
    return row is not None


def _event_metadata_mapping(event: BrokerLedgerEvent) -> dict[str, Any]:
    if not event.metadata_json:
        return {}
    try:
        payload = json.loads(event.metadata_json)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _canonical_lineage_identity(event: BrokerLedgerEvent) -> tuple[str, str | None, str, str | None]:
    metadata = _event_metadata_mapping(event)
    execution_identity = metadata.get("execution_identity")
    if execution_identity not in (None, ""):
        execution_identity = str(execution_identity)
    return (
        event.symbol,
        event.side,
        event.event_chain_id or event.parent_order_id or event.order_id,
        execution_identity,
    )


def _events_for_order(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str,
    order_id: str,
) -> list[BrokerLedgerEvent]:
    rows = conn.execute(
        """
        SELECT order_id, event_type, symbol, side, qty, price, event_time,
               sequence_id, parent_order_id, intent_type, exit_reason, pod_id, metadata_json,
               event_chain_id, parent_event_order_id
        FROM broker_event_ledger
        WHERE pod_id = ? AND order_id = ?
        ORDER BY event_time ASC, sequence_id ASC
        """,
        [pod_id, order_id],
    ).fetchall()
    return [_row_to_event(row) for row in rows]


def _validate_event_causality(
    conn: duckdb.DuckDBPyConnection,
    *,
    normalized: BrokerLedgerEvent,
) -> BrokerLedgerEvent:
    event_chain_id = normalized.event_chain_id or normalized.parent_order_id or normalized.order_id
    parent_event_order_id = normalized.parent_event_order_id
    if parent_event_order_id in ("", normalized.order_id):
        parent_event_order_id = None

    existing_events = _events_for_order(conn, pod_id=normalized.pod_id, order_id=normalized.order_id)
    is_root_order = normalized.order_id == event_chain_id
    if (
        normalized.event_type not in {BrokerEventType.ORDER_SUBMITTED, BrokerEventType.ORDER_FILLED}
        and not existing_events
        and is_root_order
    ):
        raise CausalIntegrityError(
            f"EVENT_CAUSAL_CHAIN_GAP: pod_id={normalized.pod_id} order_id={normalized.order_id} first_event_type={normalized.event_type.value}"
        )
    if existing_events:
        anchor_identity = _canonical_lineage_identity(existing_events[0])
        candidate_identity = _canonical_lineage_identity(
            BrokerLedgerEvent(
                order_id=normalized.order_id,
                event_type=normalized.event_type,
                symbol=normalized.symbol,
                side=normalized.side,
                qty=normalized.qty,
                price=normalized.price,
                event_time=normalized.event_time,
                sequence_id=normalized.sequence_id,
                parent_order_id=normalized.parent_order_id,
                intent_type=normalized.intent_type,
                exit_reason=normalized.exit_reason,
                pod_id=normalized.pod_id,
                metadata_json=normalized.metadata_json,
                event_chain_id=event_chain_id,
                parent_event_order_id=parent_event_order_id,
            )
        )
        if anchor_identity[:3] != candidate_identity[:3]:
            raise CausalIntegrityError(
                f"EVENT_CAUSAL_LINEAGE_DIVERGENCE: pod_id={normalized.pod_id} order_id={normalized.order_id}"
            )
        if (
            normalized.event_type is not BrokerEventType.ORDER_SUBMITTED
            and normalized.event_time is not None
            and existing_events[-1].event_time is not None
            and normalized.event_time < existing_events[-1].event_time
        ):
            raise OrderingError(
                f"EVENT_CAUSAL_TIME_REGRESSION: pod_id={normalized.pod_id} order_id={normalized.order_id}"
            )

    if normalized.event_type in _CAUSAL_EVENT_TYPES and normalized.order_id != event_chain_id:
        if not parent_event_order_id:
            parent_event_order_id = normalized.parent_order_id or event_chain_id
        if not parent_event_order_id:
            raise CausalIntegrityError(
                f"EVENT_CAUSAL_ORPHAN: pod_id={normalized.pod_id} order_id={normalized.order_id} event_type={normalized.event_type.value}"
            )
        if parent_event_order_id == normalized.order_id:
            raise CausalIntegrityError(
                f"EVENT_CAUSAL_SELF_PARENT: pod_id={normalized.pod_id} order_id={normalized.order_id}"
            )

        parent_events = _events_for_order(
            conn,
            pod_id=normalized.pod_id,
            order_id=parent_event_order_id,
        )
        if not parent_events:
            raise CausalIntegrityError(
                f"EVENT_CAUSAL_CHAIN_GAP: pod_id={normalized.pod_id} order_id={normalized.order_id} parent_event_order_id={parent_event_order_id}"
            )

        parent_anchor = parent_events[0]
        if (
            normalized.event_time is not None
            and parent_anchor.event_time is not None
            and normalized.event_time < parent_anchor.event_time
        ):
            raise OrderingError(
                f"EVENT_CAUSAL_TIME_REGRESSION: pod_id={normalized.pod_id} order_id={normalized.order_id} parent_event_order_id={parent_event_order_id}"
            )

    if normalized.event_type in _FILL_EVENT_TYPES and normalized.parent_order_id:
        parent_events = _events_for_order(conn, pod_id=normalized.pod_id, order_id=normalized.parent_order_id)
        if not parent_events:
            raise CausalIntegrityError(
                f"EVENT_CAUSAL_ORPHAN_FILL: pod_id={normalized.pod_id} order_id={normalized.order_id} parent_order_id={normalized.parent_order_id}"
            )
        parent_anchor = parent_events[0]
        if (
            normalized.event_time is not None
            and parent_anchor.event_time is not None
            and normalized.event_time < parent_anchor.event_time
        ):
            raise OrderingError(
                f"EVENT_CAUSAL_TIME_REGRESSION: pod_id={normalized.pod_id} order_id={normalized.order_id} parent_order_id={normalized.parent_order_id}"
            )

    return BrokerLedgerEvent(
        order_id=normalized.order_id,
        event_type=normalized.event_type,
        symbol=normalized.symbol,
        side=normalized.side,
        qty=normalized.qty,
        price=normalized.price,
        event_time=normalized.event_time,
        sequence_id=normalized.sequence_id,
        parent_order_id=normalized.parent_order_id,
        intent_type=normalized.intent_type,
        exit_reason=normalized.exit_reason,
        pod_id=normalized.pod_id,
        metadata_json=normalized.metadata_json,
        event_chain_id=event_chain_id,
        parent_event_order_id=parent_event_order_id,
    )


def append_event(
    conn: duckdb.DuckDBPyConnection,
    event: BrokerLedgerEvent | dict[str, Any],
) -> BrokerLedgerEvent:
    """Append a new immutable broker event to the ledger."""
    _ensure_event_ledger_table(conn)
    normalized = _validate_event_causality(conn, normalized=_coerce_event(event))
    if not normalized.order_id:
        raise ValueError("event order_id is required")
    if not normalized.symbol:
        raise ValueError("event symbol is required")

    event_time = normalized.event_time or datetime.now(tz=UTC)
    stored_event_time = (
        event_time.astimezone(UTC).replace(tzinfo=None)
        if event_time.tzinfo is not None
        else event_time
    )
    if normalized.sequence_id is not None:
        sequence_id = normalized.sequence_id
        _validate_sequence_id(conn, pod_id=normalized.pod_id, sequence_id=sequence_id)
    else:
        sequence_id = _next_sequence_id(conn, pod_id=normalized.pod_id)

    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            """
            INSERT INTO broker_event_ledger (
                pod_id,
                order_id,
                event_type,
                symbol,
                side,
                qty,
                price,
                event_time,
                sequence_id,
                parent_order_id,
                intent_type,
                exit_reason,
                metadata_json,
                event_chain_id,
                parent_event_order_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                normalized.pod_id,
                normalized.order_id,
                normalized.event_type.value,
                normalized.symbol,
                normalized.side,
                normalized.qty,
                normalized.price,
                stored_event_time,
                sequence_id,
                normalized.parent_order_id,
                normalized.intent_type,
                normalized.exit_reason,
                normalized.metadata_json,
                normalized.event_chain_id,
                normalized.parent_event_order_id,
            ],
        )
        validate_event_causal_closure(conn, pod_id=normalized.pod_id)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.commit()
    return BrokerLedgerEvent(
        order_id=normalized.order_id,
        event_type=normalized.event_type,
        symbol=normalized.symbol,
        side=normalized.side,
        qty=normalized.qty,
        price=normalized.price,
        event_time=event_time,
        sequence_id=sequence_id,
        parent_order_id=normalized.parent_order_id,
        intent_type=normalized.intent_type,
        exit_reason=normalized.exit_reason,
        pod_id=normalized.pod_id,
        metadata_json=normalized.metadata_json,
        event_chain_id=normalized.event_chain_id,
        parent_event_order_id=normalized.parent_event_order_id,
    )


def get_events_for_order(
    conn: duckdb.DuckDBPyConnection,
    order_id: str,
    *,
    pod_id: str = "default",
) -> list[BrokerLedgerEvent]:
    """Return immutable event history for a broker order."""
    _ensure_event_ledger_table(conn)
    rows = conn.execute(
        """
        SELECT order_id, event_type, symbol, side, qty, price, event_time,
               sequence_id, parent_order_id, intent_type, exit_reason, pod_id, metadata_json,
               event_chain_id, parent_event_order_id
        FROM broker_event_ledger
        WHERE pod_id = ? AND order_id = ?
        ORDER BY event_time ASC, sequence_id ASC
        """,
        [pod_id, order_id],
    ).fetchall()

    return [_row_to_event(row) for row in rows]


def ledger_ordering_digest(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str = "default",
) -> list[LedgerOrderingDigest]:
    _ensure_event_ledger_table(conn)
    rows = conn.execute(
        """
        SELECT event_id, event_time, sequence_id, order_id, event_type, symbol, side, qty,
               price, parent_order_id, intent_type, exit_reason, pod_id, metadata_json,
               event_chain_id, parent_event_order_id
        FROM broker_event_ledger
        WHERE pod_id = ?
        ORDER BY event_time ASC, sequence_id ASC
        """,
        [pod_id],
    ).fetchall()
    return [
        LedgerOrderingDigest(
            event_id=int(row[0]),
            event_time=_parse_dt(row[1]) or datetime.now(tz=UTC),
            sequence_id=int(row[2]),
            order_id=str(row[3]),
            event_type=BrokerEventType(row[4]),
            symbol=str(row[5]),
            side=row[6],
            qty=float(row[7]),
            price=_parse_float(row[8]) if row[8] not in (None, "") else None,
            parent_order_id=row[9],
            intent_type=row[10],
            exit_reason=row[11],
            pod_id=str(row[12]),
            metadata_json=row[13],
            event_chain_id=row[14],
            parent_event_order_id=row[15],
        )
        for row in rows
    ]


def validate_event_causal_closure(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str = "default",
) -> LedgerCausalValidationResult:
    _ensure_event_ledger_table(conn)
    rows = conn.execute(
        """
        SELECT order_id, event_type, symbol, side, qty, price, event_time,
               sequence_id, parent_order_id, intent_type, exit_reason, pod_id, metadata_json,
               event_chain_id, parent_event_order_id
        FROM broker_event_ledger
        WHERE pod_id = ?
        ORDER BY event_time ASC, sequence_id ASC
        """,
        [pod_id],
    ).fetchall()
    events = [_row_to_event(row) for row in rows]
    seen_order_ids: set[str] = set()
    graph: dict[str, set[str]] = {}
    reverse_graph: dict[str, set[str]] = {}
    links: list[LedgerCausalLink] = []
    first_event_by_order: dict[str, BrokerLedgerEvent] = {}
    last_time_by_order: dict[str, datetime] = {}

    for event in events:
        chain_id = event.event_chain_id or event.parent_order_id or event.order_id
        parent_event_order_id = event.parent_event_order_id

        first_event = first_event_by_order.get(event.order_id)
        chain_id = event.event_chain_id or event.parent_order_id or event.order_id
        is_root_order = event.order_id == chain_id
        if first_event is None:
            if event.event_type not in {BrokerEventType.ORDER_SUBMITTED, BrokerEventType.ORDER_FILLED} and is_root_order:
                raise CausalIntegrityError(
                    f"EVENT_CAUSAL_CHAIN_GAP: pod_id={pod_id} order_id={event.order_id} first_event_type={event.event_type.value}"
                )
            first_event_by_order[event.order_id] = event
        else:
            if event.event_time is not None:
                prior_time = last_time_by_order.get(event.order_id)
                if prior_time is not None and event.event_time < prior_time:
                    raise OrderingError(
                        f"EVENT_CAUSAL_TIME_REGRESSION: pod_id={pod_id} order_id={event.order_id}"
                    )
            if _canonical_lineage_identity(first_event)[:3] != _canonical_lineage_identity(event)[:3]:
                raise CausalIntegrityError(
                    f"EVENT_CAUSAL_LINEAGE_DIVERGENCE: pod_id={pod_id} order_id={event.order_id}"
                )

        seen_order_ids.add(event.order_id)
        if event.event_time is not None:
            last_time_by_order[event.order_id] = event.event_time

        if event.event_type in _CAUSAL_EVENT_TYPES and event.order_id != chain_id:
            if not parent_event_order_id:
                raise CausalIntegrityError(
                    f"EVENT_CAUSAL_CHAIN_GAP: pod_id={pod_id} order_id={event.order_id} chain_id={chain_id}"
                )
            if parent_event_order_id not in seen_order_ids:
                raise CausalIntegrityError(
                    f"EVENT_CAUSAL_CHAIN_GAP: pod_id={pod_id} order_id={event.order_id} parent_event_order_id={parent_event_order_id}"
                )
            parent_anchor = first_event_by_order.get(parent_event_order_id)
            if parent_anchor is None:
                raise CausalIntegrityError(
                    f"EVENT_CAUSAL_CHAIN_GAP: pod_id={pod_id} order_id={event.order_id} parent_event_order_id={parent_event_order_id}"
                )
            if (
                event.event_time is not None
                and parent_anchor.event_time is not None
                and event.event_time < parent_anchor.event_time
            ):
                raise OrderingError(
                    f"EVENT_CAUSAL_TIME_REGRESSION: pod_id={pod_id} order_id={event.order_id} parent_event_order_id={parent_event_order_id}"
                )
            graph.setdefault(parent_event_order_id, set()).add(event.order_id)
            reverse_graph.setdefault(event.order_id, set()).add(parent_event_order_id)

        if event.event_type in _FILL_EVENT_TYPES and event.parent_order_id:
            parent_anchor = first_event_by_order.get(event.parent_order_id)
            if parent_anchor is None:
                raise CausalIntegrityError(
                    f"EVENT_CAUSAL_ORPHAN_FILL: pod_id={pod_id} order_id={event.order_id} parent_order_id={event.parent_order_id}"
                )
            if (
                event.event_time is not None
                and parent_anchor.event_time is not None
                and event.event_time < parent_anchor.event_time
            ):
                raise OrderingError(
                    f"EVENT_CAUSAL_TIME_REGRESSION: pod_id={pod_id} order_id={event.order_id} parent_order_id={event.parent_order_id}"
                )

        links.append(
            LedgerCausalLink(
                order_id=event.order_id,
                parent_order_id=event.parent_order_id,
                parent_event_order_id=parent_event_order_id,
                event_chain_id=chain_id,
                event_type=event.event_type,
                sequence_id=event.sequence_id or 0,
                symbol=event.symbol,
                intent_type=event.intent_type,
            )
        )

    visiting: set[str] = set()
    visited: set[str] = set()

    def _visit(node: str) -> None:
        if node in visited:
            return
        if node in visiting:
            raise CausalIntegrityError(f"EVENT_CAUSAL_CYCLE: pod_id={pod_id} order_id={node}")
        visiting.add(node)
        for child in sorted(graph.get(node, set())):
            _visit(child)
        visiting.remove(node)
        visited.add(node)

    for node in sorted(seen_order_ids):
        _visit(node)

    root_order_ids = tuple(sorted(node for node in seen_order_ids if not reverse_graph.get(node)))
    leaf_order_ids = tuple(sorted(node for node in seen_order_ids if not graph.get(node)))

    return LedgerCausalValidationResult(
        pod_id=pod_id,
        links=tuple(sorted(links, key=lambda item: (item.sequence_id, item.order_id))),
        root_order_ids=root_order_ids,
        leaf_order_ids=leaf_order_ids,
    )


def rebuild_position_state_from_events(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str = "default",
) -> dict[str, RebuiltPositionState]:
    """Rebuild per-symbol position state by replaying immutable broker events."""
    _ensure_event_ledger_table(conn)
    _validate_replay_sequence_order(conn, pod_id=pod_id)
    validate_event_causal_closure(conn, pod_id=pod_id)
    rows = conn.execute(
        """
        SELECT order_id, event_type, symbol, side, qty, price, event_time,
               sequence_id, parent_order_id, intent_type, exit_reason, pod_id, metadata_json,
               event_chain_id, parent_event_order_id
        FROM broker_event_ledger
        WHERE pod_id = ?
        ORDER BY event_time ASC, sequence_id ASC
        """,
        [pod_id],
    ).fetchall()

    state: dict[str, dict[str, Any]] = {}

    for row in rows:
        event = _row_to_event(row)

        symbol_state = state.setdefault(
            event.symbol,
            {
                "symbol": event.symbol,
                "pod_id": event.pod_id,
                "position_qty": 0.0,
                "avg_cost": 0.0,
                "current_price": 0.0,
                "is_open": False,
                "is_closed": False,
                "last_event_type": None,
                "last_sequence_id": None,
                "entry_order_id": None,
                "exit_order_id": None,
                "closed_at": None,
            },
        )

        symbol_state["last_event_type"] = event.event_type
        symbol_state["last_sequence_id"] = event.sequence_id
        if event.price is not None:
            symbol_state["current_price"] = event.price

        if event.event_type in _FILL_EVENT_TYPES:
            qty = event.qty
            price = event.price or 0.0
            side = (event.side or "").lower()

            if side == "buy":
                existing_qty = symbol_state["position_qty"]
                new_qty = existing_qty + qty
                if existing_qty >= 0.0 and new_qty > 0.0:
                    total_cost = (existing_qty * symbol_state["avg_cost"]) + (qty * price)
                    symbol_state["avg_cost"] = total_cost / new_qty
                elif existing_qty < 0.0 and new_qty > 0.0:
                    symbol_state["avg_cost"] = price
                elif abs(new_qty) <= 1e-9:
                    symbol_state["avg_cost"] = 0.0
                symbol_state["position_qty"] = new_qty
                symbol_state["is_open"] = abs(new_qty) > 1e-9
                symbol_state["is_closed"] = not symbol_state["is_open"]
                symbol_state["closed_at"] = (
                    event.event_time if symbol_state["is_closed"] else None
                )
                if event.intent_type in {"entry", "entry_long", "entry_short"} and not symbol_state["entry_order_id"]:
                    symbol_state["entry_order_id"] = event.parent_order_id or event.order_id
            elif side == "sell":
                existing_qty = symbol_state["position_qty"]
                new_qty = existing_qty - qty
                if existing_qty <= 0.0 and new_qty < 0.0:
                    prior_short_qty = abs(existing_qty)
                    total_short_qty = prior_short_qty + qty
                    if total_short_qty > 0.0:
                        total_cost = (prior_short_qty * symbol_state["avg_cost"]) + (qty * price)
                        symbol_state["avg_cost"] = total_cost / total_short_qty
                elif existing_qty > 0.0 and new_qty < 0.0:
                    symbol_state["avg_cost"] = price
                elif abs(new_qty) <= 1e-9:
                    symbol_state["avg_cost"] = 0.0
                symbol_state["position_qty"] = new_qty
                symbol_state["is_open"] = abs(new_qty) > 1e-9
                symbol_state["exit_order_id"] = event.order_id
                if not symbol_state["entry_order_id"] and event.parent_order_id:
                    symbol_state["entry_order_id"] = event.parent_order_id
                symbol_state["is_closed"] = not symbol_state["is_open"]
                symbol_state["closed_at"] = (
                    event.event_time if symbol_state["is_closed"] else None
                )

        if event.event_type == BrokerEventType.POSITION_CLOSED:
            symbol_state["position_qty"] = 0.0
            symbol_state["is_open"] = False
            symbol_state["is_closed"] = True
            symbol_state["exit_order_id"] = event.order_id
            if not symbol_state["entry_order_id"] and event.parent_order_id:
                symbol_state["entry_order_id"] = event.parent_order_id
            symbol_state["closed_at"] = event.event_time

    return {
        symbol: RebuiltPositionState(
            symbol=values["symbol"],
            pod_id=values["pod_id"],
            position_qty=values["position_qty"],
            avg_cost=values["avg_cost"],
            current_price=values["current_price"],
            is_open=values["is_open"],
            is_closed=values["is_closed"],
            last_event_type=values["last_event_type"],
            last_sequence_id=values["last_sequence_id"],
            entry_order_id=values["entry_order_id"],
            exit_order_id=values["exit_order_id"],
            closed_at=values["closed_at"],
        )
        for symbol, values in state.items()
    }


__all__ = [
    "BrokerEventType",
    "BrokerLedgerEvent",
    "LedgerCausalLink",
    "LedgerCausalValidationResult",
    "LedgerOrderingDigest",
    "RebuiltPositionState",
    "append_event",
    "get_events_for_order",
    "ledger_ordering_digest",
    "rebuild_position_state_from_events",
    "validate_event_causal_closure",
]
