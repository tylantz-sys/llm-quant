"""Helper logic for broker order submission."""

from __future__ import annotations

import logging

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


def submit_alpaca_orders(
    client: AlpacaClient,
    trades: list[ExecutedTrade],
    stop_losses: dict[str, float],
    limits: RiskLimits,
    use_brackets: bool = True,
    asset_class_map: dict[str, str] | None = None,
    execution: ExecutionConfig | None = None,
) -> None:
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
    for trade in trades:
        symbol = trade.symbol
        asset_class = str(asset_class_map.get(symbol, "equity")).lower()
        if asset_class == "crypto":
            mapped_symbol = _map_crypto_symbol(symbol, execution.crypto_symbol_map)
            if trade.action in ("buy", "sell", "close"):
                notional = None
                qty = trade.shares
                if execution.crypto_order_sizing == "notional":
                    notional = trade.notional
                try:
                    client.submit_market_order(
                        symbol=mapped_symbol,
                        qty=qty,
                        side="buy" if trade.action == "buy" else "sell",
                        time_in_force=execution.crypto_time_in_force,
                        notional=notional,
                        allow_fractional=True,
                    )
                except TypeError:
                    # Backward-compatible fallback if allow_fractional not supported.
                    client.submit_market_order(
                        symbol=mapped_symbol,
                        qty=qty,
                        side="buy" if trade.action == "buy" else "sell",
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
                    client.submit_bracket_order(
                        symbol=symbol,
                        qty=trade.shares,
                        side="buy",
                        take_profit=plan.take_profit,
                        stop_loss=plan.stop_loss,
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
                    client.submit_market_order(
                        symbol=symbol, qty=trade.shares, side="buy"
                    )
            else:
                logger.info(
                    "Submitting market buy for %s: qty=%.0f (broker exit kind=%s)",
                    symbol,
                    trade.shares,
                    plan.kind,
                )
                client.submit_market_order(symbol=symbol, qty=trade.shares, side="buy")
        elif trade.action in ("sell", "close"):
            logger.info(
                "Submitting market sell for %s: qty=%.0f",
                symbol,
                trade.shares,
            )
            client.submit_market_order(symbol=symbol, qty=trade.shares, side="sell")


def _map_crypto_symbol(symbol: str, symbol_map: dict[str, str] | None = None) -> str:
    mapping = symbol_map or {}
    mapped = mapping.get(symbol, symbol)
    if "-" in mapped and "/" not in mapped:
        mapped = mapped.replace("-", "/")
    return mapped
