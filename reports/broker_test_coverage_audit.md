# Broker Test Coverage Audit

Source of truth reviewed: `docs/governance/runtime-truth-table.md`

## Overall take

Broker-specific unit coverage exists for the main new primitives under `tests/test_broker/`: intraday native OCO placement/reconciliation, reconciliation persistence, lifecycle state machine rules, event ledger rebuilds, executor submission tracking, simple take-profit math, trailing-stop math, RTH guard behavior, and crypto fractional market-order handling.

Coverage is strongest at the helper/unit level. Coverage is weaker for end-to-end runtime orchestration across the full truth-table modes, especially daily Alpaca native bracket flow, synthetic intraday Alpaca monitoring path, flatten/monitor services, and fail-loud protection verification integrated through the runtime.

## Tested behaviors

### 1. Intraday native OCO order path
- `tests/test_broker/test_intraday_orders.py::test_place_oco_exits_for_buys_creates_partial_and_oco`
  - Verifies partial take-profit limit plus OCO remainder are both created.
  - Verifies state fields are populated: `partial_tp_order_id`, `oco_order_id`, `oco_tp_order_id`, `oco_stop_order_id`.
  - Verifies quantity split and take-profit math.
- `tests/test_broker/test_intraday_orders.py::test_reconcile_orders_fallbacks_when_oco_legs_missing`
  - Verifies repeated reconciliation detects missing OCO legs and falls back to a stop order.

This maps well to the truth-table row:
- `intraday_enabled=true`
- `broker=alpaca`
- `intraday_use_oco=true`
- native exit orders with partial TP + OCO remainder + trailing management

### 2. Protection guard / fail-closed behavior for broken live protection
- `tests/test_broker/test_intraday_orders.py::test_reconcile_orders_fail_closed_when_fractional_crypto_stop_cannot_be_restored`
  - Verifies `fail_on_unprotected=True` raises `AlpacaError` if stop restoration fails.

This directly covers the runtime truth-table guard:
- “Exit protection guard: if `fail_on_unprotected_exits = true`, the runtime fails loudly when native live protection cannot be verified.”

### 3. Fractional crypto order metadata and quantity preservation
- `tests/test_broker/test_intraday_orders.py::test_place_oco_exits_for_buys_preserves_fractional_crypto_qty`
  - Verifies fractional quantity split is preserved through intraday exit creation.
- `tests/test_broker/test_executor_submission_tracking.py::test_submit_order_intents_preserves_notional_and_fractional_metadata`
  - Verifies notional, `allow_fractional`, `gtc`, and asset class metadata are preserved.
- `tests/test_broker/test_crypto_orders.py::test_crypto_market_orders_use_fractional_qty_and_gtc`
  - Verifies crypto Alpaca market orders normalize symbol format and use fractional quantity with `gtc`.

This gives decent coverage for crypto-specific broker submission behavior, but mostly on entry/submission helpers.

### 4. Reconciliation persistence and replay
- `tests/test_broker/test_reconciliation.py::test_persist_submitted_orders_stores_tracking_fields`
  - Verifies submitted-order tracking fields are persisted.
- `tests/test_broker/test_reconciliation.py::test_reconcile_broker_orders_applies_broker_fill_and_persists_lifecycle`
  - Verifies broker fills are applied to the portfolio and lifecycle is persisted.
- `tests/test_broker/test_reconciliation.py::test_reconciliation_rebuilds_portfolio_from_persisted_fill_history`
  - Verifies replay/rebuild from persisted fill history reproduces portfolio state.
- `tests/test_broker/test_reconciliation.py::test_reconciliation_requires_event_confirmed_position_close`
  - Verifies close state depends on confirmed fill/event history plus flat broker position.

This is strong evidence that reconciliation and persisted broker state are intentionally tested.

