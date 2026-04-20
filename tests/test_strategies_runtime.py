from llm_quant.brain.models import Action, Conviction, TradeSignal
from llm_quant.strategies.runtime import (
    StrategySpec,
    aggregate_strategy_signals,
    apply_group_caps,
    apply_regime_multipliers,
    merge_strategy_signals,
    required_symbols,
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


def test_merge_strategy_signals_preserves_short() -> None:
    signals = [
        TradeSignal(
            symbol="SPY",
            action=Action.SHORT,
            conviction=Conviction.MEDIUM,
            target_weight=0.04,
            stop_loss=105.0,
            reasoning="s1",
            strategy_id="s1",
        ),
        TradeSignal(
            symbol="SPY",
            action=Action.SHORT,
            conviction=Conviction.HIGH,
            target_weight=0.03,
            stop_loss=104.0,
            reasoning="s2",
            strategy_id="s2",
        ),
    ]

    merged = merge_strategy_signals(signals)
    assert len(merged) == 1
    assert merged[0].action == Action.SHORT
    assert merged[0].target_weight == 0.07


def test_merge_strategy_signals_preserves_cover() -> None:
    signals = [
        TradeSignal(
            symbol="SPY",
            action=Action.COVER,
            conviction=Conviction.MEDIUM,
            target_weight=0.04,
            stop_loss=0.0,
            reasoning="s1",
            strategy_id="s1",
        ),
        TradeSignal(
            symbol="SPY",
            action=Action.COVER,
            conviction=Conviction.HIGH,
            target_weight=0.01,
            stop_loss=0.0,
            reasoning="s2",
            strategy_id="s2",
        ),
    ]

    merged = merge_strategy_signals(signals)
    assert len(merged) == 1
    assert merged[0].action == Action.COVER
    assert merged[0].target_weight == 0.01


def test_merge_strategy_signals_conflicting_long_short_collapses_to_close() -> None:
    signals = [
        TradeSignal(
            symbol="SPY",
            action=Action.BUY,
            conviction=Conviction.MEDIUM,
            target_weight=0.05,
            stop_loss=95.0,
            reasoning="buy",
            strategy_id="s1",
        ),
        TradeSignal(
            symbol="SPY",
            action=Action.SHORT,
            conviction=Conviction.HIGH,
            target_weight=0.04,
            stop_loss=105.0,
            reasoning="short",
            strategy_id="s2",
        ),
    ]

    merged = merge_strategy_signals(signals)
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


def test_regime_multipliers_and_group_caps_apply_to_shorts() -> None:
    signals = [
        TradeSignal(
            symbol="SPY",
            action=Action.SHORT,
            conviction=Conviction.MEDIUM,
            target_weight=0.30,
            stop_loss=105.0,
            reasoning="s1",
            strategy_id="s1",
            metadata={"strategy_group": "credit_lead_lag"},
        ),
        TradeSignal(
            symbol="QQQ",
            action=Action.SHORT,
            conviction=Conviction.MEDIUM,
            target_weight=0.20,
            stop_loss=110.0,
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
    assert spy.action == Action.SHORT
    assert qqq.action == Action.SHORT
    assert spy.target_weight == 0.12
    assert qqq.target_weight == 0.08


def test_aggregate_caps_short_weight() -> None:
    signals = [
        TradeSignal(
            symbol="SPY",
            action=Action.SHORT,
            conviction=Conviction.MEDIUM,
            target_weight=0.20,
            stop_loss=105.0,
            reasoning="s1",
            strategy_id="s1",
        ),
        TradeSignal(
            symbol="QQQ",
            action=Action.SHORT,
            conviction=Conviction.MEDIUM,
            target_weight=0.10,
            stop_loss=110.0,
            reasoning="s2",
            strategy_id="s2",
        ),
    ]

    merged = aggregate_strategy_signals(signals, max_position_weight=0.10)
    spy = next(s for s in merged if s.symbol == "SPY")
    qqq = next(s for s in merged if s.symbol == "QQQ")
    assert spy.target_weight == 0.10
    assert qqq.target_weight == 0.05


def test_required_symbols_supports_pair_and_list_fields():
    specs = [
        StrategySpec(
            slug="pairs",
            strategy_name="pairs_ratio",
            parameters={"symbol_a": "ETH-USD", "symbol_b": "BTC-USD"},
        ),
        StrategySpec(
            slug="rotation",
            strategy_name="asset_rotation",
            parameters={"symbols_list": "BTC-USD, ETH-USD, SOL-USD"},
        ),
    ]
    assert required_symbols(specs) == ["BTC-USD", "ETH-USD", "SOL-USD"]
