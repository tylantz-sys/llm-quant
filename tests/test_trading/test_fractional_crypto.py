from llm_quant.brain.models import Action, Conviction, TradeSignal
from llm_quant.trading.executor import execute_signals
from llm_quant.trading.portfolio import Portfolio


def test_execute_signals_allows_fractional_crypto():
    portfolio = Portfolio(initial_capital=1000.0)
    prices = {"BTC-USD": 100.0}
    signals = [
        TradeSignal(
            symbol="BTC-USD",
            action=Action.BUY,
            conviction=Conviction.MEDIUM,
            target_weight=0.05,
            stop_loss=90.0,
            reasoning="crypto",
        )
    ]
    asset_class_map = {"BTC-USD": "crypto"}
    executed = execute_signals(
        portfolio,
        signals,
        prices,
        portfolio.nav,
        asset_class_map=asset_class_map,
    )
    assert executed
    assert executed[0].shares == 0.5


def test_execute_signals_keeps_equity_integer():
    portfolio = Portfolio(initial_capital=1000.0)
    prices = {"SPY": 100.0}
    signals = [
        TradeSignal(
            symbol="SPY",
            action=Action.BUY,
            conviction=Conviction.MEDIUM,
            target_weight=0.05,
            stop_loss=90.0,
            reasoning="equity",
        )
    ]
    asset_class_map = {"SPY": "equity"}
    executed = execute_signals(
        portfolio,
        signals,
        prices,
        portfolio.nav,
        asset_class_map=asset_class_map,
    )
    assert executed == []
