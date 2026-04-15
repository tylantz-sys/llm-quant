import pytest

from llm_quant.broker.state_machine import (
    BrokerLifecycleError,
    BrokerLifecycleState,
    advance_to_active_monitoring,
    advance_to_bracket_attached,
    advance_to_closed,
    advance_to_exit_pending,
    derive_lifecycle_state,
    get_entry_submission_gate,
    require_bracket_attachment,
    require_closed_state,
    require_entry_submission,
    require_exit_submission,
    require_monitoring,
    snapshot_from_broker_state,
)


def test_derive_lifecycle_entry_pending() -> None:
    state = derive_lifecycle_state(
        entry_status="new",
        entry_filled_qty=0,
        has_exit_orders=False,
        exit_statuses=[],
        position_qty=0,
    )
    assert state is BrokerLifecycleState.ENTRY_PENDING


def test_derive_lifecycle_entry_filled() -> None:
    state = derive_lifecycle_state(
        entry_status="filled",
        entry_filled_qty=10,
        has_exit_orders=False,
        exit_statuses=[],
        position_qty=10,
    )
    assert state is BrokerLifecycleState.ENTRY_FILLED


def test_derive_lifecycle_active_monitoring_requires_position_and_exit_orders() -> None:
    state = derive_lifecycle_state(
        entry_status="filled",
        entry_filled_qty=10,
        has_exit_orders=True,
        exit_statuses=["canceled"],
        position_qty=10,
    )
    assert state is BrokerLifecycleState.ACTIVE_MONITORING


def test_derive_lifecycle_exit_pending_when_exit_order_open() -> None:
    state = derive_lifecycle_state(
        entry_status="filled",
        entry_filled_qty=10,
        has_exit_orders=True,
        exit_statuses=["new"],
        position_qty=10,
    )
    assert state is BrokerLifecycleState.EXIT_PENDING


def test_derive_lifecycle_closed_when_exit_fills_and_position_is_flat() -> None:
    state = derive_lifecycle_state(
        entry_status="filled",
        entry_filled_qty=10,
        has_exit_orders=True,
        exit_statuses=["filled"],
        position_qty=0,
    )
    assert state is BrokerLifecycleState.CLOSED


def test_entry_submission_gate_allows_only_pending_flat_state() -> None:
    snapshot = snapshot_from_broker_state(
        symbol="AAPL",
        entry_order_id=None,
        entry_status="new",
        entry_filled_qty=0,
        position_qty=0,
    )
    gate = get_entry_submission_gate(snapshot)
    assert gate.allowed is True
    require_entry_submission(snapshot)


def test_entry_submission_gate_blocks_after_entry_fill() -> None:
    snapshot = snapshot_from_broker_state(
        symbol="AAPL",
        entry_order_id="entry-1",
        entry_status="filled",
        entry_filled_qty=5,
        position_qty=5,
    )
    with pytest.raises(BrokerLifecycleError, match="entry_submission blocked"):
        require_entry_submission(snapshot)


def test_advance_to_bracket_attached() -> None:
    snapshot = snapshot_from_broker_state(
        symbol="AAPL",
        entry_order_id="entry-1",
        entry_status="filled",
        entry_filled_qty=5,
        position_qty=5,
    )
    next_snapshot = advance_to_bracket_attached(snapshot)
    assert next_snapshot.state is BrokerLifecycleState.BRACKET_ATTACHED
    assert next_snapshot.has_exit_orders is True


def test_bracket_attachment_requires_filled_entry() -> None:
    snapshot = snapshot_from_broker_state(
        symbol="AAPL",
        entry_order_id="entry-1",
        entry_status="new",
        entry_filled_qty=0,
        position_qty=0,
    )
    with pytest.raises(BrokerLifecycleError, match="bracket_attachment blocked"):
        require_bracket_attachment(snapshot)


def test_monitoring_requires_exit_protection() -> None:
    snapshot = snapshot_from_broker_state(
        symbol="AAPL",
        entry_order_id="entry-1",
        entry_status="filled",
        entry_filled_qty=5,
        position_qty=5,
        has_exit_orders=False,
    )
    with pytest.raises(BrokerLifecycleError, match="monitoring blocked"):
        require_monitoring(snapshot)


def test_advance_to_active_monitoring_from_bracket_attached() -> None:
    snapshot = snapshot_from_broker_state(
        symbol="AAPL",
        entry_order_id="entry-1",
        entry_status="filled",
        entry_filled_qty=5,
        position_qty=5,
        has_exit_orders=True,
        exit_statuses=["canceled"],
    )
    monitored = advance_to_active_monitoring(snapshot)
    assert monitored.state is BrokerLifecycleState.ACTIVE_MONITORING


def test_exit_submission_requires_open_position() -> None:
    snapshot = snapshot_from_broker_state(
        symbol="AAPL",
        entry_order_id="entry-1",
        entry_status="filled",
        entry_filled_qty=5,
        position_qty=0,
        has_exit_orders=True,
        exit_statuses=["filled"],
    )
    with pytest.raises(BrokerLifecycleError, match="exit_submission blocked"):
        require_exit_submission(snapshot)


def test_advance_to_exit_pending_from_active_monitoring() -> None:
    snapshot = snapshot_from_broker_state(
        symbol="AAPL",
        entry_order_id="entry-1",
        entry_status="filled",
        entry_filled_qty=5,
        position_qty=5,
        has_exit_orders=True,
        exit_statuses=["canceled"],
    )
    monitoring = advance_to_active_monitoring(snapshot)
    exit_pending = advance_to_exit_pending(monitoring, exit_order_id="exit-1")
    assert exit_pending.state is BrokerLifecycleState.EXIT_PENDING
    assert exit_pending.exit_order_id == "exit-1"


def test_closed_state_requires_flat_confirmation() -> None:
    snapshot = snapshot_from_broker_state(
        symbol="AAPL",
        entry_order_id="entry-1",
        entry_status="filled",
        entry_filled_qty=5,
        position_qty=5,
        has_exit_orders=True,
        exit_statuses=["new"],
    )
    with pytest.raises(BrokerLifecycleError, match="closed_state blocked"):
        require_closed_state(snapshot)


def test_advance_to_closed_sets_terminal_state() -> None:
    snapshot = snapshot_from_broker_state(
        symbol="AAPL",
        entry_order_id="entry-1",
        entry_status="filled",
        entry_filled_qty=5,
        position_qty=0,
        has_exit_orders=True,
        exit_statuses=["filled"],
    )
    closed = advance_to_closed(snapshot, exit_order_id="exit-1")
    assert closed.state is BrokerLifecycleState.CLOSED
    assert closed.is_flat is True
    assert closed.has_open_position is False
    assert closed.exit_order_id == "exit-1"