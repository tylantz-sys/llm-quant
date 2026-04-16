# spy-regime-starter-v1 robustness execution runbook

## Purpose

This runbook converts the `spy-regime-starter-v1` robustness matrix into a concrete execution sequence. It is the next phase after documenting the robustness and promotion gates.

Use this runbook together with:

- `artifacts/spy-regime-starter-v1-robustness-matrix.md`
- `artifacts/spy-regime-starter-v1-promotion-checklist.md`
- `artifacts/spy-regime-starter-v1-walk-forward-validation-checklist.md`
- `data/strategies/spy-regime-starter-v1/research-spec.yaml`

## Current scope

This runbook is intentionally limited to deterministic research validation. It does not authorize:

- parameter re-optimization
- live trading
- using LLM output as a promotion gate
- skipping baseline correctness checks

## Pre-flight checks

Before running any robustness job:

1. Confirm the strategy spec remains frozen.
2. Confirm the current `walk-forward.yaml` was generated after the `SPY` + `VIX` symbol resolution fix.
3. Confirm tests in `tests/test_backtest/test_spy_regime_starter_strategy.py` still represent the documented decision rules.
4. Record the current spec hash from `data/strategies/spy-regime-starter-v1/research-spec.yaml`.

## Artifact convention

Keep all robustness evidence for this phase under a dedicated artifact directory such as:

- `artifacts/spy-regime-starter-v1/robustness/`

Recommended file naming convention:

- `baseline-walk-forward.txt`
- `window-B1.yaml`
- `window-B2.yaml`
- `window-B3.yaml`
- `window-B4.yaml`
- `window-B5.yaml`
- `boundary-C1.yaml`
- `boundary-C2.yaml`
- `boundary-C3.yaml`
- `boundary-C4.yaml`
- `cost-D1-notes.md`
- `cost-D2-notes.md`
- `cost-D3-notes.md`
- `perturbation-summary.md`
- `subperiod-summary.md`
- `final-robustness-review.md`

If a run overwrites `data/strategies/spy-regime-starter-v1/walk-forward.yaml`, immediately copy the resulting file into the dedicated artifact location for that case.

## Baseline reproducibility lane

### Goal
Confirm that the frozen baseline remains reproducible and structurally correct.

### Command

```bash
PYTHONPATH=src python scripts/run_walk_forward_non_ml.py --slug spy-regime-starter-v1 --train-days 504 --test-days 63 --step-days 63 --purge-days 5
```

### Record
- copy `data/strategies/spy-regime-starter-v1/walk-forward.yaml` to the baseline artifact slot
- record provenance symbols
- record fold count
- record mean OOS Sharpe
- record median OOS Sharpe
- record max OOS drawdown
- confirm the artifact is not structurally flat because of missing `VIX`

## Window variation lane

### Goal
Test stability under modest train/test geometry changes.

### Cases

#### B1 baseline
```bash
PYTHONPATH=src python scripts/run_walk_forward_non_ml.py --slug spy-regime-starter-v1 --train-days 504 --test-days 63 --step-days 63 --purge-days 5
```

#### B2 shorter training memory
```bash
PYTHONPATH=src python scripts/run_walk_forward_non_ml.py --slug spy-regime-starter-v1 --train-days 378 --test-days 63 --step-days 63 --purge-days 5
```

#### B3 longer training memory
```bash
PYTHONPATH=src python scripts/run_walk_forward_non_ml.py --slug spy-regime-starter-v1 --train-days 756 --test-days 63 --step-days 63 --purge-days 5
```

#### B4 shorter OOS windows
```bash
PYTHONPATH=src python scripts/run_walk_forward_non_ml.py --slug spy-regime-starter-v1 --train-days 504 --test-days 42 --step-days 42 --purge-days 5
```

#### B5 longer OOS windows
```bash
PYTHONPATH=src python scripts/run_walk_forward_non_ml.py --slug spy-regime-starter-v1 --train-days 504 --test-days 84 --step-days 84 --purge-days 5
```

### Record for each case
- copied `walk-forward.yaml`
- mean OOS Sharpe
- median OOS Sharpe
- max OOS drawdown
- fold count
- pass/fail judgment
- short interpretation note

## Shifted fold-boundary lane

## Goal
Test whether results depend on one specific fold alignment.

## Recommended execution method

