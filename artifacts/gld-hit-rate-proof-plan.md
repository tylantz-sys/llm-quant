# GLD hit-rate proof plan

## Purpose

This document defines a narrow governed proof plan for testing whether GLD can exceed a **52% closed-trade win rate** without any engine changes.

The plan is intentionally constrained. It preserves the **`gld-regime-starter-v1`** anchor entry regime and varies only:

- exit sensitivity
- add behavior

This is a proof question, not a promotion decision:

> Can the existing deterministic daily strategy implementation produce a GLD variant with `win_rate > 0.52` while keeping the original v1 entry logic intact?

This document does not authorize:

- engine changes
- new indicators
- new symbols
- new execution logic
- discretionary overrides
- additional strategy variants beyond the defined matrix

## Anchor reference

### Anchor strategy

- strategy slug: `gld-regime-starter-v1`
- strategy class: `spy_regime_starter`

Recorded anchor baseline:

- experiment id: `fc1ba67c`
- trades: `52`
- win rate: `51.9%`
- profit factor: `0.81`
- Sharpe: `0.272`
- max drawdown: `0.24%`

### Comparison reference

A governed comparison variant already exists and helps justify the scope constraint:

- strategy slug: `gld-regime-starter-v2`
- experiment id: `fdc87980`
- trades: `45`
- win rate: `44.4%`
- profit factor: `1.50`
- Sharpe: `0.690`
- max drawdown: `0.14%`

Interpretation:

- v2 improved efficiency metrics
- v2 reduced win rate materially below the current proof target
- therefore, this proof effort keeps entries anchored to v1 and tests only exits and adds

## Fixed controls

The following controls are fixed for the proof unless explicitly changed in the experiment matrix.

- engine: existing deterministic daily strategy implementation only
- strategy class: `spy_regime_starter`
- trade symbol: `GLD`
- volatility symbol: `VIX`
- group: `commodity_regime`
- track: `track_a`
- benchmark: `SPY 0.60 / TLT 0.40` monthly `total_return`
- rebalance frequency: daily
- execution lag: `execution_lag_days: 1`
- missing-VIX policy: `block_new_entries_allow_risk_exits`
- approval posture: shadow only; not approved for live capital
- data/governance flow: governed artifacts only
- entry regime: preserved from `gld-regime-starter-v1`

## Entry-regime invariance requirement

All proof variants must preserve the v1 anchor entry regime.

At minimum, the following entry-side conditions remain fixed from v1:

- `rsi_entry_threshold: 60.0`
- `vix_entry_max: 19.2`
- `vix_add_max: 18.5`
- `macd_add_min: 0.0`
- `cooldown_days_after_exit: 3`

Any run that alters entry gating is out of scope and must not be counted as evidence for this proof question.

## Experiment matrix

The proof authorizes exactly three governed variants.

| Variant | Test thesis | Entry regime | Exit behavior | Add behavior |
|---|---|---|---|---|
| `gld-regime-starter-v3` | Softer exits may convert marginal losers into small winners | same as v1 | changed | same as v1 |
| `gld-regime-starter-v4` | Disabling adds may remove add-induced degradation | same as v1 | same as v1 | changed |
| `gld-regime-starter-v5` | Softer exits plus no-add behavior may combine favorably | same as v1 | changed | changed |

### Variant v3: softer exits only

Intent:

- preserve v1 entries
- preserve v1 add behavior
- change only exit sensitivity

Exact parameter contract for v3:

- `trade_symbol: GLD`
- `vix_symbol: VIX`
- `starter_weight: 0.05`
- `max_weight: 0.08`
- `rsi_entry_threshold: 60.0`
- `rsi_exit_threshold: 48.0`
- `vix_entry_max: 19.2`
- `vix_add_max: 18.5`
- `vix_exit_min: 25.0`
- `macd_add_min: 0.0`
- `macd_exit_max: -0.02`
- `atr_stop_multiple: 1.25`
- `atr_stop_mode: fixed_at_entry`
- `max_adds: 1`
- `cooldown_days_after_exit: 3`
- `missing_vix_policy: block_new_entries_allow_risk_exits`
- `rebalance_frequency_days: 1`
- `execution_lag_days: 1`

Governance interpretation:

- pure exit-sensitivity test
- no change to v1 entry regime
- no change to add availability

### Variant v4: no-add only

Intent:

- preserve v1 entries
- preserve v1 exits
- change only add behavior by disabling adds

Exact parameter contract for v4:

- `trade_symbol: GLD`
- `vix_symbol: VIX`
- `starter_weight: 0.08`
- `max_weight: 0.08`
- `rsi_entry_threshold: 60.0`
- `rsi_exit_threshold: 50.0`
- `vix_entry_max: 19.2`
- `vix_add_max: 18.5`
- `vix_exit_min: 25.0`
- `macd_add_min: 0.0`
- `macd_exit_max: 0.0`
- `atr_stop_multiple: 1.10`
- `atr_stop_mode: fixed_at_entry`
- `max_adds: 0`
- `cooldown_days_after_exit: 3`
- `missing_vix_policy: block_new_entries_allow_risk_exits`
- `rebalance_frequency_days: 1`
- `execution_lag_days: 1`

Additional implementation note for v4:

- adds are disabled and should be documented as blocked
- starter allocation equals max allocation by design

Governance interpretation:

- pure add-behavior test
- no exit-softening is permitted in this lane

### Variant v5: softer exits plus no-add

Intent:

- preserve v1 entries
- combine the v3 exit changes with the v4 no-add behavior

