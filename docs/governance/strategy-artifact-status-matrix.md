# Strategy Artifact and Runtime Status Matrix

This document is the canonical inventory for tracking the difference between **documented target state**, **artifact-backed validation state**, and **runtime-enabled state** for every strategy, sleeve, and deployment pod in the `llm-quant` system.

It exists to eliminate ambiguity across lifecycle, promotion, deployment, and operations documentation. A strategy is not considered promotion-ready because a README, plan, or review says it is promising. It is considered promotion-ready only when the required artifacts exist, pass their gates, and the runtime/telemetry controls required for deployment are confirmed.

This matrix is intentionally conservative. Unknown means not yet verified. Blank means not applicable. Narrative claims do not replace artifact evidence.

---

## Purpose

This matrix answers five operational questions:

1. **What is supposed to exist?**
2. **What artifacts actually exist and are current?**
3. **Which gates are passed versus merely described?**
4. **Which strategies are enabled in runtime?**
5. **Which deployments are operationally safe to trust?**

It is the primary bridge between:
- `docs/governance/quant-lifecycle.md`
- `docs/governance/model-promotion-policy.md`
- `docs/governance/control-matrix.md`
- `docs/governance/runtime-truth-table.md`
- track-specific research and deployment plans
- pod-level runtime configuration

---

## Usage Rules

1. **This document is a status ledger, not a marketing summary.**
2. **A status may only be marked complete when backed by a concrete artifact, code path, runtime verification, or signed review.**
3. **If evidence is missing, the status is `unknown`, `missing`, or `not verified`.**
4. **Promotion readiness requires artifact-backed proof, not interpretation.**
5. **Runtime enabled does not imply governance complete.**
6. **Governance complete does not imply runtime enabled.**
7. **Every strategy/sleeve should have one current row in the detailed matrix below.**

---

## Status Vocabulary

### Artifact Status
- `missing` — required artifact does not exist or has not been found
- `draft` — artifact exists but is incomplete or not yet approved/frozen
- `frozen` — artifact exists in immutable/final form where required
- `passed` — artifact exists and the associated gate passed
- `failed` — artifact exists and the associated gate failed
- `waived` — an explicit documented waiver exists
- `n/a` — not applicable to this sleeve/stage
- `unknown` — status not yet verified

### Runtime Status
- `disabled` — not enabled in active runtime
- `candidate` — exists in research/config but not promoted/runtime active
- `paper` — active only in paper validation
- `canary` — active in limited deployment
- `promoted` — active in normal deployment
- `suspended` — intentionally halted
- `retired` — no longer eligible for deployment
- `unknown` — runtime state not yet verified

### Evidence Quality
- `artifact-backed` — supported by an on-disk artifact or recorded review
- `runtime-backed` — supported by logs, config, DB, or live verification
- `narrative-only` — described in docs but not yet backed by hard evidence
- `mixed` — partial artifact or runtime proof exists, but not complete

---

## State Model

Every strategy/sleeve should be understood through three separate lenses.

### 1. Governance Lifecycle State
The formal lifecycle state from `quant-lifecycle.md`:
- idea
- mandate
- hypothesis
- data contract
- research spec
- backtest
- robustness
- paper
- promotion
- evaluation
- retirement

### 2. Artifact Completeness State
Whether required artifacts for the current and prior stages exist and are current.

### 3. Runtime State
Whether the strategy/sleeve is actually enabled in config/runtime and whether telemetry and kill-switch requirements are wired.

A strategy is only **operationally promotable** when all three are aligned.

---

## Required Columns

Each row in the detailed matrix should be maintained using the following fields.

| Column | Meaning |
|--------|---------|
| `strategy_or_sleeve` | Human-readable name |
| `slug_or_id` | Canonical slug, deployment identifier, or sleeve name |
| `track` | Track A / B / C / D / crypto / arb / other documented track |
| `asset_class` | equity / fixed_income / commodity / crypto / multi_asset / arb / other |
| `lifecycle_state` | Current formal lifecycle state |
| `mandate` | Artifact status |
| `hypothesis` | Artifact status |
| `data_contract` | Artifact status |
| `research_spec` | Artifact status |
| `spec_frozen` | yes / no / unknown |
| `backtest` | Artifact status |
| `robustness` | Artifact status |
| `walk_forward` | Artifact status |
| `paper_trading` | Artifact status |
| `canary` | Artifact status |
| `promotion_decision` | Artifact status |
| `runtime_enabled` | disabled / candidate / paper / canary / promoted / suspended / retired / unknown |
| `kill_switches_wired` | yes / partial / no / unknown |
| `telemetry_coverage` | complete / partial / missing / unknown |
| `runtime_verification` | runtime-backed / not verified / n/a |
| `evidence_quality` | artifact-backed / runtime-backed / narrative-only / mixed |
| `last_verified` | Date status was last checked |
| `notes` | Short explanation, gaps, blockers, or dependency notes |

