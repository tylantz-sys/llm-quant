from datetime import UTC, datetime

import duckdb

import pytest

from llm_quant.broker.event_ledger import (
    BrokerEventType,
    BrokerLedgerEvent,
    append_event,
    get_events_for_order,
    ledger_ordering_digest,
    rebuild_position_state_from_events,
)
from llm_quant.broker.exceptions import OrderingError


def test_get_events_for_order_returns_immutable_event_history() -> None:
    conn = duckdb.connect(":memory:")

    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="entry-1",
            event_type=BrokerEventType.ORDER_SUBMITTED,
            symbol="AAPL",
            side="buy",
            qty=5,
            event_time=datetime(2026, 4, 1, 14, 30, tzinfo=UTC),
            intent_type="entry",
        ),
    )
    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="entry-1",
            event_type=BrokerEventType.ORDER_FILLED,
            symbol="AAPL",
            side="buy",
            qty=5,
            price=100,
            event_time=datetime(2026, 4, 1, 14, 31, tzinfo=UTC),
            intent_type="entry",
        ),
    )
    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="tp-1",
            event_type=BrokerEventType.TAKE_PROFIT_1_FILLED,
            symbol="AAPL",
            side="sell",
            qty=2,
            price=110,
            event_time=datetime(2026, 4, 1, 15, 0, tzinfo=UTC),
            parent_order_id="entry-1",
            intent_type="take_profit_1",
            exit_reason="tp1",
        ),
    )

    events = get_events_for_order(conn, "entry-1")

    assert [event.event_type for event in events] == [
        BrokerEventType.ORDER_SUBMITTED,
        BrokerEventType.ORDER_FILLED,
    ]
    assert events[0].order_id == "entry-1"
    assert events[1].price == 100.0


def test_rebuild_position_state_from_events_reconstructs_open_position() -> None:
    conn = duckdb.connect(":memory:")

    append_event(
        conn,
        {
            "order_id": "entry-1",
            "event_type": "ORDER_SUBMITTED",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "event_time": datetime(2026, 4, 1, 14, 30, tzinfo=UTC),
            "intent_type": "entry",
        },
    )
    append_event(
        conn,
        {
            "order_id": "entry-1",
            "event_type": "ORDER_FILLED",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "price": 100,
            "event_time": datetime(2026, 4, 1, 14, 31, tzinfo=UTC),
            "intent_type": "entry",
        },
    )
    append_event(
        conn,
        {
            "order_id": "tp-1",
            "event_type": "TAKE_PROFIT_1_FILLED",
            "symbol": "AAPL",
            "side": "sell",
            "qty": 4,
            "price": 110,
            "event_time": datetime(2026, 4, 1, 15, 0, tzinfo=UTC),
            "parent_order_id": "entry-1",
            "intent_type": "take_profit_1",
            "exit_reason": "tp1",
        },
    )

    rebuilt = rebuild_position_state_from_events(conn)

    assert rebuilt["AAPL"].position_qty == 6.0
    assert rebuilt["AAPL"].avg_cost == 100.0
    assert rebuilt["AAPL"].current_price == 110.0
    assert rebuilt["AAPL"].is_open is True
    assert rebuilt["AAPL"].is_closed is False
    assert rebuilt["AAPL"].entry_order_id == "entry-1"
    assert rebuilt["AAPL"].exit_order_id == "tp-1"
    assert rebuilt["AAPL"].last_event_type is BrokerEventType.TAKE_PROFIT_1_FILLED


