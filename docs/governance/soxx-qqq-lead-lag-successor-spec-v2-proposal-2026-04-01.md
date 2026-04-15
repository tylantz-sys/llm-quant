# SOXX/QQQ Lead-Lag Successor Spec Proposal (v2)

**Date:** 2026-04-01  
**Base strategy:** `soxx-qqq-lead-lag`  
**Base frozen spec hash:** `d8e92e5a1be0d6ff003c48716b46939383a138f9206b94f7331600d64c7f6681`  
**Proposal type:** conservative successor hypothesis / new spec recommendation only  
**Status:** design proposal, not yet frozen, no runtime claims

## Purpose

This note proposes a single conservative successor specification for the SOXX-leading-QQQ concept without modifying the frozen original spec.

The goal is not to invent better results. The goal is to respond directly to the current evidence:

- walk-forward is encouraging
- robustness is not sufficient for promotion
- perturbation stability failed, especially around `lag_days` and `entry_threshold`
- cost sensitivity degrades materially as costs rise
- the original concept should be preserved rather than replaced

## Why a successor is justified

The frozen v1 spec appears too brittle around the exact trigger point:

- `lag_days=4` produced a sharply negative perturbation result
- `entry_threshold=0.01` and `entry_threshold=0.04` both degraded meaningfully versus the base
- cost sensitivity falls from Sharpe `0.607` at 1x costs to `0.153` at 2x costs and negative at 3x costs

That pattern suggests the concept may still contain signal, but the current implementation is likely too dependent on a single sharp threshold and on immediate full-size exposure.

## Conservative design principle

Keep the same structural thesis:

> SOXX leads QQQ over a short multi-day horizon.

But reduce fragility by making the trigger stricter and the position sizing less aggressive.

This proposal therefore changes only the parameters most directly tied to robustness and trading-cost sensitivity, while preserving:

- leader/follower pair: `SOXX` -> `QQQ`
- lead-lag family and mechanism
- daily rebalance framework
- non-ML structure
- long-only follower implementation

## Proposed successor slug

`soxx-qqq-lead-lag-v2`

## Proposed successor hypothesis

A more selective and slightly slower SOXX-to-QQQ lead-lag rule may preserve the core directional relationship while reducing sensitivity to threshold noise and transaction costs. Requiring a somewhat stronger SOXX move over a slightly longer signal horizon, combined with lower target exposure, should trade less often, enter on higher-conviction moves, and improve robustness to small parameter perturbations relative to v1.

## Exact parameter changes versus frozen v1

### Keep unchanged
- `leader_symbol: "SOXX"`
- `follower_symbol: "QQQ"`
- `rebalance_frequency_days: 1`

### Change
- `lag_days: 5 -> 6`
- `signal_window: 5 -> 6`
- `entry_threshold: 0.02 -> 0.03`
- `exit_threshold: -0.01 -> 0.00`
- `target_weight: 0.90 -> 0.70`

## Rationale for each change

### 1. `lag_days: 6`
The perturbation evidence showed `lag_days=6` was the only lag variant classified as stable, while `lag_days=4` failed badly. Moving from 5 to 6 days is the smallest possible conservative adjustment in the direction favored by the robustness check.

### 2. `signal_window: 6`
The original spec matched `signal_window` to `lag_days` for symmetry. Preserving that symmetry in the successor avoids introducing a second degree of freedom and keeps interpretation simple.

### 3. `entry_threshold: 0.03`
The original 2% threshold was fragile to both looser and tighter tested perturbations (`0.01` and `0.04` both weakened). A middle-ground increase to 3% is a conservative compromise intended to filter weaker moves without jumping all the way to the already-tested 4% level that degraded performance.

### 4. `exit_threshold: 0.00`
Raising the exit threshold from `-1%` to flat/zero is a risk-control adjustment. It should reduce the time spent holding after the lead signal has fully faded, which is consistent with lowering cost sensitivity through shorter stale holds and with reducing dependence on continuation after signal deterioration.

### 5. `target_weight: 0.70`
This is directly supported by the perturbation set, where `target_weight=0.70` was stable. Lower exposure is the cleanest conservative lever for reducing cost sensitivity and drawdown fragility without changing the core thesis.

## Recommended new files to create

Create a new strategy directory rather than editing the frozen one:

- `data/strategies/soxx-qqq-lead-lag-v2/hypothesis.yaml`
- `data/strategies/soxx-qqq-lead-lag-v2/research-spec.yaml`
- `data/strategies/soxx-qqq-lead-lag-v2/mandate.yaml`

These are the minimum ready-to-write design artifacts needed to express the successor cleanly as a new spec/version.

## Ready-to-write content recommendations

### 1. `data/strategies/soxx-qqq-lead-lag-v2/hypothesis.yaml`

