# Walk-Forward Audit — Near-Term Candidate Strategies

Date: 2026-04-01

Scope:
- `docs/governance/strategy-artifact-status-matrix.md`
- `docs/governance/strategy-promotion-gap-closure-plan.md`
- `data/strategies` for:
  - D1 `d1-tlt-tqqq-sprint`
  - D2 `d2-btc-momentum-v2`
  - D6 `d6-lqd-tqqq-sprint`
  - `lqd-spy-credit-lead`

Conservative rule: file-backed evidence only. Missing artifact means missing/unverified, not inferred passed.

## Per-strategy status

### 1. D1 — `d1-tlt-tqqq-sprint`
- Walk-forward artifact status: **missing**
- File-backed evidence:
  - `data/strategies/d1-tlt-tqqq-sprint/` does not exist (`list_files` returned “No files found”).
  - `docs/governance/strategy-artifact-status-matrix.md` lists D1 with `walk_forward` unknown/not verified.
  - `docs/governance/strategy-promotion-gap-closure-plan.md` says D1 still needs: “run walk-forward.”
- Missing artifact at expected location:
  - `data/strategies/d1-tlt-tqqq-sprint/walk-forward.yaml`

### 2. D2 — `d2-btc-momentum-v2`
- Walk-forward artifact status: **missing**
- File-backed evidence:
  - `data/strategies/d2-btc-momentum-v2/` does not exist (`list_files` returned “No files found”).
  - `docs/governance/strategy-artifact-status-matrix.md` lists D2 with `walk_forward` unknown/not verified.
  - `docs/governance/strategy-promotion-gap-closure-plan.md` says D2 still needs: “run walk-forward.”
- Missing artifact at expected location:
  - `data/strategies/d2-btc-momentum-v2/walk-forward.yaml`

### 3. D6 — `d6-lqd-tqqq-sprint`
- Walk-forward artifact status: **missing**
- File-backed evidence:
  - `data/strategies/d6-lqd-tqqq-sprint/` does not exist (`list_files` returned “No files found”).
  - `docs/governance/strategy-artifact-status-matrix.md` lists D6 with `walk_forward` unknown/not verified.
  - `docs/governance/strategy-promotion-gap-closure-plan.md` says D6 still needs: “run walk-forward.”
- Missing artifact at expected location:
  - `data/strategies/d6-lqd-tqqq-sprint/walk-forward.yaml`

### 4. `lqd-spy-credit-lead`
- Walk-forward artifact status: **missing**
- File-backed evidence:
  - `data/strategies/lqd-spy-credit-lead/` contains:
    - `mandate.yaml`
    - `data-contract.yaml`
    - `robustness.yaml`
    - `experiment-registry.jsonl`
    - `research-spec.yaml`
    - `hypothesis.yaml`
    - `paper-trading.yaml`
    - `experiments/e91c7cf3.yaml`
  - No walk-forward artifact file is present under `data/strategies/lqd-spy-credit-lead/`.
  - `data/strategies/lqd-spy-credit-lead/robustness.yaml` is a passed robustness artifact created `2026-03-26`.
  - `data/strategies/lqd-spy-credit-lead/experiment-registry.jsonl` contains a newer experiment record `e91c7cf3` recorded `2026-04-01T15:31:52.375721+00:00`.
  - `data/strategies/lqd-spy-credit-lead/experiments/e91c7cf3.yaml` exists and is a fresh backtest artifact dated `2026-04-01T15:31:52+00:00`.
  - `docs/governance/strategy-artifact-status-matrix.md` notes `lqd-spy-credit-lead` as partial within Track A artifact debt; it does not provide artifact-backed `walk_forward = passed`.
- Interpretation constrained by files:
  - This is **not stale passed WFO**; it is **missing WFO artifact** despite otherwise substantial artifact coverage.
- Missing artifact at expected location:
  - `data/strategies/lqd-spy-credit-lead/walk-forward.yaml`

## Highest-priority WFO gaps

1. **D1 / D2 / D6 strategy directories are absent at expected slugs**
   - Highest severity for closure because no on-disk strategy artifact stack exists to verify WFO at all.

2. **`lqd-spy-credit-lead` lacks a walk-forward artifact despite having fresh surrounding evidence**
   - Highest near-term actionable WFO gap because the strategy already has:
     - research spec
     - experiment registry
     - passed robustness
     - paper-trading artifact
     - fresh 2026-04-01 experiment artifact

3. **Governance docs still treat D1 / D2 / D6 walk-forward as required future work**
   - `docs/governance/strategy-promotion-gap-closure-plan.md` explicitly says to run walk-forward for all three.
   - `docs/governance/strategy-artifact-status-matrix.md` does not show artifact-backed passed WFO for D1, D2, or D6.

## Bottom line

- Passed/current walk-forward artifact found: **none** of the audited four.
- Missing expected WFO artifact:
  - D1 `d1-tlt-tqqq-sprint`
  - D2 `d2-btc-momentum-v2`
  - D6 `d6-lqd-tqqq-sprint`
  - `lqd-spy-credit-lead`
- Most actionable immediate WFO closure candidate: **`lqd-spy-credit-lead`**.
- Largest structural artifact gap: **D1, D2, D6 expected slug directories absent on disk**.