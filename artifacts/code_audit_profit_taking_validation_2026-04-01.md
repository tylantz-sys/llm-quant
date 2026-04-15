# Code Audit: Profit-Taking, Backtesting, Walk-Forward, and Anti-Curve-Fit Reality

## Scope reviewed

Top-down code review only; no autonomous test execution performed.

Primary files read:
- `src/llm_quant/cli.py`
- `src/llm_quant/config.py`
- `src/llm_quant/trading/exits.py`
- `src/llm_quant/trading/intraday.py`
- `src/llm_quant/trading/runtime_controls.py`
- `src/llm_quant/risk/manager.py`
- `src/llm_quant/backtest/engine.py`
- `src/llm_quant/backtest/robustness.py`
- `src/llm_quant/backtest/strategies.py`
- `src/llm_quant/strategies/runtime.py`
- `src/llm_quant/strategies/rotation.py`
- `scripts/run_backtest.py`
- `scripts/run_walk_forward_non_ml.py`

Representative tests read:
- `tests/test_backtest/test_walk_forward_runner.py`
- `tests/test_trading/test_intraday_profit_taking.py`
- `tests/test_trading/test_exit_engine.py`
- `tests/test_trading/test_harvest_governance_runtime_controls.py`
- `tests/test_trading/test_profit_taking_telemetry.py`

## Executive summary

The repo has real implementation for:
- intraday partial profit-taking and trailing-stop logic,
- canonical exit-policy normalization,
- optional end-of-day flatten command,
- backtesting with fill delay, cost modeling, warmup, and stop-losses,
- fixed-split walk-forward for non-ML strategies,
- several anti-overfitting analytics in code (`PBO`, `CPCV`, shuffled-signal test, MinTRL, parameter perturbation support),
- runtime strategy rotation and overlay governance,
- telemetry around profit-taking and harvest-governance actions.

But the implementation is uneven versus the governance/docs target state:

1. **Profit-taking is materially implemented, but split across multiple layers and only partially enforced end-to-end.**
   - The canonical engine in `trading/exits.py` is the clearest “source of truth” for partial TP, trailing stop, and EOD flatten assessment.
   - Runtime in `cli.py` uses that canonical engine for intraday exit evaluation and telemetry.
   - However, there is still older overlapping logic in `trading/intraday.py` (`generate_profit_taking_signals`) that duplicates part of the same policy. In the current main run path it appears unused in favor of `evaluate_position_exits`, but it is still tested and present, which is a maintenance mismatch risk.

2. **EOD flatten exists, but it is not a universal automatic runtime invariant.**
   - There is a dedicated `pq eod-flat` CLI command that checks the cutoff and submits flatten orders.
   - `assess_eod_flatten()` disables EOD flatten for crypto runtime semantics.
   - In the main `run()` loop, EOD flatten is not automatically applied during normal execution; it depends on a separate scheduled command / service.
   - So docs describing EOD flatten as a control are only accurate if ops wiring is active.

3. **Backtesting is real and more sophisticated than a toy engine, but still simpler than the governance language implies.**
   - There is proper daily sequencing, optional T+1 fill delay, transaction costs, stop-loss execution, cost stress, and artifact logging.
   - However, the backtest engine does **not** model the runtime profit-taking stack used live/intraday:
     - no canonical partial take-profit / trailing-stop engine integration,
     - no OCO/bracket semantics,
     - no EOD flatten,
     - no harvest-governance controls,
     - no pod-aware runtime overlay path.
   - This is the biggest code-vs-doc mismatch around profit-taking validation.

4. **Walk-forward exists, but only as a fixed non-ML script, not as a broad validation framework.**
   - `scripts/run_walk_forward_non_ml.py` is a real deterministic rolling-split WFO runner.
   - It does not optimize parameters per fold; it mostly re-runs a frozen strategy over train+test data and scores test slices from snapshots.
   - That means it is a validation harness, but not a full train/re-fit/reselect WFO pipeline.