---

## Summary Matrix

This section is the high-level operator view. Replace `unknown` values only when verified by artifact or runtime evidence.

| strategy_or_sleeve | slug_or_id | track | asset_class | lifecycle_state | runtime_enabled | evidence_quality | last_verified | notes |
|---|---|---|---|---|---|---|---|---|
| Default intraday sleeve | `default` pod | track_a / mixed runtime | multi_asset | backtest / runtime history | disabled | artifact-backed | 2026-03-31 | Historical DuckDB decisions/trades and portfolio snapshots exist for `default`, but current live paper-trading services were stopped and no active runtime verification remains. Lifecycle artifacts for many optimizer-linked Track A strategies are incomplete per the lifecycle gap audit. |
| Crypto intraday sleeve | `crypto` pod | crypto | crypto | paper / evaluation candidate | disabled | mixed | 2026-03-31 | Runtime had been verified earlier on 2026-03-31 with fresh Alpaca 5-minute bars, fresh decisions, and repeated `risk_off / 0 signals`. This session intentionally stopped the active paper-trading services, so current runtime state is disabled even though the pod remains a paper-evaluation candidate. Promotion checklist exists for `crypto-ethbtc-paper`, with strategy-level detail now started for `eth-btc-ratio-mean-reversion-v5`. |
| ETH/BTC ratio mean reversion v5 | `eth-btc-ratio-mean-reversion-v5` | crypto | crypto | paper / promotion candidate | candidate | artifact-backed | 2026-04-01 | Current session verified the on-disk artifact chain for this slug: frozen research spec, experiment artifacts, passed robustness (`overall_passed: true`), passed walk-forward (`passed: true`), and an existing paper-trading artifact. Runtime/pod-level verification and promotion decision evidence were not re-verified in this pass, so the strategy remains conservatively tracked as a candidate rather than promotion-passed. |
| GLD/SLV mean reversion v4 | `gld-slv-mean-reversion-v4` | other | commodity | robustness passed / paper candidate | candidate | artifact-backed | 2026-04-01 | Formalized in this session with a frozen research spec and passed registered backtest, robustness, walk-forward, and portfolio-fit review. No paper-trading artifact, canary evidence, or formal promotion decision was verified, so the strategy is tracked as a promotion-pipeline candidate rather than promotion-ready. |
| LQD/SPY credit lead-lag | `lqd-spy-credit-lead` | track_a | fixed_income / equity | walk-forward passed / paper candidate | candidate | artifact-backed | 2026-04-01 | Current session repaired walk-forward lineage so the runner now honors frozen-spec strategy identity and execution assumptions, then reran WFO successfully (`passed: true`). Canonical 5-year baseline rerun under current frozen-spec lineage produced a weaker artifact (`5be70d7f`, Sharpe -0.141, MaxDD 7.23%, 76 trades, healthy_nonzero_trading), while older artifact `e91c7cf3` remains a non-canonical 10-year / 200-warmup exploratory baseline. Strategy now has a cleaner artifact chain, but baseline profitability under canonical assumptions is negative, so it should not be treated as promotion-ready. |
| Track C arbitrage sleeve | `track_c` / arb family | track_c | arb | research spec / implementation candidate | disabled | artifact-backed | 2026-03-31 | `track-c-plan.md` documents approved priorities: Polymarket PROCEED, CEF PROCEED, funding rate CONDITIONAL, merger arb CONDITIONAL, VIX REJECT, basis DEFER. Plan says Polymarket scanner v1 is built but live scan / validation / paper trading remain incomplete, so sleeve is not runtime-promotable. |
| Polymarket NegRisk + combinatorial arb | `polymarket-neg-risk-arb` | track_c | arb | implementation / paper candidate | candidate | artifact-backed | 2026-03-31 | `track-c-plan.md` identifies this as Priority 1 and says the module is already implemented in `src/llm_quant/arb/` with Gamma client, NegRisk scanner, Claude combinatorial detector, CLI runner, and DuckDB schema all marked complete. However the same plan leaves first live scan, historical validation, and paper trading unchecked, so it is evidence-backed as implemented but not yet paper-validated or runtime-promoted. |
| Track D research sleeve | `track_d` family | track_d | equity / macro / leveraged ETF research | robustness / paper candidate | candidate | artifact-backed | 2026-03-31 | `track-d-review-2026-03-30.md` shows D1/D2/D6/D7 passing Track D gates, D3/D5 retired, D4 conditional retry. D7 still requires CPCV + perturbation robustness and then a 30-day paper gate before promotion, so family remains candidate rather than runtime promoted. |

