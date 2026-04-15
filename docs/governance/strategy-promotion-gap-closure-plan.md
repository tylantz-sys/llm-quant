# Strategy Promotion Gap-Closure Plan

This document is the operational bridge between:

- `docs/governance/strategy-promotion-execution-plan.md`
- `docs/governance/strategy-artifact-status-matrix.md`
- `docs/governance/validation-requirements-matrix.md`
- `docs/governance/crypto-paper-promotion-checklist.md`

Its purpose is to help start testing immediately by closing **promotion-gate gaps on a strategy-by-strategy basis** rather than waiting for the entire repository to reach uniform maturity.

This is a **testing-start plan**. It is not a claim that the full system is already ready for broad production expansion.

---

## Core Operating Rule

For each strategy, separate:

1. **Start-testing blockers**  
   These prevent entry into the next validation stage.

2. **Promotion blockers**  
   These do not prevent testing, but they do prevent production expansion.

This distinction is critical. The system should not wait for every promotion blocker to be solved before beginning valid testing work.

---

## Readiness Buckets

Each strategy should be placed into one of four buckets.

### Bucket 1 — Ready to Start Testing Now
Requirements:
- strategy identity is clear
- research spec exists or can be frozen immediately
- backtest path exists
- no critical ambiguity about config, slug, or ownership

Action:
- enter active validation immediately

### Bucket 2 — Needs Artifact Cleanup Before Testing
Requirements failing:
- missing spec
- unfrozen spec
- unclear experiment lineage
- unclear strategy boundary

Action:
- backfill lifecycle artifacts before testing

### Bucket 3 — Implemented but Under-Validated
Requirements failing:
- code exists
- validation evidence is incomplete
- paper/runtime evidence is incomplete

Action:
- validate before any promotion discussion

### Bucket 4 — Explicitly Blocked Pending Redesign or Rerun
Requirements failing:
- reviewed configuration is conditional, failed, or explicitly queued for retry

Action:
- revise and refreeze the research spec before resuming validation

---

## Strategy Queue

## Wave 1 — Start Immediately

These are the first strategies to move into active testing.

| strategy | slug | track | readiness bucket | why now |
|---|---|---|---|---|
| ETH/BTC ratio mean reversion v5 | `eth-btc-ratio-mean-reversion-v5` | crypto | Bucket 1 | clearest documented candidate-to-promoted lane |
| D7 — TQQQ stacked credit | `tqqq-stacked-credit` | track_d | Bucket 3 | implemented research artifact exists, but fresh artifact-backed run is conditional and weaker than prior narrative review |
| D2 — BTC momentum v2 | `d2-btc-momentum-v2` | track_d | Bucket 1 | strong reviewed candidate that needs formalized gate evidence |

## Wave 2 — Start After Wave 1 Blocker Boards Exist

| strategy | slug | track | readiness bucket | why here |
|---|---|---|---|---|
| D1 — TLT/TQQQ sprint | `d1-tlt-tqqq-sprint` | track_d | Bucket 1 | promising but lower priority than D7/D2 |
| D6 — LQD/TQQQ sprint | `d6-lqd-tqqq-sprint` | track_d | Bucket 1 | promising, but may be redundant with D7 |
| LQD/SPY credit lead-lag | `lqd-spy-credit-lead` | track_a | Bucket 3 | artifact lineage is now cleaner after WFO repair, but canonical 5-year baseline is weak and must supersede the earlier exploratory 10-year baseline before any promotion talk |

## Wave 3 — Validation Track, Not Promotion Track

| strategy | slug | track | readiness bucket | why here |
|---|---|---|---|---|
| Polymarket NegRisk + combinatorial arb | `polymarket-neg-risk-arb` | track_c | Bucket 3 | appears implemented, but evidence stack is incomplete |

## Deferred

| strategy_or_family | slug | readiness bucket | why deferred |
|---|---|---|---|
| Default sleeve / broader Track A family | `default` / track_a family | Bucket 2 | artifact debt too large for immediate promotion-track work |
| D4 — sector sprint top-1 retry | `d4-sector-sprint-top1` | Bucket 4 | conditional retry required before resumed validation |

