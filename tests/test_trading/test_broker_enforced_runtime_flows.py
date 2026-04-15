from datetime import UTC, date, datetime

import duckdb
import pytest

from llm_quant.broker.event_ledger import BrokerEventType, BrokerLedgerEvent, append_event, rebuild_position_state_from_events
from llm_quant.broker.reconciliation import persist_submitted_orders, reconcile_broker_orders
from llm_quant.trading.portfolio import Portfolio


class FlatReconcileClient:
    def __init__(self, orders: dict[str, dict[str, object]]) -> None:
        self._orders = orders

    def get_order(self, order_id: str, nested: bool = True) -> dict[str, object]:
        return self._orders[order_id]


def test_eod_flatten_requires_broker_confirmed_flat_portfolio() -> None:
    conn = duckdb.connect(":memory:")
    portfolio = Portfolio(initial_capital=1_000.0)

    persist_submitted_orders(
        conn,
        [
            {
                "order_id": "entry-1",
                "symbol": "QQQ",
                "side": "buy",
                "qty": 3,
                "intent_type": "entry",
                "status": "accepted",
                "submitted_at": datetime(2026, 4, 8, 14, 30, tzinfo=UTC).isoformat(),
            },
            {
                "order_id": "flatten-1",
                "symbol": "QQQ",
                "side": "sell",
                "qty": 3,
                "intent_type": "stop_loss",
                "parent_order_id": "entry-1",
                "exit_reason": "eod_flatten",
                "status": "accepted",
                "submitted_at": datetime(2026, 4, 8, 19, 55, tzinfo=UTC).isoformat(),
            },
        ],
    )

    client = FlatReconcileClient(
        {
            "entry-1": {
                "id": "entry-1",
                "symbol": "QQQ",
                "side": "buy",
                "status": "filled",
                "qty": "3",
                "filled_qty": "3",
                "filled_avg_price": "100",
                "submitted_at": datetime(2026, 4, 8, 14, 30, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 8, 14, 31, tzinfo=UTC).isoformat(),
                "intent_type": "entry",
            },
            "flatten-1": {
                "id": "flatten-1",
                "symbol": "QQQ",
                "side": "sell",
                "status": "filled",
                "qty": "3",
                "filled_qty": "3",
                "filled_avg_price": "101",
                "submitted_at": datetime(2026, 4, 8, 19, 55, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 8, 19, 56, tzinfo=UTC).isoformat(),
                "intent_type": "stop_loss",
                "parent_order_id": "entry-1",
                "exit_reason": "eod_flatten",
            },
        }
    )

    result = reconcile_broker_orders(
        conn,
        client,
        portfolio=portfolio,
        broker_positions=[{"symbol": "QQQ", "qty": "0"}],
    )

    assert result.applied_fill_count == 2
    assert result.persisted_fill_count == 2
    assert portfolio.positions == {}

    rebuilt = rebuild_position_state_from_events(conn)
    assert rebuilt["QQQ"].is_closed is True
    assert rebuilt["QQQ"].position_qty == 0.0
    assert rebuilt["QQQ"].last_event_type is BrokerEventType.POSITION_CLOSED

    fill_rows = conn.execute(
        """
        SELECT order_id, symbol, side, fill_qty, exit_reason
        FROM broker_fill_events
        ORDER BY fill_time
        """
    ).fetchall()
    assert fill_rows == [
        ("entry-1", "QQQ", "buy", 3.0, None),
        ("flatten-1", "QQQ", "sell", 3.0, "eod_flatten"),
    ]


