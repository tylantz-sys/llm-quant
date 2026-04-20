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

    def submit_bracket_order(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {
            "id": "bracket_123",
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


def test_submit_alpaca_orders_short_uses_bracket_with_entry_short_intent() -> None:
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
        )
    ]

    submitted = submit_alpaca_orders(
        client,
        trades,
        stop_losses={"SPY": 102.0},
        limits=RiskLimits(),
        use_brackets=True,
    )

    assert [call["side"] for call in client.calls] == ["sell"]
    assert client.calls[0]["take_profit"] == 97.0
    assert client.calls[0]["stop_loss"] == 102.0
    assert [order.intent_type for order in submitted] == ["entry_short"]
    assert submitted[0].order_id == "bracket_123"


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


# ---------------------------------------------------------------------------
# Short bracket direction — regression guard for the "mirror hack" pattern
# ---------------------------------------------------------------------------

def test_short_bracket_take_profit_is_below_entry_price() -> None:
    """For short entries, the bracket TP must be strictly below entry price.

    Regression guard: the old executor contained a "mirror hack" that computed
    TP by reflecting the plan's TP distance above entry (i.e. producing a TP
    *above* entry for shorts).  This test fails if that pattern is re-introduced.
    """
    client = FakeAlpacaClient()
    entry_price = 200.0
    stop_price = 205.0  # above entry — correct for short

    trades = [
        ExecutedTrade(
            symbol="QQQ",
            action="short",
            shares=3,
            price=entry_price,
            notional=entry_price * 3,
            conviction="high",
            reasoning="short setup",
        )
    ]

    submitted = submit_alpaca_orders(
        client,
        trades,
        stop_losses={"QQQ": stop_price},
        limits=RiskLimits(take_profit_mode="pct", take_profit_pct=0.03),
        use_brackets=True,
    )

    assert len(submitted) == 1
    assert submitted[0].intent_type == "entry_short"
    submitted_tp = float(client.calls[0]["take_profit"])
    # TP must be below entry for a short position.
    # The mirrored (wrong) value would be above entry: entry + distance_above_entry.
    assert submitted_tp < entry_price, (
        f"Short bracket take_profit {submitted_tp} should be below entry {entry_price}; "
        "check for mirror-hack regression in executor."
    )
    # Also verify the stop is above entry (a basic sanity check)
    assert float(client.calls[0]["stop_loss"]) > entry_price
