from datetime import UTC, datetime

import duckdb
import pytest

from llm_quant.broker.event_ledger import ledger_ordering_digest, rebuild_position_state_from_events
from llm_quant.broker.exceptions import PositionInvariantError, ReconciliationError
from llm_quant.broker.reconciliation import (
    ReconciliationStatus,
    persist_submitted_orders,
    reconcile_broker_orders,
)
from llm_quant.broker.state_machine import BrokerLifecycleState
from llm_quant.trading.portfolio import Portfolio


class StubAlpacaClient:
    def __init__(self, orders: dict[str, dict[str, object]]) -> None:
        self._orders = orders

    def get_order(self, order_id: str, nested: bool = True) -> dict[str, object]:
        return self._orders[order_id]


def test_persist_submitted_orders_stores_tracking_fields() -> None:
    conn = duckdb.connect(":memory:")
    count = persist_submitted_orders(
        conn,
        [
            {
                "order_id": "entry-1",
                "symbol": "AAPL",
                "side": "buy",
                "qty": 5,
                "order_type": "market",
                "intent_type": "entry",
                "parent_order_id": None,
                "exit_reason": None,
                "status": "accepted",
            },
            {
                "order_id": "tp-1",
                "symbol": "AAPL",
                "side": "sell",
                "qty": 2.5,
                "order_type": "limit",
                "intent_type": "take_profit_1",
                "parent_order_id": "entry-1",
                "exit_reason": "tp1",
                "status": "new",
            },
        ],
    )
    assert count == 2

    rows = conn.execute(
        """
        SELECT order_id, symbol, side, qty, intent_type, parent_order_id, exit_reason, status
        FROM broker_submitted_orders
        ORDER BY order_id
        """
    ).fetchall()

    assert rows == [
        ("entry-1", "AAPL", "buy", 5.0, "entry", None, None, "accepted"),
        ("tp-1", "AAPL", "sell", 2.5, "take_profit_1", "entry-1", "tp1", "new"),
    ]


def test_reconcile_broker_orders_applies_broker_fill_and_persists_lifecycle() -> None:
    conn = duckdb.connect(":memory:")
    portfolio = Portfolio(initial_capital=1_000.0)

    persist_submitted_orders(
        conn,
        [
            {
                "order_id": "entry-1",
                "symbol": "AAPL",
                "side": "buy",
                "qty": 5,
                "order_type": "market",
                "intent_type": "entry",
                "status": "accepted",
                "submitted_at": datetime(2026, 4, 1, 14, 30, tzinfo=UTC).isoformat(),
            },
            {
                "order_id": "tp-1",
                "symbol": "AAPL",
                "side": "sell",
                "qty": 2.5,
                "order_type": "limit",
                "intent_type": "take_profit_1",
                "parent_order_id": "entry-1",
                "exit_reason": "tp1",
                "status": "accepted",
            },
        ],
    )

    client = StubAlpacaClient(
        {
            "entry-1": {
                "id": "entry-1",
                "symbol": "AAPL",
                "side": "buy",
                "status": "filled",
                "qty": "5",
                "filled_qty": "5",
                "filled_avg_price": "100",
                "submitted_at": datetime(2026, 4, 1, 14, 30, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 1, 14, 31, tzinfo=UTC).isoformat(),
                "intent_type": "entry",
            },
            "tp-1": {
                "id": "tp-1",
                "symbol": "AAPL",
                "side": "sell",
                "status": "new",
                "qty": "2.5",
                "filled_qty": "0",
                "submitted_at": datetime(2026, 4, 1, 14, 32, tzinfo=UTC).isoformat(),
                "intent_type": "take_profit_1",
                "parent_order_id": "entry-1",
                "exit_reason": "tp1",
            },
        }
    )

    with pytest.raises(ReconciliationError, match="MISSING_TP1_LEG"):
        reconcile_broker_orders(
            conn,
            client,
            portfolio=portfolio,
            broker_positions=[{"symbol": "AAPL", "qty": "5"}],
        )


