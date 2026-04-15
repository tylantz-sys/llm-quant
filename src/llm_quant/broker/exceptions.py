"""Shared broker exception hierarchy."""

from __future__ import annotations


class BrokerError(RuntimeError):
    """Base class for broker-domain failures."""


class OrderingError(BrokerError):
    """Raised when broker order sequencing or lineage invariants fail."""


class CausalIntegrityError(OrderingError):
    """Raised when broker causal lineage closure fails."""


class ReconciliationError(BrokerError):
    """Raised when authoritative reconciliation cannot complete safely."""


class PositionInvariantError(ReconciliationError):
    """Raised when post-reconciliation position invariants are violated."""


class OCOConflictError(OrderingError):
    """Raised when OCO state violates mutual-exclusivity or protection rules."""


__all__ = [
    "BrokerError",
    "OrderingError",
    "CausalIntegrityError",
    "ReconciliationError",
    "PositionInvariantError",
    "OCOConflictError",
]