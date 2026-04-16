# spy-regime-starter-v1 promotion checklist

## Purpose

This checklist defines the explicit promotion gates for `spy-regime-starter-v1` after the frozen strategy specification was created. It is intended to prevent an invalid jump from a single encouraging backtest to shadow/paper or live deployment.

This checklist is subordinate to and should be reviewed together with:

- `data/strategies/spy-regime-starter-v1/research-spec.yaml`
- `data/strategies/spy-regime-starter-v1/mandate.yaml`
- `data/strategies/spy-regime-starter-v1/hypothesis.yaml`
- `data/strategies/spy-regime-starter-v1/data-contract.yaml`
- `artifacts/spy-regime-starter-v1-robustness-matrix.md`
- `artifacts/spy-regime-starter-v1-walk-forward-validation-checklist.md`

## Promotion states

The strategy may exist in one of the following states:

1. `research_validation`
2. `shadow_paper`
3. `not_eligible_for_promotion`

This checklist does not authorize live trading.

## Non-authoritative review policy

Claude or any other LLM may be used as a review aid for:

- red-team critique
- overfitting suspicion
- robustness ideas
- documentation review

LLM review is optional and non-authoritative. It must not be used as a gate override. Promotion decisions must be based on:

- documented spec
- deterministic tests
- walk-forward evidence
- paper-trading evidence

## Gate 0: baseline correctness prerequisites

All of the following must be true before promotion evidence is even considered:

- [ ] Frozen spec exists at `data/strategies/spy-regime-starter-v1/research-spec.yaml`
- [ ] Provenance and symbol resolution include both `SPY` and `VIX`
- [ ] No known unresolved data-alignment issue remains for `VIX`
- [ ] Signal timing remains close-based with next-open execution
- [ ] Missing-VIX behavior remains: block new entries/adds, allow SPY-side risk exits
- [ ] Deterministic tests for documented entry/add/exit logic pass

Blocking note:

- If baseline correctness is unresolved, the strategy remains in `research_validation` regardless of headline performance.

## Gate 1: research validation -> shadow paper

Do **not** move to `shadow_paper` until every item below is satisfied.

### A. Baseline walk-forward acceptance

- [ ] Baseline walk-forward is regenerated from the frozen spec
- [ ] Baseline artifact provenance is recorded and auditable
- [ ] `Sharpe >= 0.80`
- [ ] `MaxDD < 0.15`
- [ ] `DSR >= 0.95`
- [ ] Walk-forward mean OOS Sharpe > 0
- [ ] Walk-forward median OOS Sharpe > 0

### B. Robustness matrix completion

- [ ] Baseline reproducibility lane passes
- [ ] Alternate walk-forward window lane is acceptable
- [ ] Shifted fold-boundary lane is acceptable
- [ ] Cost-stress lane is acceptable
- [ ] `2x` cost survival is demonstrated
- [ ] Parameter perturbation lane is acceptable
- [ ] `parameter stability > 50%` is demonstrated
- [ ] Subperiod/regime lane does not show obvious dependence on one narrow historical pocket

### C. Deterministic implementation coverage

- [ ] Tests confirm starter entry requires all documented conditions
- [ ] Tests confirm missing `VIX` blocks new entries
- [ ] Tests confirm exit behavior for RSI, MACD, VIX, and ATR stop conditions
- [ ] Tests confirm add behavior only occurs under documented constraints
- [ ] Tests confirm insufficient indicator data yields no signal
- [ ] Cooldown behavior after exit is covered or explicitly reviewed
- [ ] Add-count cap behavior is covered or explicitly reviewed
- [ ] Missing-VIX exit-only behavior is covered or explicitly reviewed

### D. Research conclusion

- [ ] The strategy is not obviously dependent on one narrow subperiod
- [ ] The strategy does not fail under modestly worse cost assumptions
- [ ] The strategy does not appear knife-edge under mild nearby parameter perturbations
- [ ] Any remaining weaknesses are documented and judged non-fatal for paper observation

### Gate 1 decision rule

Promotion from `research_validation` to `shadow_paper` is allowed only if every section above is satisfied.

If any item fails:

- outcome = `research_validation`

## Required shadow/paper operating model

When the strategy enters `shadow_paper`, it must run as a shadow validation only. It must not place live capital at risk.

Each paper event should record at minimum:

- `strategy_slug`
- `signal_timestamp`
- `decision_bar_date`
- `intended_action`
- `target_weight_before`
- `target_weight_after`
- `expected_fill_basis`
- `expected_reference_price`
- `actual_paper_fill_timestamp`
- `actual_paper_fill_price`
- `realized_slippage_bps`
- `SPY` close used for signal generation
- `VIX` input presence flag
- `sma_20`
- `sma_50`
- `rsi_14`
- `macd`
- `atr_14_at_entry` if applicable
- decision trace or reason string
- runtime status / any operational errors

## Gate 2: remain in or exit shadow paper

Do **not** move beyond shadow/paper based on a short or operationally noisy run.

The following must be true before the shadow phase can be considered successful:

### A. Operational integrity

- [ ] No unresolved runtime failures occur during the observation window
- [ ] Data arrives on time for decision generation
- [ ] Required regime inputs are present and aligned when signals are evaluated
- [ ] Broker/order plumbing behaves as expected in paper mode
- [ ] Signal logs are complete and auditable

### B. Research-to-paper consistency

- [ ] Live paper signals match research assumptions on the same completed bars
- [ ] Intended actions match the deterministic strategy logic
- [ ] Expected fill basis is documented for each signal
- [ ] Actual paper fills are captured for each signal
- [ ] Realized slippage/timing are within acceptable bounds
- [ ] Signal frequency roughly matches backtest expectations
- [ ] Turnover is not materially out of line with research expectations

### C. Observation window quality

- [ ] Observation window is sustained long enough to be meaningful
- [ ] The sample contains enough signals to judge operational behavior
- [ ] Any discrepancies between research and paper are explained and documented

### Gate 2 decision rule

If all Gate 2 conditions hold, the shadow paper phase may be considered operationally successful.

If any Gate 2 condition fails:

- outcome = `research_validation` or continued `shadow_paper`, depending on severity

This document still does **not** authorize live trading.

## Final status template

### Current status
- Status: `research_validation`
- Last updated: `TBD`
- Frozen spec hash: `5eeed7bd2cc7846c4bd84bcfb7d3b1cd5f34caff75a6060d09d5c6d52a0bc547`

### Gate summary
- Gate 0 baseline correctness: `TBD`
- Gate 1 research -> shadow paper: `TBD`
- Gate 2 shadow paper operational success: `TBD`

### Promotion outcome
Choose one:

- `Remain in research validation`
- `Eligible for shadow paper`
- `Shadow paper ongoing; not eligible for further promotion`
- `Not eligible for promotion`

## Direct answer policy for this strategy

For `spy-regime-starter-v1`, the promotion sequence is:

1. additional robustness validation first
2. shadow/paper second
3. LLM review optional and advisory only

The strategy should not skip directly from a single favorable walk-forward result to any broader deployment state.
