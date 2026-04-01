# Profit-Taking Behavior Review

## Scope reviewed

- `docs/governance/eod-profit-taking.md`
- `config/default.toml`
- `config/risk.toml`
- `config/strategies/*.toml` (directory listing only; no strategy overlays matching pod names were inspected in full during this review window)
- `scripts/execute_decision.py`
- `scripts/generate_llm_signals.py`
- `scripts/run_backtest.py`
- `src/llm_quant/cli.py`
- `src/llm_quant/config.py`
- `src/llm_quant/trading/intraday.py`
- `src/llm_quant/trading/executor.py`
- `src/llm_quant/broker/executor.py`
- `src/llm_quant/broker/intraday_orders.py`
- `tests/test_strategies_runtime.py`
- `tests/test_strategies_rotation.py`

## Executive summary

Profit-taking exists in **two different implementations**:

1. **Broker-level buy bracket TP** for non-intraday Alpaca execution:
   - Implemented in `src/llm_quant/broker/executor.py`
   - Activated from `src/llm_quant/cli.py` only when `broker=alpaca` and `intraday_enabled = false`

2. **Intraday synthetic profit-taking / OCO management**:
   - Synthetic paper exits in `src/llm_quant/trading/intraday.py`
   - Live Alpaca OCO exits in `src/llm_quant/broker/intraday_orders.py`
   - Activated from `src/llm_quant/cli.py` when `intraday_enabled = true`

There is also **EOD flatten** implemented in `src/llm_quant/cli.py:eod_flat`.

However, the repo shows a material mismatch between **documented policy** and **runtime behavior**:

- The governance doc emphasizes a fixed +3% buy take-profit override and EOD flatten.
- Actual default runtime behavior is more complex and mostly driven by **intraday partial take-profit + trailing stop + optional OCO**, configured under `[execution]`, not only `[limits]`.
- `scripts/execute_decision.py` does not use broker submission or EOD flatten logic at all, so one major execution path does **not** implement the documented broker TP policy.
- Backtesting scripts do not appear to model the intraday profit-taking stack or EOD flatten policy, so historical evidence for “does what it should” is weak.
- I found no direct tests covering profit-taking, OCO reconciliation, trailing stop updates, or EOD flatten timing.

## Phase 1 scorecard and mandate taxonomy

This section is the new canonical Phase 1 specification for making the repo profit-taking-first at the governance and configuration layer.

### Profit-taking scorecard objectives

The repo should explicitly optimize for:
- converting favorable excursion into realized profit
- limiting open-gain giveback
- harvesting winners before they become stale
- favoring sleeves that retain profits better
- evaluating Claude/overlay quality by realized harvest outcomes

### Canonical Phase 1 metrics

The scorecard introduces the following first-class metrics:

- **harvest_ratio**: realized profit divided by peak unrealized profit
- **open_gain_giveback_pct**: percent of peak open profit lost before monetization
- **tp1_hit_rate**: frequency of hitting the first profit-taking milestone
- **trailing_preservation_rate**: fraction of trades where trailing logic preserved gains effectively
- **realized_to_unrealized_ratio**: ratio of booked gains to observed peak gains
- **days_since_last_harvest**: timeliness metric for stale winner handling
- **runner_retention_quality**: whether the residual runner meaningfully improved realized outcome

These metrics are Phase 1 governance objects even before all runtime telemetry is live. The immediate purpose is to:
- define thresholds
- create configuration defaults
- establish promotion and selection rules
- create a stable vocabulary for later instrumentation

### Composite score weights

Phase 1 defines a composite profit-taking score with these default weights:

- capture ratio weight: `0.35`
- giveback penalty weight: `0.25`
- TP1 hit-rate weight: `0.15`
- trailing preservation weight: `0.15`
- runner retention weight: `0.10`

These weights are intended to be stable defaults, not final optimized parameters.

### Promotion gates

A strategy should not be promoted solely on robustness or paper profitability. Under the Phase 1 model it should also satisfy monetization quality floors:

- `min_harvest_ratio = 0.45`
- `max_open_gain_giveback_pct = 0.35`
- `min_tp1_hit_rate = 0.40`
- `min_trailing_preservation_rate = 0.40`
- `min_realized_to_unrealized_ratio = 0.55`
- `min_paper_trades_for_harvest_eval = 30`

These are intended as **initial governance defaults**, not yet final policy.

### Rotation and selection defaults

The repo should begin to reflect a harvest-first bias in ranking and capital deployment:

- `prefer_harvest_over_new_entries = true`
- `stale_winner_trim_required = true`
- `max_days_since_last_harvest = 10`
- `reserve_cash_for_rotation = 0.10`
- `block_readd_after_partial = true`

These are Phase 1 planning and configuration defaults. Runtime enforcement and telemetry come later.

