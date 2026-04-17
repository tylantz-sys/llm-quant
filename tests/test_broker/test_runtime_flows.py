from datetime import UTC, datetime

import duckdb

from llm_quant.broker.event_ledger import BrokerEventType, rebuild_position_state_from_events
from llm_quant.broker.intraday_orders import IntradayOrderState, place_oco_exits_for_buys, reconcile_orders
from llm_quant.broker.reconciliation import persist_submitted_orders, reconcile_broker_orders
from llm_quant.broker.state_machine import BrokerLifecycleState
from llm_quant.trading.executor import ExecutedTrade
from llm_quant.trading.portfolio import Portfolio


class StubReconcileClient:
    def __init__(self, orders: dict[str, dict[str, object]]) -> None:
        self._orders = orders

    def get_order(self, order_id: str, nested: bool = True) -> dict[str, object]:
        return self._orders[order_id]


class FlowOCOClient:
    def __init__(self) -> None:
        self.limit_orders: list[dict[str, object]] = []
        self.oco_orders: list[dict[str, object]] = []
        self.stop_orders: list[dict[str, object]] = []
        self.cancelled: list[str] = []
        self.orders: dict[str, dict[str, object]] = {}

    def submit_limit_order(self, symbol, qty, side, limit_price, **kwargs):
        order = {
            "id": "tp1-order",
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "limit_price": limit_price,
            "status": "new",
            "filled_qty": 0,
        }
        self.limit_orders.append(order)
        self.orders["tp1-order"] = order
        return {"id": "tp1-order"}

    def submit_oco_order(self, symbol, qty, side, take_profit, stop_loss, **kwargs):
        parent = {
            "id": "oco-parent",
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "take_profit": take_profit,
            "stop_loss": stop_loss,
            "status": "new",
            "legs": [
                {"id": "oco-tp", "type": "limit", "status": "new"},
                {"id": "oco-stop", "type": "stop", "status": "new"},
            ],
        }
        self.oco_orders.append(parent)
        self.orders["oco-parent"] = parent
        self.orders["oco-tp"] = {"id": "oco-tp", "status": "new", "filled_qty": 0}
        self.orders["oco-stop"] = {"id": "oco-stop", "status": "new", "filled_qty": 0}
        return parent

    def get_order(self, order_id, nested=False):
        order = self.orders[order_id]
        if order_id == "oco-parent" and nested:
            return order
        return order

    def list_orders(self, status="open", nested=False):
        return [self.orders["oco-parent"]]

    def submit_stop_order(self, symbol, qty, side, stop_price, **kwargs):
        order = {
            "id": "fallback-stop",
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "stop_price": stop_price,
            "status": "new",
        }
        self.stop_orders.append(order)
        self.orders["fallback-stop"] = order
        return {"id": "fallback-stop"}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        if order_id in self.orders:
            self.orders[order_id]["status"] = "canceled"
        return None


def test_daily_bracket_flow_entry_bracket_fill_and_reconcile() -> None:
    conn = duckdb.connect(":memory:")
    portfolio = Portfolio(initial_capital=10_000.0)

    persist_submitted_orders(
        conn,
        [
            {
                "order_id": "entry-1",
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "intent_type": "entry",
                "status": "accepted",
                "submitted_at": datetime(2026, 4, 7, 14, 30, tzinfo=UTC).isoformat(),
            },
            {
                "order_id": "tp-1",
                "symbol": "AAPL",
                "side": "sell",
                "qty": 10,
                "order_type": "limit",
                "intent_type": "take_profit_2",
                "parent_order_id": "entry-1",
                "exit_reason": "daily_bracket_tp",
                "status": "new",
                "submitted_at": datetime(2026, 4, 7, 14, 31, tzinfo=UTC).isoformat(),
            },
            {
                "order_id": "sl-1",
                "symbol": "AAPL",
                "side": "sell",
                "qty": 10,
                "order_type": "stop",
                "intent_type": "stop_loss",
                "parent_order_id": "entry-1",
                "exit_reason": "daily_bracket_stop",
                "status": "new",
                "submitted_at": datetime(2026, 4, 7, 14, 31, tzinfo=UTC).isoformat(),
            },
        ],
    )

    client = StubReconcileClient(
        {
            "entry-1": {
                "id": "entry-1",
                "symbol": "AAPL",
                "side": "buy",
                "status": "filled",
                "qty": "10",
                "filled_qty": "10",
                "filled_avg_price": "100",
                "submitted_at": datetime(2026, 4, 7, 14, 30, tzinfo=UTC).isoformat(),
                "filled_at": datetime(2026, 4, 7, 14, 31, tzinfo=UTC).isoformat(),
                "intent_type": "entry",
            },
            "tp-1": {
                "id": "tp-1",
                "symbol": "AAPL",
                "side": "sell",
                "status": "new",
                "qty": "10",
                "filled_qty": "0",
                "submitted_at": datetime(2026, 4, 7, 14, 31, tzinfo=UTC).isoformat(),
                "intent_type": "take_profit_2",
                "parent_order_id": "entry-1",
                "exit_reason": "daily_bracket_tp",
            },
            "sl-1": {
                "id": "sl-1",
                "symbol": "AAPL",
                "side": "sell",
                "status": "new",
                "qty": "10",
                "filled_qty": "0",
                "submitted_at": datetime(2026, 4, 7, 14, 31, tzinfo=UTC).isoformat(),
                "intent_type": "stop_loss",
                "parent_order_id": "entry-1",
                "exit_reason": "daily_bracket_stop",
            },
        }
    )

    result = reconcile_broker_orders(
        conn,
        client,
        portfolio=portfolio,
        broker_positions=[{"symbol": "AAPL", "qty": "10"}],
    )

    assert result.applied_fill_count == 1
    assert portfolio.positions["AAPL"].shares == 10
    assert result.lifecycle["AAPL"].state is BrokerLifecycleState.EXIT_PENDING

    rebuilt = rebuild_position_state_from_events(conn)
    assert rebuilt["AAPL"].position_qty == 10.0
    assert rebuilt["AAPL"].entry_order_id == "entry-1"
    assert rebuilt["AAPL"].last_event_type is BrokerEventType.ORDER_FILLED

    submitted_rows = conn.execute(
        """
        SELECT order_id, status, parent_order_id, intent_type
        FROM broker_submitted_orders
        ORDER BY order_id
        """
    ).fetchall()
    assert submitted_rows == [
        ("entry-1", "filled", None, "entry"),
        ("sl-1", "new", "entry-1", "stop_loss"),
        ("tp-1", "new", "entry-1", "take_profit_2"),
    ]