def test_reconciliation_rebuilds_portfolio_from_persisted_fill_history() -> None:
    conn = duckdb.connect(":memory:")
    portfolio = Portfolio(initial_capital=1_000.0)

    persist_submitted_orders(
        conn,
        [
            {
                "order_id": "entry-1",
                "symbol": "MSFT",
                "side": "buy",
                "qty": 4,
                "intent_type": "entry",
                "status": "accepted",
                "submitted_at": datetime(2026, 4, 2, 14, 30, tzinfo=UTC).isoformat(),
            },
            {
                "order_id": "tp-1",
                "symbol": "MSFT",
                "side": "sell",
                "qty": 2,
                "intent_type": "take_profit_1",
                "parent_order_id": "entry-1",
                "exit_reason": "tp1",
                "status": "accepted",
                "submitted_at": datetime(2026, 4, 2, 15, 0, tzinfo=UTC).isoformat(),
            },
        ],
    )

    initial_client = StubAlpacaClient(
        {
            "entry-1": {
                "id": "entry-1",
                "symbol": "MSFT",
                "side": "buy",
                "status": "filled",
                "qty": "4",
                "filled_qty": "4",
                "filled_avg_price": "50",
                "submitted_at": datetime(2026, 4, 2, 14, 30, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 2, 14, 31, tzinfo=UTC).isoformat(),
                "intent_type": "entry",
            },
            "tp-1": {
                "id": "tp-1",
                "symbol": "MSFT",
                "side": "sell",
                "status": "partially_filled",
                "qty": "2",
                "filled_qty": "2",
                "filled_avg_price": "55",
                "submitted_at": datetime(2026, 4, 2, 15, 0, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 2, 15, 5, tzinfo=UTC).isoformat(),
                "intent_type": "take_profit_1",
                "parent_order_id": "entry-1",
                "exit_reason": "tp1",
            },
        }
    )

    first_result = reconcile_broker_orders(
        conn,
        initial_client,
        portfolio=portfolio,
        broker_positions=[{"symbol": "MSFT", "qty": "2"}],
        order_ids=["entry-1", "tp-1"],
    )

    assert first_result.status is ReconciliationStatus.SUCCESS
    assert first_result.applied_fill_count == 2
    assert portfolio.positions["MSFT"].shares == 2
    assert portfolio.cash == 910.0

    rebuilt_portfolio = Portfolio(initial_capital=1_000.0)
    replay_client = StubAlpacaClient(
        {
            "entry-1": {
                "id": "entry-1",
                "symbol": "MSFT",
                "side": "buy",
                "status": "filled",
                "qty": "4",
                "filled_qty": "4",
                "filled_avg_price": "50",
                "submitted_at": datetime(2026, 4, 2, 14, 30, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 2, 14, 31, tzinfo=UTC).isoformat(),
                "intent_type": "entry",
            },
            "tp-1": {
                "id": "tp-1",
                "symbol": "MSFT",
                "side": "sell",
                "status": "partially_filled",
                "qty": "2",
                "filled_qty": "2",
                "filled_avg_price": "55",
                "submitted_at": datetime(2026, 4, 2, 15, 0, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 2, 15, 5, tzinfo=UTC).isoformat(),
                "intent_type": "take_profit_1",
                "parent_order_id": "entry-1",
                "exit_reason": "tp1",
            },
        }
    )

    replay_result = reconcile_broker_orders(
        conn,
        replay_client,
        portfolio=rebuilt_portfolio,
        broker_positions=[{"symbol": "MSFT", "qty": "2"}],
        order_ids=["entry-1", "tp-1"],
    )

    assert replay_result.status is ReconciliationStatus.SUCCESS
    assert replay_result.persisted_fill_count == 0
    assert replay_result.applied_fill_count == 2
    assert rebuilt_portfolio.positions["MSFT"].shares == 2
    assert rebuilt_portfolio.cash == 910.0