---

## Detailed Validation Matrix

This is the authoritative working table for status review. Initial entries below are intentionally conservative and should be refined as artifacts are formally checked.

| strategy_or_sleeve | slug_or_id | track | asset_class | lifecycle_state | mandate | hypothesis | data_contract | research_spec | spec_frozen | backtest | robustness | walk_forward | paper_trading | canary | promotion_decision | runtime_enabled | kill_switches_wired | telemetry_coverage | runtime_verification | evidence_quality | last_verified | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Default intraday sleeve | `default` pod | track_a / mixed | multi_asset | backtest / runtime history | mixed | mixed | mixed | mixed | unknown | mixed | mixed | unknown | unknown | n/a | unknown | disabled | partial | partial | not verified | mixed | 2026-03-31 | Historical decisions/trades/snapshots for `default` exist in DuckDB, but live paper-trading services were intentionally stopped this session. `lifecycle-gap-audit-2026-03-30.md` shows major artifact debt across optimizer-linked Track A strategies: only `soxx-qqq-lead-lag` is fully compliant, `lqd-spy-credit-lead` is partial, and 14 registered strategies have no experiment artifacts on disk. |
| Crypto intraday sleeve | `crypto` pod | crypto | crypto | paper / evaluation candidate | mixed | mixed | mixed | mixed | unknown | mixed | mixed | mixed | mixed | n/a | unknown | disabled | unknown | partial | not verified | mixed | 2026-03-31 | Earlier on 2026-03-31 runtime was verified active with fresh bars and fresh decisions plus repeated no-trade / risk-off behavior. `crypto-paper-promotion-checklist.md` provides explicit paper-gate and promotion requirements for `eth-btc-ratio-mean-reversion-v5` in `crypto-ethbtc-paper`, but this session stopped the active services so current runtime is disabled. |
| ETH/BTC ratio mean reversion v5 | `eth-btc-ratio-mean-reversion-v5` | crypto | crypto | paper / promotion candidate | passed | passed | passed | frozen | yes | passed | passed | passed | passed | unknown | unknown | candidate | unknown | partial | not verified | artifact-backed | 2026-04-01 | Current session verified the on-disk strategy artifact chain: mandate, hypothesis, data contract, frozen research spec, experiment artifacts in `experiments/`, passed robustness (`overall_passed: true` in `robustness.yaml`), passed walk-forward (`passed: true` in `walk-forward.yaml`), and an existing `paper-trading.yaml`. Candidate runtime / pod verification, kill-switch wiring, telemetry completeness, canary evidence, and formal promotion decision remain unverified in this pass, so runtime stays candidate and promotion fields stay conservative. |
| GLD/SLV mean reversion v4 | `gld-slv-mean-reversion-v4` | other | commodity | robustness passed / paper candidate | unknown | unknown | unknown | frozen | yes | passed | passed | passed | missing | unknown | missing | candidate | unknown | unknown | not verified | artifact-backed | 2026-04-01 | Current session formalized the strategy with `data/strategies/gld-slv-mean-reversion-v4/research-spec.yaml` and verified a passing registered backtest, passing robustness gates, passing walk-forward validation, and low portfolio correlation versus the existing strategy set. Paper-trading evidence, canary evidence, kill-switch verification, telemetry coverage, and a formal promotion decision were not verified, so the strategy is recorded conservatively as a promotion-pipeline candidate rather than as promoted or runtime-enabled. |
| LQD/SPY credit lead-lag | `lqd-spy-credit-lead` | track_a | fixed_income / equity | walk-forward passed / paper candidate | unknown | unknown | passed | frozen | yes | failed | unknown | passed | passed | unknown | missing | candidate | unknown | unknown | not verified | artifact-backed | 2026-04-01 | Current session repaired walk-forward lineage and reran WFO successfully under frozen-spec identity/execution assumptions (`walk-forward.yaml` now `passed: true`, 11 folds, mean OOS Sharpe 1.653, max OOS drawdown 0.0088). A new canonical 5-year baseline artifact (`5be70d7f`) was then generated and is materially worse than the older exploratory 10-year baseline `e91c7cf3`: Sharpe -0.141 vs 0.514, MaxDD 7.23% vs 0.74%, trades 76 vs 41, smoke health healthy in both. Treat `e91c7cf3` as non-canonical exploratory evidence because it used `years: 10` and `warmup_days: 200`, whereas current data-contract/WFO lineage is 5 years. Promotion readiness is blocked because the canonical baseline backtest now fails profitability. |
| Track C arbitrage sleeve | `track_c` | track_c | arb | research spec / implementation candidate | unknown | unknown | mixed | passed | no | mixed | unknown | unknown | unknown | unknown | unknown | disabled | partial | unknown | not verified | mixed | 2026-03-31 | `track-c-plan.md` is approved and states Polymarket scanner v1 is already implemented (`src/llm_quant/arb/`, CLI runner built, Gamma client/scanner/detector/schema complete), but live scan, historical validation, and paper trading are still unchecked. CEF/funding/merger work remains planned or conditional, so sleeve is not promotion-ready. |
| Polymarket NegRisk + combinatorial arb | `polymarket-neg-risk-arb` | track_c | arb | implementation / paper candidate | unknown | unknown | unknown | passed | no | mixed | unknown | unknown | unknown | unknown | unknown | candidate | partial | unknown | not verified | artifact-backed | 2026-03-31 | `track-c-plan.md` marks this strategy UNANIMOUS PROCEED and explicitly says it is already implemented as `src/llm_quant/arb/`. Week-1 implementation checklist shows Gamma API client, NegRisk scanner, Claude combinatorial detector, CLI runner, and DuckDB schema complete, while first live scan, historical validation, and paper trading remain undone. This supports an evidence-backed implementation/candidate status, but not passed backtest, robustness, walk-forward, paper, or promotion fields. |
| Track D family | `track_d` | track_d | research multi_asset | robustness / paper candidate | mixed | mixed | mixed | mixed | mixed | passed | mixed | unknown | unknown | unknown | unknown | candidate | unknown | unknown | not verified | artifact-backed | 2026-03-31 | `track-d-review-2026-03-30.md` shows D1, D2, D6, and D7 passing Track D gates; D3 and D5 retired; D4 conditional retry. D7 still needs CPCV and perturbation testing and then a 30-day paper gate before promotion. Family therefore remains candidate, with strongest evidence in research review rather than runtime deployment. |
| D1 — TLT/TQQQ sprint | `d1-tlt-tqqq-sprint` | track_d | equity / rates | backtest passed / robustness candidate | unknown | unknown | unknown | unknown | unknown | passed | unknown | unknown | unknown | unknown | unknown | candidate | unknown | unknown | not verified | artifact-backed | 2026-03-31 | `track-d-review-2026-03-30.md` marks D1 PASS with CAGR 18.5%, Sharpe 1.43, MaxDD 12.7%, DSR 0.9941. Weight variants at 30%, 50%, and 70% all pass stated Track D gates. No explicit mandate/hypothesis/data-contract/research-spec artifacts were verified in this session. |
| D2 — BTC momentum v2 | `d2-btc-momentum-v2` | track_d | crypto | backtest passed / paper candidate | unknown | unknown | unknown | unknown | unknown | passed | unknown | unknown | unknown | unknown | unknown | candidate | unknown | unknown | not verified | artifact-backed | 2026-03-31 | `track-d-review-2026-03-30.md` marks D2 PASS with approximately 10% CAGR, Sharpe 0.96, MaxDD 2.8%, DSR 0.9376. Review recommends D2 as the diversification sleeve alongside D7, but promotion still requires the Track D paper gate. |
| D4 — sector sprint top-1 retry | `d4-sector-sprint-top1` | track_d | equity | conditional retry | unknown | unknown | unknown | draft | no | failed | unknown | unknown | unknown | unknown | unknown | candidate | unknown | unknown | not verified | artifact-backed | 2026-03-31 | `track-d-review-2026-03-30.md` labels D4 CONDITIONAL rather than passed: original 20-day/top-1/weekly spec delivered Sharpe 0.36 and requires retry with `lookback_days=60` and `top_n=2`. Treat current research spec as not frozen and backtest state as failed pending rerun. |
| D6 — LQD/TQQQ sprint | `d6-lqd-tqqq-sprint` | track_d | equity / credit | backtest passed / robustness candidate | unknown | unknown | unknown | unknown | unknown | passed | unknown | unknown | unknown | unknown | unknown | candidate | unknown | unknown | not verified | artifact-backed | 2026-03-31 | `track-d-review-2026-03-30.md` marks D6 PASS with 30/50/70% weight variants all inside Track D gates; 90% fails MaxDD. Review states D6 is largely subsumed by D7 and should only continue standalone if D7 fails robustness. |
| D7 — TQQQ stacked credit | `d7-tqqq-stacked-credit` | track_d | equity / credit | robustness / paper candidate | unknown | unknown | unknown | unknown | unknown | passed | draft | unknown | unknown | unknown | unknown | candidate | unknown | unknown | not verified | artifact-backed | 2026-03-31 | `track-d-review-2026-03-30.md` marks D7 PASS on preliminary combined results with CAGR 44.2%, Sharpe 1.26, MaxDD 33.4%, DSR 0.9224, meeting Track D target CAGR > 40%. However the same review explicitly says next step is full CPCV plus perturbation testing, then a 30-day paper gate before promotion, so robustness is draft/incomplete rather than passed. |

