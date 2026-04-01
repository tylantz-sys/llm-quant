# No-Signal Runtime Runbook

This runbook explains how to diagnose and interpret a live runtime that appears active but is producing no new trades.

For current per-pod, per-sleeve, and per-strategy status, use `docs/governance/strategy-artifact-status-matrix.md` as the operator-facing source of truth. This runbook explains how to interpret a no-trade runtime state; the status matrix records whether the relevant sleeve or strategy is actually verified, enabled, promotable, or still only a candidate.

Its primary use case is the observed state:

- scheduler/service is active
- market data is fresh
- decisions are fresh
- logs show repeated `risk_off`
- logs show repeated `0 signals`
- no trades are being executed

This state is not automatically a failure. It may represent a healthy no-trade regime, an intentionally conservative policy layer, missing promoted strategy supply, stale internal state, or a broken signal pipeline. This document exists to distinguish those cases.

---

## Purpose

This runbook answers five operator questions:

1. **Is the runtime actually alive?**
2. **Is the data fresh enough to support trading decisions?**
3. **Are decisions being generated on schedule?**
4. **Are signals absent because the model/strategy sees nothing, or because policy/risk layers are suppressing them?**
5. **When should a no-trade state be accepted versus escalated?**

This document should be used alongside:
- `docs/governance/runtime-truth-table.md`
- `docs/governance/control-matrix.md`
- `docs/governance/hybrid-intraday-runtime.md`
- `docs/governance/strategy-artifact-status-matrix.md`
- `docs/governance/validation-requirements-matrix.md`

When an operator is deciding whether a quiet runtime is acceptable, consult the documents in this order:

1. `docs/governance/strategy-artifact-status-matrix.md` — verify what sleeve/strategy is actually enabled, verified, and promotion-ready versus merely described in research
2. `docs/governance/validation-requirements-matrix.md` — verify what requirements must be met before a strategy or sleeve should be trusted
3. this runbook — diagnose whether the current no-trade state is healthy inactivity, suppression, governance supply shortage, or runtime failure

---

## Canonical Definitions

### Runtime Alive
A runtime is considered alive when:
- its service/timer/scheduler is active as intended
- its decision loop is executing on expected cadence
- new logs or DB events continue to appear
- it is not silently stalled

### Data Fresh
Data is fresh when:
- required symbols have recent market bars consistent with configured cadence
- timestamps are within the freshness threshold for the sleeve
- no material gaps or stale-feed conditions exist

### Decision Fresh
A decision is fresh when:
- a new decision record is created on the expected runtime cadence
- the decision references current market context
- the decision loop is not replaying stale context

### No-Signal State
A no-signal state means:
- the runtime completed a normal decision cycle
- the strategy/model returned no actionable entries or exits
- no execution occurred because no valid trades were produced

### Suppressed-Signal State
A suppressed-signal state means:
- an underlying strategy or model produced a candidate signal
- a policy, overlay, risk, governance, or execution layer blocked it before execution

### Broken Runtime State
A broken runtime state means:
- no fresh decisions
- or no fresh data
- or logs/DB indicate failing execution path
- or signal generation is silently missing when it should not be

---

## The Core Diagnostic Distinction

A live no-trade state can come from four materially different causes:

| State | Meaning | Healthy? | Action |
|---|---|---|---|
| No data | Feed or ingestion problem | No | Escalate immediately |
| No decisions | Scheduler/model/runtime problem | No | Escalate immediately |
| No signals | Strategy/model found no valid trades | Sometimes | Investigate if persistent |
| Suppressed signals | Signals existed but were vetoed | Sometimes | Investigate policy/risk layer |

The key operator mistake this runbook is designed to prevent is treating all four states as equivalent.

---

## Primary Diagnosis Flow

Follow these checks in order.

### Step 1 — Confirm the active runtime identity
Verify:
- which pod is running
- which service/timer launched it
- which config file or profile is controlling it
- whether the runtime is intentionally enabled
- whether the corresponding sleeve/strategy is marked as `disabled`, `candidate`, `paper`, `canary`, or `promoted` in `docs/governance/strategy-artifact-status-matrix.md`

Questions:
- Is this `default`, `crypto`, or another pod?
- Is the currently running pod the one you think is running?
- Is the config path known and current?

If config identity is unknown, stop assuming runtime behavior is meaningful.

### Step 2 — Confirm the service loop is alive
Check:
- systemd/service/timer health
- current status output
- recent log timestamps
- expected cadence

