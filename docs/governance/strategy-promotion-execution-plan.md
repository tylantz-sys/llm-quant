# Strategy Promotion Execution Plan

This document converts the current governance state into an execution plan for:

1. running or continuing backtests
2. running robustness and walk-forward validation
3. reviewing strategies against promotion gates
4. promoting only strategies whose evidence stack is complete

It is designed to be the operator-facing implementation bridge between:

- `docs/governance/strategy-artifact-status-matrix.md`
- `docs/governance/validation-requirements-matrix.md`
- `docs/governance/crypto-paper-promotion-checklist.md`
- `docs/governance/crypto-strategy-promotion.md`
- `docs/governance/no-signal-runtime-runbook.md`

This plan is intentionally conservative. It does not assume that promising research is promotion-ready. It assumes that every strategy must earn promotion through artifact-backed and runtime-backed evidence.

---

## Purpose

This execution plan answers four practical questions:

1. **Which strategies should be worked first?**
2. **What exact sequence should be followed from candidate to promotion?**
3. **What are the stop/go rules at each stage?**
4. **Which strategy families are near-term candidates versus deferred cleanup work?**

---

## Core Principle

A strategy must not be promoted because:
- it looks promising in a doc
- it has high CAGR
- it has run once in paper
- the runtime is active
- there is pressure to fill a promoted set

A strategy may only be promoted when its required evidence stack is complete under the requirements in `docs/governance/validation-requirements-matrix.md` and its current status can be marked as verified in `docs/governance/strategy-artifact-status-matrix.md`.

---

## Current Portfolio of Work

Based on the current status matrix, the strategy universe should be handled in four cohorts.

### Cohort A — Near-Term Promotion Candidates

These are the highest-priority candidates for immediate evidence-building because they already have meaningful documented momentum.

| strategy | slug_or_id | track | current_state | why prioritized |
|---|---|---|---|---|
| ETH/BTC ratio mean reversion v5 | `eth-btc-ratio-mean-reversion-v5` | crypto | paper / promotion candidate | Clearest documented crypto promotion lane with explicit candidate-to-promoted workflow already defined |
| D7 — TQQQ stacked credit | `d7-tqqq-stacked-credit` | track_d | robustness / paper candidate | Strong review result, but explicitly blocked on CPCV, perturbation, and paper gate |
| D2 — BTC momentum v2 | `d2-btc-momentum-v2` | track_d | backtest passed / paper candidate | Strong documented review result and likely diversification value |

### Cohort B — Validate-Next Candidates

These have meaningful evidence but are lower priority than Cohort A.

| strategy | slug_or_id | track | current_state | why here |
|---|---|---|---|---|
| D1 — TLT/TQQQ sprint | `d1-tlt-tqqq-sprint` | track_d | backtest passed / robustness candidate | Passed documented review but still needs full promotion stack |
| D6 — LQD/TQQQ sprint | `d6-lqd-tqqq-sprint` | track_d | backtest passed / robustness candidate | Passed documented review, but may be dominated by D7 unless standalone value is proven |

### Cohort C — Implementation-First Candidates

These are not promotion candidates yet; they first need validation work to catch up with implementation.

| strategy | slug_or_id | track | current_state | why here |
|---|---|---|---|---|
| Polymarket NegRisk + combinatorial arb | `polymarket-neg-risk-arb` | track_c | implementation / paper candidate | Module appears implemented, but live scan, historical validation, and paper gates remain incomplete |

### Cohort D — Deferred / Cleanup-First

These should not enter promotion review yet.

| strategy_or_family | slug_or_id | reason for deferral |
|---|---|---|
| Default sleeve / broader Track A family | `default` pod / track_a family | Significant artifact debt and incomplete lifecycle evidence across many registered strategies |
| D4 — sector sprint top-1 retry | `d4-sector-sprint-top1` | Current reviewed form is conditional and requires rerun under revised spec |
| Any strategy with missing or unfrozen lifecycle artifacts | n/a | Cannot enter serious promotion review until artifacts are backfilled and frozen |

---

## Master Execution Sequence

All strategies in Cohorts A-C should move through the same sequence. The exact runtime implementation differs by track, but the gating order should remain stable.

1. **Inventory and freeze**
2. **Backtest**
3. **Robustness**
4. **Walk-forward**
5. **Paper validation**
6. **Promotion review**
7. **Canary**
8. **Promotion**
9. **Post-promotion monitoring**

No strategy should skip stages unless an explicit, documented waiver exists.

---

## Phase 0 — Inventory and Freeze

### Objective
Establish a clean and auditable starting point for each strategy.

### Required checks
For each strategy:
- `mandate.yaml` exists and is current
- `hypothesis.yaml` exists and is pre-results
- `data-contract.yaml` exists and is current
- `research-spec.yaml` exists
- `research-spec.yaml` is frozen
- experiment history exists in append-only form
- current row exists and is accurate in `docs/governance/strategy-artifact-status-matrix.md`

### Deliverables
Per strategy:
- lifecycle artifact checklist
- missing-artifact list
- frozen-spec confirmation
- initial promotion blocker list

