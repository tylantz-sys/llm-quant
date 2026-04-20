from datetime import UTC, datetime, timedelta

from llm_quant.brain.models import Action, Conviction, TradeSignal
from llm_quant.trading.intraday import (
    IntradayPositionState,
    apply_reentry_cooldown,
    apply_scale_in,
    generate_profit_taking_signals,
    merge_intraday_signals,
    update_state_from_trades,
)
from llm_quant.trading.portfolio import Portfolio, Position


def _portfolio_with_position(symbol: str, shares: float, price: float) -> Portfolio:
    portfolio = Portfolio(initial_capital=100_000.0)
    portfolio.positions[symbol] = Position(
        symbol=symbol,
        shares=shares,
        avg_cost=price,
        current_price=price,
        stop_loss=price * 0.95,
    )
    return portfolio


def test_partial_take_profit_signal():
    portfolio = _portfolio_with_position("SPY", 10, 100.0)
    states = {"SPY": IntradayPositionState(symbol="SPY", entry_price=100.0)}
    prices = {"SPY": 102.0}
    now = datetime.now(tz=UTC)

    signals = generate_profit_taking_signals(
        portfolio,
        prices,
        states,
        now,
        partial_tp_pct=0.02,
        partial_tp_size=0.50,
        trailing_stop_pct=0.015,
    )

    assert len(signals) == 1
    sig = signals[0]
    assert sig.action == Action.SELL
    assert sig.exit_reason == "tp_partial"
    assert sig.target_weight > 0


def test_trailing_stop_after_partial():
    portfolio = _portfolio_with_position("SPY", 10, 100.0)
    states = {
        "SPY": IntradayPositionState(
            symbol="SPY",
            entry_price=100.0,
            peak_price=105.0,
            partial_exit_taken=True,
        )
    }
    prices = {"SPY": 103.0}
    now = datetime.now(tz=UTC)

    signals = generate_profit_taking_signals(
        portfolio,
        prices,
        states,
        now,
        partial_tp_pct=0.02,
        partial_tp_size=0.50,
        trailing_stop_pct=0.015,
    )

    assert len(signals) == 1
    sig = signals[0]
    assert sig.action == Action.CLOSE
    assert sig.exit_reason == "trailing_stop"


def test_scale_in_adjustment():
    signals = [
        TradeSignal(
            symbol="SPY",
            action=Action.BUY,
            conviction=Conviction.MEDIUM,
            target_weight=0.6,
            stop_loss=95.0,
            reasoning="test",
        )
    ]
    portfolio = Portfolio(initial_capital=100_000.0)
    states = {"SPY": IntradayPositionState(symbol="SPY", entry_batch=1)}

    adjusted = apply_scale_in(signals, portfolio, states, scale_in_tranches=3)
    assert len(adjusted) == 1
    assert adjusted[0].target_weight == round(0.6 * (2 / 3), 4)


def test_reentry_cooldown_blocks_buy():
    signals = [
        TradeSignal(
            symbol="SPY",
            action=Action.BUY,
            conviction=Conviction.MEDIUM,
            target_weight=0.2,
            stop_loss=95.0,
            reasoning="test",
        )
    ]
    now = datetime.now(tz=UTC)
    states = {
        "SPY": IntradayPositionState(
            symbol="SPY",
            last_exit_ts=now - timedelta(minutes=2),
        )
    }

    filtered = apply_reentry_cooldown(
        signals,
        states,
        now,
        timeframe_minutes=5,
        cooldown_bars=1,
    )
    assert filtered == []


def test_merge_intraday_signals_prioritizes_profit_exits():
    entry = [
        TradeSignal(
            symbol="SPY",
            action=Action.BUY,
            conviction=Conviction.MEDIUM,
            target_weight=0.2,
            stop_loss=95.0,
            reasoning="entry",
        )
    ]
    other = []
    profit = [
        TradeSignal(
            symbol="SPY",
            action=Action.SELL,
            conviction=Conviction.HIGH,
            target_weight=0.1,
            stop_loss=95.0,
            reasoning="tp",
            exit_reason="tp_partial",
        )
    ]

    merged = merge_intraday_signals(entry, other, profit)
    assert len(merged) == 1
    assert merged[0].action == Action.SELL


def test_merge_intraday_signals_prioritizes_cover_profit_exits() -> None:
    entry = [
        TradeSignal(
            symbol="SPY",
            action=Action.SHORT,
            conviction=Conviction.MEDIUM,
            target_weight=0.2,
            stop_loss=105.0,
            reasoning="entry",
        )
    ]
    profit = [
        TradeSignal(
            symbol="SPY",
            action=Action.COVER,
            conviction=Conviction.HIGH,
            target_weight=0.1,
            stop_loss=0.0,
            reasoning="tp",
            exit_reason="tp_partial",
        )
    ]

    merged = merge_intraday_signals(entry, [], profit)
    assert len(merged) == 1
    assert merged[0].action == Action.COVER


def test_update_state_from_trades_short_entry_sets_short_price_anchor():
    now = datetime.now(tz=UTC)
    states: dict[str, IntradayPositionState] = {}

    class _Trade:
        symbol = "SPY"
        action = "short"
        entry_batch = 1
        price = 101.5
        exit_reason = ""

    update_state_from_trades(states, [_Trade()], now)

    state = states["SPY"]
    assert state.entry_price == 101.5
    assert state.peak_price == 101.5
    assert state.partial_exit_taken is False


def test_update_state_from_trades_cover_partial_sets_partial_exit_flag():
    now = datetime.now(tz=UTC)
    states = {
        "SPY": IntradayPositionState(
            symbol="SPY",
            entry_batch=1,
            entry_price=100.0,
            peak_price=98.0,
            partial_exit_taken=False,
        )
    }

    class _Trade:
        symbol = "SPY"
        action = "cover"
        entry_batch = 1
        price = 98.0
        exit_reason = "tp_partial"

    update_state_from_trades(states, [_Trade()], now)

    state = states["SPY"]
    assert state.partial_exit_taken is True
    assert state.last_exit_ts == now
