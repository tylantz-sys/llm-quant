# Strategy Thorough Testing Plan

This document is the operational runbook for **thorough, documentation-backed strategy testing** in `llm-quant`.

It translates the program’s research doctrine and governance requirements into an execution order that can be applied strategy by strategy. Its purpose is to help the system start testing immediately without relaxing standards, while preventing weak or ambiguous evidence from being mistaken for promotion readiness.

This document should be used together with:

- `README.md`
- `docs/research/institutional-quant-guide.md`
- `docs/governance/validation-requirements-matrix.md`
- `docs/governance/strategy-promotion-execution-plan.md`
- `docs/governance/strategy-promotion-gap-closure-plan.md`
- `docs/governance/strategy-artifact-status-matrix.md`

If any summary in this document conflicts with a canonical requirement source, the canonical source wins. This document is an execution runbook, not a lower bar.

---

## Purpose

This runbook answers four practical questions:

1. What counts as **proper backtesting** in this repository?
2. What counts as **proper walk-forward validation** in this repository?
3. In what order should testing be performed to close promotion-gate gaps efficiently?
4. When should a strategy **advance**, **pause**, or **stop**?

The goal is not to run more tests for their own sake. The goal is to build a trustworthy evidence stack that supports one of three outcomes:

- advance the strategy to the next validation stage
- hold the strategy at its current stage pending missing evidence
- stop the strategy and return it to redesign or retirement

---

## Source-of-Truth Guidance

This runbook is explicitly grounded in the following repo doctrine.

### From `README.md`
The repository defines the program-wide integrity gates and research methodology expectations:

- `DSR >= 0.95`
- `CPCV OOS/IS > 0` as a non-negotiable integrity control
- a five-gate robustness funnel before capital commitment
- promotion requires more than strong backtests alone

### From `docs/research/institutional-quant-guide.md`
Proper testing must respect the research doctrine:

- **spec freeze before backtest**
- **append-only recording of all trials**
- **multiple-testing control**
- **CPCV instead of naive shuffled cross-validation**
- **DSR / PBO as anti-overfitting controls**
- **distributional OOS inference rather than single-point inference**
- **economic rationale and anti-HARKing discipline**
- **explicit stop rules for weak or non-generalizing strategies**

### From `docs/governance/validation-requirements-matrix.md`
Promotion-track validation must satisfy the canonical gates:

- frozen research spec before backtest
- append-only experiment recording
- `fill_delay = 1` bar minimum unless diagnostic-only
- `DSR >= 0.95`
- `PBO <= 0.10`
- CPCV mean OOS Sharpe `> 0`
- CPCV median OOS Sharpe `> 0`
- 2x cost survival with Sharpe `> 0`
- parameter stability `> 50%`
- walk-forward / regime validation before promotion
- paper validation before promotion
- runtime trust and observability before trusting live operation

### From `docs/governance/strategy-promotion-gap-closure-plan.md`
Testing should start immediately, but it should start in the right order:

- prioritize **Wave 1** strategies first
- separate **start-testing blockers** from **promotion blockers**
- advance one validation stage at a time
- do not wait for repo-wide perfection before beginning valid testing

---

## Core Testing Principle

A strategy is not thoroughly tested because it has one attractive backtest.

A strategy is thoroughly tested only when it has passed the full sequence of:

1. lifecycle and artifact audit
2. frozen-spec baseline backtest
3. robustness testing
4. walk-forward validation
5. paper qualification validation
6. promotion review packaging

Each stage exists to answer a different question. No later-stage success repairs an invalid earlier stage.

---

## The Testing Ladder

All near-promotion strategies should move through the following ladder in order.

### Stage 1 — Lifecycle and Artifact Audit
Purpose:
- verify that the strategy can be tested validly

Questions answered:
- is the strategy identity clear?
- is there a canonical slug/config?
- do required lifecycle artifacts exist?
- is the research spec frozen?
- is baseline lineage attributable?

Minimum outputs:
- audit status
- blocker list
- promotion blocker list
- next executable action

### Stage 2 — Frozen-Spec Baseline Backtest
Purpose:
- establish a reproducible baseline under explicit assumptions

Questions answered:
- can the baseline result be rerun?
- are parameters and execution assumptions explicit?
- is the artifact traceable and suitable for robustness work?

Minimum outputs:
- backtest artifact
- experiment record
- metric summary
- explicit execution assumptions

### Stage 3 — Robustness Testing
Purpose:
- determine whether the edge survives realistic stress and multiple-testing controls

Questions answered:
- does the strategy survive anti-overfitting gates?
- is the result dependent on fragile parameter choices?
- does the edge survive more realistic costs?

Minimum outputs:
- CPCV results
- DSR result
- PBO result
- 2x-cost result
- parameter stability result
- robustness verdict

### Stage 4 — Walk-Forward Validation
Purpose:
- verify time-ordered generalization beyond the baseline and robustness stage

Questions answered:
- does the strategy generalize out of sample through time?
- is performance degradation acceptable?
- is the strategy overly regime-specific?

Minimum outputs:
- walk-forward artifact
- OOS degradation summary
- regime comments where practical
- walk-forward verdict