Healthy result:
- service active or timer firing on schedule
- recent logs within expected cadence
- no restart loop or dead process

Unhealthy result:
- service inactive unexpectedly
- timer not firing
- repeated crashes
- long silence in logs

### Step 3 — Confirm data freshness
Check:
- latest timestamp for required symbols
- symbol coverage for the active sleeve
- expected bar interval
- whether current market state should be generating bars at all

Healthy result:
- latest bars are within cadence expectations
- required symbols are present
- timestamps align with market hours / crypto 24x7 expectations

Unhealthy result:
- stale bars
- missing symbols
- partial feed
- outdated intraday timestamps

If data is stale, treat any no-signal state as non-trustworthy.

### Step 4 — Confirm fresh decisions are being written
Check:
- latest decision records in DB/logs
- latest context records
- cadence of decision creation
- whether the newest decisions reference current timestamps

Healthy result:
- decision timestamps advance on schedule
- context timestamps advance on schedule
- recent decisions exist for active pod

Unhealthy result:
- no recent decisions
- context lagging behind market bars
- stale or repeated decision payloads

If decisions are stale, the runtime is not healthy even if the service is “active.”

### Step 5 — Determine whether the system is in no-signal or suppressed-signal mode
Check logs/telemetry for:
- explicit `0 signals`
- explicit `risk_off`
- overlay vetoes
- expectancy gating
- rotation exclusion
- promoted strategy set emptiness
- execution rejection

Interpretation:
- `0 signals` with no veto evidence suggests a genuine no-signal state
- `risk_off` suggests policy/risk suppression
- repeated overlay vetoes suggest suppression by overlay governance
- empty promoted set suggests a configuration/governance supply issue rather than market inactivity

### Step 6 — Determine persistence and compare to expectations
A single no-trade cycle is not meaningful. What matters is persistence relative to expected signal density.

Questions:
- How many consecutive cycles showed no signals?
- Is this normal for the active sleeve/strategy family?
- Does backtest or paper history suggest a much higher expected cadence?
- Has the system moved from normal signal density to near-zero abruptly?

This is where generalization monitoring matters:
- if live signal density is materially below backtest/paper expectation, investigate
- if live signal density matches a historically sparse sleeve, the state may be healthy

---

## Known Causes of `risk_off / 0 signals`

The following categories are the main explanations for a repeated no-trade state.

### 1. Genuine market regime inactivity
Examples:
- trend filters not aligned
- mean-reversion thresholds not breached
- macro/risk filters indicate no edge
- volatility regime does not justify entry

Interpretation:
- possibly healthy
- should still be compared to historical signal density expectations

### 2. Deliberate policy suppression
Examples:
- overlay governor strictness
- `claude_overlay_only = true`
- risk governor triggered
- expectancy gate downscaling or blocking
- strategy rotation threshold excluding sparse candidates

Interpretation:
- runtime may be healthy but highly conservative
- this is not the same as “no edge detected”
- requires telemetry to distinguish signal absence from signal veto

Current audited default-pod example:
- `config/default.toml` uses `signal_source = "strategy_overlay"`
- strict overlay behavior is enabled with `overlay_governor_strict = true`
- the pod is further constrained by `claude_overlay_only = true`
- strategy rotation is enabled
- `asset_class_filter = ["equity", "fixed_income"]`
- `intraday_rth_guard = true`

Operational meaning:
- the `default` pod can legitimately produce no-trade cycles even when data and decisions are fresh
- repeated quiet cycles can reflect strict overlay governance, rotation exclusion, asset filtering, expectancy control, and RTH-only behavior rather than a broken runtime

### 3. Missing promoted strategy supply
Examples:
- active sleeve points to an empty promoted set
- candidates exist in research but none are actually promotable/runtime eligible
- catalog/config mismatch

Interpretation:
- this is a governance/config supply problem, not a market inference

Current audited example:
- the live `crypto` pod points to `strategy_set = "promoted_crypto"` in `config/strategies/crypto.toml`
- `config/strategies/catalog.toml` currently defines `promoted_crypto = []`
- candidate crypto supply exists separately, but under `crypto-ethbtc-paper` via `strategy_set = "candidate_crypto"`

Operational meaning:
- repeated `risk_off / 0 signals` for the live `crypto` pod can be a healthy governance-constrained outcome
- this should not be confused with broken market-data ingestion or a dead scheduler
- if operators expect live crypto signal generation, they must first confirm that promoted crypto supply is intentionally non-empty

