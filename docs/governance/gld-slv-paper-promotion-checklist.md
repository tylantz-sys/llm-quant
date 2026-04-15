# GLD/SLV Mean Reversion v4 Paper Promotion Checklist

Use this checklist for `gld-slv-mean-reversion-v4` before moving from a research-complete candidate to a paper-passed promotion review candidate.

## Scope

- Strategy slug: `gld-slv-mean-reversion-v4`
- Asset class: commodity
- Strategy type: pairs ratio / mean reversion
- Source-of-truth paper artifact: `data/strategies/gld-slv-mean-reversion-v4/paper-trading.yaml`

## 1) Scheduler / Runtime Health (must pass)

- Verify the scheduler, timer, or batch job responsible for this strategy is active and firing normally.
  - Pass: next trigger exists and no repeated failure loop is visible.
- Review recent service logs.
  - Pass: no repeated `FAILURE`, lock crash loops, or repeated uncaught exceptions.
- Confirm the runtime completed at least one recent cycle end-to-end.
  - Pass: data fetch, signal computation, and persistence all completed.

## 2) Data / Signal Health (must pass)

- Confirm GLD and SLV market data are fresh for the strategy’s execution cadence.
  - Pass: no stale-bar warning or missing input series.
- Confirm ratio, z-score, and entry/exit logic are visible in logs or persisted diagnostics.
  - Pass: indicator computation is happening deterministically.
- Confirm signal generation is visible.
  - Pass: logs or telemetry distinguish between:
    - no setup
    - setup generated
    - vetoed by risk checks
    - executed
- Confirm risk filters execute after signal generation.
  - Pass: runtime clearly shows risk enforcement on the strategy output.

## 3) Paper Gate Metrics (must pass all)

From `data/strategies/gld-slv-mean-reversion-v4/paper-trading.yaml`:

- `days_observed >= 30`
- `closed_trades >= 50`
- `sharpe >= 0.60`
- `max_drawdown <= 0.15`
- `operational_checks_required == true`
- all operational checks healthy

Recommended refresh/update workflow:

- update the strategy-local `paper-trading.yaml`
- confirm the latest run refreshed performance and gate fields
- confirm operational checks were evaluated, not left null

## 4) Governance Artifact Validation (must pass)

Confirm the following artifacts exist and remain consistent:

- `data/strategies/gld-slv-mean-reversion-v4/research-spec.yaml`
  - Pass: `frozen: true`
- `data/strategies/gld-slv-mean-reversion-v4/experiment-registry.jsonl`
  - Pass: baseline experiment is recorded
- `data/strategies/gld-slv-mean-reversion-v4/robustness.yaml`
  - Pass: verdict is `PASS`
- `data/strategies/gld-slv-mean-reversion-v4/walk-forward.yaml`
  - Pass: `passed: true`
- `docs/governance/gld-slv-mean-reversion-v4-promotion-scorecard.md`
  - Pass: current scorecard and status are documented
- `docs/governance/strategy-artifact-status-matrix.md`
  - Pass: row is consistent with the latest artifacts

## 5) Promotion Review Handoff

Only after all paper gates pass:

1. attach the current `paper-trading.yaml`
2. attach the current promotion scorecard
3. confirm any runtime/telemetry notes needed for reviewers
4. record approver, review date, and decision
5. update the strategy artifact status matrix to reflect the paper result

## 6) Automatic Fail Conditions (do not promote)

- Repeated stale-bar or missing-data warnings.
- Repeated runtime lock, DB lock, or scheduler crash loops.
- Paper Sharpe drops below gate.
- Paper drawdown breaches gate.
- Operational checks are incomplete, null, or unhealthy.
- Missing portfolio snapshot persistence or risk-check enforcement evidence.