### 5. Broker lifecycle state machine invariants
- `tests/test_broker/test_state_machine.py::test_derive_lifecycle_entry_pending`
- `tests/test_broker/test_state_machine.py::test_derive_lifecycle_entry_filled`
- `tests/test_broker/test_state_machine.py::test_derive_lifecycle_active_monitoring_requires_position_and_exit_orders`
- `tests/test_broker/test_state_machine.py::test_derive_lifecycle_exit_pending_when_exit_order_open`
- `tests/test_broker/test_state_machine.py::test_derive_lifecycle_closed_when_exit_fills_and_position_is_flat`
- `tests/test_broker/test_state_machine.py::test_entry_submission_gate_allows_only_pending_flat_state`
- `tests/test_broker/test_state_machine.py::test_entry_submission_gate_blocks_after_entry_fill`
- `tests/test_broker/test_state_machine.py::test_advance_to_bracket_attached`
- `tests/test_broker/test_state_machine.py::test_bracket_attachment_requires_filled_entry`
- `tests/test_broker/test_state_machine.py::test_monitoring_requires_exit_protection`
- `tests/test_broker/test_state_machine.py::test_advance_to_active_monitoring_from_bracket_attached`
- `tests/test_broker/test_state_machine.py::test_exit_submission_requires_open_position`
- `tests/test_broker/test_state_machine.py::test_advance_to_exit_pending_from_active_monitoring`
- `tests/test_broker/test_state_machine.py::test_closed_state_requires_flat_confirmation`
- `tests/test_broker/test_state_machine.py::test_advance_to_closed_sets_terminal_state`

This is one of the best-covered areas in the new broker logic.

### 6. Event ledger append/history/rebuild semantics
- `tests/test_broker/test_event_ledger.py::test_get_events_for_order_returns_immutable_event_history`
- `tests/test_broker/test_event_ledger.py::test_rebuild_position_state_from_events_reconstructs_open_position`
- `tests/test_broker/test_event_ledger.py::test_rebuild_position_state_from_events_reconstructs_closed_position`

This covers the ledger as an immutable audit trail plus state rebuild source.

### 7. Executor submission tracking
- `tests/test_broker/test_executor_submission_tracking.py::test_submit_order_intents_returns_tracked_orders_with_ids_and_status`
  - Verifies returned tracked orders contain broker IDs, status, filled qty/avg price defaults, and raw broker payload.

### 8. Basic bracket/take-profit and trailing-stop math helpers
- `tests/test_broker/test_take_profit.py::test_resolve_take_profit_pct`
- `tests/test_broker/test_take_profit.py::test_resolve_take_profit_rr`
- `tests/test_broker/test_take_profit.py::test_bracket_prices_valid`
- `tests/test_broker/test_trailing_stop.py::test_trailing_stop_updates_on_new_high`
- `tests/test_broker/test_trailing_stop.py::test_trailing_stop_no_update_without_new_high`

These validate isolated calculation helpers, not full broker workflows.

### 9. RTH guard and run-lock support around intraday runtime
- `tests/test_broker/test_rth_guard.py::test_rth_guard`
- `tests/test_broker/test_rth_guard.py::test_rth_guard_skip_logic`
- `tests/test_trading/test_run_lock.py::test_run_lock_dedupes_same_slot`

These partially cover cross-cutting truth-table guards:
- configurable RTH guard
- one run per pod per 5-minute slot

### 10. Canonical synthetic intraday exit engine behavior on trading side
Cross-cutting tests in `tests/test_trading/test_intraday_profit_taking.py` cover the policy layer that broker realization paths are supposed to honor:
- `test_partial_take_profit_signal`
- `test_trailing_stop_after_partial`
- `test_scale_in_adjustment`
- `test_reentry_cooldown_blocks_buy`
- `test_merge_intraday_signals_prioritizes_profit_exits`

These do not test broker order placement directly, but they do validate canonical synthetic exit semantics described in the truth table.

### 11. Runtime guard preventing paper executor from being used in Alpaca mode
- `tests/test_trading/test_executor_runtime_guard.py::test_ensure_runtime_execution_allowed_rejects_alpaca_mode`
- `tests/test_trading/test_executor_runtime_guard.py::test_execute_signals_rejects_alpaca_mode_without_mutating_portfolio`
- `tests/test_trading/test_executor_runtime_guard.py::test_execute_signals_still_allows_paper_mode`

This is a useful cross-cutting guard ensuring real broker mode does not silently route through the paper executor.

## Weakly tested behaviors

### 1. Daily Alpaca native bracket path
There is only helper-level math validation in:
- `tests/test_broker/test_take_profit.py::*`

But there does not appear to be a test that submits a daily Alpaca bracket order end-to-end and verifies:
- entry + attached TP/SL intent structure
- tracked submitted orders for bracket parent/legs
- lifecycle transition into bracket-attached state from actual executor behavior

Given the truth table explicitly calls out:
- `intraday_enabled=false`
- `broker=alpaca`
- “Alpaca native entry” with “Native bracket TP/SL”

this path looks under-covered.

### 2. Native OCO reconciliation breadth
`tests/test_broker/test_intraday_orders.py` covers:
- successful placement
- missing-leg fallback
- fail-closed stop restoration failure