---

## Decision Rule by Validation Stage

A strategy should be advanced one stage at a time.

### Ready for backtesting if:
- strategy slug/config is identified
- research spec exists
- research spec is frozen
- backtest path exists

### Ready for robustness if:
- baseline backtest is complete
- experiment record exists
- parameters are explicit
- execution assumptions are explicit

### Ready for walk-forward if:
- baseline backtest is good enough to advance
- robustness campaign inputs are defined
- CPCV / perturbation plan is clear

### Ready for paper if:
- robustness passed
- walk-forward passed
- runtime path exists
- telemetry is at least minimally interpretable

### Ready for promotion review if:
- paper gates passed
- runtime trust requirements passed
- required artifacts are current
- no material gate remains `unknown`, `missing`, or `failed`

---

## Per-Strategy Gap-Closure Tracker

## 1. ETH/BTC ratio mean reversion v5

- **Slug:** `eth-btc-ratio-mean-reversion-v5`
- **Track:** crypto
- **Current aim:** move into active promotion-track testing immediately
- **Readiness bucket:** Bucket 1 — Ready to Start Testing Now

### Start-testing blockers
- verify strategy-level artifact completeness
- verify research spec is frozen
- verify exact backtest artifact lineage
- verify robustness artifact existence and status
- verify walk-forward artifact existence and status

### Promotion blockers
- paper gate not yet proven complete
- strict candidate validation not yet confirmed as passed
- promoted-set transition not yet justified

### Minimum next actions
1. verify current strategy artifact inventory
2. confirm or regenerate frozen-spec backtest evidence
3. confirm or regenerate robustness artifact
4. confirm or regenerate walk-forward artifact
5. continue `crypto-ethbtc-paper` paper lane
6. refresh paper metrics and strict validation

### Testing start status
- **Can start now:** yes

### Current production blocker
- complete evidence stack plus passing paper gate

---

## 2. D7 — TQQQ stacked credit

- **Slug:** `tqqq-stacked-credit`
- **Track:** track_d
- **Current aim:** reconcile prior review claims with current artifact-backed evidence before any promotion discussion
- **Readiness bucket:** Bucket 3 — Implemented but Under-Validated

### Start-testing blockers
- reconcile slug mismatch between narrative docs and on-disk artifacts
- confirm whether `research_spec.yaml` is the canonical D7 artifact or only a preliminary combined-study output
- formalize experiment lineage for the stacked result

### Promotion blockers
- current artifact-backed result is conditional rather than passed
- DSR is below Track D threshold in the fresh combined run
- inter-signal correlation is high and did not clear the diversification claim threshold
- CPCV not yet formalized as a standalone robustness artifact
- perturbation testing not yet formalized as a standalone robustness artifact
- 2x-cost survival not yet formalized
- walk-forward not yet formalized
- paper lane not yet passed

### Minimum next actions
1. normalize D7 naming to `tqqq-stacked-credit` across governance docs
2. treat the current `research_spec.yaml` output as preliminary evidence, not promotion-grade proof
3. create a formal robustness artifact for the stacked strategy
4. run 2x-cost survival
5. run walk-forward
6. only define a paper lane if the formal robustness stack passes

### Testing start status
- **Can start now:** yes, but only as evidence reconciliation and under-validation work

### Current production blocker
- current artifact-backed result is conditional and does not support promotion readiness

---

## 3. D2 — BTC momentum v2

- **Slug:** `d2-btc-momentum-v2`
- **Track:** track_d
- **Current aim:** normalize from promising review result to formal candidate
- **Readiness bucket:** Bucket 1 — Ready to Start Testing Now

### Start-testing blockers
- verify lifecycle artifacts
- verify frozen-spec state
- verify baseline backtest artifact location and lineage

### Promotion blockers
- robustness artifact may not be fully formalized
- walk-forward evidence may not be formalized
- paper evidence not yet complete

### Minimum next actions
1. confirm lifecycle artifacts
2. freeze or confirm frozen spec
3. normalize baseline backtest evidence
4. run or verify robustness artifact
5. run walk-forward
6. define paper lane

