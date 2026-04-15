# Crypto Strategy Promotion Standard

This document defines the strategy-level promotion artifact expectations for crypto candidates. It complements `model-promotion-policy.md` and the pod/operator procedure in `crypto-paper-promotion-checklist.md`.

Use this template when a crypto strategy is moving from research-complete to paper-trading review and eventual promotion between `candidate_crypto` and `promoted_crypto`.

---

## Required Strategy Artifacts

A crypto strategy promotion candidate should have the following strategy-local artifacts under `data/strategies/<slug>/`:

- `mandate.yaml`
- `hypothesis.yaml`
- `data-contract.yaml`
- `research-spec.yaml`
- `experiment-registry.jsonl`
- `robustness.yaml`
- `walk-forward.yaml`
- `paper-trading.yaml`

If any required artifact is missing, the strategy is not promotion-ready.

---

## Stage 0: Research Freeze

Before promotion review begins, the research specification must be frozen.

### Required conditions

- `research-spec.yaml` exists
- strategy slug matches the reviewed strategy
- `frozen: true`
- freeze timestamp is recorded
- baseline experiment reference is identifiable from registry/history

### Pass outcome

The strategy becomes eligible for baseline validation review.

---

## Stage 1: Baseline Backtest Gate

The registered backtest must demonstrate a positive, risk-aware baseline.

### Minimum expectations

- Sharpe ratio > 0
- DSR >= 0.95 when available
- max drawdown <= 0.25
- experiment is recorded in `experiment-registry.jsonl`

### Evidence

- experiment registry entry
- baseline experiment artifact(s)
- any supporting review notes used in governance

---

## Stage 2: Robustness and Walk-Forward Gate

The strategy must show evidence that the baseline is not a fragile artifact.

### Required conditions

- `robustness.yaml` exists and indicates a passing verdict
- `walk-forward.yaml` exists and indicates a passing result
- no unresolved evidence gap invalidates the reviewed baseline

### Review focus

- CPCV / fold-level out-of-sample behavior where available
- parameter perturbation stability
- drawdown containment
- whether any passing verdict depends on narrow parameter choices

---

## Stage 3: Paper-Trading Gate

Paper trading is the first operational gate.

### Source of truth

- `data/strategies/<slug>/paper-trading.yaml`

### Minimum gate

- `days_observed >= 30`
- `closed_trades >= 50`
- `sharpe >= 0.60`
- `max_drawdown <= 0.25`
- `operational_checks_required == true`
- operational checks are healthy

### Expected operational checks

- scheduler/timer health
- decision logging
- order flow path healthy
- no repeated DB lock failures
- data freshness validated in runtime

---

## Stage 4: Promotion Handoff

After the strategy-level paper gate passes:

1. validate candidate set readiness
2. review pod/runtime health
3. move the slug from `candidate_crypto` to `promoted_crypto`
4. re-run strict validator on the promoted set
5. restart/reload runtime as required
6. keep rollback path available briefly after promotion

---

## Required Governance Record

A promotion review packet should explicitly record:

- strategy slug
- frozen spec status
- baseline experiment id
- backtest pass/fail summary
- robustness pass/fail summary
- walk-forward pass/fail summary
- paper-trading gate pass/fail summary
- current runtime set membership
- approval decision and date

---

## Example Interpretation for `eth-btc-ratio-mean-reversion-v5`

For `eth-btc-ratio-mean-reversion-v5`, a complete review should confirm:

- frozen research spec exists
- passed backtest baseline is recorded
- passed robustness artifact exists
- passed walk-forward artifact exists
- `paper-trading.yaml` meets 30-day / 50-trade / Sharpe / drawdown gates
- `crypto-paper-promotion-checklist.md` operator checks are all green

Only then should it move from `candidate_crypto` to `promoted_crypto`.

---

## Version History

| Version | Date | Change |
|---------|------|--------|
| 1.0 | 2026-04-01 | Created to document the strategy-level crypto promotion artifact pattern referenced by governance status notes. |