---

## Promotion Readiness Interpretation

A strategy/sleeve may only be treated as **promotion-ready** when all of the following are true:

1. `mandate`, `hypothesis`, `data_contract`, and `research_spec` are present and current
2. `spec_frozen = yes`
3. `backtest = passed`
4. `robustness = passed`
5. `walk_forward = passed`
6. `paper_trading = passed`
7. `promotion_decision = passed`
8. `kill_switches_wired = yes`
9. `telemetry_coverage = complete`
10. `runtime_verification = runtime-backed`
11. exit-stack scenario testing has been reviewed
12. canonical exit parity has been reviewed across runtime, paper, and backtest

If any field is `unknown`, `missing`, `partial`, `failed`, or `narrative-only`, the strategy is not promotion-ready.

For sleeves using profit-taking logic, promotion readiness also requires evidence that:
- exit-stack scenario behavior has been reviewed,
- canonical exit semantics are consistent across runtime, paper, and backtest,
- and any native broker realization path is treated as an implementation detail rather than a separate policy.

---

## Runtime Trust Interpretation

A sleeve may only be treated as **operationally trustworthy** when:

- runtime is enabled intentionally
- the controlling config is identified
- fresh data is verified
- fresh decisions are verified
- signal generation behavior is understood
- kill switches are wired
- telemetry is complete enough to distinguish:
  - no data
  - no decisions
  - zero signals
  - vetoed signals
  - execution failures
  - deliberate hold/no-trade states

