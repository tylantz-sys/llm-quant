"""Broker-authoritative trade lifecycle state machine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class BrokerLifecycleState(StrEnum):
    ENTRY_PENDING = "ENTRY_PENDING"
    ENTRY_FILLED = "ENTRY_FILLED"
    BRACKET_ATTACHED = "BRACKET_ATTACHED"
    ACTIVE_MONITORING = "ACTIVE_MONITORING"
    EXIT_PENDING = "EXIT_PENDING"
    CLOSED = "CLOSED"


class BrokerLifecycleError(ValueError):
    """Raised when broker lifecycle actions violate the required state machine."""


@dataclass(frozen=True, slots=True)
class BrokerLifecycleSnapshot:
    symbol: str
    state: BrokerLifecycleState
    entry_order_id: str | None = None
    exit_order_id: str | None = None
    position_qty: float = 0.0
    is_short: bool = False
    has_entry_fill: bool = False
    has_exit_orders: bool = False
    has_open_position: bool = False
    is_flat: bool = False


@dataclass(frozen=True, slots=True)
class BrokerExecutionGate:
    action: str
    allowed: bool
    state: BrokerLifecycleState
    symbol: str
    reason: str


def _normalize_status(status: str | None) -> str:
    return (status or "").strip().lower()


def _parse_float(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _error(snapshot: BrokerLifecycleSnapshot, action: str, message: str) -> BrokerLifecycleError:
    return BrokerLifecycleError(
        f"{action} blocked for {snapshot.symbol} in {snapshot.state.value}: {message}"
    )


def is_terminal_order_status(status: str | None) -> bool:
    return _normalize_status(status) in {
        "filled",
        "canceled",
        "cancelled",
        "expired",
        "rejected",
        "done_for_day",
        "replaced",
    }


def is_active_order_status(status: str | None) -> bool:
    return _normalize_status(status) in {
        "new",
        "accepted",
        "accepted_for_bidding",
        "pending_new",
        "partially_filled",
        "pending_replace",
        "pending_cancel",
        "held",
        "stopped",
        "calculated",
    }


def is_filled_order_status(status: str | None) -> bool:
    return _normalize_status(status) == "filled"


def order_has_fill(
    *,
    status: str | None,
    filled_qty: float | int | str | None,
) -> bool:
    return is_filled_order_status(status) or _parse_float(filled_qty) > 0.0


def derive_lifecycle_state(
    *,
    entry_status: str | None,
    entry_filled_qty: float | int | str | None = None,
    has_exit_orders: bool = False,
    exit_statuses: list[str] | tuple[str, ...] | None = None,
    position_qty: float | int | str | None = None,
) -> BrokerLifecycleState:
    normalized_entry_status = _normalize_status(entry_status)
    normalized_exit_statuses = [_normalize_status(status) for status in (exit_statuses or [])]
    current_position_qty = _parse_float(position_qty)
    entry_is_filled = order_has_fill(
        status=normalized_entry_status,
        filled_qty=entry_filled_qty,
    )
    any_exit_active = any(is_active_order_status(status) for status in normalized_exit_statuses)
    any_exit_filled = any(is_filled_order_status(status) for status in normalized_exit_statuses)

    if any_exit_filled and abs(current_position_qty) <= 1e-9:
        return BrokerLifecycleState.CLOSED

    if any_exit_active:
        return BrokerLifecycleState.EXIT_PENDING

    if abs(current_position_qty) > 1e-9 and has_exit_orders:
        return BrokerLifecycleState.ACTIVE_MONITORING

    if has_exit_orders:
        return BrokerLifecycleState.BRACKET_ATTACHED

    if entry_is_filled:
        return BrokerLifecycleState.ENTRY_FILLED

    return BrokerLifecycleState.ENTRY_PENDING


def get_entry_submission_gate(snapshot: BrokerLifecycleSnapshot) -> BrokerExecutionGate:
    allowed = snapshot.state is BrokerLifecycleState.ENTRY_PENDING and not snapshot.has_entry_fill and snapshot.is_flat
    reason = "entry submission allowed" if allowed else "entry orders are only allowed while flat in ENTRY_PENDING"
    return BrokerExecutionGate(
        action="entry_submission",
        allowed=allowed,
        state=snapshot.state,
        symbol=snapshot.symbol,
        reason=reason,
    )


def get_bracket_attachment_gate(snapshot: BrokerLifecycleSnapshot) -> BrokerExecutionGate:
    allowed = (
        snapshot.state in {BrokerLifecycleState.ENTRY_FILLED, BrokerLifecycleState.BRACKET_ATTACHED}
        and snapshot.has_entry_fill
        and snapshot.has_open_position
        and not snapshot.is_flat
    )
    reason = "bracket attachment allowed" if allowed else "brackets require a filled entry and an open position"
    return BrokerExecutionGate(
        action="bracket_attachment",
        allowed=allowed,
        state=snapshot.state,
        symbol=snapshot.symbol,
        reason=reason,
    )


def get_monitoring_gate(snapshot: BrokerLifecycleSnapshot) -> BrokerExecutionGate:
    allowed = (
        snapshot.state in {
            BrokerLifecycleState.BRACKET_ATTACHED,
            BrokerLifecycleState.ACTIVE_MONITORING,
            BrokerLifecycleState.EXIT_PENDING,
        }
        and snapshot.has_exit_orders
    )
    reason = "monitoring allowed" if allowed else "monitoring requires broker-managed exit protection"
    return BrokerExecutionGate(
        action="monitoring",
        allowed=allowed,
        state=snapshot.state,
        symbol=snapshot.symbol,
        reason=reason,
    )


def get_exit_submission_gate(snapshot: BrokerLifecycleSnapshot) -> BrokerExecutionGate:
    allowed = snapshot.state in {
        BrokerLifecycleState.BRACKET_ATTACHED,
        BrokerLifecycleState.ACTIVE_MONITORING,
        BrokerLifecycleState.EXIT_PENDING,
    } and snapshot.has_open_position
    reason = "exit submission allowed" if allowed else "exit orders require an open position after entry workflow"
    return BrokerExecutionGate(
        action="exit_submission",
        allowed=allowed,
        state=snapshot.state,
        symbol=snapshot.symbol,
        reason=reason,
    )


def get_closed_state_gate(snapshot: BrokerLifecycleSnapshot) -> BrokerExecutionGate:
    allowed = snapshot.state is BrokerLifecycleState.CLOSED and snapshot.is_flat and not snapshot.has_open_position
    reason = "closed state confirmed" if allowed else "closed state requires broker-confirmed flat position"
    return BrokerExecutionGate(
        action="closed_state",
        allowed=allowed,
        state=snapshot.state,
        symbol=snapshot.symbol,
        reason=reason,
    )


def require_entry_submission(snapshot: BrokerLifecycleSnapshot) -> BrokerLifecycleSnapshot:
    gate = get_entry_submission_gate(snapshot)
    if not gate.allowed:
        raise _error(snapshot, gate.action, gate.reason)
    return snapshot


def require_bracket_attachment(snapshot: BrokerLifecycleSnapshot) -> BrokerLifecycleSnapshot:
    gate = get_bracket_attachment_gate(snapshot)
    if not gate.allowed:
        raise _error(snapshot, gate.action, gate.reason)
    return snapshot


def require_monitoring(snapshot: BrokerLifecycleSnapshot) -> BrokerLifecycleSnapshot:
    gate = get_monitoring_gate(snapshot)
    if not gate.allowed:
        raise _error(snapshot, gate.action, gate.reason)
    return snapshot


def require_exit_submission(snapshot: BrokerLifecycleSnapshot) -> BrokerLifecycleSnapshot:
    gate = get_exit_submission_gate(snapshot)
    if not gate.allowed:
        raise _error(snapshot, gate.action, gate.reason)
    return snapshot


def require_closed_state(snapshot: BrokerLifecycleSnapshot) -> BrokerLifecycleSnapshot:
    gate = get_closed_state_gate(snapshot)
    if not gate.allowed:
        raise _error(snapshot, gate.action, gate.reason)
    return snapshot


def advance_to_bracket_attached(
    snapshot: BrokerLifecycleSnapshot,
) -> BrokerLifecycleSnapshot:
    require_bracket_attachment(snapshot)
    return BrokerLifecycleSnapshot(
        symbol=snapshot.symbol,
        state=BrokerLifecycleState.BRACKET_ATTACHED,
        entry_order_id=snapshot.entry_order_id,
        exit_order_id=snapshot.exit_order_id,
        position_qty=snapshot.position_qty,
        is_short=snapshot.is_short,
        has_entry_fill=snapshot.has_entry_fill,
        has_exit_orders=True,
        has_open_position=snapshot.has_open_position,
        is_flat=snapshot.is_flat,
    )


def advance_to_active_monitoring(
    snapshot: BrokerLifecycleSnapshot,
) -> BrokerLifecycleSnapshot:
    require_monitoring(snapshot)
    if snapshot.state is BrokerLifecycleState.CLOSED:
        raise _error(snapshot, "advance_to_active_monitoring", "closed positions cannot be monitored")
    return BrokerLifecycleSnapshot(
        symbol=snapshot.symbol,
        state=BrokerLifecycleState.ACTIVE_MONITORING,
        entry_order_id=snapshot.entry_order_id,
        exit_order_id=snapshot.exit_order_id,
        position_qty=snapshot.position_qty,
        is_short=snapshot.is_short,
        has_entry_fill=snapshot.has_entry_fill,
        has_exit_orders=snapshot.has_exit_orders,
        has_open_position=snapshot.has_open_position,
        is_flat=snapshot.is_flat,
    )


def advance_to_exit_pending(
    snapshot: BrokerLifecycleSnapshot,
    *,
    exit_order_id: str | None = None,
) -> BrokerLifecycleSnapshot:
    require_exit_submission(snapshot)
    return BrokerLifecycleSnapshot(
        symbol=snapshot.symbol,
        state=BrokerLifecycleState.EXIT_PENDING,
        entry_order_id=snapshot.entry_order_id,
        exit_order_id=exit_order_id or snapshot.exit_order_id,
        position_qty=snapshot.position_qty,
        is_short=snapshot.is_short,
        has_entry_fill=snapshot.has_entry_fill,
        has_exit_orders=True,
        has_open_position=snapshot.has_open_position,
        is_flat=snapshot.is_flat,
    )


def advance_to_closed(
    snapshot: BrokerLifecycleSnapshot,
    *,
    exit_order_id: str | None = None,
) -> BrokerLifecycleSnapshot:
    closed_snapshot = BrokerLifecycleSnapshot(
        symbol=snapshot.symbol,
        state=BrokerLifecycleState.CLOSED,
        entry_order_id=snapshot.entry_order_id,
        exit_order_id=exit_order_id or snapshot.exit_order_id,
        position_qty=0.0,
        is_short=snapshot.is_short,
        has_entry_fill=snapshot.has_entry_fill,
        has_exit_orders=snapshot.has_exit_orders,
        has_open_position=False,
        is_flat=True,
    )
    require_closed_state(closed_snapshot)
    return closed_snapshot


def snapshot_from_broker_state(
    *,
    symbol: str,
    entry_order_id: str | None,
    entry_status: str | None,
    entry_filled_qty: float | int | str | None,
    position_qty: float | int | str | None,
    entry_intent_type: str | None = None,
    exit_order_id: str | None = None,
    has_exit_orders: bool = False,
    exit_statuses: list[str] | tuple[str, ...] | None = None,
) -> BrokerLifecycleSnapshot:
    current_position_qty = _parse_float(position_qty)
    state = derive_lifecycle_state(
        entry_status=entry_status,
        entry_filled_qty=entry_filled_qty,
        has_exit_orders=has_exit_orders,
        exit_statuses=exit_statuses,
        position_qty=current_position_qty,
    )
    normalized_intent_type = (entry_intent_type or "").strip().lower()
    is_short = normalized_intent_type == "entry_short"
    if not normalized_intent_type and abs(current_position_qty) > 1e-9:
        is_short = current_position_qty < 0
    return BrokerLifecycleSnapshot(
        symbol=symbol,
        state=state,
        entry_order_id=entry_order_id,
        exit_order_id=exit_order_id,
        position_qty=current_position_qty,
        is_short=is_short,
        has_entry_fill=order_has_fill(status=entry_status, filled_qty=entry_filled_qty),
        has_exit_orders=has_exit_orders,
        has_open_position=abs(current_position_qty) > 1e-9,
        is_flat=abs(current_position_qty) <= 1e-9,
    )


__all__ = [
    "BrokerExecutionGate",
    "BrokerLifecycleError",
    "BrokerLifecycleState",
    "BrokerLifecycleSnapshot",
    "advance_to_active_monitoring",
    "advance_to_bracket_attached",
    "advance_to_closed",
    "advance_to_exit_pending",
    "derive_lifecycle_state",
    "get_bracket_attachment_gate",
    "get_closed_state_gate",
    "get_entry_submission_gate",
    "get_exit_submission_gate",
    "get_monitoring_gate",
    "is_active_order_status",
    "is_filled_order_status",
    "is_terminal_order_status",
    "order_has_fill",
    "require_bracket_attachment",
    "require_closed_state",
    "require_entry_submission",
    "require_exit_submission",
    "require_monitoring",
    "snapshot_from_broker_state",
]