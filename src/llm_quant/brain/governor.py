"""Post-LLM overlay governor enforcement helpers."""

from __future__ import annotations

from datetime import date
from typing import Any

from llm_quant.brain.models import (
    Action,
    Conviction,
    MarketContext,
    TradeSignal,
    TradingDecision,
)


def fallback_governor_decision(
    *,
    context: MarketContext,
    candidate_signals: list[dict[str, Any]],
    reason: str,
) -> TradingDecision:
    """Return deterministic no-trade fallback from candidate signals."""
    hold_signals: list[TradeSignal] = []
    for candidate in candidate_signals:
        symbol = str(candidate.get("symbol", "")).upper()
        if not symbol:
            continue
        hold_signals.append(
            TradeSignal(
                symbol=symbol,
                action=Action.HOLD,
                conviction=Conviction.LOW,
                target_weight=0.0,
                stop_loss=float(candidate.get("stop_loss", 0.0) or 0.0),
                take_profit=float(candidate.get("take_profit", 0.0) or 0.0),
                strategy_id=str(candidate.get("strategy_id", "") or ""),
                reasoning=f"Governor fallback: {reason}",
            )
        )

    return TradingDecision(
        date=context.date,
        market_regime=context.market_regime,
        regime_confidence=0.0,
        regime_reasoning=f"Governor fallback engaged ({reason}).",
        signals=hold_signals,
        portfolio_commentary=(
            "Strategy-first governor fallback engaged. "
            "All candidates were converted to HOLD for this run."
        ),
        decision_type="overlay",
    )


def enforce_governor_constraints(
    *,
    decision: TradingDecision,
    candidate_signals: list[dict[str, Any]],
    strict: bool,
    max_upscale: float,
    max_downscale: float,
    decision_date: date,
) -> tuple[list[TradeSignal], dict[str, Any], bool]:
    """Enforce strict candidate-bound overlay rules.

    Returns:
        sanitized_signals, audit, fallback_required
    """
    max_up = max(float(max_upscale), 0.0)
    down = float(max_downscale)
    down = 0.0 if down < 0 else down
    down = min(down, 1.0)
    violations: list[str] = []
    overlay_by_symbol = {signal.symbol: signal for signal in decision.signals}
    candidate_by_symbol = {
        str(candidate.get("symbol", "")).upper(): candidate
        for candidate in candidate_signals
    }

    # Overlay may never introduce new symbols.
    unknown_overlay = sorted(set(overlay_by_symbol) - set(candidate_by_symbol))
    if unknown_overlay:
        violations.append(f"symbol_drift={','.join(unknown_overlay)}")

    sanitized: list[TradeSignal] = []
    accepted = 0
    rejected = 0
    scaled = 0

    for candidate in candidate_signals:
        symbol = str(candidate.get("symbol", "")).upper()
        if not symbol:
            continue
        candidate_action = _safe_action(candidate.get("action"))
        if candidate_action is None:
            continue

        candidate_weight = float(candidate.get("target_weight", 0.0) or 0.0)
        candidate_stop = float(candidate.get("stop_loss", 0.0) or 0.0)
        candidate_tp = float(candidate.get("take_profit", 0.0) or 0.0)
        candidate_strategy = str(candidate.get("strategy_id", "") or "")
        candidate_reason = str(candidate.get("reasoning", "") or "")
        overlay_signal = overlay_by_symbol.get(symbol)

        if overlay_signal is None or overlay_signal.action == Action.HOLD:
            rejected += 1
            sanitized.append(
                TradeSignal(
                    symbol=symbol,
                    action=Action.HOLD,
                    conviction=Conviction.LOW,
                    target_weight=0.0,
                    stop_loss=candidate_stop,
                    take_profit=candidate_tp,
                    strategy_id=candidate_strategy,
                    reasoning="Governor reject/omit",
                )
            )
            continue

        if overlay_signal.action not in {candidate_action, Action.HOLD}:
            violations.append(
                f"side_flip:{symbol}:{candidate_action.value}->{overlay_signal.action.value}"
            )
            rejected += 1
            sanitized.append(
                TradeSignal(
                    symbol=symbol,
                    action=Action.HOLD,
                    conviction=Conviction.LOW,
                    target_weight=0.0,
                    stop_loss=candidate_stop,
                    take_profit=candidate_tp,
                    strategy_id=candidate_strategy,
                    reasoning="Governor side flip rejected",
                )
            )
            continue

        if overlay_signal.action == Action.HOLD:
            rejected += 1
            sanitized.append(
                TradeSignal(
                    symbol=symbol,
                    action=Action.HOLD,
                    conviction=Conviction.LOW,
                    target_weight=0.0,
                    stop_loss=candidate_stop,
                    take_profit=candidate_tp,
                    strategy_id=candidate_strategy,
                    reasoning="Governor reject",
                )
            )
            continue

        target_weight = candidate_weight
        if candidate_action == Action.BUY:
            min_w = candidate_weight * down
            max_w = candidate_weight * max_up
            proposed = float(overlay_signal.target_weight)
            clamped = max(min(proposed, max_w), min_w)
            target_weight = round(clamped, 4)
            if round(proposed, 4) != target_weight:
                scaled += 1

        # Stop-loss / take-profit cannot drift from candidate strategy values.
        if round(float(overlay_signal.stop_loss), 4) != round(candidate_stop, 4):
            violations.append(f"stop_drift:{symbol}")
        if round(float(overlay_signal.take_profit), 4) != round(candidate_tp, 4):
            violations.append(f"tp_drift:{symbol}")

        accepted += 1
        sanitized.append(
            TradeSignal(
                symbol=symbol,
                action=candidate_action,
                conviction=overlay_signal.conviction,
                target_weight=target_weight,
                stop_loss=candidate_stop,
                take_profit=candidate_tp,
                strategy_id=candidate_strategy,
                reasoning=overlay_signal.reasoning or candidate_reason,
                entry_batch=overlay_signal.entry_batch,
                exit_reason=overlay_signal.exit_reason,
                metadata=overlay_signal.metadata,
            )
        )

    violations = sorted(set(violations))
    fallback_required = bool(strict and violations)
    audit = {
        "candidate_count": len(candidate_signals),
        "accepted_count": accepted,
        "rejected_count": rejected,
        "scaled_count": scaled,
        "policy_violations": violations,
        "fallback_required": fallback_required,
        "mode": "strict" if strict else "lenient",
        "decision_date": str(decision_date),
    }
    return sanitized, audit, fallback_required


def _safe_action(value: Any) -> Action | None:
    if isinstance(value, Action):
        return value
    if isinstance(value, str):
        try:
            return Action(value.lower())
        except ValueError:
            return None
    return None


__all__ = ["enforce_governor_constraints", "fallback_governor_decision"]
