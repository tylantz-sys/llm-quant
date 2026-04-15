"""Adapters for translating canonical exit logic into broker-native orders."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from llm_quant.broker.alpaca import AlpacaClient
from llm_quant.broker.executor import BrokerOrderIntent
from llm_quant.config import ExecutionConfig
from llm_quant.trading.exits import BrokerExitStatus, ExitTriggerStatus

logger = logging.getLogger(__name__)

STOP_LOSS_PCT = 0.02
TAKE_PROFIT_1_PCT = 0.025
TAKE_PROFIT_2_PCT = 0.05
TRAILING_STOP_PCT = 0.015
TP1_FRACTION = 0.50


@dataclass(frozen=True)
class TradeBracketPlan:
    symbol: str
    side: str
    entry_price: float
    entry_qty: float
    stop_loss_price: float
    take_profit_1_price: float
    take_profit_1_qty: float
    take_profit_2_price: float
    take_profit_2_qty: float
    trailing_stop_pct: float
    trailing_activation_price: float


def _round_price(value: float) -> float:
    return round(float(value), 2)


def _normalize_qty(qty: float, *, allow_fractional: bool) -> float:
    if allow_fractional:
        return round(float(qty), 6)
    return float(max(int(math.floor(float(qty))), 0))


def _split_exit_quantities(qty: float, *, allow_fractional: bool) -> tuple[float, float]:
    first_half = _normalize_qty(qty * TP1_FRACTION, allow_fractional=allow_fractional)
    remainder = _normalize_qty(qty - first_half, allow_fractional=allow_fractional)
    if qty > 0 and first_half <= 0 and remainder > 0:
        first_half = _normalize_qty(min(qty, remainder), allow_fractional=allow_fractional)
        remainder = _normalize_qty(qty - first_half, allow_fractional=allow_fractional)
    if qty > 0 and remainder <= 0 and first_half > 0:
        remainder = _normalize_qty(qty - first_half, allow_fractional=allow_fractional)
    return first_half, remainder


def build_trade_brackets(
    symbol: str,
    entry_price: float,
    entry_qty: float,
    *,
    side: str = "buy",
    allow_fractional: bool = False,
) -> TradeBracketPlan:
    """Build the fixed broker-authoritative protection plan for a filled entry."""
    normalized_qty = _normalize_qty(entry_qty, allow_fractional=allow_fractional)
    tp1_qty, tp2_qty = _split_exit_quantities(
        normalized_qty,
        allow_fractional=allow_fractional,
    )
    return TradeBracketPlan(
        symbol=symbol,
        side=side,
        entry_price=float(entry_price),
        entry_qty=normalized_qty,
        stop_loss_price=_round_price(entry_price * (1.0 - STOP_LOSS_PCT)),
        take_profit_1_price=_round_price(entry_price * (1.0 + TAKE_PROFIT_1_PCT)),
        take_profit_1_qty=tp1_qty,
        take_profit_2_price=_round_price(entry_price * (1.0 + TAKE_PROFIT_2_PCT)),
        take_profit_2_qty=tp2_qty,
        trailing_stop_pct=TRAILING_STOP_PCT,
        trailing_activation_price=_round_price(entry_price * (1.0 + TAKE_PROFIT_1_PCT)),
    )


def convert_exit_to_orders(
    trigger: BrokerExitStatus,
    position_qty: float,
    *,
    allow_fractional: bool = False,
    existing_stop_order_id: str | None = None,
) -> list[BrokerOrderIntent]:
    """Convert broker exit trigger state into concrete order intents."""
    qty = _normalize_qty(position_qty, allow_fractional=allow_fractional)
    if qty <= 0:
        return []

    intents: list[BrokerOrderIntent] = []
    if trigger.stop_loss_hit:
        intents.append(
            BrokerOrderIntent(
                symbol=trigger.symbol,
                side="sell",
                qty=qty,
                order_type="market",
                intent_type="synthetic_exit_stop_loss",
                allow_fractional=allow_fractional,
                exit_reason="stop_loss_hit",
                asset_class="crypto" if allow_fractional else "equity",
            )
        )
        return intents

    if trigger.trailing_hit:
        intents.append(
            BrokerOrderIntent(
                symbol=trigger.symbol,
                side="sell",
                qty=qty,
                order_type="market",
                intent_type="synthetic_exit_trailing",
                allow_fractional=allow_fractional,
                exit_reason="trailing_hit",
                asset_class="crypto" if allow_fractional else "equity",
            )
        )
        return intents

    if trigger.tp2_hit:
        intents.append(
            BrokerOrderIntent(
                symbol=trigger.symbol,
                side="sell",
                qty=qty,
                order_type="limit",
                intent_type="synthetic_exit_tp2",
                limit_price=trigger.tp2_price,
                allow_fractional=allow_fractional,
                exit_reason="tp2_hit",
                asset_class="crypto" if allow_fractional else "equity",
            )
        )
        return intents

    if trigger.tp1_hit:
        tp1_qty, _ = _split_exit_quantities(qty, allow_fractional=allow_fractional)
        if tp1_qty > 0:
            intents.append(
                BrokerOrderIntent(
                    symbol=trigger.symbol,
                    side="sell",
                    qty=tp1_qty,
                    order_type="limit",
                    intent_type="synthetic_exit_tp1",
                    limit_price=trigger.tp1_price,
                    allow_fractional=allow_fractional,
                    exit_reason="tp1_hit",
                    asset_class="crypto" if allow_fractional else "equity",
                )
            )
        return intents

    if trigger.trailing_active:
        if trigger.trailing_stop_price is None:
            raise RuntimeError(
                f"Missing trailing_stop_price for trailing_active symbol {trigger.symbol}"
            )
        intents.append(
            BrokerOrderIntent(
                symbol=trigger.symbol,
                side="sell",
                qty=qty,
                order_type="replace_stop",
                intent_type="synthetic_exit_trailing_update",
                stop_price=trigger.trailing_stop_price,
                allow_fractional=allow_fractional,
                parent_order_id=existing_stop_order_id,
                exit_reason="trailing_active",
                asset_class="crypto" if allow_fractional else "equity",
            )
        )

    return intents


def submit_post_fill_protection_orders(
    client: AlpacaClient,
    submitted_entry_order: Any,
    execution: ExecutionConfig,
) -> list[Any]:
    """Submit stop/target protection after a broker entry fill.

    Returns SubmittedBrokerOrder objects from llm_quant.broker.executor.
    Import is local to avoid circular imports.
    """
    from llm_quant.broker.executor import SubmittedBrokerOrder

    filled_qty = float(getattr(submitted_entry_order, "filled_qty", 0.0) or 0.0)
    filled_avg_price = float(
        getattr(submitted_entry_order, "filled_avg_price", 0.0) or 0.0
    )
    symbol = str(getattr(submitted_entry_order, "symbol"))
    entry_order_id = str(getattr(submitted_entry_order, "order_id"))

    asset_class = str(getattr(submitted_entry_order, "asset_class", "equity")).lower()
    allow_fractional = asset_class == "crypto"

    if filled_qty <= 0 or filled_avg_price <= 0:
        logger.info(
            "Skipping post-fill protection for %s because fill is incomplete: qty=%.6f price=%.4f",
            symbol,
            filled_qty,
            filled_avg_price,
        )
        return []

    plan = build_trade_brackets(
        symbol=symbol,
        entry_price=filled_avg_price,
        entry_qty=filled_qty,
        allow_fractional=allow_fractional,
    )

    submitted: list[SubmittedBrokerOrder] = []

    if plan.take_profit_1_qty > 0:
        stop_resp = client.submit_stop_order(
            symbol=symbol,
            qty=plan.take_profit_1_qty,
            side="sell",
            stop_price=plan.stop_loss_price,
        )
        submitted.append(
            SubmittedBrokerOrder.from_alpaca_response(
                stop_resp,
                intent_type="stop_loss",
                symbol=symbol,
                side="sell",
                requested_qty=plan.take_profit_1_qty,
                order_type="stop",
                limit_price=None,
                stop_price=plan.stop_loss_price,
                parent_order_id=entry_order_id,
                exit_reason="stop_loss",
                asset_class=asset_class,
            )
        )

        tp1_resp = client.submit_limit_order(
            symbol=symbol,
            qty=plan.take_profit_1_qty,
            side="sell",
            limit_price=plan.take_profit_1_price,
        )
        submitted.append(
            SubmittedBrokerOrder.from_alpaca_response(
                tp1_resp,
                intent_type="take_profit_1",
                symbol=symbol,
                side="sell",
                requested_qty=plan.take_profit_1_qty,
                order_type="limit",
                limit_price=plan.take_profit_1_price,
                stop_price=None,
                parent_order_id=entry_order_id,
                exit_reason="tp1",
                asset_class=asset_class,
            )
        )

    if plan.take_profit_2_qty > 0:
        stop_resp = client.submit_stop_order(
            symbol=symbol,
            qty=plan.take_profit_2_qty,
            side="sell",
            stop_price=plan.stop_loss_price,
        )
        submitted.append(
            SubmittedBrokerOrder.from_alpaca_response(
                stop_resp,
                intent_type="stop_loss",
                symbol=symbol,
                side="sell",
                requested_qty=plan.take_profit_2_qty,
                order_type="stop",
                limit_price=None,
                stop_price=plan.stop_loss_price,
                parent_order_id=entry_order_id,
                exit_reason="stop_loss",
                asset_class=asset_class,
            )
        )

        tp2_resp = client.submit_limit_order(
            symbol=symbol,
            qty=plan.take_profit_2_qty,
            side="sell",
            limit_price=plan.take_profit_2_price,
        )
        submitted.append(
            SubmittedBrokerOrder.from_alpaca_response(
                tp2_resp,
                intent_type="take_profit_2",
                symbol=symbol,
                side="sell",
                requested_qty=plan.take_profit_2_qty,
                order_type="limit",
                limit_price=plan.take_profit_2_price,
                stop_price=None,
                parent_order_id=entry_order_id,
                exit_reason="tp2",
                asset_class=asset_class,
            )
        )

    logger.info(
        "Submitted %d post-fill protection orders for %s (entry_order_id=%s, trail_activation=%.2f, trailing_stop_pct=%.4f)",
        len(submitted),
        symbol,
        entry_order_id,
        plan.trailing_activation_price,
        plan.trailing_stop_pct,
    )
    return submitted