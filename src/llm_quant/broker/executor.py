"""Helper logic for broker order submission."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from llm_quant.broker.alpaca import AlpacaClient, AlpacaError
from llm_quant.config import ExecutionConfig, RiskLimits
from llm_quant.trading.executor import ExecutedTrade
from llm_quant.trading.exits import (
    build_broker_exit_plan,
    build_exit_policy,
    build_exit_runtime,
    resolve_take_profit_price,
)

logger = logging.getLogger(__name__)

RISK_PER_TRADE_PCT = 0.01
STOP_LOSS_DISTANCE_PCT = 0.02


@dataclass(frozen=True)
class BrokerOrderIntent:
    symbol: str
    side: str
    qty: float
    order_type: str
    intent_type: str
    limit_price: float | None = None
    stop_price: float | None = None
    notional: float | None = None
    time_in_force: str = "day"
    allow_fractional: bool = False
    parent_order_id: str | None = None
    exit_reason: str | None = None
    asset_class: str = "equity"
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class SubmittedBrokerOrder:
    order_id: str
    symbol: str
    side: str
    qty: float
    order_type: str
    intent_type: str
    status: str
    submitted_at: str | None = None
    filled_qty: float = 0.0
    filled_avg_price: float = 0.0
    limit_price: float | None = None
    stop_price: float | None = None
    notional: float | None = None
    time_in_force: str = "day"
    allow_fractional: bool = False
    parent_order_id: str | None = None
    exit_reason: str | None = None
    asset_class: str = "equity"
    broker_raw: dict[str, Any] | None = None

    @classmethod
    def from_alpaca_response(
        cls,
        response: dict[str, Any] | None,
        *,
        intent_type: str,
        symbol: str,
        side: str,
        requested_qty: float,
        order_type: str,
        limit_price: float | None,
        stop_price: float | None,
        notional: float | None,
        time_in_force: str,
        allow_fractional: bool,
        parent_order_id: str | None,
        exit_reason: str | None,
        asset_class: str,
    ) -> "SubmittedBrokerOrder":
        response = response or {}
        order_id = str(response.get("id") or "")
        if not order_id:
            order_id = (
                f"synthetic:{intent_type}:{side}:{symbol}:"
                f"{requested_qty:.8f}:{time_in_force}"
            )

        return cls(
            order_id=order_id,
            symbol=str(response.get("symbol", symbol)),
            side=str(response.get("side", side)),
            qty=float(response.get("qty") or requested_qty or 0.0),
            order_type=str(response.get("type", order_type)),
            intent_type=intent_type,
            status=str(response.get("status", "accepted")),
            submitted_at=response.get("submitted_at"),
            filled_qty=float(response.get("filled_qty") or 0.0),
            filled_avg_price=float(response.get("filled_avg_price") or 0.0),
            limit_price=(
                float(response["limit_price"])
                if response.get("limit_price") is not None
                else limit_price
            ),
            stop_price=(
                float(response["stop_price"])
                if response.get("stop_price") is not None
                else stop_price
            ),
            notional=(
                float(response["notional"])
                if response.get("notional") is not None
                else notional
            ),
            time_in_force=str(response.get("time_in_force", time_in_force)),
            allow_fractional=allow_fractional,
            parent_order_id=parent_order_id,
            exit_reason=exit_reason,
            asset_class=asset_class,
            broker_raw=response or None,
        )


def _has_fractional_quantity(qty: float) -> bool:
    return not math.isclose(float(qty), round(float(qty)), rel_tol=0.0, abs_tol=1e-9)


def _map_crypto_symbol(symbol: str, symbol_map: dict[str, str] | None = None) -> str:
    mapping = symbol_map or {}
    mapped = mapping.get(symbol, symbol)
    if "-" in mapped and "/" not in mapped:
        mapped = mapped.replace("-", "/")
    return mapped


def _normalize_qty(qty: float, *, allow_fractional: bool) -> float:
    if allow_fractional:
        return round(float(qty), 6)
    return float(max(int(math.floor(float(qty))), 0))


def resolve_take_profit(price: float, stop_loss: float, limits: RiskLimits) -> float:
    """Backward-compatible wrapper around canonical exit-policy resolution."""
    policy = build_exit_policy(limits, ExecutionConfig())
    return resolve_take_profit_price(price, stop_loss, policy)


def bracket_prices_valid(entry_price: float, stop_loss: float, take_profit: float) -> bool:
    if entry_price <= 0 or take_profit <= 0 or stop_loss <= 0:
        return False
    if take_profit <= entry_price:
        return False
    if take_profit <= stop_loss:
        return False
    return True


def build_entry_order_intents(
    portfolio: Any,
    approved_signals: list[Any],
    prices: dict[str, float],
    account_equity: float,
    asset_class_map: dict[str, str],
    execution: ExecutionConfig,
) -> list[BrokerOrderIntent]:
    """Build broker-authoritative entry intents from approved signals.

    Hard sizing rule:
    position size = (account_equity * 0.01) / 0.02
    """
    intents: list[BrokerOrderIntent] = []
    risk_budget = float(account_equity) * RISK_PER_TRADE_PCT
    target_notional = (
        risk_budget / STOP_LOSS_DISTANCE_PCT if STOP_LOSS_DISTANCE_PCT > 0 else 0.0
    )

    for signal in approved_signals:
        action = getattr(signal, "action", None)
        if getattr(action, "value", str(action)).lower() != "buy":
            continue

        symbol = str(getattr(signal, "symbol"))
        price = float(prices.get(symbol) or 0.0)
        if price <= 0:
            logger.warning(
                "Skipping broker entry intent for %s due to invalid price.", symbol
            )
            continue

        asset_class = str(asset_class_map.get(symbol, "equity")).lower()
        allow_fractional = asset_class == "crypto"
        available_cash = max(float(getattr(portfolio, "cash", 0.0)), 0.0)
        effective_notional = max(min(target_notional, available_cash), 0.0)

        if allow_fractional and execution.crypto_order_sizing == "notional":
            if effective_notional <= 0:
                continue
            qty = round(effective_notional / price, 6)
            intents.append(
                BrokerOrderIntent(
                    symbol=_map_crypto_symbol(symbol, execution.crypto_symbol_map),
                    side="buy",
                    qty=qty,
                    order_type="market",
                    intent_type="entry",
                    notional=round(effective_notional, 2),
                    time_in_force=execution.crypto_time_in_force,
                    allow_fractional=True,
                    asset_class=asset_class,
                    metadata={
                        "source_symbol": symbol,
                        "signal_target_weight": getattr(signal, "target_weight", None),
                        "risk_budget": risk_budget,
                    },
                )
            )
            continue

        qty = _normalize_qty(
            effective_notional / price,
            allow_fractional=allow_fractional,
        )
        if qty <= 0:
            logger.warning(
                "Skipping broker entry intent for %s because computed qty is zero.",
                symbol,
            )
            continue

        intents.append(
            BrokerOrderIntent(
                symbol=_map_crypto_symbol(symbol, execution.crypto_symbol_map)
                if allow_fractional
                else symbol,
                side="buy",
                qty=qty,
                order_type="market",
                intent_type="entry",
                time_in_force=execution.crypto_time_in_force if allow_fractional else "day",
                allow_fractional=allow_fractional,
                asset_class=asset_class,
                metadata={
                    "source_symbol": symbol,
                    "signal_target_weight": getattr(signal, "target_weight", None),
                    "risk_budget": risk_budget,
                },
            )
        )

    return intents


def submit_order_intents(
    client: AlpacaClient,
    intents: list[BrokerOrderIntent],
    execution: ExecutionConfig,
) -> list[SubmittedBrokerOrder]:
    """Submit normalized order intents through the Alpaca client."""
    submitted: list[SubmittedBrokerOrder] = []

    for intent in intents:
        if intent.order_type not in {"market", "limit", "stop", "replace_stop"}:
            raise AlpacaError(
                f"Unsupported order_type '{intent.order_type}' for intent submission."
            )

        if intent.side not in {"buy", "sell"}:
            raise AlpacaError(f"Unsupported intent side '{intent.side}' for {intent.symbol}")

        kwargs: dict[str, Any] = {
            "symbol": intent.symbol,
            "qty": intent.qty,
            "side": intent.side,
            "time_in_force": intent.time_in_force,
        }
        if intent.notional is not None:
            kwargs["notional"] = intent.notional
        if intent.allow_fractional:
            kwargs["allow_fractional"] = True

        try:
            if intent.order_type == "market":
                response = client.submit_market_order(**kwargs)
            elif intent.order_type == "limit":
                if intent.limit_price is None or intent.limit_price <= 0:
                    raise AlpacaError(f"Limit intent missing price for {intent.symbol}")
                response = client.submit_limit_order(
                    symbol=intent.symbol,
                    qty=intent.qty,
                    side=intent.side,
                    limit_price=float(intent.limit_price),
                    time_in_force=intent.time_in_force,
                    allow_fractional=intent.allow_fractional,
                )
            elif intent.order_type == "stop":
                if intent.stop_price is None or intent.stop_price <= 0:
                    raise AlpacaError(f"Stop intent missing price for {intent.symbol}")
                response = client.submit_stop_order(
                    symbol=intent.symbol,
                    qty=intent.qty,
                    side=intent.side,
                    stop_price=float(intent.stop_price),
                    time_in_force=intent.time_in_force,
                    allow_fractional=intent.allow_fractional,
                )
            else:
                if not intent.parent_order_id:
                    raise AlpacaError(
                        f"Replace-stop intent missing parent order id for {intent.symbol}"
                    )
                if intent.stop_price is None or intent.stop_price <= 0:
                    raise AlpacaError(
                        f"Replace-stop intent missing stop price for {intent.symbol}"
                    )
                response = client.replace_order(
                    intent.parent_order_id,
                    qty=intent.qty,
                    stop_price=float(intent.stop_price),
                )
        except TypeError:
            kwargs.pop("allow_fractional", None)
            if intent.order_type != "market":
                raise
            if (
                intent.allow_fractional
                and intent.notional is None
                and _has_fractional_quantity(intent.qty)
            ):
                raise AlpacaError(
                    f"Fractional order requires broker fractional support for {intent.symbol}"
                ) from None
            response = client.submit_market_order(**kwargs)

        submitted.append(
            SubmittedBrokerOrder.from_alpaca_response(
                response,
                intent_type=intent.intent_type,
                symbol=intent.symbol,
                side=intent.side,
                requested_qty=intent.qty,
                order_type=intent.order_type,
                limit_price=intent.limit_price,
                stop_price=intent.stop_price,
                notional=intent.notional,
                time_in_force=intent.time_in_force,
                allow_fractional=intent.allow_fractional,
                parent_order_id=intent.parent_order_id,
                exit_reason=intent.exit_reason,
                asset_class=intent.asset_class,
            )
        )

    return submitted


def submit_alpaca_orders(
    client: AlpacaClient,
    trades: list[ExecutedTrade],
    stop_losses: dict[str, float],
    limits: RiskLimits,
    use_brackets: bool = True,
    asset_class_map: dict[str, str] | None = None,
    execution: ExecutionConfig | None = None,
) -> list[SubmittedBrokerOrder]:
    """Submit Alpaca orders for executed trades.

    BUY trades are submitted using the canonical exit-policy engine. Depending on
    runtime mode this becomes either:
    - bracket TP/SL for daily/native Alpaca
    - market buy only for intraday + broker-managed OCO attachment later

    SELL/CLOSE trades are market orders.
    """
    execution = execution or ExecutionConfig()
    asset_class_map = asset_class_map or {}
    policy = build_exit_policy(limits, execution)
    runtime = build_exit_runtime("alpaca", execution)
    submitted: list[SubmittedBrokerOrder] = []

    for trade in trades:
        symbol = trade.symbol
        asset_class = str(asset_class_map.get(symbol, "equity")).lower()
        if asset_class == "crypto":
            mapped_symbol = _map_crypto_symbol(symbol, execution.crypto_symbol_map)
            if trade.action in ("buy", "sell", "close"):
                notional = None
                qty = float(trade.shares)
                if execution.crypto_order_sizing == "notional" and trade.action == "buy":
                    notional = trade.notional
                if qty <= 0 and notional is None:
                    logger.warning(
                        "Skipping crypto order for %s because qty/notional is empty.",
                        symbol,
                    )
                    continue
                kwargs: dict[str, Any] = {
                    "symbol": mapped_symbol,
                    "qty": qty,
                    "side": "buy" if trade.action == "buy" else "sell",
                    "time_in_force": execution.crypto_time_in_force,
                    "notional": notional,
                    "allow_fractional": True,
                }
                try:
                    response = client.submit_market_order(**kwargs)
                except TypeError:
                    if _has_fractional_quantity(qty):
                        raise AlpacaError(
                            f"Fractional crypto order requires broker fractional support for {mapped_symbol}"
                        ) from None
                    kwargs.pop("allow_fractional", None)
                    response = client.submit_market_order(**kwargs)

                submitted.append(
                    SubmittedBrokerOrder.from_alpaca_response(
                        response,
                        intent_type="entry" if trade.action == "buy" else "exit",
                        symbol=mapped_symbol,
                        side="buy" if trade.action == "buy" else "sell",
                        requested_qty=qty,
                        order_type="market",
                        limit_price=None,
                        stop_price=None,
                        notional=notional,
                        time_in_force=execution.crypto_time_in_force,
                        allow_fractional=True,
                        parent_order_id=None,
                        exit_reason=trade.exit_reason or None,
                        asset_class=asset_class,
                    )
                )
            continue

        if trade.action == "buy":
            stop_loss = stop_losses.get(symbol, 0.0)
            plan = build_broker_exit_plan(
                symbol=symbol,
                entry_price=trade.price,
                stop_loss=stop_loss,
                policy=policy,
                runtime=runtime,
            )
            if use_brackets and plan.kind == "bracket":
                if bracket_prices_valid(trade.price, plan.stop_loss, plan.take_profit):
                    logger.info(
                        "Submitting canonical bracket order for %s: qty=%.0f, TP=%.2f, SL=%.2f",
                        symbol,
                        trade.shares,
                        plan.take_profit,
                        plan.stop_loss,
                    )
                    _tif = execution.crypto_time_in_force if asset_class == "crypto" else "day"
                    response = client.submit_bracket_order(
                        symbol=symbol,
                        qty=trade.shares,
                        side="buy",
                        take_profit=plan.take_profit,
                        stop_loss=plan.stop_loss,
                        time_in_force=_tif,
                        allow_fractional=asset_class == "crypto",
                    )
                    submitted.append(
                        SubmittedBrokerOrder.from_alpaca_response(
                            response,
                            intent_type="entry",
                            symbol=symbol,
                            side="buy",
                            requested_qty=trade.shares,
                            order_type="market",
                            limit_price=plan.take_profit,
                            stop_price=plan.stop_loss,
                            notional=None,
                            time_in_force="day",
                            allow_fractional=False,
                            parent_order_id=None,
                            exit_reason=trade.exit_reason or None,
                            asset_class=asset_class,
                        )
                    )
                else:
                    message = (
                        "Invalid bracket for "
                        f"{symbol} (entry={trade.price:.2f}, TP={plan.take_profit:.2f}, "
                        f"SL={plan.stop_loss:.2f})"
                    )
                    if plan.fail_on_unprotected:
                        raise AlpacaError(message)
                    logger.warning("%s — submitting market order.", message)
                    response = client.submit_market_order(
                        symbol=symbol,
                        qty=trade.shares,
                        side="buy",
                    )
                    submitted.append(
                        SubmittedBrokerOrder.from_alpaca_response(
                            response,
                            intent_type="entry",
                            symbol=symbol,
                            side="buy",
                            requested_qty=trade.shares,
                            order_type="market",
                            limit_price=None,
                            stop_price=None,
                            notional=None,
                            time_in_force="day",
                            allow_fractional=False,
                            parent_order_id=None,
                            exit_reason=trade.exit_reason or None,
                            asset_class=asset_class,
                        )
                    )
            else:
                logger.info(
                    "Submitting market buy for %s: qty=%.0f (broker exit kind=%s)",
                    symbol,
                    trade.shares,
                    plan.kind,
                )
                response = client.submit_market_order(
                    symbol=symbol,
                    qty=trade.shares,
                    side="buy",
                )
                submitted.append(
                    SubmittedBrokerOrder.from_alpaca_response(
                        response,
                        intent_type="entry",
                        symbol=symbol,
                        side="buy",
                        requested_qty=trade.shares,
                        order_type="market",
                        limit_price=None,
                        stop_price=None,
                        notional=None,
                        time_in_force="day",
                        allow_fractional=False,
                        parent_order_id=None,
                        exit_reason=trade.exit_reason or None,
                        asset_class=asset_class,
                    )
                )
        elif trade.action in ("sell", "close"):
            logger.info(
                "Submitting market sell for %s: qty=%.0f",
                symbol,
                trade.shares,
            )
            response = client.submit_market_order(
                symbol=symbol,
                qty=trade.shares,
                side="sell",
            )
            submitted.append(
                SubmittedBrokerOrder.from_alpaca_response(
                    response,
                    intent_type="exit",
                    symbol=symbol,
                    side="sell",
                    requested_qty=trade.shares,
                    order_type="market",
                    limit_price=None,
                    stop_price=None,
                    notional=None,
                    time_in_force="day",
                    allow_fractional=False,
                    parent_order_id=None,
                    exit_reason=trade.exit_reason or None,
                    asset_class=asset_class,
                )
            )

    return submitted
