# SOXX/QQQ Lead-Lag Governance Decision Note

**Date:** 2026-04-01  
**Strategy:** `soxx-qqq-lead-lag`  
**Frozen spec hash:** `d8e92e5a1be0d6ff003c48716b46939383a138f9206b94f7331600d64c7f6681`  
**Canonical experiment:** `ee2f59e9`  
**Lifecycle posture:** research / paper candidate  
**Runtime posture:** Alpaca paper-only  
**Promotion posture:** blocked on current frozen spec

---

## Decision

`soxx-qqq-lead-lag` remains a research and paper-trading candidate only. It is not promotion-ready on the current frozen specification.

This decision reflects a repaired evidence chain and a stricter read of the current lifecycle gates:

- repaired lineage improved the canonical evidence base and aligned the artifact chain to the frozen spec
- walk-forward validation passed under the repaired lineage
- refreshed robustness did not pass the full gate set and is sufficient to block promotion
- any continued live monitoring remains limited to Alpaca paper trading
- any attempt to improve or replace the strategy must be done through a new strategy spec/version rather than by reinterpreting this frozen artifact set

---

## Evidence basis

### 1. Canonical lineage was repaired
The current canonical artifact chain is tied to frozen spec hash `d8e92e5a1be0d6ff003c48716b46939383a138f9206b94f7331600d64c7f6681` and canonical experiment `ee2f59e9`.

This improves governance quality because the primary validation artifacts now reference the corrected frozen-spec identity and execution assumptions instead of relying on stale or ambiguous lineage.

### 2. Walk-forward passed
`data/strategies/soxx-qqq-lead-lag/walk-forward.yaml` records:

- `passed: true`
- 11 folds
- mean OOS Sharpe `1.139842`
- median OOS Sharpe `0.748963`
- max OOS drawdown `0.037534`

This supports keeping the strategy in the research/paper-candidate set rather than retiring it.

### 3. Robustness failed and blocks promotion
`data/strategies/soxx-qqq-lead-lag/robustness.yaml` records:

- `overall_result: HOLD`
- `overall_passed: false`
- DSR gate `FAIL` with `dsr_value: 0.5675`
- CPCV gate `PASS`
- perturbation gate `FAIL` with 2 of 5 variants stable
- max drawdown check `PASS`

Under `docs/governance/quant-lifecycle.md`, robustness must pass before a strategy can progress cleanly into paper trading as a promotion candidate and ultimately into promotion review. This artifact does not satisfy that requirement. The combination of failed DSR and failed perturbation stability is sufficient to block promotion on the current frozen spec.

---

## Governance interpretation

The repaired lineage is a governance improvement, not a promotion approval.

The current governed reading is:

- evidence quality improved because the canonical experiment and downstream artifacts are now aligned to the frozen spec
- the strategy still has some positive validation signal because walk-forward passed
- the refreshed robustness artifact is the controlling blocker for promotion readiness
- the appropriate status is therefore **research / paper candidate**, not promoted and not promotion-clean

This is consistent with the repo's conservative artifact-first lifecycle policy.

---

## Operational constraint

Any ongoing runtime observation for this strategy must remain **Alpaca paper-only**.

This note does not authorize canary or promoted deployment. A passed walk-forward artifact does not override a failed robustness artifact, and paper monitoring should be treated as observational validation only, not as evidence that the current frozen spec is promotable.

---

## Successor rule

If the team wants to pursue a successor to `soxx-qqq-lead-lag`, that work must be recorded as a **new spec/version** with its own frozen hash and validation chain.

Do not amend the interpretation of this frozen spec to manufacture promotion readiness. Under the lifecycle rules, a robustness failure returns the strategy to redesign/re-specification rather than allowing informal promotion through narrative judgment.

---

## Resulting status

| Field | Status |
|---|---|
| canonical lineage | repaired / improved |
| walk-forward | passed |
| robustness | failed (`HOLD`) |
| promotion eligibility | blocked |
| allowed runtime posture | Alpaca paper-only |
| successor path | new spec/version required |

---

## Governing references

- `docs/governance/quant-lifecycle.md`
- `docs/governance/strategy-artifact-status-matrix.md`
- `data/strategies/soxx-qqq-lead-lag/walk-forward.yaml`
- `data/strategies/soxx-qqq-lead-lag/robustness.yaml`