## Sleeve mandate taxonomy

Phase 1 introduces explicit harvesting doctrines per sleeve instead of treating all promoted sleeves as exit-identical.

### Default sleeve mandate
The base promoted default sleeve should use a **balanced harvest** doctrine:

- `mandate_type = "balanced_harvest"`
- `harvest_priority = 50`
- `tp1_target_pct = 0.02`
- `tp1_size = 0.50`
- `runner_tp_mult = 2.0`
- `trailing_stop_pct = 0.015`
- `max_giveback_pct = 0.35`
- `min_harvest_ratio = 0.45`
- `stale_winner_days = 5`
- `allow_reentry_after_partial = false`
- `eod_flatten = false`

### Crypto sleeve mandate
The crypto sleeve should use a **crypto synthetic harvest** doctrine with faster monetization and tighter giveback controls:

- `mandate_type = "crypto_synthetic_harvest"`
- `harvest_priority = 80`
- `tp1_target_pct = 0.015`
- `tp1_size = 0.50`
- `runner_tp_mult = 2.0`
- `trailing_stop_pct = 0.0125`
- `max_giveback_pct = 0.30`
- `min_harvest_ratio = 0.50`
- `stale_winner_days = 2`
- `allow_reentry_after_partial = false`
- `eod_flatten = false`

These are seed mandates meant to create a stable contract between governance, future telemetry, ranking, and prompt logic.

## Intended Phase 1 outcomes

Phase 1 is not a runtime telemetry overhaul. It is a **configuration and governance contract** that does four things:

1. defines what “keeping profits well” means
2. creates explicit thresholds for promotion and sleeve behavior
3. introduces stable mandate vocabulary for default and crypto sleeves
4. creates future integration points for telemetry, ranking, governance enforcement, and Claude evaluation

## 1) Where profit-taking is actually implemented

### A. Non-intraday Alpaca bracket take-profit
File: `src/llm_quant/broker/executor.py`

- `resolve_take_profit(...)`:
  - `take_profit_mode == "pct"` → `entry * (1 + take_profit_pct)`
  - else RR-based using stop distance
- `submit_alpaca_orders(...)`:
  - For `trade.action == "buy"` and `use_brackets=True`, submits `client.submit_bracket_order(...)`
  - If bracket invalid, falls back to plain market order and only logs warning

This matches the governance doc’s fixed TP override concept reasonably well.

### B. Intraday synthetic profit-taking
File: `src/llm_quant/trading/intraday.py`

- `generate_profit_taking_signals(...)`:
  - full close on stop-loss breach
  - partial take-profit when price >= `entry_price * (1 + partial_tp_pct)`
  - trailing-stop close only **after** partial exit has been taken

Configured from:
- `config/default.toml` `[execution]`
  - `profit_take_partial_pct = 0.02`
  - `profit_take_partial_size = 0.50`
  - `profit_take_remainder_tp_mult = 2.0`
  - `trailing_stop_pct = 0.015`

This is a different policy from the governance document’s simple +3% bracket TP.

### C. Intraday Alpaca OCO exits
File: `src/llm_quant/broker/intraday_orders.py`

- `place_oco_exits_for_buys(...)`
  - submits:
    - a partial TP limit sell
    - an OCO order for remaining quantity
- `update_trailing_stops(...)`
  - adjusts stop order if a new HWM is made
- `reconcile_orders(...)`
  - handles fills, missing OCO legs, re-submitted stops, cleanup

This is the most operationally realistic intraday implementation in the repo.

### D. End-of-day flatten
File: `src/llm_quant/cli.py`

- `eod_flat(...)`
  - checks config enable flag and target time
  - checks Alpaca market clock
  - cancels all open orders
  - submits market orders for all positions
  - simulates corresponding closes in DuckDB via `execute_signals(...)` and `log_trades(...)`

This does implement the policy in the governance doc, with the same early-close caveat.

## 2) Does code match documented intent?

## Partial match, but not fully

### What matches
- `docs/governance/eod-profit-taking.md` says fixed TP override is configured in `config/risk.toml`
- `config/risk.toml` does contain:
  - `take_profit_mode = "pct"`
  - `take_profit_pct = 0.03`
  - `take_profit_rr = 2.0`
  - `eod_flatten_enabled = true`
  - `eod_flatten_time = "15:55"`
- `src/llm_quant/broker/executor.py` does implement this
- `src/llm_quant/cli.py:eod_flat` implements flattening logic

### Main mismatches

#### Mismatch 1: runtime defaults point to intraday partial TP, not just fixed +3% TP
- `config/default.toml` `[execution]` enables:
  - `intraday_enabled = true`
  - `intraday_use_oco = true`
