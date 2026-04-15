import pytest

from llm_quant.brain.models import Action, Conviction, TradeSignal
from llm_quant.trading.executor import (
    ExecutionMode,
    RuntimeExecutionNotAllowedError,
    ensure_runtime_execution_allowed,
    execute_signals,
)
from llm_quant.trading.portfolio import Portfolio


def _buy_signal() -> TradeSignal:
    return TradeSignal(
        symbol="SPY",
        action=Action.BUY,
        conviction=Conviction.MEDIUM,
        target_weight=0.10,
        stop_loss=95.0,
        reasoning="test",
    )


def test_ensure_runtime_execution_allowed_rejects_alpaca_mode() -> None:
    with pytest.raises(RuntimeExecutionNotAllowedError, match="forbidden in alpaca mode"):
        ensure_runtime_execution_allowed(ExecutionMode.ALPACA)


def test_execute_signals_rejects_alpaca_mode_without_mutating_portfolio() -> None:
    portfolio = Portfolio(initial_capital=1000.0)
    signal = _buy_signal()

    with pytest.raises(RuntimeExecutionNotAllowedError, match="forbidden in alpaca mode"):
        execute_signals(
            portfolio,
            [signal],
            {"SPY": 100.0},
            portfolio.nav,
            mode="alpaca",
        )

    assert portfolio.cash == 1000.0
    assert portfolio.positions == {}


def test_execute_signals_still_allows_paper_mode() -> None:
    portfolio = Portfolio(initial_capital=1000.0)
    signal = _buy_signal()

    executed = execute_signals(
        portfolio,
        [signal],
        {"SPY": 100.0},
        portfolio.nav,
        mode=ExecutionMode.PAPER,
    )

    assert len(executed) == 1
    assert executed[0].symbol == "SPY"
    assert "SPY" in portfolio.positions