5. **Anti-curve-fit defenses exist in code, but many are analytics utilities rather than enforced promotion gates.**
   - `backtest/robustness.py` implements substantial anti-overfit logic.
   - `run_backtest.py` does not invoke the full robustness gate.
   - I did not see a single central promotion pipeline in the reviewed code paths that hard-blocks promotion based on all documented robustness requirements.
   - So the repo has real defenses, but enforcement appears fragmented and incomplete.

## Major architecture as actually implemented

### Runtime / trading path

`src/llm_quant/cli.py` is the operational hub.

Core path in `_run_single_pod()`:
1. Load pod-scoped config.
2. Decide signal source:
   - pure LLM, or
   - strategy overlay on promoted strategies.
3. Acquire run locks and DB locks.
4. Fetch daily and optionally intraday data.
5. Load portfolio and latest prices.
6. Build context and get decisions/signals.
7. Apply:
   - overlay governor constraints,
   - asset-class filtering,
   - harvest-governance runtime controls,
   - intraday exit evaluation,
   - expectancy gate scaling,
   - risk manager filtering.
8. Execute paper or Alpaca trades.
9. For intraday + Alpaca + OCO:
   - reconcile protective orders,
   - update trailing stops,
   - place OCO exits for new buys.
10. Log trades, decision contexts, intraday snapshots, and profit-take telemetry.
11. Save portfolio snapshot.

### Strategy runtime path

Promoted runtime strategy path is:
- `strategies/runtime.py` loads strategy specs from research artifacts / catalog,
- `backtest/strategies.py` contains actual strategy classes,
- `strategies/rotation.py` can rank/select top recent strategies by realized Sharpe / drawdown / min trades,
- then `cli.py` merges and post-processes those signals before optional overlay review.

This means research/backtest strategy classes are reused in runtime, which is good for consistency at signal generation level.

### Validation / research path

Main research validation code:
- `scripts/run_backtest.py` for one strategy backtest + artifact generation,
- `scripts/run_walk_forward_non_ml.py` for deterministic WFO,
- `backtest/robustness.py` for anti-overfit analytics.

This is not yet a single cohesive lifecycle engine. It is a toolkit plus scripts.

## Profit-taking and EOD flatten: implementation reality

### What is concretely implemented

#### Canonical exit policy layer
`src/llm_quant/trading/exits.py` defines:
- `ExitPolicy`
- `ExitRuntime`
- `build_exit_policy()`
- `build_exit_runtime()`
- `evaluate_position_exits()`
- `assess_eod_flatten()`
- `build_broker_exit_plan()`

This is the cleanest implementation of documented profit-taking behavior.

Implemented behaviors:
- partial take-profit:
  - configurable threshold and size,
- trailing stop:
  - only after partial exit has been taken,
- take-profit mode:
  - fixed percent or risk-reward,
- EOD flatten enable/disable and cutoff time,
- native vs synthetic exit mode depending on broker/runtime,
- telemetry describing whether positions are protected or unprotected.

#### Synthetic intraday exit behavior
In synthetic mode (`paper` intraday or intraday without OCO):
- stop-loss triggers `CLOSE`,
- partial TP triggers `SELL` to reduce target weight,
- trailing stop after partial triggers `CLOSE`.

That is real logic, not just docs.

#### Native broker-managed path
For Alpaca:
- non-intraday runtime uses bracket-style semantics,
- intraday + OCO runtime can place partial TP, remainder TP, and trailing stop percentages through broker-order management helpers,
- runtime checks for unprotected positions and can fail hard when configured.

#### Telemetry and attribution
Trade logging and profit-taking telemetry are implemented and tested:
- canonical reason normalization (`tp_partial` → `take_profit_partial`),
- profit-take event logging linked to trade IDs and decisions,
- attribution fields like `decision_source`, `sleeve`, `source_decision_id`.

That is a meaningful implementation of auditability.

### What is not fully unified

There are effectively **two profit-taking implementations**:

