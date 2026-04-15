# Execution Path Audit: soxx-qqq-lead-lag

## Scope
Reviewed executable-path and execution-assumption code for:
- `scripts/run_backtest.py`
- `scripts/run_walk_forward_non_ml.py`
- `src/llm_quant/backtest/engine.py`
- `src/llm_quant/backtest/strategies.py`
- `src/llm_quant/backtest/strategy.py`
- `src/llm_quant/trading/portfolio.py`

Also checked artifacts:
- `data/strategies/soxx-qqq-lead-lag/research-spec.yaml`
- `data/strategies/soxx-qqq-lead-lag/experiments/9802d86c.yaml`
- `data/strategies/soxx-qqq-lead-lag/experiments/57fba00d.yaml`
- `data/strategies/soxx-qqq-lead-lag/walk-forward.yaml`
- `data/strategies/soxx-qqq-lead-lag/robustness.yaml`

## Conclusion
Canonical backtest and non-ML walk-forward use the same core execution engine and the same `LeadLagStrategy` signal generator. The weak canonical economics are best explained by two evidence-backed differences/issues:

1. **Canonical backtest did not honor frozen-spec years=5; it used CLI default years=3.**
2. **The engine’s target-weight + integer-share sizing creates massive no-op partial-sell attempts, which the canonical artifact directly records as `sell_target_below_share_floor=703`.**

These are sufficient to explain why the new canonical run is much weaker than older/other artifacts without requiring a separate hidden execution path.

## Evidence

### A. Canonical backtest is not fully spec-driven on history length
In `scripts/run_backtest.py`:
- `--years` defaults to `3`.
- After loading the frozen spec, the script overrides symbols from spec, but does **not** replace `args.years` with `spec.backtest_spec.years`.
- Data fetch uses `lookback_days = args.years * 365`.

Observed artifact evidence:
- Frozen spec: `backtest_spec.years: 5`
- Canonical artifact `9802d86c`: `policy_inputs.years: 3`
- Canonical artifact start date: `2023-05-16`
- Older strong artifact `57fba00d` start date: `2022-01-11`

By contrast, in `scripts/run_walk_forward_non_ml.py`:
- `years = int(spec.get("backtest_spec", {}).get("years", 5))`
- So WFO uses the frozen spec’s 5-year window.

This is a real execution-path difference at the runner layer even though both use the same engine.

## B. Canonical and WFO do share the same core engine path
Both runners instantiate:
- `create_strategy(...)`
- `BacktestEngine(...)`

Both ultimately call:
- `BacktestEngine.run(...)` for actual simulation
- Canonical wraps it via `run_with_cost_sensitivity(...)`, but 1.0x still goes through `run(...)`.

Shared assumptions in `src/llm_quant/backtest/engine.py`:
- Warmup is applied by dropping first `warmup_days` trading dates.
- Entry/rebalance fills with `fill_delay > 0` execute at future **open**.
- Exit-policy signals execute immediately at current **close**.
- Integer shares are enforced with `math.floor(...)`.
- Portfolio sizing is based on `target_weight * nav`.

For this slug specifically:
- Spec warmup = 30
- Spec/derived fill_delay = 1
- WFO uses warmup 30, fill_delay 1
- Canonical uses warmup 30, fill_delay 1

So there is **no fill-delay mismatch** and **no warmup mismatch** between canonical and WFO for this strategy.

## C. The canonical artifact shows a severe signal-to-execution collapse
Artifact `9802d86c` records:
- `signal_count: 717`
- `executed_trade_count: 14`
- `signal_noop_reasons.sell_target_below_share_floor: 703`

This is the strongest direct clue.

In `_execute_signals(...)`:
- BUY sizing:
  - `target_notional = signal.target_weight * nav`
  - `additional = target_notional - current_notional`
  - `shares = floor(additional / price)`
- SELL sizing:
  - `reduce = current_notional - target_notional`
  - `shares = min(floor(reduce / price), existing.shares)`
  - if `shares <= 0`: record `sell_target_below_share_floor`

Therefore, if the engine receives many SELL signals whose desired reduction is less than 1 share, almost all will become no-ops exactly as seen in the artifact.

## D. Lead-lag strategy itself does not emit partial SELLs
`LeadLagStrategy.generate_signals()` only emits:
- BUY of follower to `target_weight` when lagged leader return >= entry threshold and no position exists
- CLOSE when lagged leader return <= exit threshold and position exists
- otherwise nothing

