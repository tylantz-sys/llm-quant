# GLD/SLV Mean Reversion v4 Promotion Scorecard

## Strategy Identity

- Strategy slug: `gld-slv-mean-reversion-v4`
- Asset class: commodity
- Strategy type: `pairs_ratio`
- Baseline experiment id: `bba9fb52`
- Review date: `2026-04-01`
- Current lifecycle interpretation: Stage 2 passed, Stage 3 pending

## Stage 1 Hard Veto Snapshot

This repository currently contains clear evidence for frozen specification, registered baseline review, robustness, and walk-forward validation. Full Stage 1 statistical hard-veto coverage from `model-promotion-policy.md` is only partially represented in current on-disk artifacts.

- DSR: `0.9904` — PASS versus `>= 0.95`
- PBO: not recorded in current artifact set — NOT YET DOCUMENTED
- SPA p-value: not recorded in current artifact set — NOT YET DOCUMENTED
- MinTRL / out-of-sample evidence: walk-forward artifact exists with 11 folds — PASS
- Interim interpretation: no documented failing hard veto is visible, but a complete final promotion packet should explicitly record any unavailable Stage 1 statistics

## Stage 2 Weighted Scorecard

| Dimension | Score | Weight | Weighted Contribution | Justification |
|---|---:|---:|---:|---|
| Risk-Adjusted Returns | 92 | 0.25 | 23.00 | Baseline Sharpe of `1.1972` is near the high band and DSR is strong at `0.9904`. |
| Drawdown Characteristics | 94 | 0.20 | 18.80 | Baseline max drawdown `0.096` is comfortably inside policy guidance. Walk-forward max OOS drawdown `0.049939` remains contained. |
| Trade Statistics | 78 | 0.20 | 15.60 | Registered performance is strong enough to proceed, but trade-level promotion evidence is less fully documented than returns/drawdown evidence in the current packet. |
| Robustness | 90 | 0.20 | 18.00 | CPCV and perturbation evidence are strong overall; `80%` perturbation stability passes the current gate despite one unstable variant at `bb_std=2.5`. |
| Operational | 86 | 0.15 | 12.90 | Strategy is relatively simple, based on two liquid ETFs and standard daily execution logic, but runtime/paper evidence is still pending. |

## Composite Score

Composite formula from `model-promotion-policy.md`:

`Composite = (Risk-Adjusted * 0.25) + (Drawdown * 0.20) + (Trade Stats * 0.20) + (Robustness * 0.20) + (Operational * 0.15)`

- Composite score: `88.30 / 100`
- Policy threshold to proceed: `>= 85`
- Decision: PASS Stage 2, promote to Stage 3 paper trading

## Evidence Reviewed

- `data/strategies/gld-slv-mean-reversion-v4/research-spec.yaml`
- `data/strategies/gld-slv-mean-reversion-v4/experiment-registry.jsonl`
- `data/strategies/gld-slv-mean-reversion-v4/robustness.yaml`
- `data/strategies/gld-slv-mean-reversion-v4/walk-forward.yaml`
- `data/strategies/gld-slv-mean-reversion-v4/paper-trading.yaml`
- `docs/governance/strategy-artifact-status-matrix.md`

## Stage 3 Readiness Gaps

The following items remain open before the strategy can be considered promotion-ready:

- paper-trading history has not yet met minimum days/trades thresholds
- runtime verification is not yet documented as complete
- operational checks in `paper-trading.yaml` are still placeholders pending live paper operation
- kill-switch wiring and telemetry coverage are not yet formally verified for promotion purposes
- complete final Stage 1 hard-veto packet should explicitly record any missing PBO / SPA evidence or approved treatment

## Promotion Decision Status

- Current status: `approved for Stage 3 paper trading`
- Not yet promotion-ready for canary or live allocation
- Next required artifacts:
  - populated `data/strategies/gld-slv-mean-reversion-v4/paper-trading.yaml`
  - completed paper-promotion checklist
  - updated matrix row with paper-trading result
  - formal promotion decision after paper evidence review

## Approval

- Reviewer: `TBD`
- Decision date: `2026-04-01`
- Decision note: Strategy has sufficient frozen-research, robustness, and walk-forward evidence to begin governed paper trading, but not to bypass Stage 3.