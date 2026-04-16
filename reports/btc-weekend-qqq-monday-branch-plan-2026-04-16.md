# BTC Weekend -> QQQ Monday Branch Plan
Date: 2026-04-16

## Objective
Advance `btc-weekend-qqq-monday-risk-signal` from a promising cheap screen into a clean, repo-native governed candidate, then sequence the fallback and optional crypto branches only after the primary branch is properly validated.

## Current status
The initial cheap exploratory runner showed encouraging results:
- Base Sharpe: 2.8199
- Max drawdown: 5.24%
- CPCV mean OOS Sharpe: 1.4193
- Trade count: 91

That result did **not** survive promotion into a cleaner deterministic event-study implementation.

Repo-native validation now exists at:
- `scripts/run_btc_weekend_followthrough_study.py`

Validated event-study findings:
- Primary `QQQ` result: `mean_bp=-10.92`, `t_stat=-0.87`, `events=155`
- `QQQ` net at `10 bp`: `-20.92 bp`
- Shuffled-signal falsification p-value: `0.1620`
- Alternative followers `SPY`, `IWM`, and `DIA` were also negative on gross mean and on 10bp-net mean

Resulting flags:
- `ROBUST_TO_10BP=no`
- `BEATS_SHUFFLE_5PCT=no`
- `CONSISTENT_ACROSS_FOLLOWERS=no`

Interpretation:
- the original Monday-sliced runner was useful as a cheap smell test
- the cleaner falsification-aware branch does not support a durable BTC-weekend-to-equity-follow-through edge
- the primary branch should now be considered **killed as a research candidate**, except as a negative reference artifact

---

## Phase 1 — Harden the primary branch
Primary target: `btc-weekend-qqq-monday-risk-signal`

### Goal
Replace the temporary Monday-filter smell test with a proper repo-native backtest implementation that is causally clean, calendar-aware, and suitable for governed evaluation.

### Deliverables
1. A proper backtest script for `btc-weekend-qqq-monday-risk-signal`
2. Optional robustness companion script once the base runner is clean
3. Clean result summary with stale-price behavior eliminated or explicitly justified
4. Registry-compatible output path if the runner becomes the first official backtest for the slug

### Implementation requirements
- Compute the BTC weekend signal causally from completed weekend data only
- Map that signal to the next tradable Monday-equivalent QQQ session
- Handle Monday holidays explicitly
- Avoid stale-price fallback behavior
- Use explicit fill delay and cost assumptions consistent with `research-spec.yaml`
- Keep the baseline parameter set narrow and auditable

### Validation requirements
In addition to standard base metrics:
- CPCV mean/median-style review
- Parameter perturbation review
- Cost sensitivity
- Shuffled signal dates
- Mechanism inversion
- Alternative follower check (`SPY` or `IWM`)
- Regime split for crypto-mania windows

### Decision gate
- **Advance** if the cleaned implementation still shows a durable edge
- **Hold** if results remain decent but calendar logic is still ambiguous
- **Kill** if the edge collapses after causal/calendar cleanup

---

## Phase 2 — Run the fallback audit
Fallback target: `btc-momentum-v2`

### Goal
Determine whether the repo’s existing `btc-momentum-v2` result is truly reproducible and governed, or whether it requires redesign under Family 3.

### Deliverables
1. Re-run the existing robustness script
2. Confirm exact parameterization and sampling assumptions
3. Explain why the naive SMA proxy diverged from the repo result
4. Produce a retain / redesign / retire verdict

### Questions to resolve
- Is the edge dependent on the 3-year sample?
- Is it materially dependent on excluding 2022?
- How much does the synthetic capital workaround affect the interpretation?
- Is the result coming from trend-following class logic rather than simple SMA crossover intuition?
- Is the multi-timeframe consensus the real source of edge?

### Decision gate
- **Retain** if reproducible and internally coherent
- **Redesign** if too window-dependent or artifact-dependent
- **Retire** if the result does not survive honest rerun

---

## Phase 3 — Optional Family 2 crypto screens
Optional targets:
- `SOL/BTC`
- `ETH/SOL`

### Goal
Test whether crypto Family 2 mean-reversion still exists in a broader pair shortlist after the weak ETH/BTC scan.

### Deliverables
1. Cheap Hunt-phase screens for both pairs
2. Sanity review of trade count, drawdown, and nearby perturbations
3. Structural-trend versus mean-reversion verdict
4. Lifecycle artifact creation only for survivors

### Kill-fast criteria
Kill immediately if:
- no trades or degenerate behavior
- negative Sharpe across nearby variants
- obvious structural trend instead of reversion
- excessive drawdown for a weak edge

### Promotion rule
Do **not** create full lifecycle artifacts for both pairs by default. Promote only a survivor.

---

## Recommended execution order
1. Implement a clean repo-native runner for `btc-weekend-qqq-monday-risk-signal`
2. Re-test and eliminate the stale-price warning issue
3. If still strong, extend to a proper robustness script
4. Then rerun/audit `btc-momentum-v2`
5. Only after that, screen `SOL/BTC` and `ETH/SOL`

---

## Risk notes
Primary risks for the branch:
- calendar leakage around weekend-to-Monday mapping
- Monday-holiday handling bugs
- stale-price fallback creating misleading smoothness
- hidden regime dependence during crypto-specific mania windows

The immediate purpose of the next branch step is not to improve Sharpe. It is to prove the implementation is causally clean and governance-safe.

---

## Branch verdict
- Primary branch has now failed under cleaner deterministic validation
- Fallback branch `btc-momentum-v2` becomes the next active priority
- Family 2 exploration remains optional and should stay behind the fallback audit