1. `trading/exits.py` canonical engine
2. `trading/intraday.py` older-style `generate_profit_taking_signals()`

They are very similar:
- both check stop-loss,
- both do partial TP,
- both do trailing stop after partial.

But only the canonical engine is wired into the main run path in `cli.py`. The older function persists and is covered by tests. That creates a risk of drift between test expectations and production behavior if one evolves without the other.

### EOD flatten reality

There is a dedicated `pq eod_flat` command in `cli.py`:
- loads config,
- checks current ET using Alpaca clock,
- uses `assess_eod_flatten()`,
- skips if disabled / before cutoff / market closed / crypto runtime semantics,
- cancels open orders,
- submits market orders to flatten positions,
- then logs flatten trades to DuckDB best-effort.

Important reality:
- this is **not automatically enforced in the main `run()` path**,
- it depends on separate operational scheduling (`systemd` timers/services are present in repo),
- for crypto, `assess_eod_flatten()` explicitly disables flattening.

So:
- docs saying the system has EOD flatten are directionally true,
- but code reality is “separate scheduled process, not a built-in invariant of every runtime loop.”

## Backtesting and walk-forward: implementation reality

### Backtest engine strengths

`src/llm_quant/backtest/engine.py` has several robust features:
- daily event loop,
- causal indicator usage (`date <= current_date`),
- warmup period,
- configurable fill delay,
- execution at next-day open for delayed signals,
- stop-loss checks,
- square-root impact-ish cost model,
- cost multiplier stress reruns,
- benchmark return support,
- artifact/registry integration,
- optional volatility targeting,
- optional ML gate,
- optional meta-filters.

This is materially more than a toy notebook backtester.

### Backtest limitations relative to runtime/docs

The backtester does **not** replicate the live runtime control stack:

Not modeled in reviewed engine:
- canonical intraday exit engine from `trading/exits.py`,
- partial TP + trailing-stop sequence from live runtime,
- EOD flatten,
- OCO/bracket operational behavior,
- harvest-governance scaling/flattening,
- expectancy gate,
- overlay governor,
- pod-aware strategy-set rotation in backtest loop,
- crypto-specific protection semantics.

So if docs imply profit-taking validation is thoroughly proven by current backtests, code does not support that claim yet. Current backtests mainly validate entry/exit strategy logic plus stop-losses and cost sensitivity, not the full runtime monetization stack.

### Walk-forward implementation reality

`scripts/run_walk_forward_non_ml.py`:
- loads frozen spec,
- fetches data,
- computes indicators,
- builds deterministic rolling windows,
- reruns backtest per fold,
- scores OOS Sharpe and max drawdown on test window snapshots,
- writes a `walk-forward.yaml`.

Strengths:
- deterministic and pre-registered split defaults,
- purged gap between train and test,
- fold-by-fold reporting,
- explicit pass/fail summary.

Limitations:
- no fold-specific parameter refitting/selection,
- no explicit optimization on train then locking for test,
- uses full strategy config from frozen spec unchanged across folds,
- computes OOS from snapshot-derived nav slices, not a richer fold artifact structure,
- no integration with robustness analytics in same runner.

So this is a legitimate WFO-style validation step, but narrower than the governance narrative may suggest.

### Missing file noted by task prompt

The prompt referenced an autonomous overnight test surface that is no longer part of the repo. I did not find a corresponding maintained source runner in `scripts/`, which indicates the overnight testing path had already drifted out of sync with the active codebase.
- or the prompt reflected an earlier repo state.

That is a concrete mismatch worth flagging to the parent.

## Anti-curve-fit / curve-fit defenses: what truly exists in code

### Real implemented defenses

#### 1. Frozen-spec workflow
`run_backtest.py` calls `ensure_frozen_spec()` unless `--no-spec-check` is used.

This is a real discipline control:
- backtests are supposed to run against frozen research specs,
- artifacts include spec hash/provenance.

This helps reduce silent parameter drift.

