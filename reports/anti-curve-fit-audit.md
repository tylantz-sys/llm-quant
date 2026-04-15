# Anti-Curve-Fit Audit

## Scope

Audit-only review of anti-curve-fit controls, policy/code alignment, and major weaknesses.

No source files were modified as part of this audit. This report is a standalone audit artifact.

## Explicit anti-curve-fit controls present

### 1. Frozen-spec requirement is partially code-enforced

- `scripts/run_backtest.py` calls `ensure_frozen_spec(...)` unless `--no-spec-check` is supplied.
- `src/llm_quant/backtest/artifacts.py` defines `ensure_frozen_spec()` and `freeze_spec()`.

What this does well:
- prevents ordinary backtest runs from proceeding without a `research-spec.yaml`
- requires `frozen: true` for normal backtest flow

Gap:
- `ensure_frozen_spec()` checks only that the spec exists and is marked frozen; it does not recompute and verify `frozen_hash`.

### 2. Append-only experiment registry / trial counting exists

- `scripts/run_backtest.py` appends runs through `ExperimentRegistry.append(...)`.
- `src/llm_quant/backtest/artifacts.py`
  - `ExperimentRegistry.trial_count`
  - `ExperimentRegistry.append`
  - `ExperimentRegistry.load_all`
  - `ExperimentRegistry.get_returns_matrix`

What this does well:
- creates an audit trail of experiments
- increments `trial_number`
- provides a mechanism for penalizing repeated testing in principle

Gap:
- enforcement depends on users actually using the canonical runner and not bypassing the workflow.

### 3. Walk-forward runner exists with deterministic splits

- `scripts/run_walk_forward_non_ml.py`
  - fixed defaults:
    - train: 24 months
    - test: 3 months
    - step: 3 months
    - purge: 5 trading days
  - `build_windows(...)`
  - writes `walk-forward.yaml`

What this does well:
- makes walk-forward evidence reproducible
- captures provenance and policy inputs
- provides direct machine-reviewable artifact output

Gap:
- this is a separate manual step; `scripts/run_backtest.py` does not require it.

### 4. Robustness primitives exist in shared code

- `src/llm_quant/backtest/robustness.py`
  - `compute_pbo(...)`
  - `run_cpcv(...)`
  - `compute_min_trl(...)`
  - `shuffled_signal_test(...)`
  - `mechanism_inversion_test(...)`
  - `time_in_market(...)`
  - `run_robustness_gate(...)`

What this does well:
- the repository has real statistical/fraud-detection building blocks
- some of the controls described in governance are implemented as reusable functions

Gap:
- these controls are not consistently wired into a single enforced lifecycle gate.

## Policy-only vs code-enforced reality

### Governance requirements that are stronger than implementation

#### Stage 1 hard vetoes are not fully enforced end to end

Policy:
- `docs/governance/model-promotion-policy.md`
- `docs/governance/quant-lifecycle.md`

Required by policy:
- DSR >= 0.95
- PBO <= 10%
- Stepwise SPA p-value <= 0.05
- MinTRL >= 1 OOS period

Observed implementation:
- DSR is present in backtest artifacts and used by some runners.
- PBO is implemented in `src/llm_quant/backtest/robustness.py`, but not consistently used by audited robustness scripts.
- Stepwise SPA was not found in the audited implementation.
- MinTRL is computed in `scripts/run_backtest.py`, but only as warning/informational output.

Assessment:
- Stage 1 is mostly policy-level governance today, not uniformly code-enforced governance.

#### Alpha-hunting fraud detectors are only partially implemented

Policy:
- `docs/governance/alpha-hunting-framework.md`

Documented fraud detectors:
- shuffled returns/signal test
- economic regime split
- true untouched holdout period
- mechanism inversion
- alternative instruments

Observed implementation:
- `scripts/run_fraud_detectors.py` uses:
  - `shuffled_signal_test`
  - `mechanism_inversion_test`
  - `time_in_market`
- No audited enforcement found for:
  - true holdout contamination control
  - regime split gate
  - alternative-instrument gate

Assessment:
- framework is conceptually strong, but implementation is incomplete and mostly optional.

## Most glaring weaknesses

### 1. Stepwise SPA is required by policy but absent from audited code

Reference:
- `docs/governance/model-promotion-policy.md`

This is the largest clean doc/code mismatch in the anti-curve-fit stack. The policy presents SPA as a hard veto, but no audited implementation was found in:
- `scripts/run_backtest.py`
- `scripts/run_walk_forward_non_ml.py`
- `scripts/run_crypto_robustness.py`
- `scripts/run_a8_robustness.py`
- `scripts/run_track_c_robustness.py`
- `src/llm_quant/backtest/robustness.py`

### 2. Frozen-spec hashing is incomplete in practice

References:
- `src/llm_quant/backtest/artifacts.py`
- `docs/governance/quant-lifecycle.md`

