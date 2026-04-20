# Validation Requirements Matrix

This document is the canonical source of truth for validation, promotion, runtime-readiness, and observability requirements across the `llm-quant` strategy lifecycle.

It consolidates thresholds and process requirements that are otherwise distributed across lifecycle, promotion, runtime, crypto, and research governance documents. If another document summarizes or paraphrases a requirement, this matrix is the reference point that determines whether a requirement is universal, track-specific, crypto-specific, operational, or informational.

This document does not lower any bar. Its purpose is to make the bar explicit, comparable, and auditable.

For current per-sleeve and per-strategy status, use `docs/governance/strategy-artifact-status-matrix.md` as the canonical status ledger. This document defines the requirements; the strategy artifact/status matrix records which sleeves and strategies have actually met them.

---

## Purpose

This matrix answers six questions:

1. **What is required for all strategies, regardless of track?**
2. **What differs by track or sleeve?**
3. **What is required before paper trading?**
4. **What is required before canary or full promotion?**
5. **What runtime and observability checks are mandatory before trusting a live deployment?**
6. **What remains recommended but not yet universally enforced?**

It is the primary reconciliation layer across:
- `docs/governance/quant-lifecycle.md`
- `docs/governance/model-promotion-policy.md`
- `docs/governance/control-matrix.md`
- `docs/governance/runtime-truth-table.md`
- `docs/governance/strategy-artifact-status-matrix.md`
- `docs/governance/crypto-paper-promotion-checklist.md`
- `docs/governance/crypto-strategy-promotion.md`
- `docs/research/institutional-quant-guide.md`
- `docs/research/implementation-gaps.md`

---

## Requirement Classes

Each requirement is tagged with one of the following classes:

- `universal` — applies to all strategies unless explicitly waived
- `track-specific` — differs by research track or sleeve
- `crypto-specific` — required for crypto paper/promotion flows
- `runtime` — required to trust live operation
- `recommended` — strongly advised and supported by doctrine, but not yet uniformly enforced everywhere
- `informational` — useful for interpretation but not itself a gate

---

## Validation Pipeline Overview

A strategy is expected to progress through these validation layers in order:

1. **Lifecycle prerequisites**
2. **Frozen-spec backtesting**
3. **Robustness gate**
4. **Walk-forward / regime validation**
5. **Paper trading validation**
6. **Promotion gate**
7. **Canary validation**
8. **Runtime-readiness and observability validation**
9. **Ongoing evaluation and degradation monitoring**

A strategy is not promotable simply because it passes backtests. It must pass the full validation stack appropriate to its track and asset class.

---

## Section 1 — Universal Lifecycle Requirements

| Requirement | Class | Minimum Standard | Evidence | Gate Stage | Notes |
|---|---|---|---|---|---|
| Mandate exists | universal | `mandate.yaml` present and current | artifact | lifecycle | Required before hypothesis |
| Hypothesis exists | universal | `hypothesis.yaml` present and pre-results | artifact | lifecycle | Must be written before interpreting results |
| Data contract exists | universal | `data-contract.yaml` present and current | artifact | lifecycle | Must specify quality and freshness constraints |
| Research spec exists | universal | `research-spec.yaml` present | artifact | lifecycle | Required before backtest |
| Research spec frozen | universal | `frozen: true` and hash recorded | artifact | pre-backtest | Prevents simultaneous design/evaluation |
| Append-only experiment recording | universal | Every backtest recorded in experiment registry | artifact | backtest | No selective reporting |
| Minimum number of experiments before robustness | universal | `>= 2` completed experiments | artifact | robustness | From lifecycle reference |

---

## Section 2 — Universal Robustness Requirements

These are the baseline integrity and survivability requirements.

| Requirement | Class | Minimum Standard | Evidence | Gate Stage | Notes |
|---|---|---|---|---|---|
| DSR | universal | `>= 0.95` | robustness artifact / experiment metrics | robustness | Integrity gate |
| PBO | universal | `<= 0.10` | robustness artifact | robustness | Integrity gate |
| CPCV mean OOS Sharpe | universal | `> 0` | robustness artifact | robustness | Integrity gate |
| CPCV median OOS Sharpe | universal | `> 0` | robustness artifact | robustness | Required |
| 2x cost survival | universal | Sharpe remains `> 0` at 2x costs | robustness artifact | robustness | Fragility check |
| Parameter stability | universal | `> 50%` stable across tested perturbations | robustness artifact | robustness | Required |
| Fill delay realism | universal | Minimum `fill_delay = 1` bar unless diagnostic-only | research spec / backtest config | backtest + robustness | Prevents look-ahead execution assumptions |

