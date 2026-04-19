from llm_quant.brain.models import Action, TradeSignal
from llm_quant.broker.executor import (
    BrokerOrderIntent,
    build_entry_order_intents,
    submit_alpaca_orders,
    submit_order_intents,
)
from llm_quant.config import ExecutionConfig, RiskLimits
from llm_quant.trading.executor import ExecutedTrade


class FakeAlpacaClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def submit_market_order(self, **kwargs: object) -> dict[str, object]:
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
            "time_in_force": kwargs.get("time_in_force", "day"),
        }


class _PortfolioStub:
    def __init__(self, cash: float) -> None:
        self.cash = cash


def test_submit_order_intents_returns_tracked_orders_with_ids_and_status() -> None:
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


def test_submit_order_intents_preserves_notional_and_fractional_metadata() -> None:
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


def test_build_entry_order_intents_supports_short_action() -> None:
    signal = TradeSignal(
        symbol="SPY",
        action=Action.SHORT,
        target_weight=0.10,
        stop_loss=101.0,
        conviction="high",
        reasoning="short setup",
    )
    intents = build_entry_order_intents(
        _PortfolioStub(cash=50_000.0),
        [signal],
        prices={"SPY": 100.0},
        account_equity=100_000.0,
        asset_class_map={"SPY": "equity"},
        execution=ExecutionConfig(),
    )

    assert len(intents) == 1
    assert intents[0].side == "sell"
    assert intents[0].intent_type == "entry_short"
    assert intents[0].qty > 0


def test_submit_alpaca_orders_supports_short_and_cover_actions() -> None:
    client = FakeAlpacaClient()
    trades = [
        ExecutedTrade(
            symbol="SPY",
            action="short",
            shares=2,
            price=100.0,
            notional=200.0,
            conviction="high",
            reasoning="short",
        ),
        ExecutedTrade(
            symbol="SPY",
            action="cover",
            shares=1,
            price=95.0,
            notional=95.0,
            conviction="medium",
            reasoning="cover",
        ),
    ]

    submitted = submit_alpaca_orders(
        client,
        trades,
        stop_losses={},
        limits=RiskLimits(),
        use_brackets=False,
    )

    assert [call["side"] for call in client.calls] == ["sell", "buy"]
    assert [order.intent_type for order in submitted] == ["entry_short", "cover"]


def test_submit_alpaca_orders_routes_close_short_as_cover_buy() -> None:
    client = FakeAlpacaClient()
    trades = [
        ExecutedTrade(
            symbol="SPY",
            action="close",
            shares=2,
            price=101.0,
            notional=202.0,
            conviction="high",
            reasoning="flatten short",
            is_short_close=True,
        )
    ]

    submitted = submit_alpaca_orders(
        client,
        trades,
        stop_losses={},
        limits=RiskLimits(),
        use_brackets=False,
    )

    assert [call["side"] for call in client.calls] == ["buy"]
    assert [order.intent_type for order in submitted] == ["cover"]