### Stop/go rule
Do not begin promotion-oriented validation for a strategy if:
- research spec is missing
- research spec is not frozen
- experiment history is absent
- current status cannot be expressed in the status matrix without major unknowns

---

## Phase 1 — Backtesting Campaign

### Objective
Produce a frozen-spec baseline that can be trusted as a starting point for robustness work.

### Required work
For each strategy:
- run or rerun baseline backtest on frozen spec
- confirm realistic execution assumptions
- record experiment in append-only registry
- summarize:
  - CAGR
  - Sharpe
  - max drawdown
  - turnover
  - cost sensitivity
  - key configuration assumptions

### Minimum quality rules
- no hidden spec drift during evaluation
- no diagnostic-only assumptions presented as production evidence
- fill delay realism respected
- results logged in reproducible form

### Deliverables
Per strategy:
- backtest artifact
- experiment references
- metric summary
- recommendation: proceed / revise / retire

### Stop/go rule
Any strategy with weak or non-reproducible baseline evidence remains research-only and does not advance to robustness.

---

## Phase 2 — Robustness Campaign

### Objective
Eliminate fragile strategies before paper or promotion consideration.

### Required gates
At minimum from the validation requirements matrix:
- `DSR >= 0.95`
- `PBO <= 0.10`
- CPCV mean OOS Sharpe `> 0`
- CPCV median OOS Sharpe `> 0`
- Sharpe remains `> 0` at 2x costs
- parameter stability `> 50%`

### Required work
For each advancing strategy:
- CPCV / OOS validation
- perturbation testing
- cost stress
- parameter stability analysis
- summary of fragility and failure modes

### Strategy-specific notes
- `d7-tqqq-stacked-credit` is explicitly blocked here until CPCV and perturbation testing are complete
- crypto candidates should be treated as requiring especially clear robustness evidence before any promoted-set changes

### Deliverables
Per strategy:
- robustness artifact
- pass/fail table
- fragility summary
- next-step recommendation

### Stop/go rule
Any strategy failing DSR, PBO, CPCV, or cost-survival gates stops here and is not eligible for walk-forward or paper promotion review until revised and rerun.

---

## Phase 3 — Walk-Forward Validation

### Objective
Prove that a strategy generalizes across time rather than only fitting a single historical slice.

### Required work
For each strategy passing robustness:
- run walk-forward validation
- compare in-sample and out-of-sample behavior
- note degradation ratio
- identify regime sensitivity where practical

### Required interpretation
A strategy that passes robustness but collapses in walk-forward is not promotion-ready.

### Deliverables
Per strategy:
- walk-forward artifact
- OOS performance summary
- degradation notes
- pass/fail recommendation

### Stop/go rule
No strategy may be considered promotion-ready unless walk-forward is passed and recorded.

---

## Phase 4 — Paper Validation

### Objective
Confirm that a strategy behaves acceptably under runtime conditions rather than only under research assumptions.

### Universal paper gates
Before promotion review:
- paper duration `>= 30 days`
- paper trade count `>= 50`
- paper Sharpe `>= 0.60`
- operational systems tested

### Crypto-specific lane
For `eth-btc-ratio-mean-reversion-v5`, use the candidate runtime already defined in governance docs:
- pod: `crypto-ethbtc-paper`
- set: `candidate_crypto`

Daily checks must include:
- scheduler health
- data freshness
- runtime decision freshness
- deterministic candidate → governor → risk path
- paper metric refresh using:
  - `scripts/update_crypto_paper_eval.py`
- strict promotion validation using:
  - `scripts/validate_crypto_promotion.py --set candidate_crypto --strict`

### Non-crypto paper lane
For non-crypto strategies chosen to advance:
- define or use a paper runtime path
- track incident logs, veto behavior, signal density, and operational sanity

### Deliverables
Per strategy:
- paper-trading artifact
- incident review
- operational review
- paper gate pass/fail result

### Stop/go rule
A strategy that lacks sufficient paper duration, trade count, or operational stability remains `candidate` or `paper` and cannot move to promotion.

---

## Phase 5 — Promotion Review

### Objective
Make promotion a documented gate decision rather than an informal conclusion.

### Required review inputs
Every promotion review should check:

#### Lifecycle / research
- mandate current
- hypothesis current
- data contract current
- research spec frozen
- backtest evidence recorded
- robustness passed
- walk-forward passed
- SPA significance passed
- MinTRL satisfied

#### Paper / operations
- paper duration passed
- paper trade count passed
- paper Sharpe passed
- operational checklist complete
- incident review complete

#### Runtime trust
- config identity known
- data freshness verified
- decision freshness verified
- signal state observable
- risk veto reasons observable
- execution path observable
- kill switches wired
- telemetry sufficient for runtime trust

### Allowed outputs
Exactly one of:
- `promote`
- `keep_in_paper`
- `keep_as_candidate`
- `retire`
- `rework_and_rerun`

### Stop/go rule
If any required gate item is `unknown`, `missing`, `failed`, or materially `partial`, the promotion answer is not `promote`.