---

## Section 3 — Track-Specific Robustness Thresholds

These thresholds differ by track where explicitly documented.

| Requirement | Track A | Track B | Evidence | Gate Stage | Notes |
|---|---|---|---|---|---|
| Sharpe | `>= 0.80` | `>= 1.00` | robustness artifact | robustness | Track B has higher return-quality bar |
| Max drawdown | `< 15%` | `< 30%` | robustness artifact | robustness | Track B tolerates more drawdown |
| DSR | `>= 0.95` | `>= 0.95` | robustness artifact | robustness | Same integrity bar |
| PBO | `<= 0.10` | `<= 0.10` | robustness artifact | robustness | Same integrity bar |
| CPCV mean/median OOS Sharpe | `> 0` | `> 0` | robustness artifact | robustness | Same integrity bar |

### Interpretation
Track-specific flexibility should not be interpreted as freedom to skip universal integrity gates. It only affects risk/return envelope thresholds where explicitly documented.

---

## Section 4 — Universal Promotion Requirements

These are the minimum gates before a strategy can be considered for promotion.

| Requirement | Class | Minimum Standard | Evidence | Gate Stage | Notes |
|---|---|---|---|---|---|
| SPA significance | universal | `p <= 0.05` | promotion review artifact | promotion hard veto | Required by promotion policy summary |
| Minimum track record length (MinTRL) | universal | `>= 1` OOS period | promotion review artifact | promotion hard veto | Required; broader implementation maturity may still vary |
| Paper duration | universal | `>= 30 days` | paper-trading artifact | paper gate | Minimum live paper sample |
| Paper trade count | universal | `>= 50 trades` | paper-trading artifact | paper gate | Minimum behavioral sample |
| Paper Sharpe | universal | `>= 0.60` | paper-trading artifact | paper gate | Minimum paper performance |
| Operational checklist complete | universal | all required systems tested | paper-trading artifact / runbook | paper gate | Includes runtime controls and incidents review |
| Composite scorecard | universal | `>= 85` | promotion review artifact | promotion scorecard | Weighted decision stage |

---

## Section 5 — Canary and Deployment Requirements

| Requirement | Class | Minimum Standard | Evidence | Gate Stage | Notes |
|---|---|---|---|---|---|
| Canary allocation | universal | `10%` allocation | deployment record | canary | Controlled exposure |
| Canary duration | universal | `>= 14 days` | deployment record | canary | Minimum observation period |
| Canary drawdown | universal | `< 10%` | canary evaluation artifact | canary | Failure returns to earlier review stage |
| Canary Sharpe | universal | `>= 0.50` | canary evaluation artifact | canary | Summary threshold from policy synthesis |
| Kill switch events | universal | none triggered materially during canary | runtime logs / review | canary | Operational sanity check |
| Baseline metrics recorded | universal | yes | deployment checklist | deployment | Required before full deployment |
| Changelog/promotion record updated | universal | yes | DB / governance record | deployment | Formal audit trail |

---

## Section 6 — Crypto-Specific Requirements

These are explicitly emphasized in crypto promotion and runtime docs and should be treated as mandatory for crypto sleeves.

| Requirement | Class | Minimum Standard | Evidence | Gate Stage | Notes |
|---|---|---|---|---|---|
| Walk-forward artifact | crypto-specific | passed and recorded | artifact | pre-promotion | More explicit in crypto docs than general docs |
| Robustness artifact | crypto-specific | passed and recorded | artifact | pre-promotion | Required |
| Paper-trading artifact | crypto-specific | passed and recorded | artifact | pre-promotion | Required |
| Scheduler verification | crypto-specific | cron/timer cadence verified | runtime-backed | runtime readiness | Important for intraday crypto |
| Intraday data freshness verification | crypto-specific | fresh bars confirmed | runtime-backed | runtime readiness | Critical for 5-minute execution loops |
| Runtime decision verification | crypto-specific | fresh decisions confirmed | runtime-backed | runtime readiness | Confirms pipeline is alive |
| Runtime no-signal interpretation | crypto-specific | no-trade states documented and understood | runbook / telemetry | runtime readiness | Needed for repeated `risk_off / 0 signals` states |

