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

Profit-taking and exit handling exist across **multiple realization paths** that share overlapping policy vocabulary but are not all the same mechanism:

1. **Broker-level buy bracket TP** for non-intraday Alpaca execution:
   - Implemented in `src/llm_quant/broker/executor.py`
   - Activated from `src/llm_quant/cli.py` only when `broker=alpaca` and `intraday_enabled = false`

2. **Shared synthetic exit evaluation for intraday-style management**
   - Synthetic paper exits in `src/llm_quant/trading/intraday.py`
   - Live Alpaca OCO exits in `src/llm_quant/broker/intraday_orders.py`
   - Activated from `src/llm_quant/cli.py` when `intraday_enabled = true`

3. **EOD flatten**
   - Implemented in `src/llm_quant/cli.py:eod_flat`
   - Acts as an operational end-of-day risk-control path rather than a separate alpha system

The repo therefore has a **shared exit-policy framework with multiple realization modes**, not one universal implementation with exact parity everywhere.

The most important documentation truth is:

- policy concepts are shared across modes,
- synthetic behavior is an approximation of policy semantics,
- broker-native realization can differ operationally,
- and exact live-order parity should not be assumed unless separately validated.

This is still useful and governable, but it is more limited than a claim of full synthetic/native equivalence.

## Review framing

This review was originally written against a stronger notion of “canonical parity.” Based on the current implementation, the more accurate framing is:

- the codebase is converging on a **shared exit-policy vocabulary**
- backtests and paper paths rely on **synthetic approximation**
- live Alpaca paths may realize parts of the same policy through native order types
- parity should be described as **semantic alignment where feasible**, not as proof of identical execution behavior

That framing preserves the intent of governance without overstating what the current code guarantees.

## 1) Where profit-taking is actually implemented

### A. Non-intraday Alpaca bracket take-profit
File: `src/llm_quant/broker/executor.py`

- `resolve_take_profit(...)`:
  - `take_profit_mode == "pct"` → `entry * (1 + take_profit_pct)`
  - else RR-based using stop distance
- `submit_alpaca_orders(...)`:
  - For `trade.action == "buy"` and `use_brackets=True`, submits `client.submit_bracket_order(...)`
  - If bracket invalid, behavior is governed by current runtime protection rules rather than by a separate profit-taking subsystem

This reflects one native broker realization of the shared policy vocabulary.

### B. Synthetic/shared exit evaluation
File: `src/llm_quant/trading/intraday.py`

- synthetic evaluation covers:
  - full close on stop-loss breach
  - partial take-profit when price reaches the configured threshold
  - trailing-stop close only **after** partial exit has been taken

Configured from active execution/risk settings used by the runtime.

This is best described as a **shared synthetic approximation of exit-policy behavior**. It is not the same thing as broker-native order-state handling.

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

This is the most broker-native intraday realization of the shared policy semantics reviewed here, but it still should not be described as establishing universal parity with every synthetic path.

### D. End-of-day flatten
File: `src/llm_quant/cli.py`

- `eod_flat(...)`
  - checks config enable flag and target time
  - checks Alpaca market clock
  - cancels all open orders
  - submits market orders for all positions
  - simulates corresponding closes in DuckDB via execution/logging flows

This implements the operational EOD flatten policy. In documentation it should be framed as a governed risk-control override within the shared exit framework.

## 2) Does code match documented intent?

## Broadly aligned in intent, but not identical in implementation path

### What aligns
- `config/risk.toml` contains take-profit and EOD flatten controls
- broker-native execution paths use those controls in appropriate modes
- synthetic paths use related policy semantics for stop-loss, partial TP, trailing behavior, and EOD handling
- EOD flatten exists as an operational control

### Important qualifications

#### Qualification 1: default runtime behavior is path-dependent
- runtime behavior depends on combinations of:
  - broker selection
  - `intraday_enabled`
  - `intraday_use_oco`
- this means the active realization path may be synthetic, native, or mixed

Implication: documentation should describe **shared policy semantics with mode-dependent realization**, not a single universal implementation.

#### Qualification 2: policy language is shared, but controls are sourced from more than one config surface
- governance language has emphasized `config/risk.toml`
- operational runtime behavior also depends on execution settings

Implication: the repo has a shared framework, but not a perfectly single-source policy surface in every path.

#### Qualification 3: synthetic evaluation is not the same as native order behavior
- synthetic logic evaluates current market data against configured thresholds
- native broker paths depend on actual submitted order types, order-state transitions, and fill timing

Implication: it is accurate to say the repo seeks policy consistency, but not accurate to claim exact execution equivalence by default.

#### Qualification 4: EOD flatten is part of current operational semantics, but not the only exit path
- the repo includes a distinct `pq eod-flat` command
- that command governs operational flattening rather than replacing the rest of the exit framework

Implication: docs should present EOD flatten as one governed risk-control mechanism inside the broader framework.

## 3) Runtime gap and assurance risks

## High severity

### 1. Limited direct test evidence for exit behavior
At review time, direct test evidence around profit-taking, OCO reconciliation, trailing stop updates, and EOD flatten timing appeared limited.

That does **not** mean the logic is absent. It means assurance was weaker than the governance language implied.

### 2. Some execution paths may not exercise the full broker-native exit stack
`scripts/execute_decision.py` executes a narrower path than the full runtime orchestration.

Implication: operators should not assume every script entrypoint activates every broker-native protection mechanism.