So the 703 no-op SELLs are **not** coming from `LeadLagStrategy` directly.

That means the no-op SELL flood must be caused by engine-side execution interactions, most plausibly:
- synthetic exit-policy signals or
- re-targeting behavior from non-CLOSE SELL actions emitted elsewhere in the engine flow

Since exit-policy checks are injected in `BacktestEngine._check_exit_policy(...)`, the weak canonical economics are not purely “bad signal quality”; they involve engine-side execution accounting.

## E. WFO reporting can mask the same pathology
`run_walk_forward_non_ml.py`:
- runs `engine.run(...)` per fold
- then computes OOS Sharpe/max drawdown only from fold `snapshots` within `[test_start, test_end]`
- writes fold summary to `walk-forward.yaml`

It does **not** persist:
- `signal_count`
- `executed_trade_count`
- `signal_noop_reasons`
- `smoke_audit`

So WFO can look strong while hiding poor signal-to-trade conversion. The engine path is shared, but the diagnostics surfaced are not.

## File-level findings

### `scripts/run_backtest.py`
Findings:
- Not fully frozen-spec-driven; `years` remains CLI/default-driven.
- Uses frozen spec for:
  - symbols
  - fill delay
  - warmup days
  - cost model
- Uses `BacktestEngine.run_with_cost_sensitivity(...)`.

Impact:
- Main runner-layer mismatch is 3-year canonical vs 5-year spec/WFO.

### `scripts/run_walk_forward_non_ml.py`
Findings:
- Uses frozen spec for:
  - years
  - warmup days
  - fill delay
  - rebalance frequency
- Uses same `BacktestEngine.run(...)`.

Impact:
- Same engine assumptions, different lookback sourcing and different reporting.

### `src/llm_quant/backtest/engine.py`
Findings:
- Entry/rebalance = delayed open fill when `fill_delay=1`.
- Exit-policy signals = immediate same-day close.
- Integer-share floors are enforced.
- Smoke audit only requires some executed trades to mark run healthy.
- Canonical artifact’s 703 share-floor no-ops are fully consistent with `_execute_signals(...)`.

Impact:
- Share-floor behavior is a direct explanation for why many signals produced almost no trades/economics.

### `src/llm_quant/backtest/strategies.py`
Findings:
- `LeadLagStrategy` is simple and stateless.
- It emits BUY and CLOSE only, not SELL trims.

Impact:
- The sell-floor pathology is downstream of strategy generation.

### `src/llm_quant/backtest/strategy.py`
Findings:
- No special execution differences; confirms strategies are pure signal generators.

### `src/llm_quant/trading/portfolio.py`
Findings:
- Standard NAV / mark-to-market implementation.
- No path-specific discrepancy found.

## What likely explains the weak canonical economics
Most likely combined explanation:

1. **Wrong sample window for canonical**
   - New “canonical” run actually used 3 years, not frozen-spec 5 years.
   - That materially changes a cyclical lead-lag strategy’s realized opportunity set.

2. **Execution accounting degrades many would-be adjustments into no-ops**
   - 703 out of 717 signals became `sell_target_below_share_floor`.
   - That means the strategy/exits generated lots of intent, but almost none translated into executable trades.

3. **WFO summary does not expose this**
   - WFO can still show strong fold Sharpe because it evaluates resulting test-window NAV, not signal efficiency/no-op rates.

## What does not explain it
Not supported by code/artifact evidence:
- Different strategy implementation between canonical and WFO
- Different engine implementation between canonical and WFO
- Different fill delay for this slug
- Different warmup days for this slug
- 1x cost-model differences between canonical and WFO

## Parent-agent ready summary
- **Same engine path:** yes, canonical and WFO both execute through `BacktestEngine.run(...)` with the same lead-lag signal logic.
- **Meaningful runner difference:** canonical uses CLI/default `years=3`; WFO uses spec `years=5`.
- **Warmup/fill delay mismatch:** none for this slug (`warmup_days=30`, `fill_delay=1` in both).
- **Strongest execution clue:** canonical artifact has `717` signals but only `14` executed trades, with `703` blocked by `sell_target_below_share_floor`.
- **Interpretation:** weak canonical economics are driven by both a shorter-than-spec sample window and engine-side target-sizing/share-floor behavior that collapses most sell adjustments into no-ops.