---

## Section 7 — Runtime Readiness Requirements

These determine whether a deployment can be trusted operationally, independent of research quality.

| Requirement | Class | Minimum Standard | Evidence | Gate Stage | Notes |
|---|---|---|---|---|---|
| Config identity known | runtime | active pod/config file identified | config + runtime verification | runtime readiness | Must know what is actually running |
| Data freshness verified | runtime | latest required market data within freshness threshold | runtime-backed | runtime readiness | Distinguish stale feed from no-trade regime |
| Decision freshness verified | runtime | recent decisions created on schedule | runtime-backed | runtime readiness | Confirms model execution path |
| Signal state observable | runtime | can distinguish no data / no decision / zero signal / vetoed signal | telemetry / dashboard / logs | runtime readiness | Prevents silent ambiguity |
| Risk veto observability | runtime | risk-off / kill switch reasons visible | telemetry / logs | runtime readiness | Must explain suppression |
| Execution path observability | runtime | can see routed, rejected, filled, canceled orders | telemetry / logs | runtime readiness | Required for operator trust |
| Broker-sourced locate enforcement | runtime | when `require_locate=true`, short approval uses broker asset metadata (`shortable` / `easy_to_borrow` via Alpaca `get_asset`) rather than LLM-only metadata, and unknown locate state rejects the trade (fail-closed) | runtime-backed / risk logs | runtime readiness | Prevents ambiguous short approval paths, prompt-dependent locate drift, and silent fail-open shorting |
| Direct short rollout observability | runtime | `short_exposure_ratio` telemetry and `short_rollout` surveillance check active before enabling discretionary shorts | runtime-backed / surveillance logs | runtime readiness | Prevents silent short-cap drift during rollout |
| Kill switches wired | runtime | confirmed active in runtime path | runtime-backed / code path review | deployment | Required before trust |
| Incident logging | runtime | incidents recorded with timestamps and reasons | paper/deployment records | paper + deployment | Required for operations review |
| Telemetry completeness | runtime | at least partial lifecycle-to-runtime coverage | docs + logs + DB | deployment | Should trend toward complete |

---

## Section 8 — Recommended But Not Yet Uniformly Enforced Requirements

These are strongly supported by the research/governance review and should be elevated toward universal enforcement.

| Requirement | Class | Minimum Standard | Evidence | Why it matters |
|---|---|---|---|---|
| Universal walk-forward requirement across all tracks | recommended | every promotable strategy has passed walk-forward evidence | artifact | Currently emphasized more clearly in crypto than everywhere else |
| Regime-split validation | recommended | bull / bear / sideways / stress regime review | research artifact | Reduces hidden regime dependence |
| Generalization ratio monitoring | recommended | live-vs-backtest degradation tracked | telemetry + evaluation | Detects paper/live decay |
| Signal density monitoring | recommended | expected vs actual signals tracked by sleeve | runtime telemetry | Critical for no-trade diagnosis |
| Portfolio-level correlation gate | recommended | new strategies reviewed for incremental diversification | research/promotion review | Avoids redundant alpha stacking |
| Marginal Sharpe contribution gate | recommended | additions must improve portfolio quality | research/promotion review | Moves system toward portfolio-aware promotion |
| Capacity/crowding assessment | recommended | size/liquidity constraints tested | research artifact | Especially relevant before live capital expansion |
| Exit-stack scenario testing | recommended | TP/OCO/trailing/EOD behavior tested under multiple paths | test suite / backtest artifact | Directly affects realized profitability |
| Canonical exit parity review | recommended | runtime, paper, and backtest reviewed against one canonical exit-policy vocabulary | governance review + test suite | Prevents broker-path wording from hiding policy drift |
| Overlay veto-rate measurement | recommended | quantify how often overlay suppresses signals and with what value add | telemetry / analysis | Needed before loosening or redesigning overlay behavior |

