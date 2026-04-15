from llm_quant.broker.executor import BrokerOrderIntent, submit_order_intents
from llm_quant.config import ExecutionConfig


class FakeAlpacaClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def submit_market_order(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "id": "ord_123",
            "symbol": kwargs["symbol"],
            "side": kwargs["side"],
            "qty": kwargs["qty"],
            "type": "market",
            "status": "accepted",
            "filled_qty": "0",
            "filled_avg_price": None,
            "time_in_force": kwargs["time_in_force"],
        }


def test_submit_order_intents_returns_tracked_orders_with_ids_and_status():
    client = FakeAlpacaClient()
    intents = [
        BrokerOrderIntent(
            symbol="SPY",
            side="buy",
            qty=10.0,
            order_type="market",
            intent_type="entry",
        )
    ]

    submitted = submit_order_intents(client, intents, ExecutionConfig())

    assert len(submitted) == 1
    order = submitted[0]
    assert order.order_id == "ord_123"
    assert order.symbol == "SPY"
    assert order.side == "buy"
    assert order.qty == 10.0
    assert order.order_type == "market"
    assert order.intent_type == "entry"
    assert order.status == "accepted"
    assert order.filled_qty == 0.0
    assert order.filled_avg_price == 0.0
    assert order.time_in_force == "day"
    assert order.broker_raw is not None


def test_submit_order_intents_preserves_notional_and_fractional_metadata():
    client = FakeAlpacaClient()
    intents = [
        BrokerOrderIntent(
            symbol="BTC/USD",
            side="buy",
            qty=0.125,
            order_type="market",
            intent_type="entry",
            notional=5000.0,
            time_in_force="gtc",
            allow_fractional=True,
            asset_class="crypto",
        )
    ]

    submitted = submit_order_intents(client, intents, ExecutionConfig())

    assert client.calls[0]["allow_fractional"] is True
    assert client.calls[0]["notional"] == 5000.0

    order = submitted[0]
    assert order.order_id == "ord_123"
    assert order.qty == 0.125
    assert order.notional == 5000.0
    assert order.time_in_force == "gtc"
    assert order.allow_fractional is True
    assert order.asset_class == "crypto"