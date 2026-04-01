# Phase 6 Runtime Audit

## Scope
Audit only. No project source files modified.

## Files reviewed
- scripts/execute_decision.py
- src/llm_quant/trading/ledger.py
- src/llm_quant/trading/telemetry.py
- src/llm_quant/trading/runtime_controls.py
- src/llm_quant/trading/executor.py
- src/llm_quant/trading/harvest_metrics.py
- src/llm_quant/trading/intraday.py
- src/llm_quant/trading/exits.py
- src/llm_quant/strategies/runtime.py
- src/llm_quant/config.py
- src/llm_quant/risk/manager.py
- src/llm_quant/surveillance/models.py
- src/llm_quant/surveillance/scanner.py
- scripts/run_surveillance.py
- docs/governance/runtime-truth-table.md

## Primary runtime attachment points

### 1. `scripts/execute_decision.py`
Best primary enforcement hook for per-run Phase 6 consequences.

Current flow:
1. parse decision
2. load portfolio/prices
3. evaluate exits
4. merge signals
5. risk filter
6. execute
7. persist trades/snapshot
8. emit summary

Best insertion point:
- after portfolio/prices are loaded
- before or around `all_signals = exit_signals + decision.signals`

This supports:
- allocation shrink: scale BUY weights before risk filtering
- conservative mandate: filter/limit BUYs before risk filtering
- temporary EOD flatten: inject forced CLOSE signals for open positions
- demotion / paper revalidation: emit recommendation and telemetry, not direct config mutation

Also natural place to include a new `harvest_governance` block in the JSON summary.

### 2. `src/llm_quant/trading/runtime_controls.py`
Best place for small pure helpers used by execution runtime.

This module already centralizes testable runtime transforms. Smallest additions should live here:
- evaluate harvest thresholds against computed metrics
- scale BUY signals
- apply conservative asset-class filter
- synthesize flatten CLOSE signals

### 3. `src/llm_quant/trading/harvest_metrics.py`
Best source for governance inputs, not enforcement.

Use:
- `compute_harvest_metrics_from_db(...)` with `pod_id` and a lookback window

Keep it metrics-only.

### 4. `src/llm_quant/trading/telemetry.py`
Best current place for append-only audit of Phase 6 evaluation and actions.

Existing patterns:
- `log_decision_context`
- `log_profit_take_event`

Smallest new telemetry contract:
- a new harvest-governance evaluation/action logger parallel to existing append-only helpers

### 5. `src/llm_quant/trading/ledger.py`
Not ideal as the main governance sink, but useful for trade provenance.

Current practical options:
- encode governance provenance in `reasoning`
- pass governance-oriented `decision_source` on forced flatten batches if implementation adds that call path

### 6. `src/llm_quant/trading/exits.py`
Useful pattern source for forced flatten.

Phase 6 flatten should look like normal exit-engine output:
- `TradeSignal(action=CLOSE, target_weight=0.0, conviction=HIGH, reasoning=...)`

That avoids executor changes.

### 7. `src/llm_quant/risk/manager.py`
Not the right primary hook.

Reason:
- this module enforces safety limits
- harvest governance is a runtime policy/lifecycle layer
- consequences should be applied before signals enter risk checks

### 8. Surveillance surface
- `src/llm_quant/surveillance/scanner.py`
- `scripts/run_surveillance.py`

Good for reporting and lifecycle follow-up, but not immediate in-run enforcement.

## Consequence mapping

### Allocation shrink
- Hook: `scripts/execute_decision.py`
- Mechanism: scale BUY target weights before risk filtering
- Helper home: `src/llm_quant/trading/runtime_controls.py`

### Conservative mandate
- Hook: `scripts/execute_decision.py`
- Mechanism: filter BUYs to allowed asset classes or tighter scope
- Helper home: `src/llm_quant/trading/runtime_controls.py`
- Existing useful pattern: `filter_signals_by_asset_class`

### Temporary EOD flatten
- Hook: `scripts/execute_decision.py`
- Mechanism: prepend forced CLOSE signals for all open positions
- Pattern source: `src/llm_quant/trading/exits.py`

### Demotion
- Prefer lifecycle/governance state, not direct trading runtime mutation
- Runtime should emit recommendation + telemetry

### Paper revalidation
- Prefer lifecycle/governance state, not direct trading runtime mutation
- Runtime may optionally block new BUYs if parent implements a temporary policy gate, but the cleaner minimum is recommendation + telemetry

## Smallest proposed shared contract

### Config
Place Phase 6 config under existing profit-taking governance tree:
- `governance.profit_taking.runtime_enforcement`

Suggested fields:
- `enabled`
- `lookback_days`
- `min_executed_events`
- threshold fields:
  - `min_capture_ratio`
  - `max_giveback_ratio`
  - `min_trailing_salvage_rate`
  - `min_realized_retention`
  - `min_tp1_effectiveness`
- consequence fields:
  - `allocation_shrink_scale`
  - `conservative_allowed_asset_classes`
  - `flatten_on_breach`
  - `lifecycle_recommendation_mode`

### Runtime evaluation object
Suggested minimum shape:
- `metrics: dict[str, Any]`
- `breached_rules: list[dict[str, Any]]`
- `actions: list[str]`
- `allocation_scale: float`
- `conservative_allowed_asset_classes: list[str]`
- `force_flatten: bool`
- `lifecycle_recommendation: str | None`

### Telemetry
One append-only evaluation record per run containing:
- `pod_id`
- `timestamp`
- lookback window
- metrics snapshot
- breached rules
- actions taken
- affected signal counts

## Key assumptions
- Preserve separation of concerns:
  - metrics in `harvest_metrics.py`
  - transforms in `runtime_controls.py`
  - orchestration in `scripts/execute_decision.py`
  - audit in `telemetry.py`
- Demotion and paper revalidation are lifecycle outcomes, so runtime should recommend/record them rather than directly rewrite pod config.
- Forced flatten should be represented as ordinary CLOSE signals.

## Blockers / gaps
- No clear existing pod lifecycle state module was found in `src/llm_quant`.
- No generic governance-event telemetry helper exists today.
- Standard execution path does not currently carry rich governance provenance through ledger writes unless implementation adds it explicitly.