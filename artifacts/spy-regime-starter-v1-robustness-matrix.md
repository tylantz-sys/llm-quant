# spy-regime-starter-v1 robustness matrix

## Purpose

This document defines the deterministic research-validation gate for `spy-regime-starter-v1` before any shadow/paper deployment is allowed. Its goal is to test whether the strategy is **stable**, not merely positive in a single walk-forward run.

This artifact is governed by the frozen strategy specification and related strategy documents:

- `data/strategies/spy-regime-starter-v1/research-spec.yaml`
- `data/strategies/spy-regime-starter-v1/mandate.yaml`
- `data/strategies/spy-regime-starter-v1/hypothesis.yaml`
- `data/strategies/spy-regime-starter-v1/data-contract.yaml`
- `tests/test_backtest/test_spy_regime_starter_strategy.py`

This document does not unfreeze or retune the strategy. It defines the evidence required to determine whether the current frozen design is robust enough to advance to shadow/paper validation.

## Governing spec constraints

The frozen research spec establishes the following baseline validation and acceptance requirements:

### Strategy design constraints

- Trade symbol: `SPY`
- Reference symbol: `VIX`
- Daily bars only
- Signals generated from completed daily bars
- Fills modeled at next session open
- Fixed-at-entry ATR stop
- Max position weight: `0.05`
- Max adds: `1`
- Cooldown after exit: `2` trading days
- Missing VIX policy: block new entries/adds, still allow SPY-side risk exits

### Baseline walk-forward policy

From `validation.walk_forward` in `research-spec.yaml`:

- `train_days: 504`
- `test_days: 63`
- `step_days: 63`
- `purge_days: 5`

### Baseline robustness requirements

From `validation.robustness_requirements` in `research-spec.yaml`:

- `DSR >= 0.95`
- `PBO <= 0.10`
- `CPCV mean OOS Sharpe > 0`
- `CPCV median OOS Sharpe > 0`
- `2x cost survival`
- `parameter stability > 50%`

### Baseline acceptance criteria

From `acceptance_criteria.primary` in `research-spec.yaml`:

- `Sharpe >= 0.80`
- `MaxDD < 0.15`
- `DSR >= 0.95`
- `Walk-forward mean OOS Sharpe > 0`
- `Walk-forward median OOS Sharpe > 0`

## Promotion policy

`spy-regime-starter-v1` must remain in research validation until the matrix below is completed and judged acceptable. A single passing walk-forward result is insufficient on its own.

The strategy must **not** move to shadow/paper unless the evidence shows:

- acceptable performance under reasonable walk-forward window changes
- acceptable performance under worse cost assumptions
- acceptable stability under mild nearby parameter perturbations
- no obvious dependence on one narrow historical subperiod
- deterministic tests cover the documented entry/add/exit/cooldown/missing-data behavior

## Evaluation principles

1. **Frozen-spec first**
   - The production candidate remains the frozen configuration in `research-spec.yaml`.
   - Robustness runs are stress tests, not a license to retune the production candidate.

2. **Deterministic evidence only**
   - Promotion decisions must be based on documented rules, reproducible runs, and auditable artifacts.
   - LLM review may provide commentary but is not part of the promotion gate.

3. **Local perturbation, not optimization**
   - Parameter perturbations are intentionally small and centered on the frozen values.
   - A robustness grid that becomes an optimization sweep violates the purpose of this document.

4. **Explainable failure modes**
   - If the strategy fails under some scenarios, the failures should be interpretable and bounded.
   - The edge should not vanish purely because of a trivial change in window boundary or cost assumption.

## Required evidence set

Each run used in this matrix must record at minimum:

- command or script used
- strategy slug
- effective train/test/step/purge settings
- effective cost assumptions
- effective parameter overrides, if any
- provenance symbol set, including `SPY` and `VIX`
- date produced
- output artifact path
- summary metrics:
  - OOS Sharpe
  - max drawdown
  - annualized return if available
  - turnover if available
  - fold count
  - mean OOS Sharpe
  - median OOS Sharpe
- pass/fail conclusion for that row
- short interpretation note

## Robustness matrix

### Lane A: baseline reproducibility

Purpose: confirm that the current accepted walk-forward result is reproducible from the frozen specification and correct data inputs.

