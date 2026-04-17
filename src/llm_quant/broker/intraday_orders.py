"""Manage intraday OCO exits and trailing stop updates."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import duckdb

from llm_quant.broker.alpaca import AlpacaClient, AlpacaError
from llm_quant.broker.exceptions import OCOConflictError
from llm_quant.trading.executor import ExecutedTrade

logger = logging.getLogger(__name__)


class OCOFillPrecedence(StrEnum):
    """Deterministic same-bar precedence for mutually exclusive exits."""

    TAKE_PROFIT = "take_profit"
    STOP = "stop"


def _normalize_order_qty(qty: float) -> float:
    if qty <= 0:
        return 0.0
    rounded = round(float(qty), 8)
    if math.isclose(rounded, round(rounded), rel_tol=0.0, abs_tol=1e-8):
        return float(int(round(rounded)))
    return rounded


def _is_terminal_status(status: str | None) -> bool:
    return (status or "").lower() in {"filled", "canceled", "cancelled", "expired", "rejected"}


def _is_open_status(status: str | None) -> bool:
    value = (status or "").lower()
    return bool(value) and not _is_terminal_status(value)


def _filled_qty_from_order(order: dict[str, Any] | None) -> float:
    if not order:
        return 0.0
    return _normalize_order_qty(float(order.get("filled_qty") or 0.0))


def _extract_order_id(order: dict[str, Any] | None) -> str | None:
    if not order:
        return None
    order_id = order.get("id")
    return str(order_id) if order_id else None


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
    initial_stop_price: float = 0.0
    trailing_active: bool = False
    tp1_filled_qty: float = 0.0
    replacement_lineage: dict[str, str] = field(default_factory=dict)
    protection_qty: float = 0.0
    last_resolved_exit: OCOFillPrecedence | None = None


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

    resolved_legs: list[dict[str, Any]] = []
    for ord_row in orders:
        if ord_row.get("id") == oco_order_id and ord_row.get("legs"):
            resolved_legs = ord_row.get("legs") or []
            break
        if ord_row.get("parent_order_id") == oco_order_id:
            resolved_legs.append(ord_row)

    if not resolved_legs:
        return None, None
    return _extract_oco_leg_ids(resolved_legs)


def _required_tp_qty(total_qty: float, partial_tp_size: float) -> tuple[float, float]:
    qty_tp = _normalize_order_qty(total_qty * partial_tp_size)
    if qty_tp <= 0 and total_qty >= 2:
        qty_tp = 1.0
    qty_remainder = _normalize_order_qty(max(total_qty - qty_tp, 0.0))
    return qty_tp, qty_remainder


def _is_crypto(asset_class: str) -> bool:
    return asset_class.lower() == "crypto"


def _submit_partial_tp(
    client: AlpacaClient,
    *,
    symbol: str,
    qty_tp: float,
    partial_tp_price: float,
    asset_class: str = "equity",
    time_in_force: str = "day",
) -> str:
    allow_frac = _is_crypto(asset_class)
    tif = "gtc" if allow_frac else time_in_force
    order = client.submit_limit_order(
        symbol=symbol,
        qty=qty_tp,
        side="sell",
        limit_price=partial_tp_price,
        time_in_force=tif,
        allow_fractional=allow_frac,
    )
    order_id = _extract_order_id(order)
    if not order_id:
        raise AlpacaError(f"Partial take-profit submission missing order id for {symbol}")
    return order_id


def _submit_oco_protection(
    client: AlpacaClient,
    *,
    symbol: str,
    qty_remainder: float,
    remainder_tp_price: float,
    stop_price: float,
    asset_class: str = "equity",
    time_in_force: str = "day",
) -> tuple[str | None, str | None, str | None]:
    """Submit remainder TP + stop protection.

    For crypto, Alpaca does not support OCO orders. Instead we submit two
    independent orders: a GTC limit for the TP leg and a GTC stop-limit for
    the stop leg. Both legs share the same qty — the caller must manage the
    fact that only one can ultimately fill.

    Returns (oco_order_id, oco_tp_order_id, oco_stop_order_id).
    For crypto, oco_order_id is None (no parent bracket order).
    """
    allow_frac = _is_crypto(asset_class)
    tif = "gtc" if allow_frac else time_in_force

    if allow_frac:
        # Crypto: two independent limit/stop-limit orders
        tp_resp = client.submit_limit_order(
            symbol=symbol,
            qty=qty_remainder,
            side="sell",
            limit_price=remainder_tp_price,
            time_in_force=tif,
            allow_fractional=True,
        )
        oco_tp_order_id = _extract_order_id(tp_resp)
        if not oco_tp_order_id:
            raise AlpacaError(f"Crypto TP submission missing order id for {symbol}")

        sl_limit_price = round(stop_price * 0.995, 2)
        stop_resp = client.submit_stop_limit_order(
            symbol=symbol,
            qty=qty_remainder,
            side="sell",
            stop_price=stop_price,
            limit_price=sl_limit_price,
            time_in_force=tif,
            allow_fractional=True,
        )
        oco_stop_order_id = _extract_order_id(stop_resp)
        if not oco_stop_order_id:
            raise AlpacaError(f"Crypto stop-limit submission missing order id for {symbol}")

        return None, oco_tp_order_id, oco_stop_order_id

    # Equity: native OCO
    order = client.submit_oco_order(
        symbol=symbol,
        qty=qty_remainder,
        side="sell",
        take_profit=remainder_tp_price,
        stop_loss=stop_price,
        time_in_force=tif,
    )
    oco_order_id = _extract_order_id(order)
    if not oco_order_id:
        raise AlpacaError(f"OCO submission missing order id for {symbol}")
    oco_tp_order_id, oco_stop_order_id = _resolve_oco_legs(client, oco_order_id, order)
    return oco_order_id, oco_tp_order_id, oco_stop_order_id


def _submit_stop_protection(
    client: AlpacaClient,
    *,
    symbol: str,
    qty: float,
    stop_price: float,
    asset_class: str = "equity",
    time_in_force: str = "day",
) -> str:
    allow_frac = _is_crypto(asset_class)
    tif = "gtc" if allow_frac else time_in_force
    if allow_frac:
        sl_limit_price = round(stop_price * 0.995, 2)
        stop_order = client.submit_stop_limit_order(
            symbol=symbol,
            qty=qty,
            side="sell",
            stop_price=stop_price,
            limit_price=sl_limit_price,
            time_in_force=tif,
            allow_fractional=True,
        )
    else:
        stop_order = client.submit_stop_order(
            symbol=symbol,
            qty=qty,
            side="sell",
            stop_price=stop_price,
            time_in_force=tif,
        )
    order_id = _extract_order_id(stop_order)
    if not order_id:
        raise AlpacaError(f"Stop submission missing order id for {symbol}")
    return order_id


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
    fill_prices: dict[str, float] | None = None,
    asset_class_map: dict[str, str] | None = None,
) -> None:
    """Submit partial TP + OCO remainder orders for newly bought positions."""
    for trade in trades:
        if trade.action != "buy":
            continue

        symbol = trade.symbol
        asset_class = (asset_class_map or {}).get(symbol, "equity")
        qty = _normalize_order_qty(float(trade.shares))
        if qty <= 0:
            continue

        # Use actual broker fill price when available (H7: signal price may differ from fill)
        effective_price = (fill_prices or {}).get(symbol, trade.price)
        if effective_price <= 0:
            effective_price = trade.price

        stop_price = stop_losses.get(symbol, 0.0)
        if stop_price <= 0:
            stop_price = effective_price * (1.0 - default_stop_loss_pct)
        if stop_price <= 0:
            raise AlpacaError(f"Missing stop price for {symbol} after entry fill")

        partial_tp_price = effective_price * (1.0 + partial_tp_pct)
        remainder_tp_pct = partial_tp_pct * max(remainder_tp_mult, 0.0)
        remainder_tp_price = effective_price * (1.0 + remainder_tp_pct)
        min_remainder_tp = partial_tp_price + 0.01
        if remainder_tp_price < min_remainder_tp:
            remainder_tp_price = min_remainder_tp

        qty_tp, qty_remainder = _required_tp_qty(qty, partial_tp_size)
        if qty_tp <= 0:
            raise AlpacaError(f"TP1 quantity resolved to zero for {symbol}")

        partial_tp_order_id = _submit_partial_tp(
            client,
            symbol=symbol,
            qty_tp=qty_tp,
            partial_tp_price=partial_tp_price,
            asset_class=asset_class,
        )

        oco_order_id = None
        oco_tp_order_id = None
        oco_stop_order_id = None
        if qty_remainder > 0:
            oco_order_id, oco_tp_order_id, oco_stop_order_id = _submit_oco_protection(
                client,
                symbol=symbol,
                qty_remainder=qty_remainder,
                remainder_tp_price=remainder_tp_price,
                stop_price=stop_price,
                asset_class=asset_class,
            )
            if fail_on_unprotected and not oco_stop_order_id:
                raise AlpacaError(f"Protective stop missing for {symbol} after OCO placement")
        else:
            oco_stop_order_id = _submit_stop_protection(
                client,
                symbol=symbol,
                qty=qty,
                stop_price=stop_price,
                asset_class=asset_class,
            )

        states[symbol] = IntradayOrderState(
            symbol=symbol,
            partial_tp_order_id=partial_tp_order_id,
            oco_order_id=oco_order_id,
            oco_tp_order_id=oco_tp_order_id,
            oco_stop_order_id=oco_stop_order_id,
            hwm=effective_price,
            remaining_qty=float(qty_remainder if qty_remainder > 0 else qty),
            initial_stop_price=float(stop_price),
        )


def update_trailing_stops(
    client: AlpacaClient,
    states: dict[str, IntradayOrderState],
    prices: dict[str, float],
    trailing_pct: float,
) -> None:
    """Replace stop orders when a new high is made after TP1 fill."""
    if trailing_pct <= 0:
        return

    for symbol, state in states.items():
        price = prices.get(symbol)
        if price is None or price <= 0 or not state.oco_stop_order_id:
            continue
        if state.tp_status != "filled":
            continue
        if state.initial_stop_price <= 0:
            raise AlpacaError(f"Missing initial stop price for trailing stop update on {symbol}")

        state.trailing_active = True
        new_hwm = max(float(state.hwm or 0.0), float(price))
        state.hwm = new_hwm
        new_stop_price = max(new_hwm * (1.0 - trailing_pct), state.initial_stop_price)

        try:
            replacement = client.replace_order(
                state.oco_stop_order_id,
                stop_price=f"{new_stop_price:.2f}",
            )
            new_order_id = _extract_order_id(replacement)
            old_order_id = state.oco_stop_order_id
            if new_order_id and new_order_id != old_order_id:
                state.replacement_lineage[old_order_id] = new_order_id
                state.oco_stop_order_id = new_order_id
            state.stop_status = str(replacement.get("status") or state.stop_status or "")
            logger.info(
                "Trailing stop updated for %s: hwm=%.2f stop=%.2f",
                symbol,
                new_hwm,
                new_stop_price,
            )
        except AlpacaError as exc:
            logger.warning("Trailing stop replace failed for %s: %s", symbol, exc)


def _resolve_exit_precedence(
    *,
    symbol: str,
    tp_status: str | None,
    stop_status: str | None,
) -> OCOFillPrecedence | None:
    """Resolve mutually exclusive exits deterministically.

    Same-bar dual-trigger rule: STOP wins over TAKE_PROFIT. This preserves the
    most protective outcome and must be applied before any cancel side effects.
    """
    tp_filled = (tp_status or "").lower() == "filled"
    stop_filled = (stop_status or "").lower() == "filled"
    if tp_filled and stop_filled:
        logger.error(
            "oco_conflict_detected symbol=%s tp_status=%s stop_status=%s precedence=%s",
            symbol,
            tp_status,
            stop_status,
            OCOFillPrecedence.STOP.value,
        )
        return OCOFillPrecedence.STOP
    if stop_filled:
        return OCOFillPrecedence.STOP
    if tp_filled:
        return OCOFillPrecedence.TAKE_PROFIT
    return None


def _order_is_filled(order: dict[str, Any] | None, status: str | None) -> bool:
    if (status or "").lower() == "filled":
        return True
    return (str((order or {}).get("status") or "").lower()) == "filled"


def _cancel_order_safe(
    client: AlpacaClient,
    order_id: str | None,
    *,
    order: dict[str, Any] | None = None,
    status: str | None = None,
) -> bool:
    if not order_id:
        return False
    if _order_is_filled(order, status):
        return False
    if _is_terminal_status(status):
        return False
    try:
        client.cancel_order(order_id)
        return True
    except AlpacaError as exc:
        logger.warning("Cancel order failed (%s): %s", order_id, exc)
        return False


def _enforce_protection_invariants(
    *,
    symbol: str,
    remaining_qty: float,
    stop_order_id: str | None,
    protection_qty: float,
    fail_on_unprotected: bool,
) -> None:
    if remaining_qty > 0 and not stop_order_id:
        if fail_on_unprotected:
            raise OCOConflictError(f"UNPROTECTED_REMAINDER:{symbol}")
        logger.error(
            "unprotected_remainder_detected symbol=%s remaining_qty=%s protection_qty=%s",
            symbol,
            remaining_qty,
            protection_qty,
        )
    if remaining_qty > 0 and stop_order_id and protection_qty <= 0:
        raise OCOConflictError(f"INVALID_PROTECTION_QTY:{symbol}")


def reconcile_orders(
    client: AlpacaClient,
    states: dict[str, IntradayOrderState],
    positions: dict[str, float],
    trailing_pct: float,
    fail_on_unprotected: bool = False,
    *,
    partial_tp_size: float = 0.50,
    remainder_tp_mult: float = 2.0,
    partial_tp_pct: float | None = None,
    asset_class_map: dict[str, str] | None = None,
) -> None:
    """Repair OCO lifecycle drift using broker-authoritative state."""
    if partial_tp_pct is None:
        partial_tp_pct = 0.02

    to_delete: list[str] = []
    now = datetime.now(tz=UTC)

    for symbol, state in states.items():
        asset_class = (asset_class_map or {}).get(symbol, "equity")
        remaining_qty = _normalize_order_qty(float(positions.get(symbol, 0.0) or 0.0))
        state.remaining_qty = float(remaining_qty or 0.0)
        partial_order = None
        oco_tp_order = None
        stop_order = None
        state.protection_qty = float(remaining_qty or 0.0)

        if state.partial_tp_order_id:
            try:
                partial_order = client.get_order(state.partial_tp_order_id)
                state.tp_status = partial_order.get("status")
                state.tp1_filled_qty = _filled_qty_from_order(partial_order)
            except AlpacaError:
                state.tp_status = None

        if state.oco_tp_order_id:
            try:
                oco_tp_order = client.get_order(state.oco_tp_order_id)
                state.oco_tp_status = oco_tp_order.get("status")
            except AlpacaError:
                state.oco_tp_status = None

        if state.oco_stop_order_id:
            try:
                stop_order = client.get_order(state.oco_stop_order_id)
                state.stop_status = stop_order.get("status")
            except AlpacaError:
                state.stop_status = None

        partial_filled_before_precedence = state.tp_status == "filled"

        precedence = _resolve_exit_precedence(
            symbol=symbol,
            tp_status=state.oco_tp_status,
            stop_status=state.stop_status,
        )
        state.last_resolved_exit = precedence
        if precedence is OCOFillPrecedence.STOP:
            state.stop_status = "filled"
            if _cancel_order_safe(
                client,
                state.oco_tp_order_id,
                order=oco_tp_order,
                status=state.oco_tp_status,
            ):
                state.oco_tp_status = "canceled"
            if _cancel_order_safe(
                client,
                state.partial_tp_order_id,
                order=partial_order,
                status="filled" if partial_filled_before_precedence else state.tp_status,
            ):
                state.tp_status = "canceled"
        elif precedence is OCOFillPrecedence.TAKE_PROFIT:
            state.oco_tp_status = "filled"
            if _cancel_order_safe(
                client,
                state.oco_stop_order_id,
                order=stop_order,
                status=state.stop_status,
            ):
                state.stop_status = "canceled"

        state.last_checked_at = now
        state.trailing_active = state.tp_status == "filled"

        if remaining_qty <= 0:
            _cancel_orders(client, state)
            to_delete.append(symbol)
            continue

        if state.oco_order_id and (not state.oco_tp_order_id or not state.oco_stop_order_id):
            oco_tp_id, oco_stop_id = _resolve_oco_legs(client, state.oco_order_id)
            if oco_tp_id:
                state.oco_tp_order_id = oco_tp_id
            if oco_stop_id:
                state.oco_stop_order_id = oco_stop_id

        replacement_stop_price = state.initial_stop_price
        if state.tp_status == "filled":
            if trailing_pct > 0 and state.hwm > 0:
                replacement_stop_price = max(
                    state.hwm * (1.0 - trailing_pct),
                    state.initial_stop_price,
                )
            elif state.initial_stop_price <= 0:
                raise AlpacaError(
                    f"Cannot compute replacement stop for {symbol}: trailing_pct={trailing_pct}, hwm={state.hwm}"
                )
        elif replacement_stop_price <= 0:
            if state.hwm > 0:
                replacement_stop_price = state.hwm * (1.0 - max(partial_tp_pct, 0.0))
            elif (
                state.partial_tp_order_id
                or state.oco_order_id
                or state.oco_tp_order_id
                or state.oco_stop_order_id
            ):
                raise AlpacaError(
                    f"Cannot compute replacement stop for {symbol}: trailing_pct={trailing_pct}, hwm={state.hwm}"
                )
            else:
                raise AlpacaError(f"Missing initial stop price for {symbol} during reconciliation")

        stop_is_terminal = _is_terminal_status(state.stop_status)
        stop_is_filled = (state.stop_status or "").lower() == "filled"

        if (
            state.tp_status == "filled"
            and remaining_qty > 0
            and not stop_is_filled
            and not state.oco_stop_order_id
            and (hasattr(client, "submit_stop_order") or hasattr(client, "submit_stop_limit_order"))
        ):
            state.oco_stop_order_id = _submit_stop_protection(
                client,
                symbol=symbol,
                qty=remaining_qty,
                stop_price=replacement_stop_price,
                asset_class=asset_class,
            )
            state.protection_qty = float(remaining_qty)
            state.stop_status = None
            logger.warning("Repaired missing stop after TP1 fill for %s.", symbol)

        should_restore_tp1 = (
            (not state.partial_tp_order_id or _is_terminal_status(state.tp_status))
            and state.tp_status != "filled"
            and state.oco_tp_status != "filled"
            and hasattr(client, "submit_limit_order")
        )
        if should_restore_tp1:
            expected_tp_qty, _ = _required_tp_qty(
                remaining_qty + state.tp1_filled_qty,
                partial_tp_size,
            )
            outstanding_tp_qty = max(expected_tp_qty - state.tp1_filled_qty, 0.0)
            if outstanding_tp_qty > 0:
                if state.hwm <= 0:
                    raise AlpacaError(f"Missing broker-confirmed entry/high water mark for {symbol}")
                tp_price = state.hwm * (1.0 + partial_tp_pct)
                state.partial_tp_order_id = _submit_partial_tp(
                    client,
                    symbol=symbol,
                    qty_tp=outstanding_tp_qty,
                    partial_tp_price=tp_price,
                    asset_class=asset_class,
                )
                state.tp_status = None
                logger.warning("Re-submitted missing TP1 leg for %s.", symbol)

        if state.tp_status == "filled":
            expected_remainder_qty = remaining_qty
        else:
            _, expected_remainder_qty = _required_tp_qty(
                remaining_qty + state.tp1_filled_qty,
                partial_tp_size,
            )

        stop_missing = (not state.oco_stop_order_id or stop_is_terminal) and not stop_is_filled
        tp_leg_missing = expected_remainder_qty > 0 and (
            not state.oco_tp_order_id or _is_terminal_status(state.oco_tp_status)
        )

        if (
            expected_remainder_qty > 0
            and not stop_is_filled
            and (stop_missing or tp_leg_missing)
        ):
            if state.hwm <= 0:
                raise AlpacaError(f"Missing broker-confirmed price anchor for {symbol}")
            remainder_tp_price = state.hwm * (1.0 + (partial_tp_pct * max(remainder_tp_mult, 0.0)))
            min_remainder_tp = state.hwm * (1.0 + partial_tp_pct) + 0.01
            if remainder_tp_price < min_remainder_tp:
                remainder_tp_price = min_remainder_tp

            if _cancel_order_safe(
                client,
                state.oco_tp_order_id,
                order=oco_tp_order,
                status=state.oco_tp_status,
            ):
                state.oco_tp_status = "canceled"
            if _cancel_order_safe(
                client,
                state.oco_stop_order_id,
                order=stop_order,
                status=state.stop_status,
            ):
                state.stop_status = "canceled"

            if hasattr(client, "submit_oco_order"):
                try:
                    state.oco_order_id, state.oco_tp_order_id, state.oco_stop_order_id = (
                        _submit_oco_protection(
                            client,
                            symbol=symbol,
                            qty_remainder=expected_remainder_qty,
                            remainder_tp_price=remainder_tp_price,
                            stop_price=replacement_stop_price,
                            asset_class=asset_class,
                        )
                    )
                    state.protection_qty = float(expected_remainder_qty)
                    state.oco_tp_status = None
                    state.stop_status = None
                    logger.warning("Repaired missing/broken OCO protection for %s.", symbol)
                except AlpacaError:
                    state.oco_order_id = None
                    state.oco_tp_order_id = None
                    state.oco_stop_order_id = _submit_stop_protection(
                        client,
                        symbol=symbol,
                        qty=expected_remainder_qty,
                        stop_price=replacement_stop_price,
                        asset_class=asset_class,
                    )
                    state.protection_qty = float(expected_remainder_qty)
                    state.stop_status = None
                    logger.warning("Fell back to standalone stop protection for %s.", symbol)
            else:
                state.oco_order_id = None
                state.oco_tp_order_id = None
                state.oco_stop_order_id = _submit_stop_protection(
                    client,
                    symbol=symbol,
                    qty=expected_remainder_qty,
                    stop_price=replacement_stop_price,
                    asset_class=asset_class,
                )
                state.protection_qty = float(expected_remainder_qty)
                state.stop_status = None
                logger.warning("Fell back to standalone stop protection for %s.", symbol)

        _enforce_protection_invariants(
            symbol=symbol,
            remaining_qty=remaining_qty,
            stop_order_id=state.oco_stop_order_id,
            protection_qty=state.protection_qty,
            fail_on_unprotected=fail_on_unprotected,
        )

        if state.stop_status == "filled":
            if _cancel_order_safe(
                client,
                state.partial_tp_order_id,
                order=partial_order,
                status=state.tp_status,
            ):
                state.tp_status = "canceled"
            if _cancel_order_safe(
                client,
                state.oco_tp_order_id,
                order=oco_tp_order,
                status=state.oco_tp_status,
            ):
                state.oco_tp_status = "canceled"
            to_delete.append(symbol)

    for symbol in to_delete:
        states.pop(symbol, None)


def _cancel_orders(client: AlpacaClient, state: IntradayOrderState) -> None:
    if state.partial_tp_order_id:
        _cancel_order_safe(client, state.partial_tp_order_id, status=state.tp_status)
    _cancel_oco_orders(client, state)


def _cancel_oco_orders(client: AlpacaClient, state: IntradayOrderState) -> None:
    if state.oco_tp_order_id:
        _cancel_order_safe(client, state.oco_tp_order_id, status=state.oco_tp_status)
    if state.oco_stop_order_id:
        _cancel_order_safe(client, state.oco_stop_order_id, status=state.stop_status)