### Stage 5 — Paper Qualification
Purpose:
- confirm that research quality survives runtime reality

Questions answered:
- can the strategy run safely in the intended paper lane?
- are data freshness, signals, vetoes, and execution observable?
- does the paper record support further advancement?

Minimum outputs:
- paper lane status
- paper metrics
- incident/review log
- runtime trust note

### Stage 6 — Promotion Review Packaging
Purpose:
- package complete evidence for a strategy-specific promotion decision

Questions answered:
- are all hard gates passed?
- are any required items still unknown or missing?
- is the strategy ready for canary or continued paper only?

Minimum outputs:
- promotion evidence packet
- unresolved risk list
- explicit decision recommendation

---

## Proper Backtesting Standards

The following rules define proper backtesting in this repository.

### 1. No backtest before spec freeze
A strategy cannot enter valid promotion-track backtesting unless:

- the research spec exists
- the research spec is frozen
- the strategy identity and boundary are clear

Backtests run before freeze may be useful diagnostically, but they do not count as promotion-grade evidence.

### 2. Every run must be attributable
Every baseline or robustness run must be traceable to:

- a strategy slug
- a spec version
- a config or parameter set
- explicit execution assumptions
- an append-only experiment record

If the lineage is unclear, the evidence is not promotion-grade.

### 3. Execution assumptions must be realistic
At minimum:

- costs must be modeled
- `fill_delay = 1` bar unless the run is explicitly diagnostic-only
- position sizing assumptions must be explicit
- any data or execution simplification must be stated

A strong result under unrealistic execution assumptions is not a valid baseline.

### 4. No selective reporting
The testing doctrine requires:

- append-only experiment recording
- no cherry-picking of the best run without accounting for failed variants
- no narrative substitution for artifact-backed evidence

### 5. One strong backtest is never enough
A baseline backtest can justify advancing into robustness, but it does not justify promotion, and it does not replace walk-forward or paper validation.

---

## Proper Walk-Forward Standards

The following rules define proper walk-forward practice in this repository.

### 1. Walk-forward is a real gate
Walk-forward is not optional for promotable strategies. A strategy with attractive backtests but no walk-forward evidence is incomplete.

### 2. Time order must be respected
No shuffled or naive cross-validation should be used as a substitute for financial time-series validation. The doctrine favors:

- CPCV for robustness
- explicit walk-forward / rolling OOS evaluation for temporal generalization

### 3. Walk-forward should answer generalization, not optimization
The purpose is not to keep tuning until a rolling result looks acceptable. The purpose is to determine whether a previously defined strategy generalizes through time.

### 4. OOS degradation must be interpreted explicitly
Walk-forward review should include:

- OOS degradation versus baseline
- any obvious regime concentration or fragility
- whether degradation remains acceptable for the track and strategy family

### 5. Failed walk-forward blocks advancement
If walk-forward collapses materially, the strategy should not advance to paper or promotion merely because the baseline narrative is attractive.

---

## Canonical Robustness Thresholds

These are the minimum robustness requirements for promotion-track candidates.

| Requirement | Minimum Standard |
|---|---|
| DSR | `>= 0.95` |
| PBO | `<= 0.10` |
| CPCV mean OOS Sharpe | `> 0` |
| CPCV median OOS Sharpe | `> 0` |
| 2x cost survival | Sharpe remains `> 0` |
| Parameter stability | `> 50%` stable across tested perturbations |
| Fill delay realism | `>= 1` bar unless diagnostic-only |

Track-specific return/risk thresholds from canonical governance still apply:

| Requirement | Track A | Track B |
|---|---|---|
| Sharpe | `>= 0.80` | `>= 1.00` |
| Max drawdown | `< 15%` | `< 30%` |

These track-specific thresholds do not replace the universal integrity gates.

---

## Advancement Rules

A strategy advances only when the current stage is explicitly cleared.

### Advance from audit to baseline backtest if:
- strategy slug/config is identified
- required lifecycle artifacts exist or are explicitly backfilled
- research spec is frozen
- backtest path exists

### Advance from baseline to robustness if:
- baseline backtest completed cleanly
- experiment lineage is attributable
- parameters are explicit
- execution assumptions are explicit

### Advance from robustness to walk-forward if:
- robustness inputs are defined
- DSR, PBO, CPCV, cost, and parameter stability results are available
- the robustness verdict is sufficiently positive to justify more work

### Advance from walk-forward to paper if:
- walk-forward passed
- no major temporal generalization failure is present
- runtime path and telemetry are sufficiently defined to interpret paper behavior

### Advance from paper to promotion review if:
- paper duration and trade count gates are met
- paper Sharpe and operational gates are met
- runtime trust requirements are met
- no required item remains missing or unknown

---

## Stop-Testing and Pause Rules

Testing should stop or pause early when the next stage would not produce trustworthy evidence.

### Stop or pause immediately if:
- strategy identity is ambiguous
- research spec is missing
- research spec is unfrozen
- baseline lineage cannot be reconstructed
- execution assumptions are too ambiguous to trust the result

