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


def _short_signal() -> TradeSignal:
    return TradeSignal(
        symbol="SPY",
        action=Action.SHORT,
        conviction=Conviction.MEDIUM,
        target_weight=0.10,
        stop_loss=105.0,
        reasoning="test short",
        take_profit=90.0,
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


def test_execute_signals_supports_short_and_cover() -> None:
    portfolio = Portfolio(initial_capital=1_000.0)

    short_executed = execute_signals(
        portfolio,
        [_short_signal()],
        {"SPY": 100.0},
        portfolio.nav,
        mode=ExecutionMode.PAPER,
    )

    assert len(short_executed) == 1
    assert short_executed[0].action == "short"
    assert portfolio.positions["SPY"].shares == -1
    assert portfolio.cash == 1_100.0

    cover_signal = TradeSignal(
        symbol="SPY",
        action=Action.COVER,
        conviction=Conviction.MEDIUM,
        target_weight=0.0,
        stop_loss=0.0,
        reasoning="test cover",
    )
    cover_executed = execute_signals(
        portfolio,
        [cover_signal],
        {"SPY": 95.0},
        portfolio.nav,
        mode=ExecutionMode.PAPER,
    )

    assert len(cover_executed) == 1
    assert cover_executed[0].action == "cover"
    assert portfolio.cash == 1_005.0
    assert "SPY" not in portfolio.positions


def test_execute_signals_close_short_sets_short_close_marker() -> None:
    portfolio = Portfolio(initial_capital=1_000.0)

    execute_signals(
        portfolio,
        [_short_signal()],
        {"SPY": 100.0},
        portfolio.nav,
        mode=ExecutionMode.PAPER,
    )

    close_signal = TradeSignal(
        symbol="SPY",
        action=Action.CLOSE,
        conviction=Conviction.MEDIUM,
        target_weight=0.0,
        stop_loss=0.0,
        reasoning="flatten",
    )
    closed = execute_signals(
        portfolio,
        [close_signal],
        {"SPY": 102.0},
        portfolio.nav,
        mode=ExecutionMode.PAPER,
    )

    assert len(closed) == 1
    assert closed[0].action == "close"
    assert closed[0].is_short_close is True
