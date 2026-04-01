"""Manage intraday OCO exits and trailing stop updates."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import duckdb

from llm_quant.broker.alpaca import AlpacaClient, AlpacaError
from llm_quant.trading.executor import ExecutedTrade

logger = logging.getLogger(__name__)


def _normalize_order_qty(qty: float) -> float:
    if qty <= 0:
        return 0.0
    rounded = round(float(qty), 8)
    if math.isclose(rounded, round(rounded), rel_tol=0.0, abs_tol=1e-8):
        return float(int(round(rounded)))
    return rounded


@dataclass
class IntradayOrderState:
    symbol: str
    partial_tp_order_id: str | None = None
    oco_order_id: str | None = None
    oco_tp_order_id: str | None = None
    oco_stop_order_id: str | None = None
    oco_leg_missing_count: int = 0
    hwm: float = 0.0
    remaining_qty: float = 0.0
    tp_status: str | None = None
    oco_tp_status: str | None = None
    stop_status: str | None = None
    last_checked_at: datetime | None = None


def load_order_states(
    conn: duckdb.DuckDBPyConnection,
    pod_id: str,
) -> dict[str, IntradayOrderState]:
    rows = conn.execute(
        """
        SELECT
            symbol,
            partial_tp_order_id,
            oco_order_id,
            oco_tp_order_id,
            oco_stop_order_id,
            oco_leg_missing_count,
            hwm,
            remaining_qty,
            tp_status,
            oco_tp_status,
            stop_status,
            last_checked_at
        FROM intraday_order_state
        WHERE pod_id = ?
        """,
        [pod_id],
    ).fetchall()
    states: dict[str, IntradayOrderState] = {}
    for row in rows:
        (
            symbol,
            partial_tp_id,
            oco_id,
            oco_tp_id,
            oco_stop_id,
            oco_leg_missing_count,
            hwm,
            remaining_qty,
            tp_status,
            oco_tp_status,
            stop_status,
            last_checked_at,
        ) = row
        states[symbol] = IntradayOrderState(
            symbol=symbol,
            partial_tp_order_id=partial_tp_id,
            oco_order_id=oco_id,
            oco_tp_order_id=oco_tp_id,
            oco_stop_order_id=oco_stop_id,
            oco_leg_missing_count=int(oco_leg_missing_count or 0),
            hwm=float(hwm or 0.0),
            remaining_qty=float(remaining_qty or 0.0),
            tp_status=tp_status,
            oco_tp_status=oco_tp_status,
            stop_status=stop_status,
            last_checked_at=last_checked_at,
        )
    return states


def upsert_order_states(
    conn: duckdb.DuckDBPyConnection,
    pod_id: str,
    states: dict[str, IntradayOrderState],
) -> None:
    if not states:
        return

    rows: list[list[Any]] = []
    for state in states.values():
        rows.append(
            [
                pod_id,
                state.symbol,
                state.partial_tp_order_id,
                state.oco_order_id,
                state.oco_tp_order_id,
                state.oco_stop_order_id,
                state.oco_leg_missing_count,
                state.hwm,
                state.remaining_qty,
                state.tp_status,
                state.oco_tp_status,
                state.stop_status,
                state.last_checked_at,
                datetime.now(tz=UTC),
            ]
        )

    conn.executemany(
        """
        INSERT OR REPLACE INTO intraday_order_state (
            pod_id,
            symbol,
            partial_tp_order_id,
            oco_order_id,
            oco_tp_order_id,
            oco_stop_order_id,
            oco_leg_missing_count,
            hwm,
            remaining_qty,
            tp_status,
            oco_tp_status,
            stop_status,
            last_checked_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def compute_trailing_stop(
    hwm: float,
    price: float,
    trailing_pct: float,
) -> tuple[float, float, bool]:
    """Return (new_hwm, new_stop_price, should_update)."""
    if price <= 0 or trailing_pct <= 0:
        return hwm, 0.0, False

    if price > hwm:
        new_hwm = price
        return new_hwm, new_hwm * (1.0 - trailing_pct), True
    return hwm, hwm * (1.0 - trailing_pct), False


def _extract_oco_leg_ids(legs: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    tp_leg_id = None
    stop_leg_id = None
    for leg in legs:
        leg_type = str(leg.get("type") or leg.get("order_type") or "").lower()
        if leg_type == "limit":
            tp_leg_id = leg.get("id")
        elif leg_type in {"stop", "stop_limit"}:
            stop_leg_id = leg.get("id")
    return tp_leg_id, stop_leg_id


def _resolve_oco_legs(
    client: AlpacaClient,
    oco_order_id: str | None,
    order: dict[str, Any] | None = None,
) -> tuple[str | None, str | None]:
    if not oco_order_id:
        return None, None

    if order:
        legs = order.get("legs") or []
        if legs:
            return _extract_oco_leg_ids(legs)

    try:
        nested = client.get_order(oco_order_id, nested=True)
        legs = nested.get("legs") or []
        if legs:
            return _extract_oco_leg_ids(legs)
    except AlpacaError:
        pass

    try:
        orders = client.list_orders(status="open", nested=True)
    except AlpacaError:
        return None, None

    legs: list[dict[str, Any]] = []
    for ord_row in orders:
        if ord_row.get("id") == oco_order_id and ord_row.get("legs"):
            legs = ord_row.get("legs") or []
            break
        if ord_row.get("parent_order_id") == oco_order_id:
            legs.append(ord_row)

    if not legs:
        return None, None
    return _extract_oco_leg_ids(legs)


def place_oco_exits_for_buys(
    client: AlpacaClient,
    states: dict[str, IntradayOrderState],
    trades: list[ExecutedTrade],
    stop_losses: dict[str, float],
    partial_tp_pct: float,
    partial_tp_size: float,
    remainder_tp_mult: float,
    default_stop_loss_pct: float,
    fail_on_unprotected: bool = False,
) -> None:
    """Submit partial TP + OCO remainder orders for newly bought positions."""
    for trade in trades:
        if trade.action != "buy":
            continue

        symbol = trade.symbol
        qty = _normalize_order_qty(float(trade.shares))
        if qty <= 0:
            continue

        existing = states.get(symbol)
        if existing and (
            existing.partial_tp_order_id
            or existing.oco_order_id
            or existing.oco_stop_order_id
        ):
            continue

        stop_price = stop_losses.get(symbol, 0.0)
        if stop_price <= 0:
            stop_price = trade.price * (1.0 - default_stop_loss_pct)

        partial_tp_price = trade.price * (1.0 + partial_tp_pct)
        remainder_tp_pct = partial_tp_pct * max(remainder_tp_mult, 0.0)
        remainder_tp_price = trade.price * (1.0 + remainder_tp_pct)
        min_remainder_tp = partial_tp_price + 0.01
        if remainder_tp_price < min_remainder_tp:
            remainder_tp_price = min_remainder_tp

        qty_tp = _normalize_order_qty(qty * partial_tp_size)
        if qty_tp <= 0 and qty >= 2:
            qty_tp = 1.0
        qty_remainder = _normalize_order_qty(max(qty - qty_tp, 0.0))

        partial_tp_order_id = None
        if qty_tp > 0:
            try:
                tp_order = client.submit_limit_order(
                    symbol=symbol,
                    qty=qty_tp,
                    side="sell",
                    limit_price=partial_tp_price,
                )
                partial_tp_order_id = tp_order.get("id")
            except AlpacaError as exc:
                if fail_on_unprotected:
                    raise
                logger.warning("TP order failed for %s: %s", symbol, exc)

        oco_order_id = None
        oco_tp_order_id = None
        oco_stop_order_id = None
        if qty_remainder > 0:
            try:
                oco_order = client.submit_oco_order(
                    symbol=symbol,
                    qty=qty_remainder,
                    side="sell",
                    take_profit=remainder_tp_price,
                    stop_loss=stop_price,
                )
                oco_order_id = oco_order.get("id")
                oco_tp_order_id, oco_stop_order_id = _resolve_oco_legs(
                    client,
                    oco_order_id,
                    oco_order,
                )
                if oco_order_id and not oco_stop_order_id:
                    message = (
                        f"OCO legs unresolved for {symbol} (order={oco_order_id}); "
                        "trailing stop disabled"
                    )
                    if fail_on_unprotected:
                        raise AlpacaError(message)
                    logger.warning(message)
            except AlpacaError as exc:
                if fail_on_unprotected:
                    raise
                logger.warning("OCO order failed for %s: %s", symbol, exc)

        if fail_on_unprotected and qty_remainder > 0 and not oco_stop_order_id:
            raise AlpacaError(f"Protective stop missing for {symbol} after OCO placement")
        states[symbol] = IntradayOrderState(
            symbol=symbol,
            partial_tp_order_id=partial_tp_order_id,
            oco_order_id=oco_order_id,
            oco_tp_order_id=oco_tp_order_id,
            oco_stop_order_id=oco_stop_order_id,
            hwm=trade.price,
            remaining_qty=float(qty_remainder),
        )


def update_trailing_stops(
    client: AlpacaClient,
    states: dict[str, IntradayOrderState],
    prices: dict[str, float],
    trailing_pct: float,
) -> None:
    """Replace stop orders when a new high is made."""
    if trailing_pct <= 0:
        return

    for symbol, state in states.items():
        price = prices.get(symbol)
        if price is None or price <= 0 or not state.oco_stop_order_id:
            continue

        new_hwm, new_stop_price, should_update = compute_trailing_stop(
            state.hwm, price, trailing_pct
        )
        if not should_update:
            continue

        try:
            client.replace_order(
                state.oco_stop_order_id,
                stop_price=f"{new_stop_price:.2f}",
            )
            state.hwm = new_hwm
            logger.info(
                "Trailing stop updated for %s: hwm=%.2f stop=%.2f",
                symbol,
                new_hwm,
                new_stop_price,
            )
        except AlpacaError as exc:
            logger.warning("Trailing stop replace failed for %s: %s", symbol, exc)


def reconcile_orders(
    client: AlpacaClient,
    states: dict[str, IntradayOrderState],
    positions: dict[str, float],
    trailing_pct: float,
    fail_on_unprotected: bool = False,
) -> None:
    """Cancel/replace legs when TP or stop fills."""
    to_delete: list[str] = []
    now = datetime.now(tz=UTC)

    for symbol, state in states.items():
        prev_remaining = state.remaining_qty
        remaining_qty = _normalize_order_qty(
            float(positions.get(symbol, 0.0) or 0.0)
        )
        state.remaining_qty = float(remaining_qty or 0.0)
        state.tp_status = _order_status(client, state.partial_tp_order_id)
        state.oco_tp_status = _order_status(client, state.oco_tp_order_id)
        state.stop_status = _order_status(client, state.oco_stop_order_id)
        state.last_checked_at = now

        if state.oco_order_id and not state.oco_stop_order_id:
            oco_tp_id, oco_stop_id = _resolve_oco_legs(client, state.oco_order_id)
            if oco_stop_id:
                state.oco_tp_order_id = oco_tp_id
                state.oco_stop_order_id = oco_stop_id
                state.oco_leg_missing_count = 0
                logger.info(
                    "Resolved OCO legs for %s (stop=%s).",
                    symbol,
                    oco_stop_id,
                )
            else:
                state.oco_leg_missing_count += 1
                if state.oco_leg_missing_count >= 3 and remaining_qty > 0:
                    if trailing_pct <= 0 or state.hwm <= 0:
                        message = (
                            f"Cannot compute fallback stop for {symbol}: "
                            f"trailing_pct={trailing_pct}, hwm={state.hwm}"
                        )
                        if fail_on_unprotected:
                            raise AlpacaError(message)
                        logger.warning(message)
                    else:
                        new_stop_price = state.hwm * (1.0 - trailing_pct)
                        try:
                            stop_order = client.submit_stop_order(
                                symbol=symbol,
                                qty=remaining_qty,
                                side="sell",
                                stop_price=new_stop_price,
                            )
                            state.oco_stop_order_id = stop_order.get("id")
                            state.oco_order_id = None
                            state.oco_tp_order_id = None
                            state.stop_status = None
                            state.oco_leg_missing_count = 0
                            logger.warning(
                                "OCO legs unresolved for %s; submitted standalone stop.",
                                symbol,
                            )
                        except AlpacaError as exc:
                            if fail_on_unprotected:
                                raise
                            logger.warning(
                                "Fallback stop submit failed for %s: %s",
                                symbol,
                                exc,
                            )

        if remaining_qty <= 0:
            _cancel_orders(client, state)
            to_delete.append(symbol)
            continue

        if 0 < remaining_qty < prev_remaining:
            _cancel_oco_orders(client, state)
            if trailing_pct <= 0 or state.hwm <= 0:
                message = (
                    f"Cannot compute replacement stop for {symbol}: "
                    f"trailing_pct={trailing_pct}, hwm={state.hwm}"
                )
                if fail_on_unprotected:
                    raise AlpacaError(message)
                logger.warning(message)
            else:
                new_stop_price = state.hwm * (1.0 - trailing_pct)
                try:
                    stop_order = client.submit_stop_order(
                        symbol=symbol,
                        qty=remaining_qty,
                        side="sell",
                        stop_price=new_stop_price,
                    )
                    state.oco_stop_order_id = stop_order.get("id")
                    state.oco_order_id = None
                    state.oco_tp_order_id = None
                    state.stop_status = None
                except AlpacaError as exc:
                    if fail_on_unprotected:
                        raise
                    logger.warning("Stop re-submit failed for %s: %s", symbol, exc)
            if fail_on_unprotected and remaining_qty > 0 and not state.oco_stop_order_id:
                raise AlpacaError(
                    f"Protective stop missing for {symbol} after partial fill reconciliation"
                )
            continue

        if state.stop_status == "filled":
            if state.partial_tp_order_id:
                _cancel_order_safe(client, state.partial_tp_order_id)
            if state.oco_tp_order_id:
                _cancel_order_safe(client, state.oco_tp_order_id)
            to_delete.append(symbol)
            continue

        if state.oco_tp_status == "filled":
            if state.oco_stop_order_id:
                _cancel_order_safe(client, state.oco_stop_order_id)
            if remaining_qty <= 0:
                if state.partial_tp_order_id:
                    _cancel_order_safe(client, state.partial_tp_order_id)
                to_delete.append(symbol)
                continue

            if trailing_pct <= 0 or state.hwm <= 0:
                message = (
                    f"Cannot compute replacement stop for {symbol}: "
                    f"trailing_pct={trailing_pct}, hwm={state.hwm}"
                )
                if fail_on_unprotected:
                    raise AlpacaError(message)
                logger.warning(message)
            else:
                new_stop_price = state.hwm * (1.0 - trailing_pct)
                try:
                    stop_order = client.submit_stop_order(
                        symbol=symbol,
                        qty=remaining_qty,
                        side="sell",
                        stop_price=new_stop_price,
                    )
                    state.oco_stop_order_id = stop_order.get("id")
                    state.oco_order_id = None
                    state.oco_tp_order_id = None
                    state.stop_status = None
                except AlpacaError as exc:
                    if fail_on_unprotected:
                        raise
                    logger.warning("Stop re-submit failed for %s: %s", symbol, exc)
            if fail_on_unprotected and remaining_qty > 0 and not state.oco_stop_order_id:
                raise AlpacaError(
                    f"Protective stop missing for {symbol} after OCO take-profit fill"
                )

    for symbol in to_delete:
        states.pop(symbol, None)


def _order_status(client: AlpacaClient, order_id: str | None) -> str | None:
    if not order_id:
        return None
    try:
        order = client.get_order(order_id)
    except AlpacaError:
        return None
    return order.get("status")


def _cancel_order_safe(client: AlpacaClient, order_id: str) -> None:
    try:
        client.cancel_order(order_id)
    except AlpacaError as exc:
        logger.warning("Cancel order failed (%s): %s", order_id, exc)


def _cancel_orders(client: AlpacaClient, state: IntradayOrderState) -> None:
    if state.partial_tp_order_id:
        _cancel_order_safe(client, state.partial_tp_order_id)
    _cancel_oco_orders(client, state)


def _cancel_oco_orders(client: AlpacaClient, state: IntradayOrderState) -> None:
    if state.oco_tp_order_id:
        _cancel_order_safe(client, state.oco_tp_order_id)
    if state.oco_stop_order_id:
        _cancel_order_safe(client, state.oco_stop_order_id)