### Stop or return to redesign if:
- DSR fails
- PBO fails materially
- CPCV OOS results are non-positive
- parameter stability is clearly inadequate
- 2x-cost survival fails materially
- walk-forward collapses beyond acceptable degradation

### Pause before paper if:
- runtime path exists but is not interpretable
- signal generation, vetoes, or execution are not observable
- there is no trustworthy way to distinguish no-trade from broken runtime

### Do not promote if:
- any required item is still `missing`, `unknown`, or `failed`
- paper gates are incomplete
- walk-forward is absent
- runtime trust is incomplete

---

## Wave-Based Testing Campaign

The current campaign order should follow the gap-closure plan.

### Wave 1 — Start Immediately
These should receive the full testing ladder first.

1. `eth-btc-ratio-mean-reversion-v5`
2. `d7-tqqq-stacked-credit`
3. `d2-btc-momentum-v2`

### Wave 2 — Start After Wave 1 Blocker Boards Exist
4. `d1-tlt-tqqq-sprint`
5. `d6-lqd-tqqq-sprint`

### Wave 3 — Validation Track, Not Near-Term Promotion Track
6. `polymarket-neg-risk-arb`

### Deferred
- `default` / Track A family until selective artifact recovery is chosen
- `d4-sector-sprint-top1` until revised spec is frozen

---

## Per-Strategy Testing Checklist Template

Use the following checklist for each strategy.

### Strategy audit
- [ ] Canonical slug confirmed
- [ ] Canonical config path confirmed
- [ ] Mandate exists and is current
- [ ] Hypothesis exists and is pre-results
- [ ] Data contract exists and is current
- [ ] Research spec exists
- [ ] Research spec is frozen
- [ ] Backtest path exists
- [ ] Current artifact inventory recorded
- [ ] Start-testing blockers listed
- [ ] Promotion blockers listed

### Baseline backtest
- [ ] Frozen-spec baseline rerun completed
- [ ] Experiment record exists
- [ ] Parameters explicit
- [ ] Costs explicit
- [ ] Fill delay realistic
- [ ] Metric summary recorded
- [ ] Baseline verdict recorded

### Robustness
- [ ] CPCV completed
- [ ] DSR passed
- [ ] PBO passed
- [ ] CPCV mean OOS Sharpe positive
- [ ] CPCV median OOS Sharpe positive
- [ ] 2x-cost survival passed
- [ ] Parameter stability passed
- [ ] Robustness verdict recorded

### Walk-forward
- [ ] Walk-forward completed
- [ ] OOS degradation reviewed
- [ ] Regime sensitivity reviewed where practical
- [ ] Walk-forward verdict recorded

### Paper qualification
- [ ] Paper lane defined
- [ ] Runtime path verified
- [ ] Data freshness observable
- [ ] Decision freshness observable
- [ ] Veto/suppression state observable
- [ ] Execution path observable
- [ ] Paper metrics updated
- [ ] Incident log reviewed

### Promotion packaging
- [ ] Promotion checklist reviewed
- [ ] Unknown items resolved or explicitly blocking
- [ ] Recommendation written
- [ ] Strategy status updated in status ledger

---

## Immediate Execution Cadence

### Week 1 — Audit and freeze
Focus:
- verify artifact completeness for Wave 1
- freeze specs where needed
- build blocker boards

### Week 2 — Normalize baseline evidence
Focus:
- rerun or verify frozen-spec backtests
- establish experiment lineage
- prepare robustness inputs

### Week 3 — Run robustness and walk-forward
Focus:
- CPCV
- perturbation
- 2x-cost survival
- walk-forward

### Week 4 — Start or continue paper qualification for passing candidates
Focus:
- continue the crypto paper lane where applicable
- define paper lanes for Track D candidates that pass prior stages
- collect runtime trust evidence

### Week 5+ — Promotion review only for complete stacks
Focus:
- review only complete evidence stacks
- keep incomplete strategies in testing or paper
- avoid family-wide promotion shortcuts

---

## Immediate Next Move

The first practical move in this campaign is:

1. create this runbook
2. start with `eth-btc-ratio-mean-reversion-v5`
3. perform a **testability audit** before any new baseline or robustness claim is accepted

The audit should confirm:

- canonical slug/config identity
- lifecycle artifact completeness
- frozen-spec status
- backtest lineage
- robustness artifact status
- walk-forward artifact status
- exact blocker list for the next executable stage

---

## Explicit Anti-Shortcuts

Do not:

- treat a strong narrative review as a passed gate
- treat runtime existence as promotion readiness
- substitute a single backtest for robustness
- substitute CPCV for walk-forward, or walk-forward for paper
- use shuffled CV as proof of time-series generalization
- skip paper qualification because research metrics look attractive
- advance a strategy with unresolved required `unknown` items

---

## Definition of Success

This runbook is successful when:

- Wave 1 testing starts immediately
- each strategy has a visible blocker board
- each strategy advances one stage at a time
- backtesting and walk-forward practice remain aligned with repo doctrine
- promotion remains strategy-specific and evidence-driven

At that point, the system is testing correctly:
**fast enough to move, strict enough to trust.**