This distinction matters because a live system can be:
- **working and producing no trades**
- **working but suppressed by policy/config**
- **broken and silently failing**

This matrix is intended to prevent those states from being conflated.

---

## Recommended Maintenance Workflow

### Weekly
- update `runtime_enabled`
- verify `kill_switches_wired`
- verify `telemetry_coverage`
- refresh `last_verified`

### At every promotion review
- update artifact statuses for all lifecycle stages
- link the scorecard/promotion evidence in notes
- confirm walk-forward and paper requirements explicitly

### At every runtime change
- note pod/config changes
- note whether runtime state moved from candidate → paper → canary → promoted
- verify observability did not regress

---

## Current Known Gaps the Matrix Is Meant to Fix

This document specifically addresses the governance ambiguity identified in the March 2026 documentation review:

1. **Target-state vs implemented-state blur**
2. **Fragmented validation requirements**
3. **Inconsistent walk-forward emphasis across tracks**
4. **Unclear promotion readiness by strategy family**
5. **Unclear distinction between runtime-enabled and governance-complete**
6. **Insufficient operator-facing clarity for live no-signal states**

---

## Evidence Notes for the 2026-03-31 Bootstrap Version

The initial rows in this file were seeded from the documented review and runtime observations available on 2026-03-31:

- user systemd service had been observed executing `--pod crypto` earlier on 2026-03-31
- fresh bars and fresh decisions were verified for the crypto pod before services were intentionally stopped
- repeated `risk_off / 0 signals` were observed in logs during that verified runtime window
- governance/research docs indicate stronger target-state process than uniformly operationalized artifact state
- Track C and Track D documentation suggest partial maturity rather than universally complete artifact-backed readiness