#### 2. Cost sensitivity / stress testing
`BacktestEngine.run_with_cost_sensitivity()` re-runs at:
- `1.0x`
- `1.5x`
- `2.0x`
- `3.0x`

This is a real robustness test against fragile micro-edge assumptions.

#### 3. PBO / CSCV
`backtest/robustness.py` implements `compute_pbo()`.

This is a serious anti-overfit analytic.

#### 4. CPCV
`run_cpcv()` is implemented.

Again, this is a real overfitting defense.

#### 5. Parameter perturbation support
`generate_perturbations()` and perturbation result structures exist.

This is a concrete parameter-stability defense.

#### 6. Shuffled signal test
`shuffled_signal_test()` exists and is meaningful:
- compares real strategy timing to random invested-day selection on actual asset returns.

This is a strong anti-spurious-timing test.

#### 7. Mechanism inversion test
`mechanism_inversion_test()` exists.

Useful to test whether signal direction actually matters.

#### 8. MinTRL
`compute_min_trl()` exists and is also surfaced as warnings in:
- `run_backtest.py`
- `run_robustness_gate()`

This addresses statistical significance / track record sufficiency.

#### 9. Portfolio admission style controls
Also implemented in robustness:
- marginal Sharpe contribution,
- rolling correlation gate.

These are more about portfolio diversification discipline than curve-fit directly, but still genuine controls.

#### 10. Runtime strategy rotation with min trades
`strategies/rotation.py` requires min trades and ranks on realized results over a recent window.
This is not anti-curve-fit by itself, but it is a live selection discipline.

### What is only partially enforced

Despite those real controls, enforcement is incomplete:

- `run_backtest.py` does **not** call `run_robustness_gate()`.
- `run_walk_forward_non_ml.py` does **not** invoke PBO/CPCV/shuffle/parameter stability.
- I did not see, in reviewed code, a single promotion command/path hard-wiring:
  - frozen spec,
  - backtest,
  - walk-forward,
  - robustness gate,
  - paper trade gate,
  - promotion decision.

So anti-curve-fit logic exists as library code and utilities, but not yet as one consistently enforced pipeline.

### Potential curve-fit risks still present

1. **Large strategy zoo + catalog selection risk**
   - Many robustness scripts exist.
   - Default promoted/candidate strategy lists are hardcoded / catalog-driven.
   - Without a central “multiple-testing adjusted selection registry,” there is still opportunity for search over many variants with incomplete enforcement of PBO/DSR-style penalties.

2. **Strategy rotation based on recent realized Sharpe**
   - Runtime rotation uses recent rolling performance and min trades.
   - This may improve adaptivity, but it can also become a live overfitting layer if not itself governed by out-of-sample discipline.

3. **Meta-filters and toggles can be turned on via CLI**
   - `run_backtest.py` supports optional meta-filters and volatility targeting.
   - Good for experimentation, but if governance expects frozen pre-registration, these options can create extra degrees of freedom unless captured and audited. The script does record policy inputs, which helps.

4. **Backtest/live mismatch in exits**
   - Since backtests do not mirror runtime profit-taking stack, live monetization could look better or worse than backtest assumptions.
   - That mismatch can conceal curve-fit in exits because research validation is not testing the same mechanism.

## Tests reviewed: what they prove

### Strongly supported by tests
- canonical exit policy preference and runtime semantics,
- synthetic partial TP and trailing stop behavior,
- EOD flatten due/disabled-for-crypto logic,
- telemetry payload structure,
- intraday scale-in / cooldown / merge behavior,
- harvest-governance runtime parsing, scaling, forced flattening, and logging,
- profit-take reason normalization and event linkage,
- walk-forward window construction determinism.

### Not demonstrated by reviewed tests
- full end-to-end backtest → robustness → WFO → promotion gate,
- live run path automatically honoring EOD flatten in one loop,
- backtest parity with runtime profit-taking,
- complete crypto promotion gate enforcement in the paths reviewed,
- actual use of `run_robustness_gate()` by a production promotion workflow.

