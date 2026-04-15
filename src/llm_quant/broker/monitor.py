"""Broker monitoring helpers for open positions and forced flattening."""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import Any

from llm_quant.broker.alpaca import AlpacaClient, AlpacaError
from llm_quant.broker.reconciliation import BrokerFillEvent, BrokerOrderStatus

logger = logging.getLogger(__name__)


def _parse_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if value:
        text = str(value).replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(tz=UTC)


def _normalize_qty(qty: float) -> float:
    if qty <= 0:
        return 0.0
    rounded = round(float(qty), 8)
    if math.isclose(rounded, round(rounded), rel_tol=0.0, abs_tol=1e-8):
        return float(int(round(rounded)))
    return rounded


def force_flatten_positions(
    client: AlpacaClient,
    positions: list[dict[str, Any]] | None = None,
) -> list[BrokerOrderStatus]:
    """Cancel open orders and submit market orders to flatten broker positions."""
    try:
        client.cancel_all_orders()
    except AlpacaError as exc:
        logger.warning("Cancel all orders failed during flatten: %s", exc)

    broker_positions = positions if positions is not None else client.list_positions()
    submitted: list[BrokerOrderStatus] = []

    for position in broker_positions:
        symbol = str(position.get("symbol") or "")
        qty = abs(_parse_float(position.get("qty")))
        if not symbol or qty <= 0:
            continue

        side = "sell" if _parse_float(position.get("qty")) > 0 else "buy"
        try:
            order = client.submit_market_order(symbol=symbol, qty=qty, side=side)
        except AlpacaError as exc:
            logger.warning("Flatten order failed for %s: %s", symbol, exc)
            continue

        submitted.append(
            BrokerOrderStatus(
                order_id=str(order.get("id") or ""),
                symbol=symbol,
                side=side,
                status=str(order.get("status") or "submitted").lower(),
                qty=_normalize_qty(qty),
                filled_qty=_parse_float(order.get("filled_qty")),
                filled_avg_price=(
                    _parse_float(order.get("filled_avg_price"))
                    if order.get("filled_avg_price") not in (None, "")
                    else None
                ),
                submitted_at=_parse_dt(order.get("submitted_at")),
                updated_at=_parse_dt(order.get("updated_at") or order.get("filled_at")),
                intent_type="force_flatten",
                parent_order_id=None,
                exit_reason="force_flatten",
            )
        )

    return submitted


def monitor_open_positions(
    client: AlpacaClient,
    tracked_symbols: list[str] | None = None,
) -> list[BrokerFillEvent]:
    """Poll broker positions and emit broker-side position snapshots as fill-like events."""
    tracked = {symbol for symbol in (tracked_symbols or []) if symbol}
    events: list[BrokerFillEvent] = []

    try:
        positions = client.list_positions()
    except AlpacaError as exc:
        logger.warning("Broker position polling failed: %s", exc)
        return events

    for position in positions:
        symbol = str(position.get("symbol") or "")
        if tracked and symbol not in tracked:
            continue

        qty = _normalize_qty(abs(_parse_float(position.get("qty"))))
        if qty <= 0:
            continue

        side = "buy" if _parse_float(position.get("qty")) > 0 else "sell"
        price = _parse_float(position.get("current_price") or position.get("avg_entry_price"))
        if price <= 0:
            continue

        events.append(
            BrokerFillEvent(
                order_id=str(position.get("asset_id") or f"position:{symbol}"),
                symbol=symbol,
                side=side,
                fill_qty=qty,
                fill_price=price,
                fill_time=_parse_dt(position.get("updated_at") or position.get("lastday_price")),
                intent_type="position_monitor",
                parent_order_id=None,
                exit_reason=None,
            )
        )

    return events


__all__ = ["force_flatten_positions", "monitor_open_positions"]