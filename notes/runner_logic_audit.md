# Runner Logic Audit: `lqd-spy-credit-lead` WFO flatline

Files inspected:
- `scripts/run_walk_forward_non_ml.py`
- `scripts/run_backtest.py`
- `src/llm_quant/backtest/engine.py`
- `src/llm_quant/backtest/strategies.py`
- `src/llm_quant/backtest/strategy.py`
- `src/llm_quant/backtest/metrics.py`

## Concise diagnosis

The walk-forward runner can legitimately produce all-zero OOS fold metrics when the OOS NAV is perfectly flat. In this codebase, that is most plausibly caused by generated BUY signals never becoming executable trades because the engine floors share count to an integer and records a noop instead of trading when target notional is below one share.

### Strongest code evidence

1. **WFO zeros are consistent with a flat NAV, not necessarily a metric bug.**
   - `scripts/run_walk_forward_non_ml.py` computes fold metrics from `result.snapshots` inside each test window.
   - If there are at least 2 NAV points, it computes returns from NAV changes.
   - `src/llm_quant/backtest/metrics.py::compute_sharpe()` returns `0.0` when return volatility is zero.
   - `src/llm_quant/backtest/metrics.py::compute_max_drawdown()` returns `0.0` when NAV never drops.
   - Therefore `test_days_used: 63` with `oos_sharpe: 0.0` and `oos_max_drawdown: 0.0` is exactly what happens when NAV is constant across all 63 OOS days.

2. **The engine can generate signals but execute zero trades due to the one-share floor.**
   - In `src/llm_quant/backtest/engine.py::_execute_signals()`, BUY sizing is:
     - `target_notional = signal.target_weight * nav`
     - `additional = target_notional - current_notional`
     - `shares = math.floor(additional / price)`
   - If `shares <= 0`, it records noop reason `buy_target_below_share_floor` and does nothing.
   - This is a direct code path for “signals exist, NAV stays flat, metrics all zero.”

3. **WFO runner uses different strategy-selection semantics than the normal backtest runner.**
   - `scripts/run_walk_forward_non_ml.py` sets:
     - `strategy_name = str(spec.get("strategy_type", "pairs_ratio"))`
   - `scripts/run_backtest.py` prefers:
     - `spec.get("strategy_class", spec.get("strategy_type", strategy_name))`
   - So if a frozen spec relies on `strategy_class` differing from `strategy_type`, WFO can instantiate the wrong strategy.

4. **WFO hard-codes fill delay instead of honoring the frozen spec.**
   - `scripts/run_walk_forward_non_ml.py` calls `engine.run(... fill_delay=1, ...)`
   - `scripts/run_backtest.py` uses `_spec_fill_delay(spec)`, which reads:
     - `parameters.execution_lag_days`
     - or spec `fill_delay`
   - This is a real semantic mismatch versus baseline backtest behavior.

5. **WFO config-building is thinner than backtest config-building.**
   - `scripts/run_walk_forward_non_ml.py::_build_strategy_config()` only maps `rebalance_frequency_days -> rebalance_frequency`.
   - `scripts/run_backtest.py::_build_strategy_config()` contains broader parameter mapping and strategy-specific normalization.
   - That makes WFO more exposed to spec/config mismatch.

6. **Lead-lag strategy itself does not force zeros.**
   - `src/llm_quant/backtest/strategies.py::LeadLagStrategy.generate_signals()`:
     - computes lagged leader return
     - enters follower on threshold breach
     - exits when return falls below exit threshold
   - It only needs modest history (`sig_window + lag_days + 2`) and can produce trades normally.
   - So an all-zero result is more likely execution/config related than an inherent lead-lag logic impossibility.

7. **Warmup can reduce usable fold history, but is not the strongest explanation for this exact artifact.**
   - `engine.run()` drops the first `warmup_days` dates of the fold-local slice before trading:
     - `trading_dates = all_dates[warmup_days:]`
   - WFO passes train+test data into each fold run and then measures only the test range.
   - This can suppress early fold activity if warmup is large, but since the artifact still reports `test_days_used: 63`, the direct observed zeros are still most consistent with flat NAV, not missing test snapshots.

## Bottom line

Most likely code-consistent cause:
- WFO folds were flat because signals did not become executable trades, especially via the engine’s integer-share floor (`buy_target_below_share_floor`).

Secondary code-consistent causes that can make WFO diverge from the successful baseline backtest:
- WFO uses `strategy_type` instead of preferring `strategy_class`.
- WFO forces `fill_delay=1` instead of reading spec execution lag.
- WFO uses a reduced config-mapping path relative to `run_backtest.py`.

No repository files were modified for behavior; this note is audit output only.