But it does not obviously cover broader native-order reconciliation scenarios such as:
- partial TP fill changing remaining quantities
- trailing stop updates across multiple market highs
- order cancel/replace flows
- broker status permutations beyond the narrow stubs used

### 3. Reconciliation edge cases
`tests/test_broker/test_reconciliation.py` is solid for core persistence/replay, but weak on:
- duplicate broker events across repeated runs
- canceled/rejected orders
- partial fills across multiple events for the same order
- mismatches between broker positions and local state beyond a simple flat/non-flat close case
- multiple symbols/pods interacting in one reconciliation pass

### 4. Event ledger breadth
The ledger tests validate append/history/rebuild, but not much around:
- duplicate event idempotency
- event ordering anomalies
- partial-fill sequences over time
- relation between ledger events and live broker reconciliation in a larger integrated flow

### 5. Crypto runtime integration
`tests/test_trading/test_crypto_runtime_integration.py` is only a placeholder file, so despite crypto-specific broker tests, there is no real integration test for the truth-table crypto sleeve behavior:
- `intraday_rth_guard=false`
- `intraday_use_oco=false`
- synthetic monitoring by design

## Major gaps

### 1. No real tests for Alpaca synthetic intraday path (`intraday_use_oco=false`)
The truth table explicitly requires an Alpaca intraday mode with:
- market/limit entry orders
- synthetic partial TP / trailing / stop-loss
- context snapshots / position state

I did not find broker tests that exercise Alpaca broker mode with synthetic monitoring instead of native OCO exits. The nearest coverage is on the trading-side synthetic exit engine in:
- `tests/test_trading/test_intraday_profit_taking.py`

But that is policy-only coverage, not broker-runtime integration.

### 2. No visible tests for flatten/monitor services
The requested broker capability set includes flatten/monitoring. I found no explicit tests in `tests/test_broker/` for:
- end-of-day flatten override
- monitor loop behavior
- orphaned live protection remediation from a monitoring service
- broker-driven forced close flows

No test names reference `monitor` or `flatten`.

### 3. No end-to-end test for exit telemetry/context snapshot guardrails
The truth table requires:
- `intraday_position_state`
- `intraday_order_state`
- context snapshots with policy/runtime/protection metadata

I did not find tests asserting those telemetry payloads or snapshot contents in broker flows.

### 4. No cross-mode truth-table coverage
There is no visible suite that systematically exercises the runtime matrix:
- paper daily
- Alpaca daily bracket
- paper intraday synthetic
- Alpaca intraday native OCO
- Alpaca intraday synthetic

Coverage is fragmented across helpers rather than organized by truth-table mode.

### 5. No broker tests for overlay starvation / expectancy / drawdown guards
Those guards are in the governance doc, but broker-facing tests do not appear to cover their effect on execution or broker submission suppression. They may be tested elsewhere, but not as broker-runtime behavior.

### 6. No substantive tests in top-level `tests/test_crypto_orders.py`
This file is a placeholder and contributes no executable coverage.

## Bottom line

### Best-covered
- Lifecycle state machine
- Reconciliation persistence/replay
- Event ledger reconstruction
- Intraday native OCO helper flow
- Basic crypto fractional submission metadata
- RTH/run-lock helper guards

### Partially covered
- Protection fail-closed behavior
- Native OCO maintenance/reconciliation
- Daily bracket math/helpers
- Canonical synthetic intraday exit policy

### Not covered or effectively missing
- End-to-end daily Alpaca bracket execution flow
- End-to-end Alpaca intraday synthetic path (`intraday_use_oco=false`)
- Monitor/flatten orchestration
- Telemetry/context snapshot assertions for exit protection
- Comprehensive truth-table mode integration tests
- Real crypto runtime integration tests

## Files reviewed
- `docs/governance/runtime-truth-table.md`
- `tests/test_broker/test_intraday_orders.py`
- `tests/test_broker/test_reconciliation.py`
- `tests/test_broker/test_state_machine.py`
- `tests/test_broker/test_event_ledger.py`
- `tests/test_broker/test_executor_submission_tracking.py`
- `tests/test_broker/test_take_profit.py`
- `tests/test_broker/test_trailing_stop.py`
- `tests/test_broker/test_rth_guard.py`
- `tests/test_broker/test_crypto_orders.py`
- `tests/test_trading/test_intraday_profit_taking.py`
- `tests/test_trading/test_executor_runtime_guard.py`
- `tests/test_trading/test_run_lock.py`
- `tests/test_trading/test_crypto_runtime_integration.py`
- `tests/test_strategies_runtime.py`
- `tests/test_crypto_orders.py`