Observed behavior:
- `freeze_spec()` stores `frozen_hash`
- `ensure_frozen_spec()` does not verify the current file content against that hash

Why this matters:
- the repo documents a hash-chain discipline, but the loading path does not enforce integrity
- a spec could remain marked frozen while its contents drift

### 3. `--no-spec-check` is an explicit bypass of the main anti-snooping control

Reference:
- `scripts/run_backtest.py`

Observed behavior:
- backtests can be run with `--no-spec-check`
- help text says this is for quick testing only

Assessment:
- this is an exploratory convenience that weakens lifecycle discipline
- it also embeds an autonomous-testing style assumption the user explicitly does not prefer

### 4. Robustness logic is fragmented and inconsistent across scripts

References:
- `scripts/run_crypto_robustness.py`
- `scripts/run_a8_robustness.py`
- `scripts/run_track_c_robustness.py`

Observed differences:
- `scripts/run_crypto_robustness.py`:
  - structured thresholds
  - uses latest registry entry
  - computes CPCV
  - runs perturbation set
  - writes `robustness.yaml`
- `scripts/run_a8_robustness.py`:
  - has its own local CPCV implementation
  - skips PBO
  - manually picks a specific experiment from registry history
  - approximates cost stress instead of measuring it directly
  - writes handcrafted YAML text
- `scripts/run_track_c_robustness.py`:
  - uses a different gate family entirely for Track C
  - two subclasses are placeholders and reject by default

Assessment:
- robustness semantics differ materially by script
- inconsistent gates create room for accidental or selective curve-fit leakage

### 5. PBO exists in shared code but is not consistently operationalized

Reference:
- `src/llm_quant/backtest/robustness.py`

Observed reality:
- `compute_pbo(...)` exists
- audited robustness runners do not consistently invoke or enforce it

Assessment:
- a key anti-overfitting control exists as a library primitive, but not as a reliable gate

### 6. MinTRL is warning-only in the backtest runner

Reference:
- `scripts/run_backtest.py`

Observed behavior:
- MinTRL is computed from backtest returns
- failure emits a warning only

Assessment:
- this is weaker than the governance framing of minimum OOS evidence / hard-veto promotion discipline

### 7. CPCV implementation is weaker than the governance description

References:
- `src/llm_quant/backtest/robustness.py`
- `docs/governance/quant-lifecycle.md`

Observed behavior:
- `run_cpcv(...)` works on an already realized return series
- it does not appear to re-fit or re-select parameters fold by fold

Assessment:
- useful as a resampling diagnostic
- weaker than full train/test model-selection CPCV implied by governance language

### 8. True holdout discipline is policy-only

Reference:
- `docs/governance/alpha-hunting-framework.md`

Observed reality:
- no audited contamination-tracking or one-time holdout enforcement was found

Assessment:
- one of the strongest anti-curve-fit ideas in the docs remains unenforced

### 9. Test evidence for anti-curve-fit controls appears weak or absent

Observed during audit:
- searches in `tests/` for direct coverage of robustness/lifecycle primitives did not surface matching evidence in this audit path

Assessment:
- even where controls exist, there is limited evidence that their behavior is protected by dedicated automated tests

## Notes on autonomous-testing assumptions

The repository still contains workflow patterns that assume repeated autonomous experimentation is normal:

- `scripts/run_backtest.py --no-spec-check`
- many bespoke `scripts/run_*_robustness.py`
- `scripts/run_fraud_detectors.py` batch-running a hardcoded strategy list

Given current user preference, these should be treated as embedded assumptions in the research workflow, not as recommended operating practice.

## Bottom-line assessment

The repository has several meaningful anti-curve-fit building blocks:
- frozen-spec concept
- append-only experiment registry
- trial counting
- walk-forward runner
- PBO/CPCV/MinTRL/fraud-detector utilities

However, the most important weakness is that governance is stronger and cleaner than implementation. The anti-curve-fit framework is only partially enforced, highly script-dependent, and inconsistent across strategy classes.

Biggest gaps:
1. no audited Stepwise SPA implementation
2. no frozen-hash verification on spec load
3. explicit spec-check bypass in the canonical backtest runner
4. inconsistent robustness runners
5. PBO and holdout discipline not uniformly enforced

## Files referenced

- `docs/governance/model-promotion-policy.md`
- `docs/governance/quant-lifecycle.md`
- `docs/governance/control-matrix.md`
- `docs/governance/alpha-hunting-framework.md`
- `scripts/run_backtest.py`
- `scripts/run_walk_forward_non_ml.py`
- `scripts/run_fraud_detectors.py`
- `scripts/run_crypto_robustness.py`
- `scripts/run_a8_robustness.py`
- `scripts/run_track_c_robustness.py`
- `src/llm_quant/backtest/artifacts.py`
- `src/llm_quant/backtest/robustness.py`