### 4. State or dependency drift
Examples:
- stale internal position/order state
- scheduler drift
- environment/config mismatch
- broken dependency path while process remains technically alive

Interpretation:
- dangerous because it can look superficially healthy
- requires DB/log/config verification

### 5. Broken signal generation path
Examples:
- strategy code not running as expected
- symbol filtering excludes intended universe
- execution path bypasses normal runtime logic
- decision parser succeeds but candidate extraction is empty due to mismatch

Interpretation:
- operational issue until disproven

---

## When the State Is Acceptable

A repeated no-trade state may be acceptable only if all of the following are true:

- runtime identity is known
- service cadence is healthy
- data freshness is healthy
- decisions are fresh
- the active strategy set is non-empty and intended
- telemetry shows the reason for no trades
- the observed no-trade frequency is consistent with historical expectation for that sleeve
- no hidden veto/error path is masking signal opportunities

If those are not all true, do not assume the quiet runtime is healthy.

---

## When to Escalate Immediately

Escalate immediately if any of the following occur:

- no fresh data
- no fresh decisions
- active runtime identity unknown
- active promoted set appears empty unintentionally
- signal state cannot distinguish “no signal” from “vetoed signal”
- repeated `risk_off` with no reason code or telemetry detail
- abrupt live drop in signal density relative to backtest/paper baseline
- execution path appears bypassed or inconsistent with documented runtime
- logs indicate repeated silent failures, retries, or stale state loops

---

## Required Operator Checks

Use the following checklist before interpreting a no-trade runtime as healthy:

- [ ] Active service/timer identified
- [ ] Active pod identified
- [ ] Active config path identified
- [ ] Latest market data timestamps verified
- [ ] Required symbols verified present
- [ ] Latest decision timestamps verified
- [ ] Latest context timestamps verified
- [ ] Signal count observed
- [ ] Reason for `risk_off` or veto state observed
- [ ] Promoted strategy supply confirmed non-empty if expected
- [ ] Execution rejection / broker failure ruled out
- [ ] Signal density compared against backtest/paper expectation

If any item remains unchecked, the interpretation is incomplete.

## Fast Operator Classification Checklist

Use this appendix when triaging a quiet runtime quickly.

### Case 1 — Intentionally stopped services
Indicators:
- `systemctl` / `systemctl --user` shows the runtime service or timer is inactive or disabled by design
- `docs/governance/strategy-artifact-status-matrix.md` marks the pod as runtime-disabled
- there are no fresh runtime logs because the runtime is not supposed to be running

Interpretation:
- this is an intentional operational state, not a no-signal runtime
- do not diagnose signal generation until service state is restored intentionally

### Case 2 — Healthy no-signal state
Indicators:
- service cadence is healthy
- data freshness is healthy
- decision cadence is healthy
- logs show normal cycle completion with `0 signals` or quiet hold behavior
- no hard broker/runtime failures are present

Interpretation:
- the runtime is alive, but no valid trades were produced this cycle
- this can be acceptable for sparse sleeves or strict policy-governed sleeves

### Case 3 — Stale or missing data
Indicators:
- required symbols are missing
- bar timestamps are outside expected freshness bounds
- intraday symbol coverage is incomplete
- the decision loop may still run, but it is operating on degraded inputs

Interpretation:
- do not trust a no-signal outcome until data freshness is restored
- treat as a data-plane incident first, not a market-regime conclusion

### Case 4 — Broken runtime
Indicators:
- no fresh decisions
- no fresh contexts
- repeated hard errors
- broker/client/clock/protection failures
- execution path or scheduler is failing unexpectedly

Interpretation:
- this is an operational incident
- do not interpret `risk_off / 0 signals` as meaningful market behavior

### Case 5 — Healthy but policy-suppressed runtime
Indicators:
- service, data, and decisions are all fresh
- logs show repeated `risk_off`
- overlay, governance, rotation, or expectancy controls are active
- candidate signals may exist but are vetoed or downscaled before execution

Interpretation:
- the runtime is functioning, but the policy stack is intentionally restrictive
- compare veto frequency against design expectations before changing controls

---

## Recommended Telemetry Fields

The runtime should expose enough information to make no-signal states interpretable. Recommended fields:

