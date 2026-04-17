import pytest

from llm_quant.broker.alpaca import AlpacaError
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

    def submit_limit_order(self, symbol, qty, side, limit_price, **kwargs):
        self.limit_orders.append(
            {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "limit_price": limit_price,
            }
        )
        return {"id": "tp1"}

    def submit_oco_order(self, symbol, qty, side, take_profit, stop_loss, **kwargs):
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

    def submit_stop_order(self, symbol, qty, side, stop_price, **kwargs):
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


class FractionalClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.stop_limit_orders: list[dict[str, object]] = []
        self.cancelled: list[str] = []

    def submit_limit_order(self, symbol, qty, side, limit_price, **kwargs):
        order = super().submit_limit_order(symbol, qty, side, limit_price)
        return order

    def submit_oco_order(self, symbol, qty, side, take_profit, stop_loss, **kwargs):
        order = super().submit_oco_order(symbol, qty, side, take_profit, stop_loss)
        return order

    def submit_stop_limit_order(self, symbol, qty, side, stop_price, limit_price, **kwargs):
        self.stop_limit_orders.append(
            {"symbol": symbol, "qty": qty, "side": side, "stop_price": stop_price, "limit_price": limit_price}
        )
        return {"id": f"sl-{len(self.stop_limit_orders)}"}

    def get_order(self, order_id, nested=False):
        return {"id": order_id, "status": "new", "filled_qty": 0}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return None


class BrokenProtectionClient:
    def get_order(self, order_id, nested=False):
        return {"id": order_id, "status": "filled" if order_id == "oco_tp" else "new"}

    def cancel_order(self, order_id):
        return None

    def submit_stop_order(self, symbol, qty, side, stop_price, **kwargs):
        raise AlpacaError("stop submit failed")

    def submit_stop_limit_order(self, symbol, qty, side, stop_price, limit_price, **kwargs):
        raise AlpacaError("stop submit failed")


class OCOCancelClient:
    def __init__(self, orders: dict[str, dict[str, object]]) -> None:
        self.orders = orders
        self.cancelled: list[str] = []

    def get_order(self, order_id, nested=False):
        return self.orders[order_id]

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        if order_id in self.orders:
            self.orders[order_id]["status"] = "canceled"
        return None


def test_place_oco_exits_for_buys_preserves_fractional_crypto_qty():
    """Crypto: TP1 for 50%, full-size stop for 100%. TP2 deferred until TP1 fills."""
    client = FractionalClient()
    states = {}
    trades = [
        ExecutedTrade(
            symbol="BTC-USD",
            action="buy",
            shares=0.75,
            price=40000.0,
            notional=30000.0,
            conviction="medium",
            reasoning="test",
        )
    ]

    place_oco_exits_for_buys(
        client,
        states,
        trades,
        stop_losses={"BTC-USD": 38000.0},
        partial_tp_pct=0.02,
        partial_tp_size=0.50,
        remainder_tp_mult=2.0,
        default_stop_loss_pct=0.05,
        fail_on_unprotected=True,
        asset_class_map={"BTC-USD": "crypto"},
    )

    # TP1 limit for 50% of position
    assert len(client.limit_orders) == 1
    assert client.limit_orders[0]["qty"] == pytest.approx(0.375)
    # Full-size stop for 100% of position (NOT 50%)
    assert len(client.stop_limit_orders) == 1
    assert client.stop_limit_orders[0]["qty"] == pytest.approx(0.75)
    # No OCO bracket orders
    assert client.oco_orders == []
    # State: remaining_qty tracks full position (stop covers all)
    assert states["BTC-USD"].remaining_qty == pytest.approx(0.75)
    assert states["BTC-USD"].oco_tp_order_id is None   # TP2 not placed yet
    assert states["BTC-USD"].oco_stop_order_id is not None