```yaml
# Hypothesis: soxx-qqq-lead-lag-v2
strategy_slug: "soxx-qqq-lead-lag-v2"
statement: >
  SOXX (semiconductor ETF) leads QQQ (Nasdaq-100) by approximately 6 trading
  days on stronger multi-day moves. When SOXX posts a 6-day return >= 3%
  (measured 6 days ago), QQQ produces positive follow-through over the next
  several trading days with enough consistency to outperform transaction costs
  more robustly than the original v1 rule.
mechanism: >
  Semiconductors remain a high-beta, leading-indicator sub-sector for the
  broader Nasdaq complex. A more selective trigger should focus the strategy on
  higher-conviction semiconductor impulses that are more likely to propagate
  into QQQ, while a lower target weight and faster neutral exit reduce reliance
  on marginal continuation and lower exposure to trading-cost drag.
falsification_criteria:
  - "Sharpe does not improve materially versus v1 under the same cost model"
  - "Perturbation stability remains below 60% of tested nearby variants"
  - "2x cost sensitivity drives Sharpe near zero or negative"
  - "CPCV OOS mean Sharpe is non-positive"
  - "DSR remains below promotion-quality threshold"
conviction: "medium"
predecessor_strategy_slug: "soxx-qqq-lead-lag"
predecessor_frozen_spec_hash: "d8e92e5a1be0d6ff003c48716b46939383a138f9206b94f7331600d64c7f6681"
created_at: "2026-04-01"
```

### 2. `data/strategies/soxx-qqq-lead-lag-v2/research-spec.yaml`

```yaml
strategy_slug: "soxx-qqq-lead-lag-v2"
group: "semis_lead_lag"
strategy_type: "lead_lag"
frozen: false
predecessor_strategy_slug: "soxx-qqq-lead-lag"
predecessor_frozen_spec_hash: "d8e92e5a1be0d6ff003c48716b46939383a138f9206b94f7331600d64c7f6681"
design_goal: "Reduce parameter fragility and cost sensitivity while preserving the SOXX-leading-QQQ thesis."
parameters:
  leader_symbol: "SOXX"
  follower_symbol: "QQQ"
  lag_days: 6
  signal_window: 6
  entry_threshold: 0.03
  exit_threshold: 0.00
  target_weight: 0.70
  rebalance_frequency_days: 1
cost_model:
  spread_bps: 5.0
  flat_slippage_bps: 2.0
  slippage_volatility_factor: 0.1
  commission_per_share: 0.0
  min_commission: 0.0
parameter_rationale:
  lag_days: "Moved from 5 to 6 because robustness perturbation indicated lag_days=6 was stable while lag_days=4 failed badly."
  signal_window: "Set equal to lag_days to preserve the original symmetric lead-lag measurement design."
  entry_threshold: "Raised from 2% to 3% to filter weaker moves while avoiding the already-tested 4% setting that degraded performance."
  exit_threshold: "Raised from -1% to 0% to exit sooner once the leader signal no longer remains positive."
  target_weight: "Reduced from 90% to 70% because robustness perturbation showed 70% sizing was stable and this should reduce cost and drawdown sensitivity."
backtest_spec:
  symbols: ["SOXX", "QQQ"]
  years: 5
  initial_capital: 100000
  warmup_days: 30
family: "lead_lag_soxx_qqq"
family_trial_number: 2
family_prior_trials: 1
created_at: "2026-04-01"
updated_at: "2026-04-01"
```

### 3. `data/strategies/soxx-qqq-lead-lag-v2/mandate.yaml`

```yaml
# Mandate: soxx-qqq-lead-lag-v2
strategy_slug: "soxx-qqq-lead-lag-v2"
objective: >
  Generate alpha by exploiting a conservative version of the SOXX-to-QQQ
  lead-lag relationship. When SOXX posts a sufficiently strong 6-day move,
  enter a reduced-size long position in QQQ to capture delayed follow-through,
  and exit once the leader signal is no longer positive.
benchmark: "SPY buy-and-hold"
benchmark_weights:
  SPY: 1.00
universe: ["SOXX", "QQQ"]
constraints:
  max_drawdown: 0.15
  min_sharpe: 0.80
  min_trades: 20
capital: 100000
notes:
  - "This successor is intended to be more selective and less cost-sensitive than v1."
  - "It must earn its own validation chain and must not inherit pass status from the frozen predecessor."
created_at: "2026-04-01"
```

## Recommended validation focus for the parent agent

When this successor is implemented, testing should focus on whether the parameter changes improved the exact weak points seen in v1:

1. perturbation stability around `lag_days` and `entry_threshold`
2. cost sensitivity at 1.5x and 2x costs
3. trade count impact from the stricter trigger
4. whether earlier exit meaningfully reduces stale holding periods
5. whether the lower target weight preserves enough Sharpe after costs

## What not to do

- Do not edit `data/strategies/soxx-qqq-lead-lag/research-spec.yaml`
- Do not reinterpret v1 robustness as a pass
- Do not claim any backtest, robustness, or paper-trading result for v2 before artifacts exist
- Do not reuse the v1 frozen hash for the successor

## Bottom line

The cleanest conservative successor is a new spec that keeps the SOXX-leading-QQQ idea but uses:

- a slightly longer lag
- a slightly stronger trigger
- a faster neutral exit
- smaller position size

This is the narrowest evidence-based redesign that directly responds to the current robustness failures without abandoning the original strategy concept.