---

## Section 9 — Explicit Anti-Shortcuts

The following are not valid substitutes for passing validation:

| Invalid Shortcut | Why it is invalid |
|---|---|
| “The README says it passed” | Narrative summaries are not artifact-backed proof |
| “It has high CAGR” | High return alone does not address overfitting, fragility, or live viability |
| “It ran in paper once” | Promotion requires minimum paper duration, trade count, and ops verification |
| “The runtime is active” | Runtime enabled does not imply governance complete |
| “Shorts are tiny, so we can skip dedicated monitoring” | Small short sleeves can still breach locate/margin/exposure controls if not explicitly observed |
| “No trades means safety” | It could also mean stale data, suppression, or broken signal generation |
| “Crypto is special, so we can skip general rigor” | Crypto docs actually require more operational proof, not less |
| “We can loosen thresholds later if it stays quiet” | Quiet runtime should trigger diagnosis and validation, not blind de-risking of standards |

---

## Section 10 — Minimum Promotion Checklist

A strategy should only be marked promotion-ready if every item below is explicitly satisfied:

- [ ] Mandate current
- [ ] Hypothesis current and pre-results
- [ ] Data contract current
- [ ] Research spec frozen
- [ ] Backtests recorded in append-only registry
- [ ] DSR passed
- [ ] PBO passed
- [ ] CPCV mean OOS Sharpe positive
- [ ] CPCV median OOS Sharpe positive
- [ ] 2x cost survival passed
- [ ] Parameter stability passed
- [ ] Walk-forward passed
- [ ] SPA significance passed
- [ ] MinTRL satisfied
- [ ] Paper period >= 30 days
- [ ] Paper trades >= 50
- [ ] Paper Sharpe >= 0.60
- [ ] Operational systems tested
- [ ] Exit-stack scenario testing reviewed
- [ ] Canonical exit parity reviewed across runtime/paper/backtest
- [ ] Canary passed
- [ ] Kill switches active
- [ ] Baseline metrics recorded
- [ ] Telemetry sufficient for runtime trust
- [ ] Promotion record written

If any item remains unknown, the strategy is not promotion-ready.

---

## Section 11 — Relationship to the Strategy Status Ledger

`docs/governance/strategy-artifact-status-matrix.md` is the operator-facing status ledger that should be used with this document.

Use the two documents together as follows:

- Use **this validation requirements matrix** to determine what the bar is
- Use **the strategy artifact/status matrix** to determine whether a specific sleeve or strategy has actually cleared that bar
- When a requirement here changes, update corresponding interpretations or rows in the strategy artifact/status matrix
- When runtime state changes, update the strategy artifact/status matrix first, then review whether any requirement interpretation in this document also needs revision

This separation is intentional:
- `validation-requirements-matrix.md` defines the **requirements**
- `strategy-artifact-status-matrix.md` records the **current verified status**

---

## Section 12 — Relationship to the Master Improvement Plan

This matrix directly supports the broader program by making three critical improvements explicit:

1. **Governance truth unification**  
   Removes ambiguity around what is universal, track-specific, or merely aspirational.

2. **Validation hardening**  
   Elevates walk-forward, regime testing, and runtime observability into a more operationally coherent framework.

3. **Runtime trust**  
   Makes it harder to confuse “no trades” with “healthy strategy inactivity” unless the supporting telemetry exists.

---

## Version History

| Version | Date | Change |
|---------|------|--------|
| 1.2 | 2026-04-01 | Added canonical exit parity review language to the recommended requirements and minimum promotion checklist so profit-taking validation is explicitly compared across runtime, paper, and backtest surfaces. |
| 1.3 | 2026-04-19 | Added direct short rollout runtime requirement (`short_rollout` surveillance + `short_exposure_ratio` telemetry) and explicit anti-shortcut guidance for short capability activation. |
| 1.1 | 2026-03-31 | Cross-linked the strategy artifact/status matrix as the canonical per-strategy and per-sleeve status ledger, clarifying that this document defines requirements while the status matrix records verified state. |
| 1.0 | 2026-03-31 | Initial canonical validation matrix created to unify lifecycle, robustness, promotion, crypto, and runtime-readiness requirements. |