def test_reconcile_crypto_tp1_fill_splits_stop_and_adds_tp2():
    """After crypto TP1 fills: cancel full-size stop, submit remainder stop + TP2."""

    class CryptoReconcileClient(FractionalClient):
        def get_order(self, order_id, nested=False):
            if order_id == "tp1":
                return {"id": "tp1", "status": "filled", "filled_qty": 1.188688}
            return {"id": order_id, "status": "new", "filled_qty": 0}

    client = CryptoReconcileClient()
    states = {
        "ETH-USD": IntradayOrderState(
            symbol="ETH-USD",
            partial_tp_order_id="tp1",
            oco_tp_order_id=None,       # TP2 not placed yet
            oco_stop_order_id="full-stop",
            hwm=2500.0,
            remaining_qty=1.188688,     # position remaining after TP1 filled
            initial_stop_price=2125.0,
            stop_status="new",
            protection_qty=2.377376,    # full-size stop was for 100%
        )
    }

    reconcile_orders(
        client,
        states,
        positions={"ETH-USD": 1.188688},
        trailing_pct=0.0,
        partial_tp_pct=0.015,
        remainder_tp_mult=2.0,
        asset_class_map={"ETH-USD": "crypto"},
    )

    # Full-size stop should have been cancelled
    assert "full-stop" in client.cancelled
    # New remainder-sized stop submitted
    assert len(client.stop_limit_orders) == 1
    assert client.stop_limit_orders[0]["qty"] == pytest.approx(1.188688)
    # TP2 limit submitted for remainder
    assert len(client.limit_orders) == 1
    assert client.limit_orders[0]["qty"] == pytest.approx(1.188688)
    # State updated
    assert states["ETH-USD"].oco_tp_order_id is not None
    assert states["ETH-USD"].oco_stop_order_id is not None
    assert states["ETH-USD"].protection_qty == pytest.approx(1.188688)


def test_reconcile_orders_fail_closed_when_fractional_crypto_stop_cannot_be_restored():
    client = BrokenProtectionClient()
    states = {
        "BTC-USD": IntradayOrderState(
            symbol="BTC-USD",
            oco_order_id="oco1",
            oco_tp_order_id="oco_tp",
            oco_stop_order_id="oco_stop",
            hwm=42000.0,
            remaining_qty=0.375,
        )
    }

    with pytest.raises(
        AlpacaError,
        match="stop submit failed",
    ):
        reconcile_orders(
            client,
            states,
            positions={"BTC-USD": 0.375},
            trailing_pct=0.01,
            fail_on_unprotected=True,
        )


def test_reconcile_orders_duplicate_cancels_are_idempotent_for_terminal_orders():
    client = OCOCancelClient(
        {
            "tp1": {"id": "tp1", "status": "canceled", "filled_qty": 0},
            "oco_tp": {"id": "oco_tp", "status": "canceled", "filled_qty": 0},
            "oco_stop": {"id": "oco_stop", "status": "filled", "filled_qty": 5},
        }
    )
    states = {
        "SPY": IntradayOrderState(
            symbol="SPY",
            partial_tp_order_id="tp1",
            oco_tp_order_id="oco_tp",
            oco_stop_order_id="oco_stop",
            stop_status="filled",
            tp_status="canceled",
            oco_tp_status="canceled",
            hwm=100.0,
            remaining_qty=5.0,
        )
    }

    reconcile_orders(
        client,
        states,
        positions={"SPY": 5.0},
        trailing_pct=0.01,
    )

    assert client.cancelled == []
    assert "SPY" not in states


def test_reconcile_orders_same_bar_dual_trigger_uses_stop_precedence():
    client = OCOCancelClient(
        {
            "tp1": {"id": "tp1", "status": "filled", "filled_qty": 5},
            "oco_tp": {"id": "oco_tp", "status": "filled", "filled_qty": 5},
            "oco_stop": {"id": "oco_stop", "status": "filled", "filled_qty": 5},
        }
    )
    states = {
        "SPY": IntradayOrderState(
            symbol="SPY",
            partial_tp_order_id="tp1",
            oco_tp_order_id="oco_tp",
            oco_stop_order_id="oco_stop",
            hwm=100.0,
            remaining_qty=5.0,
        )
    }

    reconcile_orders(
        client,
        states,
        positions={"SPY": 5.0},
        trailing_pct=0.01,
    )

    assert client.cancelled == []
    assert "SPY" not in states


def test_reconcile_orders_cancel_after_fill_is_ignored_safely():
    client = OCOCancelClient(
        {
            "tp1": {"id": "tp1", "status": "new", "filled_qty": 0},
            "oco_tp": {"id": "oco_tp", "status": "filled", "filled_qty": 5},
            "oco_stop": {"id": "oco_stop", "status": "filled", "filled_qty": 5},
        }
    )
    states = {
        "SPY": IntradayOrderState(
            symbol="SPY",
            partial_tp_order_id="tp1",
            oco_tp_order_id="oco_tp",
            oco_stop_order_id="oco_stop",
            hwm=100.0,
            remaining_qty=5.0,
        )
    }

    reconcile_orders(
        client,
        states,
        positions={"SPY": 5.0},
        trailing_pct=0.01,
    )

    assert client.cancelled == ["tp1"]
    assert "SPY" not in states
