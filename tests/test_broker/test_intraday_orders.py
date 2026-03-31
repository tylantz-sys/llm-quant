from llm_quant.broker.intraday_orders import (
    IntradayOrderState,
    place_oco_exits_for_buys,
    reconcile_orders,
)
from llm_quant.trading.executor import ExecutedTrade


class FakeClient:
    def __init__(self) -> None:
        self.limit_orders = []
        self.oco_orders = []

    def submit_limit_order(self, symbol, qty, side, limit_price):
        self.limit_orders.append(
            {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "limit_price": limit_price,
            }
        )
        return {"id": "tp1"}

    def submit_oco_order(self, symbol, qty, side, take_profit, stop_loss):
        self.oco_orders.append(
            {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "take_profit": take_profit,
                "stop_loss": stop_loss,
            }
        )
        return {
            "id": "oco1",
            "legs": [
                {"id": "oco_tp", "type": "limit"},
                {"id": "oco_stop", "type": "stop"},
            ],
        }

    def get_order(self, order_id, nested=False):
        return {
            "id": order_id,
            "legs": [
                {"id": "oco_tp", "type": "limit"},
                {"id": "oco_stop", "type": "stop"},
            ],
        }

    def list_orders(self, status="open", nested=False):
        return []


class MissingLegClient:
    def __init__(self) -> None:
        self.stop_orders = []

    def get_order(self, order_id, nested=False):
        return {"id": order_id, "legs": []}

    def list_orders(self, status="open", nested=False):
        return []

    def submit_stop_order(self, symbol, qty, side, stop_price):
        self.stop_orders.append(
            {"symbol": symbol, "qty": qty, "side": side, "stop_price": stop_price}
        )
        return {"id": "stop1"}

    def cancel_order(self, order_id):
        return None


def test_place_oco_exits_for_buys_creates_partial_and_oco():
    client = FakeClient()
    states = {}
    trades = [
        ExecutedTrade(
            symbol="SPY",
            action="buy",
            shares=10,
            price=100.0,
            notional=1000.0,
            conviction="medium",
            reasoning="test",
        )
    ]

    place_oco_exits_for_buys(
        client,
        states,
        trades,
        stop_losses={"SPY": 95.0},
        partial_tp_pct=0.02,
        partial_tp_size=0.50,
        remainder_tp_mult=2.0,
        default_stop_loss_pct=0.05,
    )

    assert client.limit_orders
    assert client.oco_orders

    state = states["SPY"]
    assert state.partial_tp_order_id == "tp1"
    assert state.oco_order_id == "oco1"
    assert state.oco_tp_order_id == "oco_tp"
    assert state.oco_stop_order_id == "oco_stop"

    assert client.limit_orders[0]["qty"] == 5
    assert client.oco_orders[0]["qty"] == 5
    assert client.oco_orders[0]["take_profit"] > client.limit_orders[0]["limit_price"]
    assert client.oco_orders[0]["take_profit"] == 104.0


def test_reconcile_orders_fallbacks_when_oco_legs_missing():
    client = MissingLegClient()
    states = {
        "SPY": IntradayOrderState(
            symbol="SPY",
            oco_order_id="oco1",
            hwm=100.0,
            remaining_qty=5.0,
        )
    }

    for _ in range(3):
        reconcile_orders(
            client,
            states,
            positions={"SPY": 5.0},
            trailing_pct=0.01,
        )

    state = states["SPY"]
    assert state.oco_order_id is None
    assert state.oco_stop_order_id == "stop1"
    assert client.stop_orders