- That means the common runtime path in `src/llm_quant/cli.py` is likely the intraday path, not the simple bracket TP path.
- In intraday mode, `submit_alpaca_orders(...)` is called with `use_brackets=not config.execution.intraday_enabled`, so brackets are disabled when intraday is on.

Implication: the governance doc reads like the canonical profit-taking policy is bracket +3% TP, but default runtime behavior appears to be intraday partial/OCO logic instead.

#### Mismatch 2: profit-taking controls are split across `[risk]` and `[execution]`
- Governance doc frames profit-taking as controlled by `config/risk.toml`
- But the active intraday profit-taking parameters live in `config/default.toml` `[execution]`
- That split makes operational intent harder to reason about and easy to misconfigure

#### Mismatch 3: `scripts/execute_decision.py` bypasses broker TP and EOD policy
- `scripts/execute_decision.py`:
  - parses decision
  - risk-filters
  - `execute_signals(...)`
  - logs trades / snapshots
- It never calls:
  - `submit_alpaca_orders(...)`
  - intraday OCO handlers
  - `eod_flat(...)`

Implication: anyone using this script as an execution entrypoint may believe profit-taking is active when it is not.

#### Mismatch 4: governance says “only BUY orders submitted to Alpaca”
That is true for bracket TP logic, but:
- intraday OCO exits are placed after buys
- synthetic paper profit-taking creates exit signals inside runtime, not at original buy submission
So actual behavior is broader and more path-dependent than the document suggests.

## 3) Silent failure / runtime gap risks

## High severity

### 1. No tests found for profit-taking behavior
Search across `tests/` found no matches for:
- `profit_taking`
- `intraday`
- `oco`
- `trailing`
- `eod_flat`
- `take_profit`
- `stop_loss`
- `expectancy`
- `cooldown`

That is the biggest red flag. The system has meaningful logic in:
- `src/llm_quant/trading/intraday.py`
- `src/llm_quant/broker/intraday_orders.py`
- `src/llm_quant/cli.py:eod_flat`
- `src/llm_quant/broker/executor.py`

but there is no direct evidence these workflows are tested end-to-end or even unit-tested.

### 2. `scripts/execute_decision.py` can silently omit intended profit-taking
This script executes approved signals into the paper portfolio only. If operators use it as “the executor,” they do not get:
- bracket TP submission
- OCO exit placement
- EOD flatten scheduling
- trailing stop maintenance

That is a serious behavior gap between expectation and implementation.

### 3. Broker order failures in intraday OCO path degrade to warnings
In `src/llm_quant/broker/intraday_orders.py`:
- partial TP order failures are logged as warnings
- OCO order failures are logged as warnings
- fallback stop failures are logged as warnings

There is no clear hard fail / kill-switch / alert escalation here. That means a position can be opened while intended exits fail to attach.

## Medium severity

### 4. EOD flatten is a separate command, not integrated into main run loop
- `pq run` does not automatically flatten at end of day.
- Flatten depends on separately scheduled `pq eod-flat`.

The doc does mention systemd timers, but operationally this means the policy is only as good as external scheduler reliability. If timer/service breaks, overnight exposure may persist silently.

### 5. Early close handling is acknowledged but not solved
Doc explicitly notes early-close sessions will be skipped by 3:55pm ET because market is already closed.
- This is documented honestly.
- But it still means policy intent “always flat EOD” is not actually guaranteed.

### 6. State-dependent intraday logic may diverge from actual fills
`src/llm_quant/trading/intraday.py:update_state_from_trades(...)` updates state based on executed internal trades.
But in live Alpaca mode:
- actual fill timing/order state can differ from internal assumptions
- state updates happen after local `execute_signals(...)`, not after broker fill confirmation

This can create discrepancies between local state and real broker state, especially around partial exits and trailing behavior.

### 7. Profit-taking trigger prices use current price snapshots, not robust bar semantics
Synthetic profit-taking in `generate_profit_taking_signals(...)` checks latest `price` only.
That means:
- no explicit high/low intrabar logic
- no guarantee trigger would be detected if the latest stored close misses transient threshold crossings

For 5-minute bar logic this may be acceptable, but it is weaker than true order-based broker protection.

## Lower severity / design concerns

### 8. `now_ts` parameter in `generate_profit_taking_signals(...)` is unused
This suggests either incomplete intended behavior or leftover scaffolding.

### 9. `cooldown_until_ts` is stored but not actually used for enforcement
`apply_reentry_cooldown(...)` uses `last_exit_ts + cooldown_delta`, not `cooldown_until_ts`.
State field exists, gets persisted, but appears redundant / partially implemented.

### 10. Bracket validity fallback can remove exits entirely
In `src/llm_quant/broker/executor.py`, invalid bracket → market order only.
That follows the doc, but operationally it means malformed stop/TP inputs can leave positions unprotected unless another system adds exits later.