This file should now become the canonical place to refine those statuses from `unknown/mixed` into evidence-backed values.

---

## Catalog Semantics Freeze (Phase 1)

To make `config/strategies/catalog.toml` obey `docs/governance/quant-lifecycle.md`, catalog membership must use the same lifecycle meanings as the governance documents. The catalog is not allowed to use `promoted` as a convenience label for research-stage or partially validated strategies.

### Phase 1 policy

Effective for cleanup planning, catalog buckets are interpreted as follows:

| Catalog bucket | Required meaning |
|---|---|
| `promoted_default` | Fully lifecycle-complete, runtime-approved strategies only |
| `candidate_default` | Research or validation candidates that are not promotion-approved |
| `needs_revalidation` | Historically important or formerly promoted strategies that must re-earn promotion under the current governed funnel |
| `retired` | Strategies not eligible for deployment absent a new thesis and a new lifecycle pass |
| `candidate_crypto` | Crypto research/paper candidates not yet promotion-approved |
| `promoted_crypto` | Fully lifecycle-complete crypto strategies only |

### Hard inclusion rule for `promoted_default`

A slug may appear in `promoted_default` only if all of the following are true:

1. required upstream lifecycle artifacts exist and are current
2. `research-spec.yaml` is frozen
3. canonical backtest evidence is present and acceptable
4. `robustness.yaml` is present and passed
5. `walk-forward.yaml` is present and passed
6. `paper-trading.yaml` is present and passed
7. promotion decision evidence exists
8. runtime/telemetry controls required for safe deployment are verified

If any of the above is missing, failed, stale, or unknown, the slug is not promotion-clean and must not remain in `promoted_default`.

### Strict interim posture

Until the promoted roster has been revalidated against the lifecycle, the conservative target posture is:

- `promoted_default = []`
- unsupported or partially evidenced slugs move to `candidate_default` or `needs_revalidation`
- historically weak slugs move to `retired`

This posture prefers honest inactivity over false promotion confidence.

### Interpretation rule

Runtime-enabled state and catalog membership must not be conflated:
- a strategy can be runtime-known without being promoted
- a strategy can be artifact-backed without being runtime-enabled
- a strategy is only catalog-promoted when lifecycle, artifacts, and runtime trust all align

## Phase 2 Decision Ledger (Current Promoted Roster)

The current `config/strategies/catalog.toml` `promoted_default` roster must be treated as provisional until each slug is classified against the lifecycle rules above. This ledger is the decision table that should drive the eventual catalog rewrite.

