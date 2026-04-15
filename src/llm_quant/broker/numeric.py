"""Deterministic numeric utilities for broker state and reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose

BROKER_EPSILON = 1e-8
CASH_PRECISION = 8
QTY_PRECISION = 8
PRICE_PRECISION = 8


def round_cash(value: float) -> float:
    return round(float(value), CASH_PRECISION)


def round_qty(value: float) -> float:
    rounded = round(float(value), QTY_PRECISION)
    return 0.0 if abs(rounded) <= BROKER_EPSILON else rounded


def round_price(value: float) -> float:
    return round(float(value), PRICE_PRECISION)


def floats_equal(left: float, right: float, *, tolerance: float = BROKER_EPSILON) -> bool:
    return isclose(float(left), float(right), rel_tol=0.0, abs_tol=tolerance)


def is_effectively_zero(value: float, *, tolerance: float = BROKER_EPSILON) -> bool:
    return floats_equal(float(value), 0.0, tolerance=tolerance)


@dataclass(frozen=True, slots=True)
class DriftBounds:
    quantity: float
    cash: float
    price: float


@dataclass(frozen=True, slots=True)
class DriftAccount:
    quantity: float = 0.0
    cash: float = 0.0
    price: float = 0.0

    def within_bounds(self, bounds: DriftBounds) -> bool:
        return (
            abs(self.quantity) <= bounds.quantity
            and abs(self.cash) <= bounds.cash
            and abs(self.price) <= bounds.price
        )


def default_drift_bounds(*, event_count: int) -> DriftBounds:
    scaled_event_count = max(int(event_count), 1)
    return DriftBounds(
        quantity=scaled_event_count * (10 ** -QTY_PRECISION) + BROKER_EPSILON,
        cash=scaled_event_count * (10 ** -CASH_PRECISION) + BROKER_EPSILON,
        price=scaled_event_count * (10 ** -PRICE_PRECISION) + BROKER_EPSILON,
    )


def accumulate_cash_drift(account: DriftAccount, *, raw: float, rounded: float) -> DriftAccount:
    return DriftAccount(
        quantity=account.quantity,
        cash=round_cash(account.cash + (rounded - raw)),
        price=account.price,
    )


def accumulate_qty_drift(account: DriftAccount, *, raw: float, rounded: float) -> DriftAccount:
    return DriftAccount(
        quantity=round_qty(account.quantity + (rounded - raw)),
        cash=account.cash,
        price=account.price,
    )


def accumulate_price_drift(account: DriftAccount, *, raw: float, rounded: float) -> DriftAccount:
    return DriftAccount(
        quantity=account.quantity,
        cash=account.cash,
        price=round_price(account.price + (rounded - raw)),
    )


__all__ = [
    "BROKER_EPSILON",
    "CASH_PRECISION",
    "PRICE_PRECISION",
    "QTY_PRECISION",
    "DriftAccount",
    "DriftBounds",
    "accumulate_cash_drift",
    "accumulate_price_drift",
    "accumulate_qty_drift",
    "default_drift_bounds",
    "floats_equal",
    "is_effectively_zero",
    "round_cash",
    "round_price",
    "round_qty",
]