def test_event_ledger_append_rebuild_matches_reconciled_portfolio() -> None:
    conn = duckdb.connect(":memory:")
    portfolio = Portfolio(initial_capital=2_000.0)

    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="entry-1",
            event_type=BrokerEventType.ORDER_SUBMITTED,
            symbol="IWM",
            side="buy",
            qty=4,
            event_time=datetime(2026, 4, 9, 14, 30, tzinfo=UTC),
            intent_type="entry",
        ),
    )
    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="entry-1",
            event_type=BrokerEventType.ORDER_FILLED,
            symbol="IWM",
            side="buy",
            qty=4,
            price=50,
            event_time=datetime(2026, 4, 9, 14, 31, tzinfo=UTC),
            intent_type="entry",
        ),
    )
    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="tp1-1",
            event_type=BrokerEventType.TAKE_PROFIT_1_FILLED,
            symbol="IWM",
            side="sell",
            qty=1,
            price=52,
            event_time=datetime(2026, 4, 9, 15, 0, tzinfo=UTC),
            parent_order_id="entry-1",
            intent_type="take_profit_1",
            exit_reason="tp1",
        ),
    )

    rebuilt = rebuild_position_state_from_events(conn)
    assert rebuilt["IWM"].position_qty == 3.0

    persist_submitted_orders(
        conn,
        [
            {
                "order_id": "entry-1",
                "symbol": "IWM",
                "side": "buy",
                "qty": 4,
                "intent_type": "entry",
                "status": "filled",
                "submitted_at": datetime(2026, 4, 9, 14, 30, tzinfo=UTC).isoformat(),
            },
            {
                "order_id": "tp1-1",
                "symbol": "IWM",
                "side": "sell",
                "qty": 1,
                "intent_type": "take_profit_1",
                "parent_order_id": "entry-1",
                "exit_reason": "tp1",
                "status": "partially_filled",
                "submitted_at": datetime(2026, 4, 9, 15, 0, tzinfo=UTC).isoformat(),
            },
        ],
    )

    client = FlatReconcileClient(
        {
            "entry-1": {
                "id": "entry-1",
                "symbol": "IWM",
                "side": "buy",
                "status": "filled",
                "qty": "4",
                "filled_qty": "4",
                "filled_avg_price": "50",
                "submitted_at": datetime(2026, 4, 9, 14, 30, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 9, 14, 31, tzinfo=UTC).isoformat(),
                "intent_type": "entry",
            },
            "tp1-1": {
                "id": "tp1-1",
                "symbol": "IWM",
                "side": "sell",
                "status": "partially_filled",
                "qty": "1",
                "filled_qty": "1",
                "filled_avg_price": "52",
                "submitted_at": datetime(2026, 4, 9, 15, 0, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 9, 15, 1, tzinfo=UTC).isoformat(),
                "intent_type": "take_profit_1",
                "parent_order_id": "entry-1",
                "exit_reason": "tp1",
            },
        }
    )

    with pytest.raises(RuntimeError, match="EVENT LEDGER STATE DIVERGENCE"):
        reconcile_broker_orders(
            conn,
            client,
            portfolio=portfolio,
            broker_positions=[{"symbol": "IWM", "qty": "3"}],
        )


def test_event_ledger_append_rebuild_and_portfolio_match_after_reconcile_only_flow() -> None:
    conn = duckdb.connect(":memory:")
    portfolio = Portfolio(initial_capital=2_000.0)

    persist_submitted_orders(
        conn,
        [
            {
                "order_id": "entry-2",
                "symbol": "DIA",
                "side": "buy",
                "qty": 2,
                "intent_type": "entry",
                "status": "accepted",
                "submitted_at": datetime(2026, 4, 10, 14, 30, tzinfo=UTC).isoformat(),
            },
            {
                "order_id": "exit-2",
                "symbol": "DIA",
                "side": "sell",
                "qty": 2,
                "intent_type": "take_profit_2",
                "parent_order_id": "entry-2",
                "exit_reason": "tp2",
                "status": "accepted",
                "submitted_at": datetime(2026, 4, 10, 15, 30, tzinfo=UTC).isoformat(),
            },
        ],
    )

    client = FlatReconcileClient(
        {
            "entry-2": {
                "id": "entry-2",
                "symbol": "DIA",
                "side": "buy",
                "status": "filled",
                "qty": "2",
                "filled_qty": "2",
                "filled_avg_price": "200",
                "submitted_at": datetime(2026, 4, 10, 14, 30, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 10, 14, 31, tzinfo=UTC).isoformat(),
                "intent_type": "entry",
            },
            "exit-2": {
                "id": "exit-2",
                "symbol": "DIA",
                "side": "sell",
                "status": "filled",
                "qty": "2",
                "filled_qty": "2",
                "filled_avg_price": "204",
                "submitted_at": datetime(2026, 4, 10, 15, 30, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 10, 15, 35, tzinfo=UTC).isoformat(),
                "intent_type": "take_profit_2",
                "parent_order_id": "entry-2",
                "exit_reason": "tp2",
            },
        }
    )

    result = reconcile_broker_orders(
        conn,
        client,
        portfolio=portfolio,
        broker_positions=[{"symbol": "DIA", "qty": "0"}],
    )

    assert result.applied_fill_count == 2
    assert portfolio.positions == {}

    rebuilt = rebuild_position_state_from_events(conn)
    assert rebuilt["DIA"].position_qty == 0.0
    assert rebuilt["DIA"].is_closed is True
    assert rebuilt["DIA"].entry_order_id == "entry-2"
    assert rebuilt["DIA"].exit_order_id == "exit-2"