| slug | current_catalog_bucket | artifact posture | current assessment | recommended_bucket | decision_status | reason |
|---|---|---|---|---|---|---|
| `lqd-spy-credit-lead` | `promoted_default` | partial but materially improved | canonical baseline currently unprofitable | `needs_revalidation` | reviewed | Walk-forward lineage was repaired and passed, but the current canonical baseline artifact is economically weak/negative, so the slug is not promotion-clean under current lifecycle semantics. |
| `agg-spy-credit-lead` | `promoted_default` | incomplete | not fully reviewed in this audit pass | `candidate_default` | provisional | Historically grouped with promoted credit-lead names, but no fully reviewed lifecycle-complete evidence chain was confirmed in this pass. Keep visible as a candidate, not as promoted. |
| `spy-overnight-momentum` | `promoted_default` | incomplete | not fully reviewed in this audit pass | `candidate_default` | provisional | Current catalog membership overstates certainty. Until lifecycle-complete artifacts are explicitly revalidated, it should be tracked as a candidate rather than a promoted strategy. |
| `agg-qqq-credit-lead` | `promoted_default` | incomplete | not fully reviewed in this audit pass | `candidate_default` | provisional | No confirmed promotion-clean evidence chain was established in this pass. Candidate status is the conservative classification. |
| `vcit-qqq-credit-lead` | `promoted_default` | incomplete | not fully reviewed in this audit pass | `candidate_default` | provisional | Promoted semantics are not justified by the currently reviewed artifact evidence. Reclassify as candidate until proven otherwise. |
| `lqd-qqq-credit-lead` | `promoted_default` | incomplete | not fully reviewed in this audit pass | `candidate_default` | provisional | This slug remains runtime-known/research-known, but promotion cleanliness was not established in the audit. |
| `emb-spy-credit-lead` | `promoted_default` | incomplete | not fully reviewed in this audit pass | `candidate_default` | provisional | No fully reviewed lifecycle-complete proof was established here; candidate bucket is the conservative home. |
| `hyg-spy-5d-credit-lead` | `promoted_default` | incomplete | not fully reviewed in this audit pass | `candidate_default` | provisional | Remains interesting enough to keep visible, but not justified as promoted on currently reviewed evidence. |
| `agg-efa-credit-lead` | `promoted_default` | incomplete | not fully reviewed in this audit pass | `candidate_default` | provisional | Catalog promotion currently outruns verified evidence. Candidate is the correct interim classification. |
| `hyg-qqq-credit-lead` | `promoted_default` | incomplete | not fully reviewed in this audit pass | `candidate_default` | provisional | No promotion-clean artifact chain was confirmed in this pass. Maintain as candidate only. |
| `soxx-qqq-lead-lag` | `promoted_default` | stronger than peers but still not fully re-promoted in this cleanup | best live candidate among audited promoted names | `candidate_default` | provisional | This is the strongest provisional survivor in the currently promoted roster, but this cleanup is deliberately using strict semantics: without an explicitly revalidated promotion decision, it remains candidate rather than promoted. |

### Phase 2 decision rule

The ledger is intentionally conservative:
- `reviewed` means current audit work supports the decision directly
- `provisional` means the slug has not yet earned promoted status in this cleanup and therefore defaults to a non-promoted bucket
- no slug remains in `promoted_default` unless it has been affirmatively re-approved against the current lifecycle rules

This means the Phase 2 output supports the strict interim target posture:
- `promoted_default = []`
- `candidate_default` contains the visible but not promotion-clean research roster
- `needs_revalidation` contains historically important names with specific reasons they failed current promotion trust

## Version History

| Version | Date | Change |
|---------|------|--------|
| 1.6 | 2026-04-01 | Added `lqd-spy-credit-lead` to the matrix after repairing walk-forward lineage, rerunning WFO successfully, and comparing a new canonical 5-year baseline artifact against the older non-canonical 10-year exploratory baseline; strategy remains candidate because the canonical baseline backtest is unprofitable. |
| 1.5 | 2026-04-01 | Verified the on-disk artifact chain for `eth-btc-ratio-mean-reversion-v5` and updated its matrix row to artifact-backed passed statuses for mandate, hypothesis, data contract, frozen research spec, backtest, robustness, walk-forward, and paper-trading, while keeping runtime/promotion verification conservative. |
| 1.4 | 2026-04-01 | Extended promotion-readiness interpretation to explicitly require exit-stack scenario review and canonical runtime/paper/backtest exit-parity review for profit-taking sleeves. |
| 1.3 | 2026-04-01 | Added `gld-slv-mean-reversion-v4` as an artifact-backed promotion-pipeline candidate after formalized strategy spec, passed registered backtest, passed robustness, passed walk-forward, and completed portfolio-fit review. |
| 1.2 | 2026-03-31 | Added an evidence-backed Track C strategy row for Polymarket NegRisk + combinatorial arbitrage, distinguishing implemented module status from still-pending live scan, validation, and paper-trading gates. |
| 1.1 | 2026-03-31 | Added initial crypto strategy-level coverage for `eth-btc-ratio-mean-reversion-v5`, linking crypto promotion and paper-gate requirements into the canonical status matrix with conservative unverified artifact states. |
| 1.0 | 2026-03-31 | Initial canonical status matrix created to unify artifact state, runtime state, and evidence quality across strategies and sleeves. |