| Case ID | Description | Expected evidence | Minimum pass condition |
|---|---|---|---|
| A1 | Baseline rerun using frozen spec | Regenerated walk-forward artifact with provenance | Provenance includes both `SPY` and `VIX`, and baseline acceptance criteria remain satisfied |
| A2 | Baseline artifact integrity review | Summary metrics and fold metrics documented | No structural flatness caused by missing signal-symbol ingestion or missing VIX alignment |

Notes:

- This lane confirms the post-fix validation state described in `artifacts/spy-regime-starter-v1-walk-forward-validation-checklist.md`.
- A failed baseline reproducibility lane blocks all further promotion work.

### Lane B: walk-forward window variation

Purpose: test whether results depend on one exact train/test geometry.

Suggested cases:

| Case ID | Train | Test | Step | Purge | Rationale |
|---|---:|---:|---:|---:|---|
| B1 | 504 | 63 | 63 | 5 | Baseline |
| B2 | 378 | 63 | 63 | 5 | Shorter training memory |
| B3 | 756 | 63 | 63 | 5 | Longer training memory |
| B4 | 504 | 42 | 42 | 5 | Shorter OOS windows |
| B5 | 504 | 84 | 84 | 5 | Longer OOS windows |

Minimum pass condition:

- Baseline case must pass.
- Most alternate cases should remain directionally acceptable.
- No modest window change should cause a broad collapse in OOS behavior.
- If one window case fails, the failure must be explainable and not indicative of generalized fragility.

Interpretation standard:

- The strategy does not need identical scores in every case.
- The strategy is considered fragile if performance flips from acceptable to clearly poor across several modest window variants.

### Lane C: shifted fold boundaries

Purpose: test whether the result depends on one favorable start date or fold alignment.

Suggested cases:

| Case ID | Shift | Rationale |
|---|---:|---|
| C1 | 0 trading days | Baseline start |
| C2 | 21 trading days | Approximate one-month shift |
| C3 | 42 trading days | Approximate two-month shift |
| C4 | 63 trading days | Approximate one baseline test window shift |

Minimum pass condition:

- Results should remain directionally similar under reasonable fold boundary shifts.
- Mean and median OOS Sharpe should not become strongly negative simply because the folds begin a few weeks later.
- No evidence should indicate that one exact split date is doing all of the work.

### Lane D: cost stress

Purpose: confirm that the strategy’s edge survives worse execution assumptions.

Baseline cost model from frozen spec:

- `spread_bps: 5.0`
- `flat_slippage_bps: 2.0`
- `slippage_volatility_factor: 0.1`

Suggested cases:

| Case ID | Spread | Flat slippage | Volatility factor | Rationale |
|---|---:|---:|---:|---|
| D1 | 5.0 | 2.0 | 0.1 | Baseline |
| D2 | 7.5 | 3.0 | 0.15 | 1.5x stress |
| D3 | 10.0 | 4.0 | 0.2 | 2.0x stress |
| D4 | 10.0 | 2.0 | 0.1 | Spread-only stress |
| D5 | 5.0 | 4.0 | 0.2 | Slippage-heavy stress |

Minimum pass condition:

- Baseline case must pass.
- The strategy must satisfy the frozen requirement of `2x cost survival`.
- Turnover-adjusted edge should not disappear under modestly worse costs.

Interpretation standard:

- A strategy that only works at the most favorable cost assumptions is not ready for paper validation.

### Lane E: mild parameter perturbation

Purpose: test local stability around the frozen parameter set without re-optimizing the design.

Frozen reference parameters:

- `rsi_entry_threshold: 55.0`
- `rsi_exit_threshold: 40.0`
- `vix_entry_max: 19.2`
- `vix_add_max: 16.4`
- `vix_exit_min: 25.0`
- `macd_add_min: 0.0`
- `macd_exit_max: -0.20`
- `atr_stop_multiple: 1.75`

Suggested one-at-a-time perturbations:

| Parameter | Lower | Frozen | Upper |
|---|---:|---:|---:|
| `rsi_entry_threshold` | 54.0 | 55.0 | 56.0 |
| `rsi_exit_threshold` | 39.0 | 40.0 | 41.0 |
| `vix_entry_max` | 18.7 | 19.2 | 19.7 |
| `vix_add_max` | 15.9 | 16.4 | 16.9 |
| `vix_exit_min` | 24.5 | 25.0 | 25.5 |
| `macd_exit_max` | -0.25 | -0.20 | -0.15 |
| `atr_stop_multiple` | 1.50 | 1.75 | 2.00 |