The current `scripts/run_walk_forward_non_ml.py` exposes train/test/step/purge controls, but not an explicit start-date offset. For now, execute this lane through one of the following governed approaches:

1. add a temporary research-only wrapper that filters the initial date range before calling the existing walk-forward runner, or
2. manually patch a local research branch to start from a shifted first trading date and save artifacts separately

### Target offsets
- C1: `0` trading days
- C2: `21` trading days
- C3: `42` trading days
- C4: `63` trading days

### Required evidence
For each offset:
- exact method used to apply the shift
- resulting date coverage
- resulting fold count
- mean OOS Sharpe
- median OOS Sharpe
- max OOS drawdown
- interpretation of whether the result materially depends on one start date

## Cost-stress lane

## Goal
Test whether the edge survives worse execution assumptions.

### Current tooling note
The current `scripts/run_walk_forward_non_ml.py` reads costs from the frozen spec and does not expose cost overrides from CLI.

### Recommended governed approach
Create a research-only robustness helper or wrapper that:
- loads the frozen spec
- applies temporary in-memory cost multipliers
- runs the same walk-forward process without altering the frozen spec on disk
- writes case-specific output files

### Required cases
- D1: baseline costs
- D2: 1.5x cost stress
- D3: 2.0x cost stress
- D4: spread-only stress
- D5: slippage-heavy stress

### Required recorded fields
- exact effective spread bps
- exact effective flat slippage bps
- exact effective slippage volatility factor
- mean OOS Sharpe
- median OOS Sharpe
- max OOS drawdown
- turnover if available
- pass/fail versus `2x cost survival`

## Mild parameter perturbation lane

### Goal
Test local stability without re-optimizing.

### Guardrail
Run one-at-a-time local perturbations only. Do not redefine the production candidate based on this lane.

### Required cases
Use the perturbations defined in `artifacts/spy-regime-starter-v1-robustness-matrix.md` for:

- `rsi_entry_threshold`
- `rsi_exit_threshold`
- `vix_entry_max`
- `vix_add_max`
- `vix_exit_min`
- `macd_exit_max`
- `atr_stop_multiple`

### Recommended execution method
Create a research-only helper that:
- loads the frozen spec
- applies one in-memory parameter override at a time
- runs the same walk-forward logic
- stores one output artifact per case
- tallies whether acceptable nearby points exceed 50%

### Required output
- case-by-case table
- count of acceptable perturbation cases
- percent stable
- final decision on `parameter stability > 50%`

## Subperiod and regime lane

### Goal
Test whether the edge depends on one narrow market pocket.

### Suggested segment views
- earliest third of available sample
- middle third of available sample
- latest third of available sample
- higher-volatility windows
- lower-volatility windows
- selected drawdown/recovery episodes

### Recommended method
Use the same frozen strategy logic with bounded date filtering and preserve:
- identical signal logic
- identical fill-delay assumptions
- identical warmup logic where feasible
- identical cost assumptions unless explicitly running a cost lane

### Required output
For each segment:
- date range
- reason for segmentation
- fold count or evaluation count
- summary performance
- note on whether behavior is acceptable, weak-but-explainable, or disqualifying

## Deterministic test review

Before leaving research validation, review:

```bash
PYTHONPATH=src pytest tests/test_backtest/test_spy_regime_starter_strategy.py
```

Confirm whether additional explicit coverage is needed for:
- cooldown behavior after exit
- add-count cap enforcement
- exit-only behavior when `VIX` is missing
- next-open execution assumption compatibility

## Final review package

At the end of this runbook, prepare one final summary artifact containing:

1. baseline result
2. window variation summary
3. boundary-shift summary
4. cost-stress summary
5. perturbation summary
6. subperiod summary
7. deterministic test review
8. final promotion recommendation:
   - remain in research validation
   - eligible for shadow paper
   - not eligible for promotion

## Immediate next implementation recommendation

The most useful code step after this runbook is to add a dedicated research-only robustness runner for `spy-regime-starter-v1` that can:

- reuse `scripts/run_walk_forward_non_ml.py` logic
- accept case definitions for windows, cost stress, and parameter perturbations
- write case-specific artifacts without mutating the frozen spec
- emit one consolidated summary for the promotion checklist

Until that helper exists, this runbook is the authoritative execution plan for the next research phase.
