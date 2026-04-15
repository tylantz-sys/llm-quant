"""Replay/live parity validation utilities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from llm_quant.broker.numeric import (
    DriftAccount,
    accumulate_cash_drift,
    accumulate_qty_drift,
    default_drift_bounds,
    floats_equal,
    round_cash,
    round_qty,
)

if TYPE_CHECKING:
    from llm_quant.broker.replay import ReplayBrokerOrderIntent, SimulatedFill, SimulatedOrder


class ParityMode(StrEnum):
    STRICT = "strict"
    SEMANTIC = "semantic"


class ParityDiffCategory(StrEnum):
    EVENT = "event"
    STATE_TRANSITION = "state_transition"
    EXPOSURE = "exposure"


@dataclass(frozen=True, slots=True)
class ParityState:
    positions: dict[str, float]
    cash: float
    order_states: dict[str, str]
    event_keys: tuple[str, ...] = ()
    exposure_delta: float = 0.0
    cumulative_exposure: float = 0.0
    state_digest: tuple[tuple[str, float | str], ...] = ()


@dataclass(frozen=True, slots=True)
class ParityDiff:
    stage: str
    key: str
    expected: object
    actual: object
    category: ParityDiffCategory = ParityDiffCategory.STATE_TRANSITION
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ParityValidationResult:
    ok: bool
    diffs: list[ParityDiff]
    mode: ParityMode
    drift: DriftAccount


@dataclass(frozen=True, slots=True)
class RecordedSession:
    intents: list[ReplayBrokerOrderIntent]
    fills: list[SimulatedFill]
    cancels: list[str]


def _normalized_order_state(value: str | None, *, mode: ParityMode) -> str | None:
    if value is None:
        return None
    normalized = str(value).lower()
    if mode is ParityMode.SEMANTIC:
        if normalized in {"accepted", "new", "held", "pending_new"}:
            return "open"
        if normalized in {"partially_filled"}:
            return "open_partial"
        if normalized in {"canceled", "cancelled", "replaced", "expired", "done_for_day"}:
            return "closed_unfilled"
    return normalized


def _event_key_signature(value: str, *, mode: ParityMode) -> str:
    if mode is ParityMode.STRICT:
        return value
    parts = value.split("|")
    if len(parts) <= 1:
        return value
    return "|".join(parts[1:])


def _semantic_positions(positions: dict[str, float]) -> dict[str, float]:
    return {
        symbol: round_qty(qty)
        for symbol, qty in sorted(positions.items())
        if not floats_equal(qty, 0.0)
    }


def _event_multiset(event_keys: tuple[str, ...], *, mode: ParityMode) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event_key in event_keys:
        signature = _event_key_signature(event_key, mode=mode)
        counts[signature] = counts.get(signature, 0) + 1
    return counts


def _state_digest(positions: dict[str, float], cash: float, order_states: dict[str, str]) -> tuple[tuple[str, float | str], ...]:
    digest: list[tuple[str, float | str]] = [("cash", round_cash(cash))]
    digest.extend((f"position:{symbol}", round_qty(qty)) for symbol, qty in sorted(positions.items()))
    digest.extend((f"order:{order_id}", state) for order_id, state in sorted(order_states.items()))
    return tuple(digest)


def validate_parity(
    *,
    expected_states: list[ParityState],
    actual_states: list[ParityState],
    mode: ParityMode = ParityMode.STRICT,
) -> ParityValidationResult:
    diffs: list[ParityDiff] = []
    drift = DriftAccount()
    max_len = max(len(expected_states), len(actual_states))
    for index in range(max_len):
        expected = expected_states[index] if index < len(expected_states) else None
        actual = actual_states[index] if index < len(actual_states) else None
        if expected is None or actual is None:
            diffs.append(
                ParityDiff(
                    stage=f"step:{index}",
                    key="state_count",
                    expected=expected,
                    actual=actual,
                    category=ParityDiffCategory.STATE_TRANSITION,
                    detail="Parity stream length mismatch",
                )
            )
            continue

        expected_cash = round_cash(expected.cash)
        actual_cash = round_cash(actual.cash)
        drift = accumulate_cash_drift(drift, raw=actual.cash - expected.cash, rounded=actual_cash - expected_cash)
        if not floats_equal(expected_cash, actual_cash):
            diffs.append(
                ParityDiff(
                    stage=f"step:{index}",
                    key="cash",
                    expected=expected.cash,
                    actual=actual.cash,
                    category=ParityDiffCategory.STATE_TRANSITION,
                    detail="Cash mismatch",
                )
            )

        expected_positions = _semantic_positions(expected.positions)
        actual_positions = _semantic_positions(actual.positions)
        for symbol in sorted(set(expected_positions) | set(actual_positions)):
            expected_qty = expected_positions.get(symbol, 0.0)
            actual_qty = actual_positions.get(symbol, 0.0)
            drift = accumulate_qty_drift(drift, raw=actual_qty - expected_qty, rounded=round_qty(actual_qty - expected_qty))
            if not floats_equal(expected_qty, actual_qty):
                diffs.append(
                    ParityDiff(
                        stage=f"step:{index}",
                        key=f"position:{symbol}",
                        expected=expected.positions.get(symbol, 0.0),
                        actual=actual.positions.get(symbol, 0.0),
                        category=ParityDiffCategory.STATE_TRANSITION,
                        detail="Position quantity mismatch",
                    )
                )

        for order_id in sorted(set(expected.order_states) | set(actual.order_states)):
            expected_state = _normalized_order_state(expected.order_states.get(order_id), mode=mode)
            actual_state = _normalized_order_state(actual.order_states.get(order_id), mode=mode)
            if expected_state != actual_state:
                diffs.append(
                    ParityDiff(
                        stage=f"step:{index}",
                        key=f"order:{order_id}",
                        expected=expected.order_states.get(order_id),
                        actual=actual.order_states.get(order_id),
                        category=ParityDiffCategory.STATE_TRANSITION,
                        detail="Order lifecycle mismatch",
                    )
                )

        expected_event_keys = tuple(_event_key_signature(event_key, mode=mode) for event_key in expected.event_keys)
        actual_event_keys = tuple(_event_key_signature(event_key, mode=mode) for event_key in actual.event_keys)
        if expected_event_keys != actual_event_keys:
            detail = "Event sequence mismatch"
            if mode is ParityMode.SEMANTIC:
                if _event_multiset(expected.event_keys, mode=mode) != _event_multiset(actual.event_keys, mode=mode):
                    detail = "Event multiset mismatch under semantic parity"
                else:
                    detail = "Permutation-equivalent events converged through divergent semantic path"
            diffs.append(
                ParityDiff(
                    stage=f"step:{index}",
                    key="event_keys",
                    expected=expected.event_keys,
                    actual=actual.event_keys,
                    category=ParityDiffCategory.EVENT,
                    detail=detail,
                )
            )

        expected_digest = expected.state_digest or _state_digest(expected.positions, expected.cash, expected.order_states)
        actual_digest = actual.state_digest or _state_digest(actual.positions, actual.cash, actual.order_states)
        if expected_digest != actual_digest:
            diffs.append(
                ParityDiff(
                    stage=f"step:{index}",
                    key="state_digest",
                    expected=expected_digest,
                    actual=actual_digest,
                    category=ParityDiffCategory.STATE_TRANSITION,
                    detail="State convergence mismatch",
                )
            )

        expected_exposure = round_qty(expected.exposure_delta)
        actual_exposure = round_qty(actual.exposure_delta)
        drift = accumulate_qty_drift(
            drift,
            raw=actual.exposure_delta - expected.exposure_delta,
            rounded=actual_exposure - expected_exposure,
        )
        if not floats_equal(expected_exposure, actual_exposure):
            diffs.append(
                ParityDiff(
                    stage=f"step:{index}",
                    key="exposure_delta",
                    expected=expected.exposure_delta,
                    actual=actual.exposure_delta,
                    category=ParityDiffCategory.EXPOSURE,
                    detail="Exposure delta mismatch",
                )
            )

        expected_cumulative_exposure = round_qty(expected.cumulative_exposure)
        actual_cumulative_exposure = round_qty(actual.cumulative_exposure)
        drift = accumulate_qty_drift(
            drift,
            raw=actual.cumulative_exposure - expected.cumulative_exposure,
            rounded=actual_cumulative_exposure - expected_cumulative_exposure,
        )
        if not floats_equal(expected_cumulative_exposure, actual_cumulative_exposure):
            diffs.append(
                ParityDiff(
                    stage=f"step:{index}",
                    key="cumulative_exposure",
                    expected=expected.cumulative_exposure,
                    actual=actual.cumulative_exposure,
                    category=ParityDiffCategory.EXPOSURE,
                    detail="Cumulative exposure path mismatch",
                )
            )

    bounds = default_drift_bounds(event_count=max_len)
    ok = not diffs and drift.within_bounds(bounds)
    if not drift.within_bounds(bounds):
        diffs.append(
            ParityDiff(
                stage="summary",
                key="drift_bounds",
                expected={"max": bounds},
                actual={"observed": drift},
                category=ParityDiffCategory.EXPOSURE,
                detail="Bounded drift exceeded",
            )
        )
    return ParityValidationResult(ok=ok, diffs=diffs, mode=mode, drift=drift)


def snapshot_parity_state(
    *,
    positions: dict[str, float],
    cash: float,
    orders: dict[str, SimulatedOrder],
    event_keys: tuple[str, ...] = (),
    exposure_delta: float = 0.0,
) -> ParityState:
    normalized_positions = dict(sorted(positions.items()))
    normalized_orders = {order_id: order.status.value for order_id, order in sorted(orders.items())}
    return ParityState(
        positions=normalized_positions,
        cash=float(cash),
        order_states=normalized_orders,
        event_keys=tuple(event_keys),
        exposure_delta=float(exposure_delta),
        cumulative_exposure=float(exposure_delta),
        state_digest=_state_digest(normalized_positions, cash, normalized_orders),
    )


__all__ = [
    "ParityDiff",
    "ParityDiffCategory",
    "ParityMode",
    "ParityState",
    "ParityValidationResult",
    "RecordedSession",
    "snapshot_parity_state",
    "validate_parity",
]