### 3. Native order-attachment and synthetic-state behavior can diverge
In broker-native paths:
- actual fill timing may differ from local assumptions
- OCO/order reconciliation adds complexity
- partial exit state and trailing management can depend on real broker events

Implication: shared semantics reduce drift, but live state can still diverge from synthetic assumptions.

## Medium severity

### 4. EOD flatten depends on operational scheduling
- `pq eod-flat` is a separate operational command
- correct behavior depends on scheduler reliability and correct deployment

This is consistent with the implementation, but it is still an operational dependency rather than an automatic guarantee inside every run loop.

### 5. Early-close handling remains a practical limitation
The documented early-close caveat is real:
- if the market is already closed before the configured flatten cutoff, the command can skip

That is an implementation limitation that should remain explicitly documented.

### 6. Synthetic triggering uses available price snapshots
Synthetic evaluation relies on available observed prices rather than full broker-native intrabar/order-book behavior.

For many review and backtest purposes this is acceptable, but it is still an approximation.

## Lower severity / design concerns

### 7. Path complexity increases documentation risk
Because behavior varies by mode, it is easy for docs to over-compress the story into a simpler narrative than the code actually supports.

### 8. Config split can obscure ownership
Policy and realization controls are not all expressed in one file or section, which can make the active behavior harder to reason about.

### 9. Fallback and degradation semantics need careful wording
Where native protection cannot be attached or reconciled cleanly, the important documentation question is not “does the repo have a feature?” but “what does the runtime actually guarantee in that mode?”

## 4) Signs the bot may not be “doing what it should”

### A. Documentation can overstate simplicity and parity if not carefully worded
A simple “one canonical exit engine with full parity” story is too strong for the current implementation.

A more truthful statement is:
- the repo uses shared exit-policy semantics,
- multiple realization paths exist,
- and synthetic/native behavior can still differ.

### B. Backtest evidence should be interpreted as policy-consistent approximation
Backtests and other synthetic paths are useful for reviewing policy behavior and reducing logic drift.

They should **not** be treated as proof that all live broker behaviors, fill sequences, or order-state transitions match exactly.

### C. Default runtime may activate more complex machinery than a simplified doc implies
If intraday/OCO settings are enabled, the active runtime path is more complex than a simple fixed-TP description suggests.

### D. Scheduler-dependent EOD flatten remains an operational dependency
If the scheduler does not run, the code path itself does not automatically guarantee flattening.

## File-specific findings

### `docs/governance/eod-profit-taking.md`
Strength:
- Captures the intended policy vocabulary and operational risk-control intent.

Concern:
- Needs careful wording so it describes shared-framework semantics and synthetic approximation without overclaiming parity.

### `config/default.toml`
Strength:
- Exposes operational realization knobs for intraday behavior.

Concern:
- Contributes to mode-dependent behavior, so documentation must avoid implying that one static path is always active.

### `config/risk.toml`
Strength:
- Contains take-profit and EOD flatten controls used by the shared framework.

Concern:
- By itself it does not fully describe every runtime realization choice.

### `src/llm_quant/cli.py`
Strength:
- Central orchestration for synthetic exits, broker-native paths, and EOD flatten exists.

Concern:
- Branching behavior means implementation details vary materially by mode.

### `src/llm_quant/trading/intraday.py`
Strength:
- Clear partial TP and trailing-stop logic.
- Provides the synthetic/shared policy evaluator used for approximation and consistency.

Concern:
- Synthetic triggering should not be documented as identical to native execution behavior.

### `src/llm_quant/broker/intraday_orders.py`
Strength:
- Implements more realistic broker-native order management.

Concern:
- Still subject to broker/order-state realities that synthetic paths do not reproduce exactly.

### `src/llm_quant/broker/executor.py`
Strength:
- Clean implementation of fixed-percent or RR-based take-profit for applicable modes.

Concern:
- Represents one realization path, not the entire framework.

### `scripts/execute_decision.py`
Concern:
- Narrower than the full runtime orchestration; should not be assumed to prove end-to-end exit-policy coverage.

### `scripts/generate_llm_signals.py`
Concern:
- Not meaningful evidence for profit-taking behavior validation by itself.

### `scripts/run_backtest.py`
Concern:
- Useful for reviewing shared-policy approximation, but not evidence of exact live broker parity.

## Priority concerns

### Highest priority
1. **Documentation should avoid overclaiming parity**
2. **Synthetic approximation limits should be stated explicitly**
3. **Mode-dependent runtime behavior should be described accurately**
4. **Operational dependencies such as scheduled EOD flatten should remain visible**

### Medium priority
5. **Test coverage and assurance should continue to improve**
6. **Backtest/research claims should remain scoped to synthetic-policy behavior**
7. **Config ownership/realization boundaries should stay clear in docs**

## Bottom line

The repo does contain real profit-taking and EOD-risk logic, and the current direction is coherent: a **shared exit-policy framework** used across multiple realization modes.

But the truthful implementation statement is narrower than “full parity”:

- native and synthetic paths share policy semantics where practical,
- backtests and paper paths use synthetic approximation,
- live broker execution can still differ in fill timing, order-state behavior, and operational reliability,
- and EOD flatten is a governed operational control rather than a separate strategy feature.

So the implementation is not best described as one perfectly uniform profit-taking engine. It is better described as a shared framework with synthetic and native realizations whose semantics are aligned where possible, with important approximation limits that documentation should state clearly.