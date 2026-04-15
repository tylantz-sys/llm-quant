# `btc-eem-lead-lag` Test Note

Date: 2026-04-14

## Governing documents reviewed
- `docs/governance/strategy-thorough-testing-plan.md`
- `docs/governance/validation-requirements-matrix.md`

## Test path used
- Script: `scripts/run_h8_robustness.py`
- Command:
  ```bash
  python3 scripts/run_h8_robustness.py
  ```

## Observed metrics
- Base Sharpe: `-0.9323`
- Max drawdown: `0.3771`
- CPCV mean OOS Sharpe: `-0.8965`
- CPCV std: `0.6295`
- DSR: `0.2639`

## Additional runtime observations
The run emitted repeated messages of the form:

```text
No price provided for EEM – keeping stale price ...
```

This indicates the test is relying on stale carried-forward `EEM` prices across calendar gaps relative to `BTC-USD`. That may be expected for a cross-asset daily setup, but it should be treated as an important interpretation note for this strategy because the follower asset does not trade on the same calendar as the leader.

## Governance interpretation
Against `docs/governance/validation-requirements-matrix.md`:

- `DSR >= 0.95` → **FAIL**
- `CPCV mean OOS Sharpe > 0` → **FAIL**
- `CPCV median OOS Sharpe > 0` → not explicitly printed by this script, but the strongly negative CPCV mean does not support advancement
- Track drawdown threshold:
  - Track A `< 15%` → **FAIL**
  - Track B `< 30%` → **FAIL**
- Base Sharpe is negative → **FAIL**

## Verdict
`btc-eem-lead-lag` fails the initial robustness screen and should not advance. Under the documented testing ladder, it is currently a **stop / redesign candidate** rather than a promotion-track candidate.

## Recommended next action
Proceed to the next crypto strategy candidate with a governed robustness or baseline test path, prioritizing:
- `eth-btc-ratio-mean-reversion-v5`
