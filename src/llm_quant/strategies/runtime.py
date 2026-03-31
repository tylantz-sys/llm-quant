"""Runtime strategy loader for promoted research specs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from llm_quant.backtest.strategy import StrategyConfig
from llm_quant.backtest.strategies import create_strategy
from llm_quant.brain.models import Action, Conviction, TradeSignal
from llm_quant.trading.portfolio import Portfolio

logger = logging.getLogger(__name__)


PROMOTED_STRATEGY_SLUGS: list[str] = [
    "lqd-spy-credit-lead",
    "agg-spy-credit-lead",
    "spy-overnight-momentum",
    "agg-qqq-credit-lead",
    "vcit-qqq-credit-lead",
    "lqd-qqq-credit-lead",
    "emb-spy-credit-lead",
    "hyg-spy-5d-credit-lead",
    "agg-efa-credit-lead",
    "hyg-qqq-credit-lead",
    "soxx-qqq-lead-lag",
]


@dataclass
class StrategySpec:
    slug: str
    strategy_name: str
    parameters: dict[str, Any]
    group: str = "ungrouped"


def load_promoted_specs(base_dir: Path | None = None) -> list[StrategySpec]:
    """Load the 11 promoted strategy specs from data/strategies."""
    base = base_dir or Path("data/strategies")
    specs: list[StrategySpec] = []
    for slug in PROMOTED_STRATEGY_SLUGS:
        path = base / slug / "research-spec.yaml"
        if not path.exists():
            logger.warning("Strategy spec missing: %s", path)
            continue
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        strategy_name = (
            raw.get("strategy_name")
            or raw.get("strategy_type")
            or raw.get("strategy_class")
        )
        if not strategy_name:
            logger.warning("Spec %s missing strategy_name/type; skipping", slug)
            continue
        params = raw.get("parameters", {}) or {}
        group = raw.get("group") or "ungrouped"
        specs.append(
            StrategySpec(
                slug=slug,
                strategy_name=strategy_name,
                parameters=params,
                group=str(group),
            )
        )
    return specs


def generate_strategy_signals(
    specs: list[StrategySpec],
    indicators_df: pl.DataFrame,
    portfolio: Portfolio,
    prices: dict[str, float],
    as_of_date,
) -> list[TradeSignal]:
    """Generate TradeSignals from promoted strategies."""
    signals: list[TradeSignal] = []
    for spec in specs:
        config = StrategyConfig(
            name=spec.slug,
            parameters=spec.parameters,
        )
        try:
            strategy = create_strategy(spec.strategy_name, config)
        except ValueError as exc:
            logger.warning("Unknown strategy %s (%s): %s", spec.slug, spec.strategy_name, exc)
            continue

        try:
            strat_signals = strategy.generate_signals(
                as_of_date=as_of_date,
                indicators_df=indicators_df,
                portfolio=portfolio,
                prices=prices,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Strategy %s failed: %s", spec.slug, exc)
            continue

        for signal in strat_signals:
            signal.strategy_id = spec.slug
            signal.metadata["strategy_group"] = spec.group
            signals.append(signal)

    return signals


def required_symbols(specs: list[StrategySpec]) -> list[str]:
    symbols: set[str] = set()
    for spec in specs:
        params = spec.parameters or {}
        if "leader_symbol" in params:
            symbols.add(str(params.get("leader_symbol")))
        if "follower_symbol" in params:
            symbols.add(str(params.get("follower_symbol")))
        if "symbol" in params:
            symbols.add(str(params.get("symbol")))
    return sorted(sym for sym in symbols if sym)


def merge_strategy_signals(
    signals: list[TradeSignal],
) -> list[TradeSignal]:
    """Merge signals by symbol, preserving group metadata."""
    by_symbol: dict[str, list[TradeSignal]] = {}
    for sig in signals:
        by_symbol.setdefault(sig.symbol, []).append(sig)

    merged: list[TradeSignal] = []
    for symbol, group in by_symbol.items():
        actions = [g.action for g in group]
        if Action.CLOSE in actions:
            action = Action.CLOSE
        elif Action.SELL in actions:
            action = Action.SELL
        elif Action.BUY in actions:
            action = Action.BUY
        else:
            action = Action.HOLD

        if action == Action.CLOSE:
            target_weight = 0.0
        elif action == Action.SELL:
            target_weight = min(g.target_weight for g in group if g.action == Action.SELL)
        elif action == Action.BUY:
            target_weight = sum(g.target_weight for g in group if g.action == Action.BUY)
        else:
            target_weight = 0.0

        stop_losses = [g.stop_loss for g in group if g.stop_loss > 0]
        stop_loss = min(stop_losses) if stop_losses else 0.0

        conviction = _max_conviction([g.conviction for g in group])
        strategy_ids = ",".join(sorted({g.strategy_id for g in group if g.strategy_id}))
        groups = sorted(
            {g.metadata.get("strategy_group", "ungrouped") for g in group}
        )
        reasoning = "; ".join(
            f"{g.strategy_id or 'strategy'}:{g.reasoning}" for g in group
        )

        merged.append(
            TradeSignal(
                symbol=symbol,
                action=action,
                conviction=conviction,
                target_weight=round(target_weight, 4),
                stop_loss=stop_loss,
                reasoning=reasoning,
                strategy_id=strategy_ids,
                metadata={"strategy_groups": groups},
            )
        )

    return merged


def apply_regime_multipliers(
    signals: list[TradeSignal],
    regime_mults: dict[str, dict[str, float]],
    market_regime: str,
) -> list[TradeSignal]:
    """Scale BUY weights by group/regime multipliers."""
    if not regime_mults:
        return signals

    for sig in signals:
        if sig.action != Action.BUY:
            continue
        group = sig.metadata.get("strategy_group", "ungrouped")
        group_mults = regime_mults.get(group, {})
        mult = group_mults.get(market_regime, 1.0)
        sig.target_weight = round(sig.target_weight * float(mult), 4)

    return signals


def apply_group_caps(
    signals: list[TradeSignal],
    group_caps: dict[str, float],
) -> list[TradeSignal]:
    """Scale BUY weights so each strategy group respects its cap."""
    if not group_caps:
        return signals

    totals: dict[str, float] = {}
    for sig in signals:
        if sig.action != Action.BUY:
            continue
        groups = sig.metadata.get("strategy_groups") or [
            sig.metadata.get("strategy_group", "ungrouped")
        ]
        for group in groups:
            totals[group] = totals.get(group, 0.0) + sig.target_weight

    group_factors: dict[str, float] = {}
    for group, total in totals.items():
        cap = group_caps.get(group)
        if cap is None or cap <= 0:
            continue
        if total > cap:
            group_factors[group] = cap / total

    if not group_factors:
        return signals

    for sig in signals:
        if sig.action != Action.BUY:
            continue
        groups = sig.metadata.get("strategy_groups") or [
            sig.metadata.get("strategy_group", "ungrouped")
        ]
        factors = [group_factors.get(group, 1.0) for group in groups]
        scale = min(factors) if factors else 1.0
        if scale < 1.0:
            sig.target_weight = round(sig.target_weight * scale, 4)

    return signals


def apply_max_position_cap(
    signals: list[TradeSignal],
    max_position_weight: float,
) -> list[TradeSignal]:
    """Proportional scaling: if any BUY exceeds cap, scale all BUY weights."""
    max_buy_weight = max(
        (s.target_weight for s in signals if s.action == Action.BUY),
        default=0.0,
    )
    if max_buy_weight > max_position_weight and max_buy_weight > 0:
        scale = max_position_weight / max_buy_weight
        for sig in signals:
            if sig.action == Action.BUY:
                sig.target_weight = round(sig.target_weight * scale, 4)
        logger.info(
            "Scaled BUY weights by %.3f to fit max_position_weight=%.2f",
            scale,
            max_position_weight,
        )
    return signals


def aggregate_strategy_signals(
    signals: list[TradeSignal],
    max_position_weight: float,
) -> list[TradeSignal]:
    """Backwards-compatible merge + max-position scaling."""
    merged = merge_strategy_signals(signals)
    return apply_max_position_cap(merged, max_position_weight)


def _max_conviction(convictions: list[Conviction]) -> Conviction:
    rank = {Conviction.HIGH: 3, Conviction.MEDIUM: 2, Conviction.LOW: 1}
    return max(convictions, key=lambda c: rank.get(c, 0), default=Conviction.MEDIUM)