### Testing start status
- **Can start now:** yes

### Current production blocker
- incomplete formal robustness, walk-forward, and paper evidence

---

## 4. D1 — TLT/TQQQ sprint

- **Slug:** `d1-tlt-tqqq-sprint`
- **Track:** track_d
- **Current aim:** determine if it deserves paper-lane entry
- **Readiness bucket:** Bucket 1 — Ready to Start Testing Now

### Start-testing blockers
- confirm artifact completeness
- verify frozen-spec state
- normalize current backtest evidence

### Promotion blockers
- robustness not yet formalized
- walk-forward not yet formalized
- paper gate not yet complete
- portfolio-priority ranking not yet settled

### Minimum next actions
1. confirm artifacts
2. normalize baseline backtest
3. run robustness
4. run walk-forward
5. decide if it advances to paper based on both gate results and portfolio value

### Testing start status
- **Can start now:** probably yes

### Current production blocker
- incomplete full evidence stack

---

## 5. D6 — LQD/TQQQ sprint

- **Slug:** `d6-lqd-tqqq-sprint`
- **Track:** track_d
- **Current aim:** determine if it adds enough value beyond D7
- **Readiness bucket:** Bucket 1 — Ready to Start Testing Now

### Start-testing blockers
- confirm artifact completeness
- verify frozen-spec state
- normalize current backtest evidence

### Promotion blockers
- robustness not yet formalized
- walk-forward not yet formalized
- paper gate not yet complete
- diversification value versus D7 not yet established

### Minimum next actions
1. confirm artifacts
2. normalize baseline backtest
3. run robustness
4. run walk-forward
5. review marginal diversification value
6. decide whether it merits paper lane entry

### Testing start status
- **Can start now:** probably yes

### Current production blocker
- incomplete gate stack and unresolved redundancy question

---

## 6. Polymarket NegRisk + combinatorial arb

- **Slug:** `polymarket-neg-risk-arb`
- **Track:** track_c
- **Current aim:** convert implementation into validated candidate status
- **Readiness bucket:** Bucket 3 — Implemented but Under-Validated

### Start-testing blockers
- verify exact strategy boundary
- verify config and data assumptions
- define historical validation path

### Promotion blockers
- historical validation incomplete
- live scan validation incomplete
- paper evidence incomplete
- promotion stack largely absent

### Minimum next actions
1. confirm implemented scope in code/config
2. run historical validation
3. run robustness where applicable
4. run live scan validation
5. define paper lane

### Testing start status
- **Can start now:** yes, but as implementation validation rather than near-term promotion testing

### Current production blocker
- validation evidence has not yet caught up to implementation maturity

---

## 7. LQD/SPY credit lead-lag

- **Slug:** `lqd-spy-credit-lead`
- **Track:** track_a
- **Current aim:** treat as an evidence-reconciliation candidate, not a promotion candidate
- **Readiness bucket:** Bucket 3 — Implemented but Under-Validated

### Start-testing blockers
- baseline lineage was mixed until this session
- canonical baseline evidence had to be regenerated to match the frozen-spec / data-contract / WFO pathway
- older exploratory artifact still exists and can be mistaken for the canonical baseline if not called out explicitly

### Promotion blockers
- canonical baseline backtest is now negative under the current 5-year lineage
- canonical baseline materially underperforms the older exploratory 10-year artifact
- robustness state was not revalidated after baseline lineage repair
- paper artifact exists, but promotion logic should not lean on it while canonical baseline economics are weak
- strategy remains partial within the broader Track A artifact debt context

### Minimum next actions
1. treat `e91c7cf3` as exploratory / non-canonical evidence in governance discussions
2. use `5be70d7f` as the canonical current baseline because it matches the 5-year data-contract and repaired WFO lineage
3. decide whether to retire the strategy, redesign the spec, or run a fresh robustness campaign only if there is a strong reason to believe the canonical baseline is still salvageable
4. do not advance this strategy toward promotion until canonical baseline profitability is re-established