def test_rebuild_position_state_from_events_reconstructs_closed_position() -> None:
    conn = duckdb.connect(":memory:")

    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="entry-1",
            event_type=BrokerEventType.ORDER_FILLED,
            symbol="MSFT",
            side="buy",
            qty=5,
            price=200,
            event_time=datetime(2026, 4, 1, 14, 31, tzinfo=UTC),
            intent_type="entry",
        ),
    )
    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="stop-1",
            event_type=BrokerEventType.STOP_TRIGGERED,
            symbol="MSFT",
            side="sell",
            qty=5,
            price=190,
            event_time=datetime(2026, 4, 1, 15, 15, tzinfo=UTC),
            parent_order_id="entry-1",
            intent_type="stop_loss",
            exit_reason="stop_loss",
        ),
    )
    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="stop-1",
            event_type=BrokerEventType.POSITION_CLOSED,
            symbol="MSFT",
            side="sell",
            qty=0,
            event_time=datetime(2026, 4, 1, 15, 16, tzinfo=UTC),
            parent_order_id="entry-1",
            intent_type="stop_loss",
            exit_reason="stop_loss",
        ),
    )

    rebuilt = rebuild_position_state_from_events(conn)

    assert rebuilt["MSFT"].position_qty == 0.0
    assert rebuilt["MSFT"].is_open is False
    assert rebuilt["MSFT"].is_closed is True
    assert rebuilt["MSFT"].entry_order_id == "entry-1"
    assert rebuilt["MSFT"].exit_order_id == "stop-1"
    assert rebuilt["MSFT"].last_event_type is BrokerEventType.POSITION_CLOSED
    assert rebuilt["MSFT"].closed_at == datetime(2026, 4, 1, 15, 16, tzinfo=UTC)


def test_same_timestamp_stable_ordering_uses_sequence_id() -> None:
    conn = duckdb.connect(":memory:")
    timestamp = datetime(2026, 4, 1, 14, 30, tzinfo=UTC)

    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="entry-1",
            event_type=BrokerEventType.ORDER_SUBMITTED,
            symbol="AAPL",
            side="buy",
            qty=5,
            event_time=timestamp,
        ),
    )
    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="entry-1",
            event_type=BrokerEventType.ORDER_FILLED,
            symbol="AAPL",
            side="buy",
            qty=5,
            price=100,
            event_time=timestamp,
            intent_type="entry",
        ),
    )
    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="tp-1",
            event_type=BrokerEventType.TAKE_PROFIT_1_FILLED,
            symbol="AAPL",
            side="sell",
            qty=2,
            price=110,
            event_time=timestamp,
            parent_order_id="entry-1",
            intent_type="take_profit_1",
            exit_reason="tp1",
        ),
    )

    digest = ledger_ordering_digest(conn)

    assert [(item.event_time, item.sequence_id, item.event_type) for item in digest] == [
        (timestamp, 1, BrokerEventType.ORDER_SUBMITTED),
        (timestamp, 2, BrokerEventType.ORDER_FILLED),
        (timestamp, 3, BrokerEventType.TAKE_PROFIT_1_FILLED),
    ]


def test_persist_load_replay_preserves_byte_identical_sequence_ordering() -> None:
    conn = duckdb.connect(":memory:")
    timestamp = datetime(2026, 4, 1, 14, 30, tzinfo=UTC)

    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="entry-1",
            event_type=BrokerEventType.ORDER_SUBMITTED,
            symbol="AAPL",
            side="buy",
            qty=5,
            event_time=timestamp,
        ),
    )
    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="entry-1",
            event_type=BrokerEventType.ORDER_FILLED,
            symbol="AAPL",
            side="buy",
            qty=5,
            price=100,
            event_time=timestamp,
            intent_type="entry",
        ),
    )
    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="stop-1",
            event_type=BrokerEventType.ORDER_CANCELED,
            symbol="AAPL",
            side="sell",
            qty=5,
            event_time=timestamp,
            parent_order_id="entry-1",
            intent_type="stop_loss",
            exit_reason="canceled",
        ),
    )

    digest_before = ledger_ordering_digest(conn)
    rebuilt = rebuild_position_state_from_events(conn)

    digest_after = ledger_ordering_digest(conn)

    assert digest_after == digest_before
    assert rebuilt["AAPL"].last_sequence_id == 3


def test_out_of_order_insert_raises() -> None:
    conn = duckdb.connect(":memory:")
    timestamp = datetime(2026, 4, 1, 14, 30, tzinfo=UTC)

    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="entry-1",
            event_type=BrokerEventType.ORDER_SUBMITTED,
            symbol="AAPL",
            side="buy",
            qty=5,
            event_time=timestamp,
            sequence_id=5,
        ),
    )

    with pytest.raises(OrderingError, match="EVENT_SEQUENCE_REGRESSION"):
        append_event(
            conn,
            BrokerLedgerEvent(
                order_id="entry-2",
                event_type=BrokerEventType.ORDER_SUBMITTED,
                symbol="AAPL",
                side="buy",
                qty=5,
                event_time=timestamp,
                sequence_id=4,
            ),
        )