from llm_quant.brain.models import Action, Conviction, TradeSignal
from llm_quant.strategies.runtime import (
    aggregate_strategy_signals,
    apply_group_caps,
    apply_regime_multipliers,
    merge_strategy_signals,
)


def test_aggregate_caps_weight():
    signals = [
        TradeSignal(
            symbol="SPY",
            action=Action.BUY,
            conviction=Conviction.MEDIUM,
            target_weight=0.08,
            stop_loss=90.0,
            reasoning="s1",
            strategy_id="s1",
        ),
        TradeSignal(
            symbol="SPY",
            action=Action.BUY,
            conviction=Conviction.MEDIUM,
            target_weight=0.07,
            stop_loss=89.0,
            reasoning="s2",
            strategy_id="s2",
        ),
    ]

    merged = aggregate_strategy_signals(signals, max_position_weight=0.10)
    assert len(merged) == 1
    assert merged[0].action == Action.BUY
    assert merged[0].target_weight == 0.10


def test_aggregate_close_priority():
    signals = [
        TradeSignal(
            symbol="SPY",
            action=Action.BUY,
            conviction=Conviction.MEDIUM,
            target_weight=0.05,
            stop_loss=90.0,
            reasoning="buy",
            strategy_id="s1",
        ),
        TradeSignal(
            symbol="SPY",
            action=Action.CLOSE,
            conviction=Conviction.HIGH,
            target_weight=0.0,
            stop_loss=0.0,
            reasoning="close",
            strategy_id="s2",
        ),
    ]

    merged = aggregate_strategy_signals(signals, max_position_weight=0.10)
    assert len(merged) == 1
    assert merged[0].action == Action.CLOSE
    assert merged[0].target_weight == 0.0


def test_proportional_scaling_preserves_ratios():
    signals = [
        TradeSignal(
            symbol="SPY",
            action=Action.BUY,
            conviction=Conviction.MEDIUM,
            target_weight=0.20,
            stop_loss=90.0,
            reasoning="s1",
            strategy_id="s1",
        ),
        TradeSignal(
            symbol="QQQ",
            action=Action.BUY,
            conviction=Conviction.MEDIUM,
            target_weight=0.10,
            stop_loss=90.0,
            reasoning="s2",
            strategy_id="s2",
        ),
    ]

    merged = aggregate_strategy_signals(signals, max_position_weight=0.10)
    spy = next(s for s in merged if s.symbol == "SPY")
    qqq = next(s for s in merged if s.symbol == "QQQ")
    assert spy.target_weight == 0.10
    assert qqq.target_weight == 0.05


def test_regime_multipliers_and_group_caps():
    signals = [
        TradeSignal(
            symbol="SPY",
            action=Action.BUY,
            conviction=Conviction.MEDIUM,
            target_weight=0.30,
            stop_loss=90.0,
            reasoning="s1",
            strategy_id="s1",
            metadata={"strategy_group": "credit_lead_lag"},
        ),
        TradeSignal(
            symbol="QQQ",
            action=Action.BUY,
            conviction=Conviction.MEDIUM,
            target_weight=0.20,
            stop_loss=90.0,
            reasoning="s2",
            strategy_id="s2",
            metadata={"strategy_group": "credit_lead_lag"},
        ),
    ]

    signals = apply_regime_multipliers(
        signals,
        {"credit_lead_lag": {"risk_off": 0.5}},
        "risk_off",
    )
    merged = merge_strategy_signals(signals)
    capped = apply_group_caps(merged, {"credit_lead_lag": 0.20})

    spy = next(s for s in capped if s.symbol == "SPY")
    qqq = next(s for s in capped if s.symbol == "QQQ")
    assert spy.target_weight == 0.12
    assert qqq.target_weight == 0.08
