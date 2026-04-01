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
| ETH/BTC ratio mean reversion v5 | `eth-btc-ratio-mean-reversion-v5` | crypto | crypto | paper / promotion candidate | candidate | artifact-backed | 2026-03-31 | `crypto-paper-promotion-checklist.md` and `crypto-strategy-promotion.md` define explicit artifact gates for this slug: frozen spec, passed backtest, passed walk-forward, passed robustness, and a paper-trading artifact meeting 30-day / 50-trade / Sharpe / drawdown gates before promotion. Current session verified the governance requirements and candidate-pod workflow, but not the underlying artifact files or live candidate-pod status, so strategy remains conservatively marked as candidate rather than paper/promotion-passed. |
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
| ETH/BTC ratio mean reversion v5 | `eth-btc-ratio-mean-reversion-v5` | crypto | crypto | paper / promotion candidate | unknown | unknown | unknown | unknown | unknown | unknown | unknown | unknown | unknown | unknown | unknown | candidate | unknown | partial | not verified | artifact-backed | 2026-03-31 | `crypto-strategy-promotion.md` defines required gates for this slug: frozen research spec, passed backtest (`sharpe_ratio > 0`, `dsr >= 0.95`, `max_drawdown <= 0.25`), passed walk-forward artifact, passed robustness artifact, and a passing paper-trading artifact. `crypto-paper-promotion-checklist.md` further specifies the candidate pod `crypto-ethbtc-paper`, scheduler health checks, runtime health checks, paper metrics (`days_observed >= 30`, `closed_trades >= 50`, `sharpe >= 0.60`, `max_drawdown <= 0.25`), and strict validator readiness before moving from `candidate_crypto` to `promoted_crypto`. Those requirements are documented, but the underlying per-strategy files and live candidate-pod evidence were not verified in this session, so artifact fields remain conservative/unknown and runtime stays candidate. |
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

If any field is `unknown`, `missing`, `partial`, `failed`, or `narrative-only`, the strategy is not promotion-ready.

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

## Version History

| Version | Date | Change |
|---------|------|--------|
| 1.2 | 2026-03-31 | Added an evidence-backed Track C strategy row for Polymarket NegRisk + combinatorial arbitrage, distinguishing implemented module status from still-pending live scan, validation, and paper-trading gates. |
| 1.1 | 2026-03-31 | Added initial crypto strategy-level coverage for `eth-btc-ratio-mean-reversion-v5`, linking crypto promotion and paper-gate requirements into the canonical status matrix with conservative unverified artifact states. |
| 1.0 | 2026-03-31 | Initial canonical status matrix created to unify artifact state, runtime state, and evidence quality across strategies and sleeves. |