### Data Layer
- pod_id
- symbol
- latest_bar_timestamp
- bar_count_ingested
- freshness_status

### Decision Layer
- decision_id
- decision_timestamp
- context_timestamp
- model
- regime classification
- signal_count

### Policy / Overlay Layer
- base_signal_count
- post_overlay_signal_count
- overlay_veto_count
- veto_reason
- risk_off_reason
- expectancy_gate_status
- rotation_filter_status

### Execution Layer
- eligible_orders
- routed_orders
- rejected_orders
- canceled_orders
- fill_count
- rejection_reason

### Monitoring Layer
- expected_signal_density
- actual_signal_density
- paper_signal_density
- degradation_ratio
- consecutive_zero_signal_cycles

Without these distinctions, a quiet runtime remains ambiguous.

---

## Operator Interpretation Guide

### Case A — Healthy no-trade regime
Conditions:
- fresh data
- fresh decisions
- no veto anomalies
- historically sparse signal sleeve
- no execution/path issues

Interpretation:
- accept and continue monitoring

### Case B — Conservative suppression
Conditions:
- fresh data
- fresh decisions
- repeated vetoes or `risk_off`
- overlay/risk/rotation filters highly active

Interpretation:
- runtime healthy, but policy stack may be too restrictive for current objectives
- do not loosen controls blindly; measure veto rate and value-add first

### Case C — Governance supply shortage
Conditions:
- runtime alive
- no eligible promoted strategies
- candidate strategies exist in docs/research but not in promoted runtime set

Interpretation:
- this is a strategy supply / promotion pipeline issue, not a live execution issue
- confirm this explicitly in `docs/governance/strategy-artifact-status-matrix.md` before treating the runtime as healthy inactivity

Current audited crypto example:
- live crypto runtime intent is promoted-only
- candidate crypto experimentation is intentionally isolated into `crypto-ethbtc-paper`
- operators should not assume that the existence of candidate crypto research implies live crypto signal supply

### Case D — Broken runtime
Conditions:
- stale data, stale decisions, missing contexts, or silent failures

Interpretation:
- operational incident
- do not infer market regime from this state

---

## Relation to the Master Improvement Plan

This runbook supports the broader plan in four ways:

1. **Governance truth alignment**  
   It separates runtime activation from promotion/completeness.

2. **Validation hardening**  
   It makes signal-density and degradation monitoring operationally necessary.

3. **Runtime observability**  
   It defines the minimum distinctions needed to interpret `risk_off / 0 signals`.

4. **Future regime-scaling work**  
   It provides the observability layer needed before evaluating softer staged exposure instead of binary suppression.

---

## Current 2026-03-31 Interpretation Baseline

Based on the reviewed runtime observations available during the March 31 analysis, the currently observed crypto runtime state is:

- service loop active
- 5-minute crypto bar ingestion active
- fresh crypto decisions present
- repeated `risk_off / 0 signals`
- no evidence yet that this alone implies a broken runtime

The correct conclusion at this stage is:

**the runtime appears alive, but the no-trade state must be interpreted through config, promoted strategy supply, policy veto rates, and historical signal-density expectations before it can be called healthy or overly restrictive.**

Additional audited config interpretation:

- the live `crypto` pod is best interpreted as a promoted-only sleeve, not a candidate-research sleeve
- `crypto-ethbtc-paper` is the candidate-paper lane and should be treated separately from live crypto runtime expectations
- the `default` pod has non-empty promoted supply, but strict overlay governance, strategy rotation, asset-class filtering, expectancy controls, and RTH gating can all legitimately reduce trade output to zero during healthy runtime operation

---

## Version History

| Version | Date | Change |
|---------|------|--------|
| 1.2 | 2026-03-31 | Added audited operator guidance clarifying why the live `crypto` pod can show `risk_off / 0 signals` when `promoted_crypto` is empty, why the `default` pod can legitimately remain quiet under strict overlay/rotation/RTH controls, added a fast classification checklist, and clarified the separation between live promoted crypto and `crypto-ethbtc-paper` candidate runtime intent. |
| 1.1 | 2026-03-31 | Cross-linked the strategy artifact/status matrix as the operator-facing source of truth for sleeve/strategy status and clarified the order of use between status, requirements, and no-signal diagnosis. |
| 1.0 | 2026-03-31 | Initial no-signal runtime runbook created to interpret fresh-data/fresh-decision/no-trade states and distinguish healthy inactivity from suppression or runtime failure. |
