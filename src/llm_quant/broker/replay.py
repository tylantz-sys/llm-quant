"""Deterministic broker-simulation replay harness.

The replay harness validates broker and reconciliation logic against
historical bars without routing through any live broker code paths.

Design goals
------------
- Deterministic and replayable: identical inputs produce identical outputs.
- No live broker dependency: this module never uses AlpacaClient.
- Broker-authoritative portfolio mutation: portfolio state changes only
  through ``reconcile_broker_orders``-compatible reconciliation.
- Auditable: every fill, status transition, and mismatch is preserved in
  structured output.

The harness models only a narrow deterministic execution universe. It is
intentionally *not* realistic and does not simulate an order book,
external fills, or non-deterministic latency.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, cast

import duckdb

from llm_quant.broker.event_ledger import ledger_ordering_digest
from llm_quant.broker.numeric import (
    DriftAccount,
    DriftBounds,
    accumulate_cash_drift,
    accumulate_price_drift,
    accumulate_qty_drift,
    default_drift_bounds,
    floats_equal,
    is_effectively_zero,
    round_cash,
    round_price,
    round_qty,
)
from llm_quant.broker.parity import (
    ParityDiff,
    ParityDiffCategory,
    ParityMode,
    ParityState,
    ParityValidationResult,
    snapshot_parity_state,
    validate_parity,
)
from llm_quant.broker.reconciliation import (
    BrokerFillEvent,
    ReconciliationResult,
    persist_submitted_orders,
    reconcile_broker_orders,
)
from llm_quant.trading.portfolio import Portfolio

logger = logging.getLogger(__name__)


class ReplayEventType(StrEnum):
    ON_BAR = "on_bar"
    ON_SIGNAL = "on_signal"
    ON_ORDER_INTENT = "on_order_intent"
    ON_ORDER_REJECTED = "on_order_rejected"
    ON_FILL_EVENT = "on_fill_event"
    ON_FORCED_LIQUIDATION = "on_forced_liquidation"
    ON_RECONCILE = "on_reconcile"
    ON_INVARIANT_FAILURE = "on_invariant_failure"


class SimulatedOrderState(StrEnum):
    NEW = "new"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    REPLACED = "replaced"


@dataclass(frozen=True, slots=True)
class HistoricalBar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            object.__setattr__(self, "timestamp", self.timestamp.replace(tzinfo=UTC))
        if self.open <= 0 or self.high <= 0 or self.low <= 0 or self.close <= 0:
            raise ValueError("Historical bars require positive OHLC values")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("Bar high must bound open/close/low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("Bar low must bound open/close/high")
        if self.volume < 0:
            raise ValueError("Bar volume must be non-negative")


@dataclass(frozen=True, slots=True)
class ReplaySignal:
    symbol: str
    action: str
    qty: float
    strategy_id: str
    chain_id: str
    timestamp: datetime
    order_type: str = "market"
    limit_price: float | None = None
    stop_price: float | None = None
    tif: str = "day"
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ReplayBrokerOrderIntent:
    symbol: str
    side: str
    qty: float
    order_type: str
    strategy_id: str
    chain_id: str
    timestamp: datetime
    tif: str = "day"
    limit_price: float | None = None
    stop_price: float | None = None
    intent_type: str = "entry"
    order_id: str | None = None
    replace_order_id: str | None = None
    cancel_order_id: str | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            object.__setattr__(self, "timestamp", self.timestamp.replace(tzinfo=UTC))
        if self.qty <= 0:
            raise ValueError("ReplayBrokerOrderIntent qty must be positive")
        if self.side not in {"buy", "sell"}:
            raise ValueError("ReplayBrokerOrderIntent side must be buy/sell")
        if self.order_type not in {"market", "limit", "stop", "cancel", "replace"}:
            raise ValueError("Unsupported replay order_type")


@dataclass(slots=True)
class SimulatedFill:
    order_id: str
    symbol: str
    side: str
    fill_qty: float
    fill_price: float
    fill_time: datetime
    intent_type: str
    parent_order_id: str | None = None
    exit_reason: str | None = None
    chain_id: str | None = None
    strategy_id: str | None = None
    is_forced_liquidation: bool = False
    commission: float = 0.0


@dataclass(slots=True)
class SimulatedOrder:
    order_id: str
    symbol: str
    side: str
    qty: float
    order_type: str
    strategy_id: str
    chain_id: str
    intent_type: str
    tif: str
    created_at: datetime
    status: SimulatedOrderState = SimulatedOrderState.NEW
    limit_price: float | None = None
    stop_price: float | None = None
    parent_order_id: str | None = None
    replace_order_id: str | None = None
    replaced_by_order_id: str | None = None
    cancel_requested_at: datetime | None = None
    accepted_at: datetime | None = None
    updated_at: datetime | None = None
    filled_qty: float = 0.0
    remaining_qty: float = 0.0
    avg_fill_price: float | None = None
    fill_count: int = 0
    reported_filled_qty: float = 0.0
    reported_avg_fill_price: float | None = None
    rejection_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.remaining_qty <= 0.0:
            self.remaining_qty = float(self.qty)


@dataclass(frozen=True, slots=True)
class ReplayFillModelConfig:
    market_fill_delay_bars: int = 1
    volume_participation_rate: float = 0.25
    min_fill_chunk: float = 1.0
    slippage_bps: float = 0.0

    def fill_capacity(self, volume: float) -> float:
        if volume <= 0:
            return 0.0
        base = volume * self.volume_participation_rate
        return max(self.min_fill_chunk, base)


@dataclass(frozen=True, slots=True)
class ReplayValidationSnapshot:
    label: str
    cash: float
    positions: dict[str, float]
    open_orders: dict[str, str]
    realized_pnl: float
    unrealized_pnl: float
    cumulative_exposure: float = 0.0


@dataclass(frozen=True, slots=True)
class ReplayMismatch:
    category: str
    key: str
    expected: Any
    actual: Any
    detail: str


@dataclass(frozen=True, slots=True)
class ReplayValidationResult:
    expected: ReplayValidationSnapshot | None
    actual: ReplayValidationSnapshot
    mismatches: list[ReplayMismatch]
    invariant_failures: list[str]
    parity: ParityValidationResult | None = None
    drift_account: DriftAccount = DriftAccount()
    drift_bounds: DriftBounds = DriftBounds(quantity=0.0, cash=0.0, price=0.0)

    @property
    def ok(self) -> bool:
        return (
            not self.mismatches
            and not self.invariant_failures
            and (self.parity is None or self.parity.ok)
            and self.drift_account.within_bounds(self.drift_bounds)
        )


@dataclass(slots=True)
class ReplayEvent:
    event_type: ReplayEventType
    timestamp: datetime
    payload: dict[str, Any]


@dataclass(slots=True)
class ReplayResult:
    validation: ReplayValidationResult
    reconciliation: list[ReconciliationResult]
    events: list[ReplayEvent]
    fills: list[SimulatedFill]
    final_snapshot: ReplayValidationSnapshot
    order_history: dict[str, SimulatedOrder]
    design_summary: str
    component_diagram: str
    data_flow_diagram: str
    failure_modes_exposed: list[str]
    hard_stop_conditions: list[str]


class ReplayStrategy(Protocol):
    def on_bar(
        self,
        bar: HistoricalBar,
        *,
        portfolio: Portfolio,
        open_orders: dict[str, SimulatedOrder],
    ) -> list[ReplaySignal] | None: ...


class ReplayBrokerClient:
    """Broker-like adapter exposing ``get_order`` for reconciliation."""

    def __init__(self, simulator: "DeterministicBrokerSimulator") -> None:
        self._simulator = simulator

    def get_order(self, order_id: str, nested: bool = True) -> dict[str, Any]:
        return self._simulator.order_record(order_id)


@dataclass(frozen=True, slots=True)
class ReplayPositionLimitConfig:
    max_positions: int | None = None


def _signed_fill_qty(side: str, qty: float) -> float:
    return qty if side == "buy" else -qty


def _aggregate_position_qty_from_broker_fills(fills: list[BrokerFillEvent]) -> dict[str, float]:
    qtys: dict[str, float] = defaultdict(float)
    for fill in fills:
        qtys[fill.symbol] = round_qty(qtys[fill.symbol] + _signed_fill_qty(fill.side, fill.fill_qty))
    return {symbol: qty for symbol, qty in qtys.items() if not is_effectively_zero(qty)}


def _aggregate_cash_delta_from_broker_fills(fills: list[BrokerFillEvent]) -> tuple[float, DriftAccount]:
    cash_delta = 0.0
    drift = DriftAccount()
    for fill in fills:
        raw_gross_notional = fill.fill_qty * fill.fill_price
        rounded_gross_notional = round_price(raw_gross_notional)
        drift = accumulate_price_drift(
            drift,
            raw=raw_gross_notional,
            rounded=rounded_gross_notional,
        )
        signed_notional = -rounded_gross_notional if fill.side == "buy" else rounded_gross_notional
        raw_cash_delta = cash_delta + signed_notional - fill.commission
        rounded_cash_delta = round_cash(raw_cash_delta)
        drift = accumulate_cash_drift(drift, raw=raw_cash_delta, rounded=rounded_cash_delta)
        cash_delta = rounded_cash_delta
    return cash_delta, drift


def _event_signature(event: ReplayEvent) -> str:
    payload_items = ",".join(
        f"{key}={event.payload[key]}"
        for key in sorted(event.payload)
    )
    return f"{event.event_type}|{event.timestamp.isoformat()}|{payload_items}"


def _position_exposure_delta(positions: dict[str, float]) -> float:
    return round_qty(sum(abs(qty) for qty in positions.values()))


def _build_parity_states_from_events(
    events: list[ReplayEvent],
    snapshots: list[ReplayValidationSnapshot],
) -> list[ParityState]:
    states: list[ParityState] = []
    event_keys: list[str] = []
    prior_exposure = 0.0
    cumulative_exposure = 0.0
    snapshot_by_timestamp = {snapshot.label: snapshot for snapshot in snapshots}
    for event in events:
        event_keys.append(_event_signature(event))
        label = event.payload.get("snapshot_label")
        if isinstance(label, str) and label in snapshot_by_timestamp:
            snapshot = snapshot_by_timestamp[label]
            exposure = _position_exposure_delta(snapshot.positions)
            exposure_delta = round_qty(exposure - prior_exposure)
            cumulative_exposure = round_qty(cumulative_exposure + abs(exposure_delta))
            state = snapshot_parity_state(
                positions=snapshot.positions,
                cash=snapshot.cash,
                orders={},
                event_keys=tuple(event_keys),
                exposure_delta=exposure_delta,
            )
            states.append(
                ParityState(
                    positions=state.positions,
                    cash=state.cash,
                    order_states=state.order_states,
                    event_keys=state.event_keys,
                    exposure_delta=state.exposure_delta,
                    cumulative_exposure=snapshot.cumulative_exposure or cumulative_exposure,
                    state_digest=state.state_digest,
                )
            )
            prior_exposure = exposure
    return states


class DeterministicBrokerSimulator:
    """Deterministic bar-based broker simulator.

    Fill rules
    ----------
    - Market orders fill on the next eligible bar open plus deterministic slippage.
    - Buy limit orders fill only when ``bar.low <= limit_price``.
    - Sell limit orders fill only when ``bar.high >= limit_price``.
    - Buy stop orders trigger only when ``bar.high >= stop_price`` and then fill
      at ``max(stop_price, bar.open)`` plus slippage.
    - Sell stop orders trigger only when ``bar.low <= stop_price`` and then fill
      at ``min(stop_price, bar.open)`` minus slippage.
    - Partial fills are capped by a deterministic per-bar capacity derived from
      ``volume_participation_rate * bar.volume`` with a minimum fill chunk.
    - Cancels and replacements are explicit terminal transitions.
    """

    def __init__(
        self,
        config: ReplayFillModelConfig | None = None,
        *,
        position_limit_config: ReplayPositionLimitConfig | None = None,
    ) -> None:
        self.config = config or ReplayFillModelConfig()
        self.position_limit_config = position_limit_config or ReplayPositionLimitConfig()
        self.orders: dict[str, SimulatedOrder] = {}
        self.fill_history: list[SimulatedFill] = []
        self._sequence = 0

    def _next_order_id(self) -> str:
        self._sequence += 1
        return f"replay-order-{self._sequence:06d}"

    def submit_intent(self, intent: ReplayBrokerOrderIntent) -> SimulatedOrder:
        if intent.order_type == "cancel":
            return self.cancel_order(intent.cancel_order_id, timestamp=intent.timestamp)
        if intent.order_type == "replace":
            return self.replace_order(intent)

        order_id = intent.order_id or self._next_order_id()
        metadata = dict(intent.metadata or {})
        order = SimulatedOrder(
            order_id=order_id,
            symbol=intent.symbol,
            side=intent.side,
            qty=float(intent.qty),
            order_type=intent.order_type,
            strategy_id=intent.strategy_id,
            chain_id=intent.chain_id,
            intent_type=intent.intent_type,
            tif=intent.tif,
            created_at=intent.timestamp,
            status=SimulatedOrderState.NEW,
            limit_price=intent.limit_price,
            stop_price=intent.stop_price,
            parent_order_id=metadata.get("parent_order_id"),
            accepted_at=None,
            updated_at=intent.timestamp,
            remaining_qty=float(intent.qty),
            metadata=metadata,
        )
        self._apply_acceptance_decision(order)
        self.orders[order_id] = order
        return order

    def cancel_order(self, order_id: str | None, *, timestamp: datetime) -> SimulatedOrder:
        if not order_id or order_id not in self.orders:
            raise ValueError("Cannot cancel unknown replay order")
        order = self.orders[order_id]
        if order.status in {
            SimulatedOrderState.FILLED,
            SimulatedOrderState.CANCELED,
            SimulatedOrderState.REJECTED,
            SimulatedOrderState.REPLACED,
        }:
            return order
        order.status = SimulatedOrderState.CANCELED
        order.cancel_requested_at = timestamp
        order.updated_at = timestamp
        return order

    def replace_order(self, intent: ReplayBrokerOrderIntent) -> SimulatedOrder:
        if not intent.replace_order_id or intent.replace_order_id not in self.orders:
            raise ValueError("Replacement requires an existing replay order")
        prior = self.orders[intent.replace_order_id]
        if prior.status not in {
            SimulatedOrderState.ACCEPTED,
            SimulatedOrderState.PARTIALLY_FILLED,
        }:
            raise ValueError("Only active replay orders may be replaced")

        prior.status = SimulatedOrderState.REPLACED
        prior.updated_at = intent.timestamp
        replacement = self.submit_intent(
            ReplayBrokerOrderIntent(
                symbol=intent.symbol,
                side=intent.side,
                qty=intent.qty,
                order_type="limit" if intent.limit_price is not None else "stop",
                strategy_id=intent.strategy_id,
                chain_id=intent.chain_id,
                timestamp=intent.timestamp,
                tif=intent.tif,
                limit_price=intent.limit_price,
                stop_price=intent.stop_price,
                intent_type=intent.intent_type,
                metadata=intent.metadata,
            )
        )
        replacement.parent_order_id = prior.order_id
        prior.replaced_by_order_id = replacement.order_id
        replacement.replace_order_id = prior.order_id
        return replacement

    def process_bar(self, bar: HistoricalBar) -> list[SimulatedFill]:
        fills: list[SimulatedFill] = []
        for order in sorted(self.orders.values(), key=lambda item: item.order_id):
            if order.symbol != bar.symbol:
                continue
            if order.status not in {
                SimulatedOrderState.ACCEPTED,
                SimulatedOrderState.PARTIALLY_FILLED,
            }:
                continue

            if not self._eligible_for_bar(order, bar):
                continue

            fill_qty, fill_price = self._resolve_fill(order, bar)
            if fill_qty <= 0 or fill_price <= 0:
                continue

            self._apply_fill(order, fill_qty=fill_qty, fill_price=fill_price, fill_time=bar.timestamp)
            fill = self.fill_history[-1]
            fills.append(fill)
        return fills

    def _eligible_for_bar(self, order: SimulatedOrder, bar: HistoricalBar) -> bool:
        min_timestamp = order.created_at + timedelta(minutes=self.config.market_fill_delay_bars)
        return bar.timestamp >= min_timestamp

    def _apply_acceptance_decision(self, order: SimulatedOrder) -> None:
        rejection_reason = self._position_limit_rejection_reason(order)
        if rejection_reason is not None:
            order.status = SimulatedOrderState.REJECTED
            order.rejection_reason = rejection_reason
            order.updated_at = order.created_at
            logger.info(
                "replay_order_rejected order_id=%s symbol=%s side=%s reason=%s",
                order.order_id,
                order.symbol,
                order.side,
                rejection_reason,
            )
            return
        order.status = SimulatedOrderState.ACCEPTED
        order.accepted_at = order.created_at
        order.updated_at = order.created_at

    def _position_limit_rejection_reason(self, order: SimulatedOrder) -> str | None:
        max_positions = self.position_limit_config.max_positions
        if max_positions is None or order.intent_type not in {"entry", "entry_short"}:
            return None
        projected_symbols = self._projected_open_symbols_with_order(order)
        if len(projected_symbols) > max_positions:
            return "POSITION_LIMIT_EXCEEDED"
        return None

    def _projected_open_symbols_with_order(self, order: SimulatedOrder) -> set[str]:
        symbols = self._active_or_filled_entry_symbols()
        symbols.add(order.symbol)
        return symbols

    def _active_or_filled_entry_symbols(self) -> set[str]:
        symbols: set[str] = set()
        for existing in self.orders.values():
            if existing.intent_type not in {"entry", "entry_short"}:
                continue
            if existing.status in {
                SimulatedOrderState.ACCEPTED,
                SimulatedOrderState.PARTIALLY_FILLED,
                SimulatedOrderState.FILLED,
            }:
                if existing.filled_qty > 0 or existing.status != SimulatedOrderState.FILLED:
                    symbols.add(existing.symbol)
        return symbols

    def _apply_fill(
        self,
        order: SimulatedOrder,
        *,
        fill_qty: float,
        fill_price: float,
        fill_time: datetime,
        is_forced_liquidation: bool = False,
    ) -> None:
        if fill_qty > order.remaining_qty and not floats_equal(fill_qty, order.remaining_qty):
            raise RuntimeError(f"Replay overfill detected for order {order.order_id}")
        prior_filled_qty = order.filled_qty
        raw_filled_qty = order.filled_qty + fill_qty
        order.filled_qty = round_qty(raw_filled_qty)
        raw_remaining_qty = order.remaining_qty - fill_qty
        order.remaining_qty = round_qty(raw_remaining_qty)
        if order.remaining_qty < 0.0 and not is_effectively_zero(order.remaining_qty):
            raise RuntimeError(f"Replay negative remaining_qty for order {order.order_id}")
        if is_effectively_zero(order.remaining_qty):
            order.remaining_qty = 0.0
        total_notional = ((order.avg_fill_price or 0.0) * prior_filled_qty) + (
            fill_qty * fill_price
        )
        order.avg_fill_price = (
            round_price(total_notional / order.filled_qty) if order.filled_qty > 0 else None
        )
        order.fill_count += 1
        order.updated_at = fill_time
        order.status = (
            SimulatedOrderState.FILLED
            if is_effectively_zero(order.remaining_qty)
            else SimulatedOrderState.PARTIALLY_FILLED
        )
        fill = SimulatedFill(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            fill_qty=fill_qty,
            fill_price=fill_price,
            fill_time=fill_time,
            intent_type=order.intent_type,
            parent_order_id=order.parent_order_id,
            exit_reason=order.metadata.get("exit_reason"),
            chain_id=order.chain_id,
            strategy_id=order.strategy_id,
            is_forced_liquidation=is_forced_liquidation,
            commission=0.0,
        )
        self.fill_history.append(fill)
        if order.metadata.get("is_forced_liquidation"):
            logger.info(
                "replay_forced_liquidation_fill order_id=%s symbol=%s qty=%s price=%s",
                order.order_id,
                order.symbol,
                fill_qty,
                fill_price,
            )
        elif order.status is SimulatedOrderState.PARTIALLY_FILLED:
            logger.info(
                "replay_partial_fill order_id=%s symbol=%s filled_qty=%s remaining_qty=%s price=%s",
                order.order_id,
                order.symbol,
                fill_qty,
                order.remaining_qty,
                fill_price,
            )

    def _resolve_fill(self, order: SimulatedOrder, bar: HistoricalBar) -> tuple[float, float]:
        if order.order_type == "market":
            price = self._apply_slippage(bar.open, order.side)
            return self._fill_slice(order.remaining_qty, bar.volume), price

        if order.order_type == "limit":
            if order.limit_price is None:
                return 0.0, 0.0
            if order.side == "buy" and bar.low <= order.limit_price:
                return self._fill_slice(order.remaining_qty, bar.volume), order.limit_price
            if order.side == "sell" and bar.high >= order.limit_price:
                return self._fill_slice(order.remaining_qty, bar.volume), order.limit_price
            return 0.0, 0.0

        if order.order_type == "stop":
            if order.stop_price is None:
                return 0.0, 0.0
            if order.side == "buy" and bar.high >= order.stop_price:
                px = self._apply_slippage(max(order.stop_price, bar.open), order.side)
                return self._fill_slice(order.remaining_qty, bar.volume), px
            if order.side == "sell" and bar.low <= order.stop_price:
                px = self._apply_slippage(min(order.stop_price, bar.open), order.side)
                return self._fill_slice(order.remaining_qty, bar.volume), px
            return 0.0, 0.0

        return 0.0, 0.0

    def _fill_slice(self, remaining_qty: float, volume: float) -> float:
        capacity = self.config.fill_capacity(volume)
        return max(min(remaining_qty, capacity), 0.0)

    def _apply_slippage(self, price: float, side: str) -> float:
        if self.config.slippage_bps == 0:
            return float(price)
        direction = 1.0 if side == "buy" else -1.0
        return float(price) * (1.0 + direction * self.config.slippage_bps / 10_000.0)

    def order_record(self, order_id: str) -> dict[str, Any]:
        order = self.orders[order_id]
        status = order.status.value
        updated_at = order.updated_at or order.created_at
        delta_qty = max(order.filled_qty - order.reported_filled_qty, 0.0)
        record = {
            "id": order.order_id,
            "symbol": order.symbol,
            "side": order.side,
            "status": status,
            "qty": str(order.qty),
            "filled_qty": str(order.filled_qty),
            "remaining_qty": str(order.remaining_qty),
            "filled_avg_price": (
                None if order.avg_fill_price is None else str(round(order.avg_fill_price, 10))
            ),
            "submitted_at": order.created_at.isoformat(),
            "updated_at": updated_at.isoformat(),
            "filled_at": updated_at.isoformat() if delta_qty > 0 else None,
            "intent_type": order.intent_type,
            "parent_order_id": order.parent_order_id,
            "exit_reason": order.metadata.get("exit_reason"),
            "replaced_by_order_id": order.replaced_by_order_id,
            "rejection_reason": order.rejection_reason,
            "fill_events": [
                {
                    "order_id": fill.order_id,
                    "symbol": fill.symbol,
                    "side": fill.side,
                    "fill_qty": fill.fill_qty,
                    "fill_price": fill.fill_price,
                    "fill_time": fill.fill_time.isoformat(),
                    "intent_type": fill.intent_type,
                    "parent_order_id": fill.parent_order_id,
                    "exit_reason": fill.exit_reason,
                    "chain_id": fill.chain_id,
                    "strategy_id": fill.strategy_id,
                    "is_forced_liquidation": fill.is_forced_liquidation,
                    "commission": fill.commission,
                }
                for fill in self.fill_history
                if fill.order_id == order_id
            ],
        }
        order.reported_filled_qty = order.filled_qty
        order.reported_avg_fill_price = order.avg_fill_price
        return record

    def open_orders(self) -> dict[str, SimulatedOrder]:
        return {
            order_id: order
            for order_id, order in self.orders.items()
            if order.status in {SimulatedOrderState.ACCEPTED, SimulatedOrderState.PARTIALLY_FILLED}
        }

    def broker_positions(self) -> list[dict[str, str]]:
        qtys: dict[str, float] = defaultdict(float)
        for fill in self.fill_history:
            qtys[fill.symbol] += _signed_fill_qty(fill.side, fill.fill_qty)
        return [
            {"symbol": symbol, "qty": str(qty)}
            for symbol, qty in sorted(qtys.items())
            if not is_effectively_zero(qty)
        ]


def signal_to_order_intent(signal: ReplaySignal) -> ReplayBrokerOrderIntent:
    action = signal.action.lower()
    if action == "buy":
        side = "buy"
        intent_type = "entry"
    elif action == "short":
        side = "sell"
        intent_type = "entry_short"
    elif action == "cover":
        side = "buy"
        intent_type = "cover"
    else:
        side = "sell"
        intent_type = "exit"

    return ReplayBrokerOrderIntent(
        symbol=signal.symbol,
        side=side,
        qty=signal.qty,
        order_type=signal.order_type,
        strategy_id=signal.strategy_id,
        chain_id=signal.chain_id,
        timestamp=signal.timestamp,
        tif=signal.tif,
        limit_price=signal.limit_price,
        stop_price=signal.stop_price,
        intent_type=intent_type,
        metadata=signal.metadata,
    )


def build_validation_snapshot(
    *,
    label: str,
    portfolio: Portfolio,
    open_orders: dict[str, SimulatedOrder],
    market_prices: dict[str, float],
    realized_pnl: float,
) -> ReplayValidationSnapshot:
    unrealized = 0.0
    positions: dict[str, float] = {}
    for symbol, position in portfolio.positions.items():
        current_price = market_prices.get(symbol, position.current_price)
        unrealized += (current_price - position.avg_cost) * position.shares
        positions[symbol] = float(position.shares)

    cumulative_exposure = 0.0
    if positions:
        cumulative_exposure = _position_exposure_delta(positions)
    return ReplayValidationSnapshot(
        label=label,
        cash=float(portfolio.cash),
        positions=positions,
        open_orders={order_id: order.status.value for order_id, order in open_orders.items()},
        realized_pnl=float(realized_pnl),
        unrealized_pnl=float(unrealized),
        cumulative_exposure=cumulative_exposure,
    )


def validate_replay(
    *,
    expected: ReplayValidationSnapshot | None,
    actual: ReplayValidationSnapshot,
    fills: list[SimulatedFill],
    portfolio: Portfolio,
    expected_events: list[ReplayEvent] | None = None,
    actual_events: list[ReplayEvent] | None = None,
    parity_mode: ParityMode = ParityMode.STRICT,
) -> ReplayValidationResult:
    mismatches: list[ReplayMismatch] = []
    invariant_failures: list[str] = []
    drift_account = DriftAccount()
    drift_bounds = default_drift_bounds(event_count=max(len(fills), 1))

    if expected is not None:
        drift_account = accumulate_cash_drift(
            drift_account,
            raw=actual.cash - expected.cash,
            rounded=round_cash(actual.cash) - round_cash(expected.cash),
        )
        if not floats_equal(expected.cash, actual.cash):
            mismatches.append(
                ReplayMismatch(
                    category="cash",
                    key="cash",
                    expected=expected.cash,
                    actual=actual.cash,
                    detail="Cash balance mismatch",
                )
            )

        for symbol in sorted(set(expected.positions) | set(actual.positions)):
            expected_qty = expected.positions.get(symbol, 0.0)
            actual_qty = actual.positions.get(symbol, 0.0)
            drift_account = accumulate_qty_drift(
                drift_account,
                raw=actual_qty - expected_qty,
                rounded=round_qty(actual_qty - expected_qty),
            )
            if not floats_equal(expected_qty, actual_qty):
                mismatches.append(
                    ReplayMismatch(
                        category="positions",
                        key=symbol,
                        expected=expected_qty,
                        actual=actual_qty,
                        detail="Position quantity mismatch",
                    )
                )

        for order_id in sorted(set(expected.open_orders) | set(actual.open_orders)):
            if expected.open_orders.get(order_id) != actual.open_orders.get(order_id):
                mismatches.append(
                    ReplayMismatch(
                        category="open_orders",
                        key=order_id,
                        expected=expected.open_orders.get(order_id),
                        actual=actual.open_orders.get(order_id),
                        detail="Open order state mismatch",
                    )
                )

    broker_fill_events = [
        BrokerFillEvent(
            order_id=fill.order_id,
            symbol=fill.symbol,
            side=fill.side,
            fill_qty=fill.fill_qty,
            fill_price=fill.fill_price,
            fill_time=fill.fill_time,
            intent_type=fill.intent_type,
            parent_order_id=fill.parent_order_id,
            exit_reason=fill.exit_reason,
            is_forced_liquidation=fill.is_forced_liquidation,
            commission=fill.commission,
        )
        for fill in fills
    ]
    fill_position_qtys = _aggregate_position_qty_from_broker_fills(broker_fill_events)
    cash_delta_from_fills, fill_drift = _aggregate_cash_delta_from_broker_fills(broker_fill_events)
    drift_account = DriftAccount(
        quantity=round_qty(drift_account.quantity + fill_drift.quantity),
        cash=round_cash(drift_account.cash + fill_drift.cash),
        price=round_price(drift_account.price + fill_drift.price),
    )
    cash_from_fills = float(portfolio.initial_capital) + cash_delta_from_fills

    actual_positions = {symbol: position.shares for symbol, position in portfolio.positions.items()}

    for symbol in sorted(set(fill_position_qtys) | set(actual_positions)):
        drift_account = accumulate_qty_drift(
            drift_account,
            raw=actual_positions.get(symbol, 0.0) - fill_position_qtys.get(symbol, 0.0),
            rounded=round_qty(actual_positions.get(symbol, 0.0) - fill_position_qtys.get(symbol, 0.0)),
        )
        if not floats_equal(fill_position_qtys.get(symbol, 0.0), actual_positions.get(symbol, 0.0)):
            invariant_failures.append(f"fill-history mismatch for {symbol}")

    drift_account = accumulate_cash_drift(
        drift_account,
        raw=portfolio.cash - cash_from_fills,
        rounded=round_cash(portfolio.cash) - round_cash(cash_from_fills),
    )
    if not floats_equal(cash_from_fills, portfolio.cash):
        invariant_failures.append("cash balance does not reconcile from fill history")

    for symbol, qty in actual_positions.items():
        if not is_effectively_zero(qty) and symbol not in fill_position_qtys:
            invariant_failures.append(f"phantom position detected for {symbol}")

    parity_result: ParityValidationResult | None = None
    if expected is not None and expected_events is not None and actual_events is not None:
        expected_state = ParityState(
            positions=expected.positions,
            cash=expected.cash,
            order_states=expected.open_orders,
            event_keys=tuple(_event_signature(event) for event in expected_events),
            exposure_delta=_position_exposure_delta(expected.positions),
            cumulative_exposure=expected.cumulative_exposure,
        )
        actual_state = ParityState(
            positions=actual.positions,
            cash=actual.cash,
            order_states=actual.open_orders,
            event_keys=tuple(_event_signature(event) for event in actual_events),
            exposure_delta=_position_exposure_delta(actual.positions),
            cumulative_exposure=actual.cumulative_exposure,
        )
        parity_result = validate_parity(
            expected_states=[expected_state],
            actual_states=[actual_state],
            mode=parity_mode,
        )
        if parity_result.diffs:
            invariant_failures.append("EVENT_LEDGER_STATE_DIVERGENCE")

    if not drift_account.within_bounds(drift_bounds):
        invariant_failures.append("bounded drift exceeded over replay sequence")

    return ReplayValidationResult(
        expected=expected,
        actual=actual,
        mismatches=mismatches,
        invariant_failures=invariant_failures,
        parity=parity_result,
        drift_account=drift_account,
        drift_bounds=drift_bounds,
    )


def _intent_record(order: SimulatedOrder) -> dict[str, Any]:
    return {
        "order_id": order.order_id,
        "symbol": order.symbol,
        "side": order.side,
        "qty": order.qty,
        "order_type": order.order_type,
        "time_in_force": order.tif,
        "intent_type": order.intent_type,
        "parent_order_id": order.parent_order_id,
        "exit_reason": order.metadata.get("exit_reason"),
        "status": order.status.value,
        "submitted_at": order.created_at.isoformat(),
        "updated_at": (order.updated_at or order.created_at).isoformat(),
    }


def _reconciliation_snapshot_payload(
    simulator: DeterministicBrokerSimulator,
    broker_positions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "intraday_position_state": {
            str(item.get("symbol") or ""): dict(item)
            for item in (broker_positions or [])
            if str(item.get("symbol") or "")
        },
        "order_state": {
            order_id: {"status": order.status.value, "symbol": order.symbol}
            for order_id, order in simulator.orders.items()
        },
        "lifecycle_state": {},
        "exit_policy_state": {},
    }


def _component_diagram() -> str:
    return "\n".join(
        [
            "ReplayStrategy -> ReplayEngine -> DeterministicBrokerSimulator",
            "DeterministicBrokerSimulator -> ReplayBrokerClient -> reconcile_broker_orders",
            "reconcile_broker_orders -> broker_fill_events/event_ledger -> Portfolio rebuild",
            "ReplayEngine -> ValidationFramework -> diff report",
        ]
    )


def _data_flow_diagram() -> str:
    return "\n".join(
        [
            "HistoricalBar stream",
            "  -> on_bar",
            "  -> strategy signals",
            "  -> canonical ReplayBrokerOrderIntent",
            "  -> deterministic broker state machine",
            "  -> fill events/status records",
            "  -> reconcile_broker_orders",
            "  -> ledger + portfolio rebuild",
            "  -> snapshot/invariant validation",
        ]
    )


def _design_summary() -> str:
    return (
        "Modules: replay engine, canonical intent schema, deterministic broker "
        "simulator, reconciliation bridge, validation framework. Order lifecycle: "
        "NEW -> ACCEPTED -> PARTIALLY_FILLED -> FILLED/CANCELED/REJECTED/REPLACED. "
        "Portfolio changes occur only through reconciliation-driven fill replay."
    )


def _failure_modes_exposed() -> list[str]:
    return [
        "Portfolio state diverges from fill history",
        "Cash balance drifts from reconciled fill notional",
        "Order replacement leaves orphaned active order",
        "Canceled orders still fill on later bars",
        "Limit cross rules produce ambiguous fills",
        "Partial fills fail to accumulate deterministically",
        "Concurrent position gating differs from expected signal policy",
        "Forced EOD flatten leaves residual shares",
        "Lifecycle state disagrees with broker order state",
        "Open order registry contains terminal orders",
        "Semantic parity matches while strict event parity diverges",
        "Bounded drift exceeds precision budget across event sequence",
    ]


def _hard_stop_conditions() -> list[str]:
    return [
        "No order book depth modeling",
        "No stochastic latency; only deterministic bar delay",
        "No external fills or venue-driven partials",
        "No intrabar sequencing beyond explicit high/low crossing rules",
        "No live broker codepaths",
    ]


class ReplayEngine:
    def __init__(
        self,
        *,
        strategy: ReplayStrategy,
        broker: DeterministicBrokerSimulator | None = None,
        initial_capital: float = 100_000.0,
        pod_id: str = "replay",
    ) -> None:
        self.strategy = strategy
        self.broker = broker or DeterministicBrokerSimulator()
        self.initial_capital = initial_capital
        self.pod_id = pod_id

    def run(
        self,
        bars: list[HistoricalBar],
        *,
        expected: ReplayValidationSnapshot | None = None,
        force_flatten_at_eod: bool = False,
        expected_events: list[ReplayEvent] | None = None,
        parity_mode: ParityMode = ParityMode.STRICT,
    ) -> ReplayResult:
        conn = duckdb.connect(":memory:")
        portfolio = Portfolio(initial_capital=self.initial_capital, pod_id=self.pod_id)
        broker_client = ReplayBrokerClient(self.broker)
        events: list[ReplayEvent] = []
        fills: list[SimulatedFill] = []
        reconciliations: list[ReconciliationResult] = []
        market_prices: dict[str, float] = {}
        realized_pnl = 0.0

        for bar in sorted(bars, key=lambda item: (item.timestamp, item.symbol)):
            market_prices[bar.symbol] = bar.close
            events.append(
                ReplayEvent(
                    event_type=ReplayEventType.ON_BAR,
                    timestamp=bar.timestamp,
                    payload={"symbol": bar.symbol, "close": bar.close},
                )
            )

            signals = self.strategy.on_bar(
                bar,
                portfolio=portfolio,
                open_orders=self.broker.open_orders(),
            ) or []
            for signal in signals:
                events.append(
                    ReplayEvent(
                        event_type=ReplayEventType.ON_SIGNAL,
                        timestamp=signal.timestamp,
                        payload={"symbol": signal.symbol, "action": signal.action, "qty": signal.qty},
                    )
                )
                intent = signal_to_order_intent(signal)
                self._submit_and_persist_intent(conn, intent, events)

            bar_fills = self.broker.process_bar(bar)
            for fill in bar_fills:
                fills.append(fill)
                events.append(
                    ReplayEvent(
                        event_type=ReplayEventType.ON_FILL_EVENT,
                        timestamp=fill.fill_time,
                        payload={
                            "order_id": fill.order_id,
                            "symbol": fill.symbol,
                            "side": fill.side,
                            "fill_qty": fill.fill_qty,
                            "fill_price": fill.fill_price,
                        },
                    )
                )

            broker_positions = self.broker.broker_positions()
            snapshot = _reconciliation_snapshot_payload(self.broker, broker_positions)
            result = reconcile_broker_orders(
                conn,
                cast(Any, broker_client),
                portfolio=portfolio,
                pod_id=self.pod_id,
                broker_positions=broker_positions,
                log_kwargs=snapshot,
            )
            reconciliations.append(result)
            current_snapshot = build_validation_snapshot(
                label=f"bar:{bar.timestamp.isoformat()}",
                portfolio=portfolio,
                open_orders=self.broker.open_orders(),
                market_prices=market_prices,
                realized_pnl=realized_pnl,
            )
            events.append(
                ReplayEvent(
                    event_type=ReplayEventType.ON_RECONCILE,
                    timestamp=bar.timestamp,
                    payload={
                        "persisted_fill_count": result.persisted_fill_count,
                        "applied_fill_count": result.applied_fill_count,
                        "snapshot_label": current_snapshot.label,
                    },
                )
            )

        if force_flatten_at_eod:
            last_timestamp = max((bar.timestamp for bar in bars), default=datetime.now(tz=UTC))
            positions_to_flatten = {
                symbol: float(position.shares)
                for symbol, position in list(portfolio.positions.items())
                if not is_effectively_zero(float(position.shares))
            }
            for symbol, qty in positions_to_flatten.items():
                side = "sell" if qty > 0 else "buy"
                parent_order_id = next(
                    (
                        order.order_id
                        for order in reversed(list(self.broker.orders.values()))
                        if order.symbol == symbol
                        and order.intent_type in {"entry", "entry_short"}
                        and order.filled_qty > 0
                    ),
                    None,
                )
                if parent_order_id is None:
                    raise RuntimeError(f"FORCED_EXIT_WITHOUT_PARENT_ORDER:{symbol}")
                intent = ReplayBrokerOrderIntent(
                    symbol=symbol,
                    side=side,
                    qty=abs(qty),
                    order_type="market",
                    strategy_id="eod",
                    chain_id=f"eod:{symbol}",
                    timestamp=last_timestamp,
                    intent_type="forced_exit",
                    metadata={
                        "exit_reason": "eod_forced_exit",
                        "is_forced_liquidation": True,
                        "parent_order_id": parent_order_id,
                    },
                )
                self._submit_and_persist_intent(conn, intent, events)
                events.append(
                    ReplayEvent(
                        event_type=ReplayEventType.ON_FORCED_LIQUIDATION,
                        timestamp=last_timestamp,
                        payload={
                            "symbol": symbol,
                            "side": side,
                            "qty": abs(qty),
                            "fill_price_basis": "last_bar_close",
                        },
                    )
                )

            for symbol, qty in positions_to_flatten.items():
                price = float(market_prices[symbol])
                forced_order = next(
                    order
                    for order in reversed(list(self.broker.orders.values()))
                    if order.symbol == symbol
                    and order.intent_type == "forced_exit"
                    and order.status in {
                        SimulatedOrderState.ACCEPTED,
                        SimulatedOrderState.PARTIALLY_FILLED,
                    }
                )
                self.broker._apply_fill(
                    forced_order,
                    fill_qty=abs(qty),
                    fill_price=price,
                    fill_time=last_timestamp,
                    is_forced_liquidation=True,
                )
                forced_fill = self.broker.fill_history[-1]
                fills.append(forced_fill)
                events.append(
                    ReplayEvent(
                        event_type=ReplayEventType.ON_FILL_EVENT,
                        timestamp=forced_fill.fill_time,
                        payload={
                            "order_id": forced_fill.order_id,
                            "symbol": forced_fill.symbol,
                            "side": forced_fill.side,
                            "fill_qty": forced_fill.fill_qty,
                            "fill_price": forced_fill.fill_price,
                            "is_forced_liquidation": True,
                        },
                    )
                )

            broker_positions = self.broker.broker_positions()
            result = reconcile_broker_orders(
                conn,
                cast(Any, broker_client),
                portfolio=portfolio,
                pod_id=self.pod_id,
                broker_positions=broker_positions,
                log_kwargs=_reconciliation_snapshot_payload(self.broker, broker_positions),
            )
            reconciliations.append(result)
            post_eod_snapshot = build_validation_snapshot(
                label="post_eod_check",
                portfolio=portfolio,
                open_orders=self.broker.open_orders(),
                market_prices=market_prices,
                realized_pnl=realized_pnl,
            )
            if any(
                not is_effectively_zero(qty)
                for qty in post_eod_snapshot.positions.values()
            ):
                raise RuntimeError("EOD_FORCED_LIQUIDATION_INCOMPLETE")

        final_snapshot = build_validation_snapshot(
            label="final",
            portfolio=portfolio,
            open_orders=self.broker.open_orders(),
            market_prices=market_prices,
            realized_pnl=realized_pnl,
        )
        final_ledger_digest = ledger_ordering_digest(conn, pod_id=self.pod_id)
        validation = validate_replay(
            expected=expected,
            actual=final_snapshot,
            fills=fills,
            portfolio=portfolio,
            expected_events=expected_events,
            actual_events=events,
            parity_mode=parity_mode,
        )
        if len(final_ledger_digest) > 1:
            ordered_digest = sorted(
                final_ledger_digest,
                key=lambda item: (item.event_time, item.sequence_id),
            )
            if ordered_digest != final_ledger_digest:
                validation.invariant_failures.append(
                    "ledger ordering digest diverged from replay order"
                )
        return ReplayResult(
            validation=validation,
            reconciliation=reconciliations,
            events=events,
            fills=fills,
            final_snapshot=final_snapshot,
            order_history=dict(self.broker.orders),
            design_summary=_design_summary(),
            component_diagram=_component_diagram(),
            data_flow_diagram=_data_flow_diagram(),
            failure_modes_exposed=_failure_modes_exposed(),
            hard_stop_conditions=_hard_stop_conditions(),
        )

    def _submit_and_persist_intent(
        self,
        conn: duckdb.DuckDBPyConnection,
        intent: ReplayBrokerOrderIntent,
        events: list[ReplayEvent],
    ) -> None:
        metadata = dict(intent.metadata or {})
        if intent.intent_type in {"exit", "cover"} and not metadata.get("parent_order_id"):
            parent_order_id = next(
                (
                    order.order_id
                    for order in reversed(list(self.broker.orders.values()))
                    if order.symbol == intent.symbol
                    and order.intent_type in {"entry", "entry_short"}
                    and order.filled_qty > 0
                ),
                None,
            )
            if parent_order_id is not None:
                metadata.setdefault("parent_order_id", parent_order_id)
                intent = replace(
                    intent,
                    metadata=metadata,
                )

        order = self.broker.submit_intent(intent)
        persist_submitted_orders(conn, [_intent_record(order)], pod_id=self.pod_id)
        events.append(
            ReplayEvent(
                event_type=ReplayEventType.ON_ORDER_INTENT,
                timestamp=intent.timestamp,
                payload={
                    "order_id": order.order_id,
                    "symbol": order.symbol,
                    "side": order.side,
                    "order_type": order.order_type,
                    "chain_id": order.chain_id,
                },
            )
        )
        if order.status is SimulatedOrderState.REJECTED:
            events.append(
                ReplayEvent(
                    event_type=ReplayEventType.ON_ORDER_REJECTED,
                    timestamp=intent.timestamp,
                    payload={
                        "order_id": order.order_id,
                        "symbol": order.symbol,
                        "reason": order.rejection_reason or "REJECTED",
                    },
                )
            )


__all__ = [
    "DeterministicBrokerSimulator",
    "HistoricalBar",
    "ParityDiff",
    "ParityDiffCategory",
    "ParityMode",
    "ReplayBrokerClient",
    "ReplayBrokerOrderIntent",
    "ReplayEngine",
    "ReplayEvent",
    "ReplayEventType",
    "ReplayFillModelConfig",
    "ReplayMismatch",
    "ReplayResult",
    "ReplaySignal",
    "ReplayStrategy",
    "ReplayValidationResult",
    "ReplayValidationSnapshot",
    "SimulatedFill",
    "SimulatedOrder",
    "SimulatedOrderState",
    "build_validation_snapshot",
    "signal_to_order_intent",
    "validate_replay",
]
