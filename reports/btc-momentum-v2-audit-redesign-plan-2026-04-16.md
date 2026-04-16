# btc-momentum-v2 Audit / Redesign Plan
Date: 2026-04-16  
Scope: Family 3 momentum fallback branch  
Status: audit-only, pre-redesign note

## What appears to exist
The repository currently shows a **single concrete btc-momentum-v2 implementation artifact** in scope:

- `scripts/run_btc_momentum_v2_robustness.py`

That script implies a prior passing or near-passing internal result for a BTC trend system with these characteristics:

- strategy class: `trend_following`
- instrument: `BTC-USD`
- signal shape: multi-timeframe momentum using `lookback_short=20` and `lookback_medium=50`
- trend gate: `sma_trend=50`
- position sizing: `target_weight=0.30`
- execution assumptions:
  - `fill_delay=1`
  - elevated crypto cost model
  - daily rebalance
- test framing:
  - only a ~3-year BTC window
  - explicit exclusion of the 2022 crypto crash from the in-sample period
  - synthetic `$10M` capital to work around integer-share BTC handling in the engine

The script text also states, in its own comments, that btc-momentum-v2 was positioned as a fix for an earlier failed `btc-momentum-sprint` concept and that the design change from an `asset_rotation` framing to `trend_following` was important.

Notably, no matching lifecycle package was found under `data/strategies/btc-momentum-v2/` at read time, and no separate markdown research note for this slug surfaced in `reports/`, `notes/`, or `research/`.

## Why naive SMA momentum likely diverges from the repo result
A naive recreation such as "long BTC when price > SMA and flat otherwise" is likely **not** the same object as the repo's passing result.

Most plausible reasons:

1. **The repo result is not textbook single-rule SMA crossover**
   - The script describes **multi-timeframe momentum** plus a **trend filter**.
   - That is materially different from a lone SMA rule.

2. **The time window is highly specific**
   - The script explicitly says the test uses a **3-year window** and excludes the **2022 crypto crash**.
   - A naive full-history SMA test will include very different regimes and should be expected to produce different performance.

3. **The implementation uses the repo's exact engine semantics**
   - `fill_delay=1`
   - daily rebalancing
   - the repo's cost model
   - the repo's stop/risk logic through `StrategyConfig`
   Small implementation differences can matter a lot in BTC.

4. **Position sizing and exposure are constrained**
   - `target_weight=0.30` means the strategy is not simply 100% long when bullish.
   - A naive always-in 1.0x long/flat comparison will not match both return and drawdown behavior.

5. **The strategy may rely on the internal `trend_following` constructor behavior**
   - Parameters such as `lookback_days`, `min_timeframes_positive`, and `sma_trend` are passed into `create_strategy`.
   - If that strategy class combines signals in a specific way, a hand-built SMA proxy will miss the actual decision rule.

6. **The apparent pass may partly depend on a regime carve-out**
   - Under the alpha-hunting framework, Family 3 already carries a warning that textbook momentum failed and needs redesign.
   - A BTC-only pass that depends on dropping the 2022 crash is exactly the sort of regime sensitivity that can make a simple external replication look inconsistent.

## Most plausible redesign path under Family 3 guidance
Per `docs/governance/alpha-hunting-framework.md`, Family 3 should move **away from textbook SMA crossover** and toward **risk-adjusted / dual-momentum style logic**.

The best redesign path is therefore probably:

1. **Treat btc-momentum-v2 as an audit target, not a promotion candidate**
   - First determine whether the reported result is mostly driven by:
     - restricted sample window
     - BTC-specific post-crash rebound regime
     - engine quirks
     - genuine momentum structure

2. **Reframe from BTC-only trend to causal Family 3 design**
   - Use **absolute momentum with volatility normalization**, not plain SMA.
   - Prefer a parameter-light rule such as:
     - momentum score = trailing return / trailing realized vol
     - trade only when score is positive and above a minimal threshold
   - This is much closer to the Family 3 governance guidance than raw SMA logic.

3. **Add an absolute-momentum gate and an explicit cash/defensive state**
   - The framework specifically points toward Antonacci-style dual momentum.
   - For crypto research, the practical analogue is:
     - absolute momentum gate on BTC
     - optional relative momentum comparison versus another crypto or a defensive proxy
     - otherwise go to cash / no-position

4. **Test cross-sectional or relative variants instead of BTC in isolation**
   - BTC-only trend may just be beta timing.
   - A stronger Family 3 candidate would compare assets on the same mechanism:
     - BTC vs ETH
     - BTC vs SOL
     - BTC vs cash / flat
   - This better fits the "different mechanism, not just same market drift" requirement.

5. **Use full-regime validation instead of preserving the favorable 3-year carve-out**
   - Any redesign should include the 2022 crash regime rather than excluding it.
   - If the edge disappears when the unfavorable regime is restored, kill or narrow the hypothesis.

## Clear next implementation steps
1. **Do a code-level audit of the actual `trend_following` strategy behavior**
   - Verify exactly how `lookback_short`, `lookback_medium`, `lookback_days`, `sma_trend`, and `min_timeframes_positive` are combined.
   - Confirm whether stops or other hidden defaults materially affect the result.

2. **Reconstruct the existing btc-momentum-v2 logic as a frozen pre-backtest package**
   - Create lifecycle artifacts only if the parent agent wants this branch promoted to formal research.
   - The package should document:
     - narrow sample dependence
     - integer-share workaround
     - anti-lookahead execution assumptions
     - regime-risk concerns

3. **Run a strict audit comparison**
   - Compare:
     - current repo logic
     - naive SMA proxy
     - same logic over longer history including 2022
   - Goal: identify which ingredient actually explains the divergence.

4. **If the audit shows fragility, replace rather than tune**
   - Avoid parameter-mining around 20/50/50.
   - Move to a **risk-adjusted dual-momentum redesign** with very few parameters.

5. **Preferred redesign candidate**
   - Family 3 crypto momentum v3:
     - absolute momentum = trailing return over medium horizon
     - normalization = divide by trailing realized volatility
     - optional relative choice = BTC vs ETH or BTC vs SOL
     - defensive state = flat if absolute momentum fails
   - This is more aligned with the governance note that dual momentum, not textbook SMA crossover, is the intended redesign direction.

## Bottom line
`btc-momentum-v2` appears to exist mainly as a robustness script pointing to a **specific, engineered BTC trend configuration**, not as a fully documented lifecycle strategy package. The most likely reason naive SMA replicas do not match is that the repo result is a combination of **multi-timeframe signal logic, 30% target exposure, repo-specific execution semantics, and a favorable truncated sample**. Under Family 3 guidance, the right next step is **audit first, then redesign toward risk-adjusted dual momentum**, not further optimization of plain BTC SMA rules.