## 4) Signs the bot may not be “doing what it should”

### A. Documentation likely overstates simplicity and uniformity
The policy doc implies a clean config-driven universal TP/EOD system. In reality there are multiple modes:
- paper-only synthetic exits
- Alpaca bracket exits
- Alpaca intraday OCO exits
- separate EOD flatten command

This is workable, but more fragmented than the governance document suggests.

### B. Backtest path does not validate live profit-taking policy
- `scripts/run_backtest.py` uses `BacktestEngine`
- no evidence in reviewed files that it models:
  - intraday partial TP
  - OCO remainder logic
  - trailing stop maintenance
  - scheduled EOD flatten
- `scripts/generate_llm_signals.py` is also explicitly placeholder and returns empty signals

So there is weak evidence that the live profit-taking behavior has been historically tested in the same form it runs operationally.

### C. Default runtime seems optimized for intraday machinery, not documented EOD-only policy
Because `config/default.toml` enables intraday mode and OCO usage, the main path is not the simple doc-described bracket-profit-taker. If the operator expects the doc behavior, the bot may already be “doing something else.”

### D. Scheduler-dependent EOD flatten can fail outside code visibility
If the timer doesn’t run, repo logic itself does not rescue the policy.

## File-specific findings

### `docs/governance/eod-profit-taking.md`
Strength:
- Clearly documents intended policy and rollback path.

Concern:
- Understates actual runtime complexity and omits intraday partial/OCO path that appears to be the default active mechanism.

### `config/default.toml`
Strength:
- Exposes intraday profit-taking knobs.

Concern:
- These controls live under `[execution]`, not `[risk]`, splitting profit-taking policy across files/sections.
- `intraday_enabled = true` changes actual behavior away from the simpler governance doc path.

### `config/risk.toml`
Strength:
- Contains documented fixed TP and EOD flatten controls.

Concern:
- Search found no strategy TOML references to these settings, so pod overlays may or may not preserve them.
- Track C section lacks explicit TP/EOD fields, unlike Track B and base limits.

### `src/llm_quant/cli.py`
Strength:
- Central orchestration for synthetic exits, OCO exits, and EOD flatten exists.

Concerns:
- Complex branching means behavior varies materially by `broker`, `intraday_enabled`, `intraday_use_oco`, and RTH state.
- `eod_flat` is not integrated into regular `run`.
- Local execution occurs before broker order-management reconciliation, increasing divergence risk.

### `src/llm_quant/trading/intraday.py`
Strength:
- Clear, readable partial TP / trailing-stop logic.

Concerns:
- No direct tests found.
- `now_ts` unused.
- Triggering relies on latest price snapshot only.
- Trailing stop only activates after partial exit, which may be intended but is not highlighted in governance doc.

### `src/llm_quant/broker/intraday_orders.py`
Strength:
- Attempts realistic order-state reconciliation and fallback stop handling.

Concerns:
- Warning-only failure paths for missing exits.
- State complexity with no visible tests is risky.
- Partial quantities are integerized; edge cases for small positions may cause no partial TP order.

### `src/llm_quant/broker/executor.py`
Strength:
- Clean implementation of fixed percent or RR take-profit.
- Validation guard exists.

Concern:
- Invalid bracket falls back to naked market order.
- Used only in selected runtime modes, not universal.

### `scripts/execute_decision.py`
Concern:
- Most important runtime gap: this script does not exercise actual broker TP / OCO / EOD policy.

### `scripts/generate_llm_signals.py`
Concern:
- Placeholder only, returns no signals; provides no evidence that strategy/backtest pipeline validates profit-taking behavior.

### `scripts/run_backtest.py`
Concern:
- No reviewed evidence of modeling operational profit-taking stack, so “evidence it behaves as intended” is weak.

## Priority concerns

### Highest priority
1. **No tests for profit-taking / OCO / EOD flatten**
2. **`scripts/execute_decision.py` bypasses actual profit-taking implementation**
3. **Governance doc does not reflect default intraday runtime behavior**
4. **Live order-attachment failures can degrade to warnings, leaving positions insufficiently protected**

### Medium priority
5. **EOD flatten depends on external scheduler**
6. **Backtest/research path likely does not validate live exit behavior**
7. **Config split across risk/execution increases misconfiguration risk**

## Bottom line

The repo does contain real profit-taking logic, but it is **fragmented across multiple execution modes**, and the default runtime path appears more complex than the governance docs imply. The strongest evidence that the bot may not always be “doing what it should” is:

- one execution script that does not use the broker exit machinery at all,
- no direct tests for the exit logic,
- scheduler-dependent EOD flatten,
- and warning-only degradation paths when protective orders fail.

So the implementation is not obviously broken, but the evidence that it behaves consistently and as governed is currently **insufficient**.