Exact parameter contract for v5:

- `trade_symbol: GLD`
- `vix_symbol: VIX`
- `starter_weight: 0.08`
- `max_weight: 0.08`
- `rsi_entry_threshold: 60.0`
- `rsi_exit_threshold: 48.0`
- `vix_entry_max: 19.2`
- `vix_add_max: 18.5`
- `vix_exit_min: 25.0`
- `macd_add_min: 0.0`
- `macd_exit_max: -0.02`
- `atr_stop_multiple: 1.25`
- `atr_stop_mode: fixed_at_entry`
- `max_adds: 0`
- `cooldown_days_after_exit: 3`
- `missing_vix_policy: block_new_entries_allow_risk_exits`
- `rebalance_frequency_days: 1`
- `execution_lag_days: 1`

Governance interpretation:

- combined interaction test
- highest-priority candidate only after isolated effects from v3 and v4 are observed

## Required metrics

Each proof run must record at minimum:

- strategy slug
- experiment id
- trade count
- closed-trade win rate
- profit factor
- Sharpe ratio
- max drawdown
- total return
- benchmark return
- excess return versus benchmark

If available in the standard reporting path, also capture:

- average winner
- average loser
- expectancy per trade
- exposure or average capital utilization

### Mandatory gating metrics

For proof acceptance, the two hard thresholds are:

- `win_rate > 0.52`
- `trade_count >= 30`

All other metrics are required for interpretation and guardrail review.

## Pass / fail rules

### Pass condition

A variant passes the proof objective only if all of the following are true:

- [ ] `win_rate > 0.52`
- [ ] `trade_count >= 30`
- [ ] result is generated without engine changes
- [ ] entry regime is identical to `gld-regime-starter-v1`
- [ ] only the authorized exit and/or add changes for that variant are present

### Fail condition

A variant fails the proof if any of the following occur:

- [ ] `win_rate <= 0.52`
- [ ] `trade_count < 30`
- [ ] entry logic differs from v1
- [ ] unauthorized parameters are changed
- [ ] engine code is modified
- [ ] result is not reproducible from governed artifacts

### Secondary quality screen

A variant that passes the win-rate proof should be marked **provisionally credible** only if it also avoids obvious degradation such as:

- severe collapse in profit factor
- clearly unacceptable increase in max drawdown for the GLD starter context
- suspiciously low trade count near the minimum threshold
- evidence that improvement is dependent on a narrow historical pocket

This secondary screen does not change whether the hit-rate proof passed. It changes how the result is interpreted.

## Interpretation categories

### Category A: objective achieved cleanly

Criteria:

- `win_rate > 0.52`
- `trade_count >= 30`
- no engine changes
- no material degradation in supporting quality metrics

Interpretation:

- strongest evidence that the target is reachable within current governed scope
- eligible for follow-on robustness and walk-forward work

### Category B: objective achieved but fragile

Criteria:

- `win_rate > 0.52`
- `trade_count >= 30`
- one or more supporting metrics deteriorate enough to create practical doubt

Interpretation:

- proof question answered yes in a narrow sense
- not sufficient for promotion
- requires robustness review before any further consideration

### Category C: near miss

Criteria:

- win rate improves meaningfully versus v1
- but remains `<= 0.52` or trade count drops below threshold

Interpretation:

- thesis may have directional support
- proof target not achieved
- no basis for expanding scope unless explicitly re-authorized

### Category D: thesis rejected

Criteria:

- win rate does not improve meaningfully, or worsens
- and/or supporting metrics indicate the change damaged overall behavior

Interpretation:

- the tested mechanism does not support the hit-rate objective under current constraints

## Stopping rules

Stop the proof effort immediately under any of the following conditions.

### 1. Scope violation

Stop if any proposed run requires:

- engine changes
- altered v1 entry logic
- new indicators
- new symbols
- new execution assumptions
- extra variants outside v3, v4, or v5

### 2. Matrix exhaustion

Stop when:

- v3, v4, and v5 have all been executed and classified
- no further variants are authorized by this proof plan

### 3. Clean success

Stop when:

- at least one variant reaches Category A

Next step should move to robustness or paper-governance work rather than ad hoc additional tuning.

### 4. Uniform rejection

Stop when:

- all three variants fail to exceed `52%` win rate with at least `30` trades

Conclusion:

- under current engine and entry-anchor constraints, the target was not demonstrated

### 5. Reproducibility or data-integrity issue

Stop when:

- a run cannot be reproduced from governed artifacts
- required metrics are missing or inconsistent
- governance provenance is unclear

Execution should pause until integrity is restored.

## Recommended execution order

Execute in the following order:

1. `gld-regime-starter-v3`
2. `gld-regime-starter-v4`
3. `gld-regime-starter-v5`

Rationale:

- v3 isolates exit softening
- v4 isolates add removal
- v5 evaluates the combination only after the component effects are observed individually

This ordering reduces attribution ambiguity.

## Decision summary template

Use the following summary block after each run:

- variant:
- experiment id:
- trades:
- win rate:
- profit factor:
- Sharpe:
- max drawdown:
- pass/fail against objective:
- interpretation category:
- next action:

## Governance notes

- This is a proof-plan document only.
- It does not modify or supersede any strategy YAML.
- It does not authorize promotion.
- It authorizes no experiments beyond `gld-regime-starter-v3`, `gld-regime-starter-v4`, and `gld-regime-starter-v5`.
- Any follow-on robustness, walk-forward, or paper-trading work must continue through the normal governed artifact flow.