def test_reconciliation_requires_event_confirmed_position_close() -> None:
    conn = duckdb.connect(":memory:")
    portfolio = Portfolio(initial_capital=1_000.0)

    persist_submitted_orders(
        conn,
        [
            {
                "order_id": "entry-1",
                "symbol": "TSLA",
                "side": "buy",
                "qty": 5,
                "intent_type": "entry",
                "status": "accepted",
            },
            {
                "order_id": "exit-1",
                "symbol": "TSLA",
                "side": "sell",
                "qty": 5,
                "intent_type": "stop_loss",
                "parent_order_id": "entry-1",
                "exit_reason": "stop",
                "status": "accepted",
            },
        ],
    )

    client = StubAlpacaClient(
        {
            "entry-1": {
                "id": "entry-1",
                "symbol": "TSLA",
                "side": "buy",
                "status": "filled",
                "qty": "5",
                "filled_qty": "5",
                "filled_avg_price": "100",
                "submitted_at": datetime(2026, 4, 3, 14, 30, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 3, 14, 31, tzinfo=UTC).isoformat(),
                "intent_type": "entry",
            },
            "exit-1": {
                "id": "exit-1",
                "symbol": "TSLA",
                "side": "sell",
                "status": "filled",
                "qty": "5",
                "filled_qty": "5",
                "filled_avg_price": "95",
                "submitted_at": datetime(2026, 4, 3, 14, 40, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 3, 14, 45, tzinfo=UTC).isoformat(),
                "intent_type": "stop_loss",
                "parent_order_id": "entry-1",
                "exit_reason": "stop",
            },
        }
    )

    result = reconcile_broker_orders(
        conn,
        client,
        portfolio=portfolio,
        broker_positions=[{"symbol": "TSLA", "qty": "0"}],
        order_ids=["entry-1", "exit-1"],
    )

    assert result.status is ReconciliationStatus.SUCCESS
    assert result.lifecycle["TSLA"].state is BrokerLifecycleState.CLOSED
    assert "TSLA" not in portfolio.positions

    fill_rows = conn.execute(
        """
        SELECT order_id, symbol, side, fill_qty, fill_price, intent_type
        FROM broker_fill_events
        ORDER BY fill_time
        """
    ).fetchall()
    assert fill_rows == [
        ("entry-1", "TSLA", "buy", 5.0, 100.0, "entry"),
        ("exit-1", "TSLA", "sell", 5.0, 95.0, "stop_loss"),
    ]


def test_reconcile_short_entry_and_cover_rebuilds_flat_portfolio() -> None:
    conn = duckdb.connect(":memory:")
    portfolio = Portfolio(initial_capital=1_000.0)

    persist_submitted_orders(
        conn,
        [
            {
                "order_id": "short-1",
                "symbol": "SPY",
                "side": "sell",
                "qty": 5,
                "intent_type": "entry_short",
                "status": "accepted",
            },
            {
                "order_id": "cover-1",
                "symbol": "SPY",
                "side": "buy",
                "qty": 5,
                "intent_type": "cover",
                "parent_order_id": "short-1",
                "exit_reason": "cover",
                "status": "accepted",
            },
        ],
    )

    client = StubAlpacaClient(
        {
            "short-1": {
                "id": "short-1",
                "symbol": "SPY",
                "side": "sell",
                "status": "filled",
                "qty": "5",
                "filled_qty": "5",
                "filled_avg_price": "100",
                "submitted_at": datetime(2026, 4, 3, 14, 30, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 3, 14, 31, tzinfo=UTC).isoformat(),
                "intent_type": "entry_short",
            },
            "cover-1": {
                "id": "cover-1",
                "symbol": "SPY",
                "side": "buy",
                "status": "filled",
                "qty": "5",
                "filled_qty": "5",
                "filled_avg_price": "96",
                "submitted_at": datetime(2026, 4, 3, 14, 40, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 3, 14, 45, tzinfo=UTC).isoformat(),
                "intent_type": "cover",
                "parent_order_id": "short-1",
                "exit_reason": "cover",
            },
        }
    )

    result = reconcile_broker_orders(
        conn,
        client,
        portfolio=portfolio,
        broker_positions=[{"symbol": "SPY", "qty": "0"}],
        order_ids=["short-1", "cover-1"],
    )

    assert result.status is ReconciliationStatus.SUCCESS
    assert result.lifecycle["SPY"].state is BrokerLifecycleState.CLOSED
    assert "SPY" not in portfolio.positions
    assert portfolio.cash == 1_020.0


