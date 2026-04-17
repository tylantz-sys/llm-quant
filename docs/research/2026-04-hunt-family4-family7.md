# Phase 1 + Phase 2 Hunt Results: Family 4 & Family 7

**Date:** 2026-04 session  
**Researcher:** PM AI  
**Status:** BOTH KILLED — do not retry

---

## Family 4: VIX Term Structure (Volatility Regime Harvesting)

**Hypothesis:** When VIX futures are in contango (VIX3M > VIX), volatility is expensive → underweight equities. When in backwardation (VIX > VIX3M), fear premium is real → overweight equities.

**Data:** VIX, VIX3M, SPY, BIL (2014–2024, 2516 rows)

**Phase 1 Results:**
- Sharpe: 0.623 (VIX/VIX3M ratio-based sizing, long SPY)
- MaxDD: -24.2%
- Fails all 3 stress periods (worse than SPY in 2018/2020/2022)
- Inversion Sharpe: +0.505 (not negative) → mechanism not cleanly directional

**Verdict: KILL**
- Inversion test failure is the kill shot. A genuine volatility regime signal should hurt when inverted.
- The contango/backwardation signal captures broad equity beta, not timing.

---

## Family 7: VIX Spike Contrarian (Sentiment Contrarian)

**Hypothesis:** Extreme fear (VIX > 25-30) = market panic = mean reversion entry opportunity. Buy SPY on VIX spike, hold 10-30 days.

**Data:** VIX, SPY (2010–2023, 3521 rows)

### Iteration 1: Raw VIX>threshold on first crossing

**Phase 1 grid results (selected):**
| Config | Sharpe | Notes |
|--------|--------|-------|
| VIX>30, hold=10d | 0.455 | Below 0.6 floor |
| VIX>25, hold=20d | 0.615 | Barely passes threshold |
| VIX>25, hold=30d | ~0.615 | Best raw config |

**Phase 2 results (VIX>25, hold=30d):**
- Real Sharpe: 0.3280
- Shuffled mean: 0.3494 — **strategy BELOW shuffled mean**
- p-value: 0.510 — **FAIL** (need p < 0.05)
- Inversion Sharpe: -0.3280 (PASS — direction correct)
- Lead/lag bias: PASS

**Crisis performance:**
| Period | Return | vs SPY |
|--------|--------|--------|
| 2011 EU crisis | -9.8% | Worse |
| 2015-16 correction | +6.6% | Better |
| 2018 vol spike | +0.9% | Better |
| 2020 COVID | **-17.4%** | Worse |
| 2022 bear | **-11.8%** | Worse |

Root cause: VIX first crossing >25 in 2020 and 2022 was the START of sustained multi-month drawdowns, not a panic peak. The strategy catches knives in bear markets.

### Iteration 2: VIX declining from peak (refined entry)

Added filter: VIX > threshold AND VIX[today] < VIX[today - N days] (already declining).

**Grid search (11 configs, 500 shuffled permutations each):**
- Best: VIX>25, lb=5d, hold=20d → Sharpe=0.582, p=0.198
- **No configuration passed the shuffled returns test (all p > 0.05)**

### Iteration 3: Option SKEW as alternative sentiment signal

**^SKEW index (CBOE Skew):** measures tail risk demand in options market.

**Tested signals (500 perms each):**
| Signal | Sharpe | p-value | Passes? |
|--------|--------|---------|---------|
| SKEW>150, hold 10d | 0.785 | 0.036 | Borderline |
| SKEW>145, hold 10d | 0.485 | 0.284 | ✗ |
| VVIX>100, hold 20d | 0.853 | 0.166 | ✗ |
| VVIX z>1.5, hold 10d | 0.524 | 0.264 | ✗ |

SKEW>150 passed at p=0.036 but:
1. Only 220 active days out of 2966 total (~22 events in 12 years)
2. SPY buy-hold Sharpe was 0.862 → strategy underperforms buy-and-hold
3. Bonferroni adjustment (12 tests) → required p < 0.004 → FAILS

**SKEW>150 as Family 1 overlay filter:**
- Captures only 4% of Family 1 HYG→SPY trades
- No Sharpe improvement: HYG→SPY filtered Sharpe = 0.544 vs baseline 0.590
- LQD→SPY: marginal +0.027 lift at 3.8% trade capture (noise-level)

**Verdict: KILL**

### Final conclusion — Family 7

The VIX fear-entry mechanism is directionally real (inversion tests pass, economic intuition sound) but is not systematically tradeable because:
1. Event frequency too low (8-26 signals in 12 years depending on threshold)
2. Cannot distinguish panic peaks (good entries) from start-of-bear-market spikes (terrible entries)
3. Shuffled returns test fails — signal timing adds no value over random entry

**Potential future use:** Discretionary overlay — PM judgment in real-time during extreme panic events. Not a systematic rule.

---

## Next Research Priority

Family 2 (GLD/SLV mean reversion) had a prior scan result of Sharpe ~1.28. Needs full lifecycle.

See: [Alpha Hunting Framework](../governance/alpha-hunting-framework.md)
