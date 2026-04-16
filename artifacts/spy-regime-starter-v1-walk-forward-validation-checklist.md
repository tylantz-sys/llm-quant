# spy-regime-starter-v1 post-fix validation checklist

## Context confirmed from current artifact/spec
- `data/strategies/spy-regime-starter-v1/research-spec.yaml`
  - `parameters.trade_symbol: SPY`
  - `parameters.vix_symbol: VIX`
  - `backtest_spec.symbols: [SPY]`
  - `backtest_spec.signal_symbols: [VIX]`
  - `missing_vix_policy: block_new_entries_allow_risk_exits`
- `scripts/run_walk_forward_non_ml.py`
  - `_resolve_symbols(spec)` currently drives the symbol list passed to `fetch_ohlcv(symbols, ...)`
  - resolved `symbols` are also written into walk-forward provenance under `provenance.symbols`

## What to verify immediately after the resolver fix
1. **Resolved symbol set includes both trade and signal symbols**
   - For `spy-regime-starter-v1`, `_resolve_symbols(spec)` should resolve exactly:
     - `SPY`
     - `VIX`
   - Deduped/sorted output should be `["SPY", "VIX"]`.

2. **Runner fetch log shows both symbols**
   - Running `scripts/run_walk_forward_non_ml.py --slug spy-regime-starter-v1 ...` should print:
     - `Fetching data for spy-regime-starter-v1: symbols=['SPY', 'VIX'], ...`
   - This is the fastest first-pass confirmation that signal-symbol ingestion is fixed.

3. **Fetched data actually contains VIX rows**
   - `prices_df = fetch_ohlcv(symbols, ...)` should now include both `SPY` and `VIX`.
   - If inspecting intermediate data, verify the symbol column contains both names and VIX is not silently absent.

4. **Walk-forward artifact provenance records both symbols**
   - In `data/strategies/spy-regime-starter-v1/walk-forward.yaml`, verify:
     - `provenance.symbols` includes both `SPY` and `VIX`
     - top-level / provenance symbol lists are not `["SPY"]` only

5. **Strategy now has the signal context it expects**
   - Because the strategy rules rely on `vix_close`, the fold runs should no longer be blocked solely because VIX was never fetched.
   - This specifically addresses the spec’s missing-VIX behavior:
     - block new entries/adds when VIX is missing
     - still allow SPY-side risk exits

## What the resulting artifact should no longer look like
6. **Not structurally flat for the old ingestion reason**
   - The new walk-forward result should no longer be flat *solely because* `VIX` was omitted from ingestion.
   - A fully flat result after the fix would need some other explanation (market conditions, thresholds, warmup/data coverage, strategy logic), not the resolver omission.

7. **Fold metrics should be re-evaluated, not assumed identical**
   - The previous all-zero / flat fold pattern caused by missing VIX context should disappear if VIX data is available and aligned.
   - Check whether:
     - some folds now have non-zero `test_days_used`
     - OOS Sharpe / drawdown values are no longer uniformly zero across all folds for the same structural reason

## High-signal artifact fields to inspect
8. Inspect these fields in the regenerated `walk-forward.yaml`:
   - `provenance.symbols`
   - `policy_inputs.warmup_days`
   - `summary.fold_count`
   - `summary.mean_oos_sharpe`
   - `summary.median_oos_sharpe`
   - `summary.max_oos_drawdown`
   - each entry in `folds[*].oos_sharpe`
   - each entry in `folds[*].oos_max_drawdown`

## Minimal acceptance for integration
9. Treat the fix as validated if all of the following are true:
   - resolver returns `["SPY", "VIX"]` for this spec
   - runner fetches both symbols
   - regenerated walk-forward provenance records both symbols
   - strategy is no longer forced flat by missing signal-symbol ingestion alone

## Caveat
- This fix guarantees **symbol ingestion coverage**, not profitable results.
- Walk-forward may still fail acceptance criteria, but if it does, the failure should be attributable to strategy performance rather than missing `VIX` input data.

## Next gated research artifacts
- Use `artifacts/spy-regime-starter-v1-robustness-matrix.md` as the next-stage research validation framework before any shadow/paper promotion.
- Use `artifacts/spy-regime-starter-v1-promotion-checklist.md` as the explicit promotion gate from research validation to shadow/paper.
- Treat this checklist as a prerequisite correctness check, not sufficient promotion evidence on its own.