def test_missing_tp_leg_is_classified_explicitly_not_silently_repaired() -> None:
    conn = duckdb.connect(":memory:")
    portfolio = Portfolio(initial_capital=1_000.0)

    persist_submitted_orders(
        conn,
        [
            {
                "order_id": "entry-2",
                "symbol": "NVDA",
                "side": "buy",
                "qty": 10,
                "intent_type": "entry",
                "status": "filled",
                "submitted_at": datetime(2026, 4, 4, 14, 30, tzinfo=UTC).isoformat(),
            },
            {
                "order_id": "tp2-2",
                "symbol": "NVDA",
                "side": "sell",
                "qty": 5,
                "intent_type": "take_profit_2",
                "parent_order_id": "entry-2",
                "exit_reason": "tp2",
                "status": "new",
                "submitted_at": datetime(2026, 4, 4, 15, 0, tzinfo=UTC).isoformat(),
            },
        ],
    )

    client = StubAlpacaClient(
        {
            "entry-2": {
                "id": "entry-2",
                "symbol": "NVDA",
                "side": "buy",
                "status": "filled",
                "qty": "10",
                "filled_qty": "10",
                "filled_avg_price": "100",
                "submitted_at": datetime(2026, 4, 4, 14, 30, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 4, 14, 31, tzinfo=UTC).isoformat(),
                "intent_type": "entry",
            },
            "tp2-2": {
                "id": "tp2-2",
                "symbol": "NVDA",
                "side": "sell",
                "status": "new",
                "qty": "5",
                "filled_qty": "0",
                "submitted_at": datetime(2026, 4, 4, 15, 0, tzinfo=UTC).isoformat(),
                "intent_type": "take_profit_2",
                "parent_order_id": "entry-2",
                "exit_reason": "tp2",
            },
        }
    )

    with pytest.raises(ReconciliationError, match="MISSING_TP1_LEG"):
        reconcile_broker_orders(
            conn,
            client,
            portfolio=portfolio,
            broker_positions=[{"symbol": "NVDA", "qty": "10"}],
            order_ids=["entry-2", "tp2-2"],
        )


def test_inconsistent_ledger_fails_reconciliation() -> None:
    conn = duckdb.connect(":memory:")
    portfolio = Portfolio(initial_capital=2_000.0)

    persist_submitted_orders(
        conn,
        [
            {
                "order_id": "entry-3",
                "symbol": "IWM",
                "side": "buy",
                "qty": 4,
                "intent_type": "entry",
                "status": "accepted",
                "submitted_at": datetime(2026, 4, 5, 14, 30, tzinfo=UTC).isoformat(),
            },
            {
                "order_id": "tp1-3",
                "symbol": "IWM",
                "side": "sell",
                "qty": 1,
                "intent_type": "take_profit_1",
                "parent_order_id": "entry-3",
                "exit_reason": "tp1",
                "status": "accepted",
                "submitted_at": datetime(2026, 4, 5, 15, 0, tzinfo=UTC).isoformat(),
            },
        ],
    )

    client = StubAlpacaClient(
        {
            "entry-3": {
                "id": "entry-3",
                "symbol": "IWM",
                "side": "buy",
                "status": "filled",
                "qty": "4",
                "filled_qty": "4",
                "filled_avg_price": "50",
                "submitted_at": datetime(2026, 4, 5, 14, 30, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 5, 14, 31, tzinfo=UTC).isoformat(),
                "intent_type": "entry",
            },
            "tp1-3": {
                "id": "tp1-3",
                "symbol": "IWM",
                "side": "sell",
                "status": "partially_filled",
                "qty": "1",
                "filled_qty": "1",
                "filled_avg_price": "52",
                "submitted_at": datetime(2026, 4, 5, 15, 0, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 5, 15, 1, tzinfo=UTC).isoformat(),
                "intent_type": "take_profit_1",
                "parent_order_id": "entry-3",
                "exit_reason": "tp1",
            },
        }
    )

    with pytest.raises(PositionInvariantError, match="POSITION_SUM_MISMATCH|EVENT LEDGER STATE DIVERGENCE"):
        reconcile_broker_orders(
            conn,
            client,
            portfolio=portfolio,
            broker_positions=[{"symbol": "IWM", "qty": "99"}],
            order_ids=["entry-3", "tp1-3"],
        )


