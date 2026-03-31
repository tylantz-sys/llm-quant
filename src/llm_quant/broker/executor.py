"""Helper logic for broker order submission."""

from __future__ import annotations

import logging

from llm_quant.broker.alpaca import AlpacaClient
from llm_quant.config import RiskLimits
from llm_quant.trading.executor import ExecutedTrade

logger = logging.getLogger(__name__)


def resolve_take_profit(price: float, stop_loss: float, limits: RiskLimits) -> float:
    """Resolve take-profit price based on risk limits."""
    mode = getattr(limits, "take_profit_mode", "rr")
    if mode == "pct":
        pct = getattr(limits, "take_profit_pct", 0.03)
        return round(price * (1.0 + pct), 2)
    rr = getattr(limits, "take_profit_rr", 2.0)
    risk = max(price - stop_loss, 0.0)
    return round(price + rr * risk, 2)


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
) -> None:
    """Submit Alpaca orders for executed trades.

    BUY trades are submitted as brackets with fixed % TP (when enabled)
    unless ``use_brackets`` is False. SELL/CLOSE trades are market orders.
    """
    for trade in trades:
        symbol = trade.symbol
        if trade.action == "buy":
            if use_brackets:
                stop_loss = stop_losses.get(symbol, 0.0)
                take_profit = resolve_take_profit(trade.price, stop_loss, limits)
                if bracket_prices_valid(trade.price, stop_loss, take_profit):
                    logger.info(
                        "Submitting bracket order for %s: qty=%.0f, TP=%.2f, SL=%.2f",
                        symbol,
                        trade.shares,
                        take_profit,
                        stop_loss,
                    )
                    client.submit_bracket_order(
                        symbol=symbol,
                        qty=trade.shares,
                        side="buy",
                        take_profit=take_profit,
                        stop_loss=stop_loss,
                    )
                else:
                    logger.warning(
                        "Invalid bracket for %s (entry=%.2f, TP=%.2f, SL=%.2f) — "
                        "submitting market order.",
                        symbol,
                        trade.price,
                        take_profit,
                        stop_loss,
                    )
                    client.submit_market_order(
                        symbol=symbol, qty=trade.shares, side="buy"
                    )
            else:
                logger.info(
                    "Submitting market buy for %s: qty=%.0f (brackets disabled)",
                    symbol,
                    trade.shares,
                )
                client.submit_market_order(symbol=symbol, qty=trade.shares, side="buy")
        elif trade.action in ("sell", "close"):
            logger.info(
                "Submitting market sell for %s: qty=%.0f",
                symbol,
                trade.shares,
            )
            client.submit_market_order(symbol=symbol, qty=trade.shares, side="sell")