### Testing start status
- **Can start now:** yes, but only as evidence reconciliation / redesign review rather than promotion-track acceleration

### Current production blocker
- canonical baseline is unprofitable, so promotion readiness is not supported

---

## 8. Default sleeve / broader Track A family

- **Slug:** `default` / track_a family
- **Track:** track_a
- **Current aim:** reduce artifact debt enough to re-enter the queue selectively
- **Readiness bucket:** Bucket 2 — Needs Artifact Cleanup Before Testing

### Start-testing blockers
- artifact debt across multiple strategies
- unclear which strategies are worth immediate recovery
- inconsistent lifecycle completeness

### Promotion blockers
- broad missing evidence
- many strategies not strategy-level promotion-ready
- unclear prioritization within the family

### Minimum next actions
1. identify the top few Track A strategies worth salvaging first
2. backfill or verify lifecycle artifacts only for those
3. freeze those specs
4. move them into normal backtest / robustness / walk-forward sequence

### Testing start status
- **Can start now:** only selectively, not as a full family

### Current production blocker
- artifact debt and low strategy-level clarity

---

## 9. D4 — sector sprint top-1 retry

- **Slug:** `d4-sector-sprint-top1`
- **Track:** track_d
- **Current aim:** rerun only after revised spec is prepared
- **Readiness bucket:** Bucket 4 — Explicitly Blocked Pending Redesign or Rerun

### Start-testing blockers
- current reviewed configuration is conditional
- revised spec not yet frozen

### Promotion blockers
- baseline reviewed form did not clear the necessary bar

### Minimum next actions
1. revise the research spec
2. freeze the updated spec
3. rerun backtest
4. only then re-enter the normal validation ladder

### Testing start status
- **Can start now:** no, not until revised spec is frozen

### Current production blocker
- redesign/rerun required

---

## Gap Priority Order

Close gaps in this order:

1. missing or unfrozen research specs
2. missing strategy identity / config clarity
3. missing baseline backtest lineage
4. canonical-vs-exploratory baseline lineage cleanup for strategies with mixed evidence
5. missing robustness artifacts for Wave 1 strategies
6. missing walk-forward artifacts for Wave 1 strategies
7. missing paper-lane structure for near-term candidates
8. portfolio-ranking and redundancy review
9. broad family cleanup for deferred groups

This order is designed to unlock testing as quickly as possible while still maintaining valid promotion discipline.

---

## Immediate Weekly Execution Plan

### Week 1 — Build blocker boards and freeze specs
Focus:
- verify artifact completeness for Wave 1
- freeze specs where needed
- identify exact start-testing blockers
- identify exact promotion blockers

### Week 2 — Normalize baseline evidence
Focus:
- confirm or regenerate frozen-spec backtests
- establish clean experiment lineage
- prepare robustness inputs

### Week 3 — Run robustness and walk-forward on Wave 1
Focus:
- CPCV
- perturbation
- 2x-cost survival
- walk-forward

### Week 4 — Start paper-path qualification for passing candidates
Focus:
- continue crypto candidate paper lane
- define or start paper lanes for Track D candidates that pass prior stages
- gather runtime trust evidence

### Week 5+ — Promotion review only for complete stacks
Focus:
- review only strategies with full evidence
- keep incomplete strategies in candidate or paper
- do not bulk-promote by family

---

## Explicit Do-Not-Do Rules

Do not:
- wait for repo-wide perfection before starting testing
- promote a strategy because it has a strong narrative review
- treat “runtime exists” as equivalent to “promotion-ready”
- send Track A into mass cleanup before selecting high-value names
- move a strategy into production with unresolved `unknown` gate items
- skip walk-forward or paper gates because backtest results look attractive

---

## Definition of Success

This plan succeeds when:
- testing starts immediately for Wave 1 strategies
- each strategy has a visible blocker list
- blockers are resolved one validation stage at a time
- production expansion remains strategy-specific rather than family-wide
- no strategy reaches production without clearing its own required gates

At that point, the system is doing the right thing:
**testing can move now, while production expansion remains evidence-driven.**