## Glaring weaknesses / mismatches

### 1. Backtest/live exit mismatch is the biggest weakness
Live runtime uses:
- canonical exit engine,
- broker-native OCO/brackets,
- synthetic partial TP,
- trailing stop after partial,
- harvest-governance intervention.

Backtest engine uses:
- stop-loss only.

That is a major documentation-vs-code gap if docs imply profit-taking policy is thoroughly validated by backtests.

### 2. EOD flatten is operationally external, not intrinsic
It exists as a separate CLI command and likely systemd timer. If that timer fails, the main run loop does not backstop it. That weakens claims of hard runtime enforcement.

### 3. Duplicate profit-taking logic
`trading/exits.py` and `trading/intraday.py` overlap. This increases drift risk and muddles “single source of truth.”

### 4. Robustness gate implementation is not obviously enforced by default workflows
There is excellent analytics code, but reviewed runners do not hard-wire all of it. This makes anti-curve-fit protections “available” more than “guaranteed.”

### 5. Walk-forward is narrow
Current WFO is valid but basic:
- fixed splits,
- no parameter re-fit loop,
- no integrated robustness battery,
- no direct tie-in to promotion gate in reviewed code.

### 6. Missing source for overnight WFO path
Prompt-referenced file/test do not appear present as source. That weakens confidence in an overnight WFO story unless another agent finds equivalent docs or alternate implementation.

### 7. Harvest governance depends on surveillance table state
Runtime harvest controls load the latest `surveillance_scans` record and act on recommended actions. That means runtime governance is only as good as the upstream surveillance process frequency and correctness. In reviewed path, I did not inspect the detector implementation itself.

## Practical bottom line for parent agent

### Does repo follow docs on profit-taking?
Partially.
- Yes: there is real partial TP, trailing stop, telemetry, native/synthetic exit logic, and EOD flatten command.
- No: backtests do not fully validate the live profit-taking stack, and EOD flatten is not intrinsic to the main run loop.

### Does repo follow docs on backtesting and walk-forward?
Partially.
- Yes: there is a real backtest engine and a deterministic non-ML walk-forward runner.
- No: the framework is not unified, and WFO/robustness are not obviously enforced as one promotion pipeline in reviewed code.

### What still needs testing/gating in code reality?
Most importantly:
1. parity tests between live canonical exit engine and backtest assumptions,
2. end-to-end promotion workflow enforcement using robustness gates,
3. end-to-end crypto runtime and promotion gating beyond isolated runtime controls,
4. scheduled EOD flatten operational dependency / failure-mode handling,
5. strategy rotation overfit defenses,
6. actual integrated walk-forward + robustness + paper/promotion path.

### Does logic exist to avoid curve fitting?
Yes, substantial logic exists.
But:
- much of it is library-level or script-level,
- not all of it is clearly enforced in the main promotion path,
- therefore the answer is **“defenses exist, but enforcement appears incomplete.”**

## Concise evidence bullets

- **Canonical profit-taking exists:** `src/llm_quant/trading/exits.py`
- **Main runtime uses canonical exit engine:** `src/llm_quant/cli.py`
- **EOD flatten exists as separate command:** `src/llm_quant/cli.py:eod_flat`
- **Crypto EOD flatten disabled:** `assess_eod_flatten(... runtime.is_crypto ...)`
- **Backtester has stop-loss but not canonical live profit-taking:** `src/llm_quant/backtest/engine.py`
- **Walk-forward runner exists for non-ML only:** `scripts/run_walk_forward_non_ml.py`
- **Robustness analytics exist:** `src/llm_quant/backtest/robustness.py`
- **Full robustness gate not seen wired into run_backtest default path:** `scripts/run_backtest.py`
- **Duplicate intraday profit-taking logic remains:** `src/llm_quant/trading/intraday.py`
- **Profit-taking telemetry and attribution are real and tested:** `tests/test_trading/test_profit_taking_telemetry.py`