def test_intraday_oco_flow_tp1_then_trailing_then_tp2_close() -> None:
    client = FlowOCOClient()
    states: dict[str, IntradayOrderState] = {}

    place_oco_exits_for_buys(
        client,
        states,
        [
            ExecutedTrade(
                symbol="SPY",
                action="buy",
                shares=10,
                price=100.0,
                notional=1_000.0,
                conviction="high",
                reasoning="entry",
            )
        ],
        stop_losses={"SPY": 95.0},
        partial_tp_pct=0.02,
        partial_tp_size=0.50,
        remainder_tp_mult=2.0,
        default_stop_loss_pct=0.05,
        fail_on_unprotected=True,
    )

    state = states["SPY"]
    assert state.partial_tp_order_id == "tp1-order"
    assert state.oco_stop_order_id == "oco-stop"
    assert state.remaining_qty == 5.0

    client.orders["tp1-order"]["status"] = "filled"
    client.orders["tp1-order"]["filled_qty"] = 5
    client.orders["oco-tp"]["status"] = "new"
    client.orders["oco-stop"]["status"] = "new"

    reconcile_orders(
        client,
        states,
        positions={"SPY": 5.0},
        trailing_pct=0.015,
        partial_tp_pct=0.02,
    )

    assert states["SPY"].trailing_active is True
    assert states["SPY"].tp_status == "filled"
    assert states["SPY"].oco_stop_order_id == "oco-stop"

    client.orders["oco-tp"]["status"] = "filled"
    reconcile_orders(
        client,
        states,
        positions={"SPY": 0.0},
        trailing_pct=0.015,
        partial_tp_pct=0.02,
    )

    assert "SPY" not in states
    assert client.cancelled == ["oco-stop"]


def test_intraday_synthetic_mode_without_oco_uses_canonical_exit_signals() -> None:
    from llm_quant.trading.intraday import IntradayPositionState, generate_profit_taking_signals
    from llm_quant.trading.portfolio import Position

    portfolio = Portfolio(initial_capital=5_000.0)
    portfolio.positions["BTC-USD"] = Position(
        symbol="BTC-USD",
        shares=1.0,
        avg_cost=100.0,
        current_price=100.0,
        stop_loss=95.0,
    )
    states = {
        "BTC-USD": IntradayPositionState(
            symbol="BTC-USD",
            entry_price=100.0,
            peak_price=104.0,
            partial_exit_taken=True,
        )
    }

    signals = generate_profit_taking_signals(
        portfolio,
        prices={"BTC-USD": 102.0},
        states=states,
        now_ts=datetime(2026, 4, 7, 16, 0, tzinfo=UTC),
        partial_tp_pct=0.02,
        partial_tp_size=0.50,
        trailing_stop_pct=0.015,
    )

    assert len(signals) == 1
    assert signals[0].symbol == "BTC-USD"
    assert signals[0].exit_reason == "trailing_stop"
    assert signals[0].action.value.lower() == "close"