---

## Phase 6 — Canary Validation

### Objective
Limit risk between paper success and full promotion.

### Universal canary gates
- `10%` allocation
- `>= 14 days`
- drawdown `< 10%`
- canary Sharpe `>= 0.50`
- no material kill switch events
- baseline metrics recorded
- promotion record updated

### Deliverables
Per strategy:
- canary deployment record
- canary evaluation artifact
- decision: continue / revert / extend / retire

### Stop/go rule
No strategy should move from paper directly to full promoted status without successful canary unless an explicit waiver is documented.

---

## Phase 7 — Promotion and Post-Promotion Monitoring

### Objective
Promote selectively and preserve rollback safety.

### Crypto promotion path
Only after all crypto gates pass:
1. remove `eth-btc-ratio-mean-reversion-v5` from `candidate_crypto`
2. add it to `promoted_crypto`
3. validate the promoted set with strict validation
4. reload runtime
5. keep the candidate paper pod alive for one extra day as rollback protection

### General promotion path
For non-crypto strategies:
1. move from candidate/paper to canary
2. complete canary review
3. move to promoted only after canary passes
4. record promotion decision and rationale

### Ongoing monitoring
After any promotion:
- review live-vs-paper degradation
- review signal density drift
- review veto-rate behavior
- review incidents and kill-switch activity
- update status matrix and governance records

---

## Prioritized Work Queue

The recommended order of execution is:

1. `eth-btc-ratio-mean-reversion-v5`
2. `d7-tqqq-stacked-credit`
3. `d2-btc-momentum-v2`
4. `d1-tlt-tqqq-sprint`
5. `d6-lqd-tqqq-sprint`
6. `polymarket-neg-risk-arb`
7. default / Track A cleanup work
8. `d4-sector-sprint-top1` retry after revised spec

This order balances:
- clarity of current governance lane
- likelihood of near-term promotion relevance
- current evidence strength
- expected artifact debt

---

## Suggested Weekly Operating Cadence

### Week 1 — Inventory and Freeze
Focus:
- finalize cohort assignments
- backfill missing lifecycle artifacts
- freeze specs
- list missing evidence per strategy

### Week 2 — Backtests
Focus:
- run or normalize frozen-spec backtests
- ensure experiment records are append-only
- produce comparable metric summaries

### Week 3 — Robustness and Walk-Forward
Focus:
- CPCV
- perturbation
- 2x-cost survival
- parameter stability
- walk-forward

### Week 4+ — Paper Validation
Focus:
- continue crypto candidate paper lane
- start selected non-crypto paper lanes
- collect incident and runtime observability evidence

### Promotion Board After Evidence Completion
Focus:
- review only strategies with complete stacks
- promote selectively
- do not bulk-promote by sleeve

---

## Strategy-by-Strategy Immediate Next Step

| strategy | immediate next step |
|---|---|
| `eth-btc-ratio-mean-reversion-v5` | verify artifact completeness, rerun/confirm backtest + robustness + walk-forward evidence, continue candidate paper lane |
| `d7-tqqq-stacked-credit` | run CPCV and perturbation robustness, then walk-forward, then paper if passed |
| `d2-btc-momentum-v2` | verify artifact completeness, normalize evidence stack, then run robustness/walk-forward if not already artifact-backed |
| `d1-tlt-tqqq-sprint` | confirm artifact completeness and move through robustness + walk-forward before paper review |
| `d6-lqd-tqqq-sprint` | same as D1, while explicitly testing whether it adds value beyond D7 |
| `polymarket-neg-risk-arb` | complete live scan, historical validation, and paper validation before any promotion discussion |
| `default` / Track A family | perform artifact cleanup and status-matrix hardening before promotion work |
| `d4-sector-sprint-top1` | rerun under revised research spec before any further gating |

---

## Explicit Do-Not-Do Rules

Do not:
- promote a strategy because a narrative review says it is promising
- treat runtime-enabled as equivalent to governance-complete
- skip walk-forward because backtest results are strong
- treat quiet runtime behavior as proof of safety
- promote a whole sleeve when only one strategy has complete evidence
- add crypto strategies to `promoted_crypto` before the candidate paper checklist passes in full

---

## Definition of Done

A strategy is ready for promotion only when all of the following are true:

- lifecycle artifacts are present and current
- research spec is frozen
- backtests are recorded and reproducible
- robustness passed
- walk-forward passed
- paper gates passed
- runtime trust requirements passed
- canary passed
- promotion decision is recorded
- status matrix can be updated using evidence-backed values rather than `unknown`, `mixed`, or narrative-only interpretations

If any one of these remains incomplete, the strategy remains below promotion status.

---

## Next Documentation Step

This file defines the operating plan.

The next natural implementation steps are:
1. create a per-strategy promotion tracker table or checklist artifact
2. wire exact command/runbook sections for each cohort
3. update `docs/governance/strategy-artifact-status-matrix.md` as evidence is produced
4. keep runtime docs aligned with promotion state transitions