Minimum pass condition:

- The frozen point must pass.
- Nearby points should remain acceptable often enough to satisfy the spec requirement of `parameter stability > 50%`.
- No single threshold should appear knife-edge.

Guardrails:

- Do not sweep large parameter ranges.
- Do not use perturbation results to silently redefine the production strategy.
- Any production-parameter change would require a separate governed research update, not this matrix.

### Lane F: subperiod and regime analysis

Purpose: test whether results depend on a single narrow historical environment.

Suggested segment views:

| Case ID | Segment type | Example split |
|---|---|---|
| F1 | Time terciles | Earliest third of sample |
| F2 | Time terciles | Middle third of sample |
| F3 | Time terciles | Latest third of sample |
| F4 | Volatility regime | Higher-volatility subperiods |
| F5 | Volatility regime | Lower-volatility subperiods |
| F6 | Stress episode review | Known SPY drawdown and recovery windows |

Minimum pass condition:

- The strategy need not outperform in every segment.
- However, it must not be obviously dependent on one narrow subperiod for all of its credibility.
- Weak periods must be understandable and bounded rather than catastrophic everywhere outside one favorable pocket.

Interpretation standard:

- If nearly all acceptable performance comes from one isolated regime while the rest of the sample is flat or poor, the strategy is not yet stable enough for promotion.

## Deterministic test coverage requirements

Promotion to shadow/paper is blocked unless deterministic tests cover the documented decision logic. At minimum, the test suite should confirm:

- entry requires all documented starter conditions
- missing `VIX` blocks new entry
- exit triggers on:
  - RSI breakdown
  - MACD deterioration
  - VIX risk-off breach
  - fixed ATR stop
- add signal only occurs below max weight and under confirming conditions
- insufficient indicator data yields no signal

Current coverage already exists in:

- `tests/test_backtest/test_spy_regime_starter_strategy.py`

Before paper deployment, also verify whether explicit coverage exists for:

- cooldown behavior after exit
- next-open execution assumption compatibility
- missing-VIX behavior during exit-only scenarios
- add-count cap enforcement under repeated qualifying days

## Matrix scoring summary

The final robustness review should summarize each lane as:

- `pass`
- `conditional pass`
- `fail`

Suggested final decision rubric:

- **Pass**: baseline passes and no lane shows material fragility
- **Conditional pass**: isolated weakness exists but does not invalidate the overall hypothesis; must be documented before paper
- **Fail**: baseline fails, `2x` cost survival fails, parameter stability is inadequate, or evidence shows dependence on a narrow subperiod or single fold alignment

## Research-to-paper gate

`spy-regime-starter-v1` may advance to shadow/paper only if all of the following are true:

- baseline walk-forward remains acceptable
- window variation lane is acceptable
- shifted-boundary lane is acceptable
- cost-stress lane is acceptable, including `2x` cost survival
- parameter perturbation lane satisfies `parameter stability > 50%`
- subperiod analysis does not show obvious one-regime dependence
- deterministic tests cover the documented decision logic
- no unresolved data-timing or symbol-alignment issue remains

## Explicit exclusions

This matrix does not authorize:

- live trading
- automatic promotion based on one summary metric
- parameter re-optimization
- replacing deterministic gates with LLM commentary

## LLM review policy

Claude or other LLM review may be used for:

- red-team critique
- robustness suggestions
- overfitting suspicion review
- documentation quality review

LLM review is strictly optional and non-authoritative. It must not override:

- the frozen spec
- deterministic tests
- walk-forward evidence
- paper/shadow evidence

## Result template

The completed review should end with a concise table like this:

| Lane | Status | Key evidence | Notes |
|---|---|---|---|
| Baseline reproducibility | TBD | walk-forward artifact path | |
| Window variation | TBD | run set / artifact paths | |
| Shifted boundaries | TBD | run set / artifact paths | |
| Cost stress | TBD | run set / artifact paths | |
| Parameter perturbation | TBD | run set / artifact paths | |
| Subperiod analysis | TBD | analysis artifact path | |
| Deterministic test coverage | TBD | test file / results | |

Final decision:

- `Remain in research validation`
- `Eligible for shadow/paper`
- `Not eligible for promotion`
