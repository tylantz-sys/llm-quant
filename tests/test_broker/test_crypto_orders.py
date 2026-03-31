from llm_quant.broker.executor import submit_alpaca_orders
from llm_quant.config import ExecutionConfig, RiskLimits
from llm_quant.trading.executor import ExecutedTrade


class _FakeClient:
    def __init__(self) -> None:
        self.calls = []

    def submit_market_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        time_in_force: str = "day",
        notional: float | None = None,
        allow_fractional: bool = False,
    ) -> None:
        self.calls.append(
            {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "time_in_force": time_in_force,
                "notional": notional,
                "allow_fractional": allow_fractional,
            }
        )


def test_crypto_market_orders_use_fractional_qty_and_gtc():
    client = _FakeClient()
    trade = ExecutedTrade(
        symbol="BTC-USD",
        action="buy",
        shares=0.25,
        price=100.0,
        notional=25.0,
        conviction="medium",
        reasoning="test",
    )
    submit_alpaca_orders(
        client,
        [trade],
        stop_losses={},
        limits=RiskLimits(),
        use_brackets=False,
        asset_class_map={"BTC-USD": "crypto"},
        execution=ExecutionConfig(crypto_time_in_force="gtc", crypto_order_sizing="qty"),
    )
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["symbol"] == "BTC/USD"
    assert call["allow_fractional"] is True
    assert call["time_in_force"] == "gtc"