def test_reconciliation_rebuild_on_valid_persisted_history_does_not_regress() -> None:
    conn = duckdb.connect(":memory:")
    portfolio = Portfolio(initial_capital=1_000.0)

    persist_submitted_orders(
        conn,
        [
            {
                "order_id": "entry-10",
                "symbol": "AMD",
                "side": "buy",
                "qty": 3,
                "intent_type": "entry",
                "status": "accepted",
                "submitted_at": datetime(2026, 4, 6, 14, 30, tzinfo=UTC).isoformat(),
            },
            {
                "order_id": "tp1-10",
                "symbol": "AMD",
                "side": "sell",
                "qty": 1,
                "intent_type": "take_profit_1",
                "parent_order_id": "entry-10",
                "exit_reason": "tp1",
                "status": "accepted",
                "submitted_at": datetime(2026, 4, 6, 14, 30, tzinfo=UTC).isoformat(),
            },
        ],
    )

    client = StubAlpacaClient(
        {
            "entry-10": {
                "id": "entry-10",
                "symbol": "AMD",
                "side": "buy",
                "status": "filled",
                "qty": "3",
                "filled_qty": "3",
                "filled_avg_price": "100",
                "submitted_at": datetime(2026, 4, 6, 14, 30, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 6, 14, 30, tzinfo=UTC).isoformat(),
                "intent_type": "entry",
            },
            "tp1-10": {
                "id": "tp1-10",
                "symbol": "AMD",
                "side": "sell",
                "status": "filled",
                "qty": "1",
                "filled_qty": "1",
                "filled_avg_price": "105",
                "submitted_at": datetime(2026, 4, 6, 14, 30, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 6, 14, 30, tzinfo=UTC).isoformat(),
                "intent_type": "take_profit_1",
                "parent_order_id": "entry-10",
                "exit_reason": "tp1",
            },
        }
    )

    result = reconcile_broker_orders(
        conn,
        client,
        portfolio=portfolio,
        broker_positions=[{"symbol": "AMD", "qty": "2"}],
        order_ids=["entry-10", "tp1-10"],
    )

    assert result.status is ReconciliationStatus.SUCCESS

    digest = ledger_ordering_digest(conn)
    assert [(item.event_time, item.sequence_id, item.order_id, item.event_type.value) for item in digest] == [
        (datetime(2026, 4, 6, 14, 30, tzinfo=UTC), 1, "entry-10", "ORDER_SUBMITTED"),
        (datetime(2026, 4, 6, 14, 30, tzinfo=UTC), 2, "tp1-10", "ORDER_SUBMITTED"),
        (datetime(2026, 4, 6, 14, 30, tzinfo=UTC), 3, "entry-10", "ORDER_FILLED"),
        (datetime(2026, 4, 6, 14, 30, tzinfo=UTC), 4, "tp1-10", "TAKE_PROFIT_1_FILLED"),
    ]

    rebuilt = rebuild_position_state_from_events(conn)
    assert rebuilt["AMD"].position_qty == 2.0
    assert rebuilt["AMD"].last_sequence_id == 4