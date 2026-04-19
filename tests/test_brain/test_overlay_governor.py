from datetime import date

from llm_quant.brain.governor import (
    enforce_governor_constraints,
    fallback_governor_decision,
)
from llm_quant.brain.models import (
    Action,
    Conviction,
    MarketContext,
    MarketRegime,
    TradeSignal,
    TradingDecision,
)


def _context() -> MarketContext:
    return MarketContext(
        date=date(2026, 3, 31),
        nav=100000.0,
        cash=100000.0,
        cash_pct=100.0,
        gross_exposure_pct=0.0,
        net_exposure_pct=0.0,
        market_regime=MarketRegime.RISK_ON,
    )


def _decision(signals: list[TradeSignal]) -> TradingDecision:
    return TradingDecision(
        date=date(2026, 3, 31),
        market_regime=MarketRegime.RISK_ON,
        regime_confidence=0.8,
        regime_reasoning="test",
        signals=signals,
        portfolio_commentary="",
        decision_type="overlay",
    )


def test_governor_clamps_weight_without_fallback():
    candidate_signals = [
        {
            "symbol": "BTC-USD",
            "action": "buy",
            "target_weight": 0.10,
            "stop_loss": 60000.0,
            "take_profit": 70000.0,
            "strategy_id": "strat1",
            "reasoning": "candidate",
        }
    ]
    decision = _decision(
        [
            TradeSignal(
                symbol="BTC-USD",
                action=Action.BUY,
                conviction=Conviction.HIGH,
                target_weight=0.40,
                stop_loss=60000.0,
                take_profit=70000.0,
                strategy_id="strat1",
                reasoning="overlay scale up",
            )
        ]
    )
    sanitized, audit, fallback_required = enforce_governor_constraints(
        decision=decision,
        candidate_signals=candidate_signals,
        strict=True,
        max_upscale=1.25,
        max_downscale=0.0,
        decision_date=date(2026, 3, 31),
    )
    assert fallback_required is False
    assert audit["scaled_count"] == 1
    assert sanitized[0].target_weight == 0.125


def test_governor_detects_symbol_drift_and_requires_fallback():
    candidate_signals = [
        {
            "symbol": "BTC-USD",
            "action": "buy",
            "target_weight": 0.10,
            "stop_loss": 60000.0,
            "take_profit": 70000.0,
            "strategy_id": "strat1",
            "reasoning": "candidate",
        }
    ]
    decision = _decision(
        [
            TradeSignal(
                symbol="BTC-USD",
                action=Action.BUY,
                conviction=Conviction.HIGH,
                target_weight=0.10,
                stop_loss=60000.0,
                take_profit=70000.0,
                strategy_id="strat1",
                reasoning="overlay ok",
            ),
            TradeSignal(
                symbol="ETH-USD",
                action=Action.BUY,
                conviction=Conviction.HIGH,
                target_weight=0.10,
                stop_loss=2000.0,
                take_profit=2300.0,
                strategy_id="strat2",
                reasoning="drift",
            ),
        ]
    )
    _sanitized, audit, fallback_required = enforce_governor_constraints(
        decision=decision,
        candidate_signals=candidate_signals,
        strict=True,
        max_upscale=1.25,
        max_downscale=0.0,
        decision_date=date(2026, 3, 31),
    )
    assert fallback_required is True
    assert any("symbol_drift" in item for item in audit["policy_violations"])


def test_fallback_governor_decision_holds_all_candidates():
    fallback = fallback_governor_decision(
        context=_context(),
        candidate_signals=[
            {
                "symbol": "BTC-USD",
                "action": "buy",
                "target_weight": 0.10,
                "stop_loss": 60000.0,
                "take_profit": 70000.0,
                "strategy_id": "strat1",
            },
            {
                "symbol": "ETH-USD",
                "action": "buy",
                "target_weight": 0.10,
                "stop_loss": 2000.0,
                "take_profit": 2300.0,
                "strategy_id": "strat2",
            },
        ],
        reason="policy violation",
    )
    assert fallback.decision_type == "overlay"
    assert all(signal.action == Action.HOLD for signal in fallback.signals)


def test_governor_clamps_short_weight_without_side_flip() -> None:
    candidate_signals = [
        {
            "symbol": "SPY",
            "action": "short",
            "target_weight": 0.05,
            "stop_loss": 505.0,
            "take_profit": 480.0,
            "strategy_id": "strat-short",
            "reasoning": "candidate short",
        }
    ]
    decision = _decision(
        [
            TradeSignal(
                symbol="SPY",
                action=Action.SHORT,
                conviction=Conviction.HIGH,
                target_weight=0.10,
                stop_loss=505.0,
                take_profit=480.0,
                strategy_id="strat-short",
                reasoning="overlay short scale up",
            )
        ]
    )
    sanitized, audit, fallback_required = enforce_governor_constraints(
        decision=decision,
        candidate_signals=candidate_signals,
        strict=True,
        max_upscale=1.20,
        max_downscale=0.0,
        decision_date=date(2026, 3, 31),
    )
    assert fallback_required is False
    assert audit["scaled_count"] == 1
    assert sanitized[0].action == Action.SHORT
    assert sanitized[0].target_weight == 0.06
