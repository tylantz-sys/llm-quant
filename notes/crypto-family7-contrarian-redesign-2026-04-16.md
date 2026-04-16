# Crypto Family 7 Contrarian Redesign Memo

## Scope

This note proposes stronger **Family 7: sentiment contrarian** crypto hypotheses than a plain RSI setup, following:

- `docs/governance/alpha-hunting-framework.md`
- `docs/governance/quant-lifecycle.md`

This is a **research-prioritization memo only**. It does **not** create a mandate, hypothesis artifact, data contract, research spec, backtest result, robustness claim, or promotion recommendation. The goal is to define better Hunt-phase candidates for later lifecycle formalization if any cheap screen survives.

---

## Bottom Line

**Plain BTC RSI should not be the first Family 7 crypto candidate.**

It is too generic, too easy to over-scan, and too weakly tied to a distinct behavioral mechanism. RSI is a price transform, not sentiment data. In crypto, where trend persistence, reflexive leverage, and structural 24/7 trading often dominate, a simple "oversold/overbought" oscillator can easily become:

- a disguised short-horizon mean-reversion bet
- a parameter-mined threshold exercise
- a weak proxy for the thing Family 7 actually wants: **behavioral crowding and emotional exhaustion**

If Family 7 is supposed to add a genuinely different mechanism family to the portfolio, the first crypto candidates should focus on **forced positioning, crowd attention, retail panic/euphoria, or basis/liquidation stress** rather than on a standalone RSI threshold.

---

## Why Plain RSI Is the Wrong Starting Point

### 1. It does not uniquely identify sentiment

RSI only says recent price moved up or down enough to hit an arbitrary transform threshold. It does **not** directly measure:

- crowd positioning
- leverage stress
- liquidation imbalance
- retail attention
- panic versus exhaustion
- divergence between price and participation

That makes it a weak fit for Family 7 relative to richer contrarian mechanisms.

### 2. It overlaps too much with Family 2 mean reversion

A plain RSI rule is usually just a generic mean-reversion entry condition. That creates family blur:

- **Family 2** should own "price stretched away from equilibrium"
- **Family 7** should own "crowd behavior reached an emotional or positioning extreme"

If we start with RSI, we risk researching the wrong family under the wrong label.

### 3. It invites parameter sprawl immediately

RSI setups tempt endless tuning:

- lookback 2, 3, 5, 7, 14?
- threshold 20, 25, 30, 35?
- exit at 50, 55, 60?
- trade long only or both sides?
- apply trend filter or not?

That is exactly the type of design the lifecycle tries to suppress before a frozen spec exists.

### 4. Crypto often trends through RSI extremes

Crypto is especially prone to:

- reflexive upside melt-ups
- liquidation cascades
- persistent weekend trend continuation
- regime shifts where "oversold" gets more oversold

So RSI can trigger too early and repeatedly, with little evidence that the trigger itself captures the true exhaustion point.

---

## Redesign Principle for Crypto Family 7

The stronger Family 7 crypto hypotheses should satisfy all of these:

1. **Behavior-first mechanism**
   - The explanation should rely on crowd behavior, forced flow, or attention extremes.

2. **Minimal default parameters**
   - One default threshold, one lookback, one holding horizon where possible.

3. **Cheap existence test**
   - A single Hunt-phase scan should be enough to kill or advance the idea.

4. **Clear separation from Family 2 and Family 3**
   - Not just "price went too far" and not just "trend continuation."

5. **Portable to lifecycle artifacts**
   - If a candidate survives, it should convert cleanly into mandate/hypothesis/data-contract/research-spec documents.

---

## Ranked Candidate Hypotheses

## 1. Perpetual Funding-Rate Washout Reversal

### Mechanism statement

> I expect extreme negative perpetual funding in BTC or ETH to predict short-horizon positive returns because crowded short positioning and forced hedging during panic create temporary price overshoots that mean-revert once liquidation pressure exhausts.

This is stronger than RSI because it targets **positioning stress**, not just price shape.

### Why this is better than plain RSI

- funding is closer to **crowd positioning**
- extreme negative funding directly encodes one-sided bearishness
- the reversal story comes from **forced flow exhaustion**, not just "price looks oversold"

### Minimal default parameters

- instrument: `BTC-USD` first, optionally `ETH-USD` second
- signal variable: daily aggregated perpetual funding rate
- default extreme threshold: bottom 5% of historical daily funding observations
- entry timing: next daily bar after extreme funding day
- holding horizon: 1 day, then 3 days as the nearest robustness sniff
- side: long only

Keep it this narrow at Hunt stage.

### Cheap-screen logic

Run one existence test:

- long BTC next day when funding is in the worst 5% tail
- compare versus unconditional next-day BTC return
- require:
  - Sharpe or t-stat equivalent clearly positive
  - enough events across sample
  - no single regime dominating all gains

Then a 3-point sniff:

- tail at 4%, 5%, 6%
- holding horizon 1 day only

If best result is much better than adjacent thresholds, kill as fragile.

### Kill criteria

Kill if any of the following show up in the cheap scan:

- fewer than roughly 25-30 signal events over sample
- effect only exists in one crash regime
- adjacent thresholds flip sign or collapse sharply
- reversal disappears after one-bar lag discipline

### Data needs if advanced later

- daily BTC/ETH prices
- daily or end-of-day aggregated perp funding from a stable source
- explicit anti-lookahead handling for funding timestamps

---

## 2. Liquidation Spike Exhaustion Rebound

### Mechanism statement

> I expect unusually large long-liquidation or total-liquidation spikes to predict short-horizon rebound returns because forced deleveraging is mechanically inelastic flow that overshoots fair value and leaves fewer marginal sellers immediately afterward.

This is a cleaner contrarian mechanism than RSI because it ties directly to **forced selling**.

### Why this is better than plain RSI

- liquidation data reflects actual stress, not inferred stress
- price can be oversold without forced flow; liquidation spikes identify the more meaningful subset
- the mechanism is explicit: seller exhaustion after leverage flush

### Minimal default parameters

- instrument: `BTC-USD`
- signal variable: daily notional liquidations, preferably long liquidations for long-rebound hypothesis
- default threshold: top 5% of liquidation days
- confirmation filter: same-day BTC return negative
- entry timing: next daily bar
- holding horizon: 1 day

The confirmation filter should stay minimal: only require that the liquidation spike coincides with a down day.

### Cheap-screen logic

Run the narrowest test first:

- identify top 5% long-liquidation days with BTC down on the day
- buy next day's close-to-close or open-to-close depending data contract later
- compare to unconditional next-day BTC return

Nearby sniff:

- threshold 4%, 5%, 6%
- with and without the same-day negative-return confirmation

If the whole signal disappears when removing the return confirmation, that may still be acceptable; if it only works in one exact threshold, kill it.

### Kill criteria

- too few observations
- effect vanishes outside a single crash cluster
- large dependence on one exchange's liquidation feed
- no asymmetry between extreme liquidation and ordinary down days

### Data needs if advanced later

- BTC daily prices
- exchange-aggregated liquidation series
- timestamp normalization across venues

---

## 3. Attention Crash Divergence Reversal

### Mechanism statement

> I expect sharp price drawdowns accompanied by a collapse in public attention growth to predict short-horizon rebounds because panic selling that fails to sustain broad incremental attention often reflects late-stage exhaustion rather than the start of durable new information.

This aims at **attention exhaustion**, which is much closer to Family 7 than RSI.

### Why this is better than plain RSI

- separates "price down with everyone still piling in" from "price down and attention already fading"
- uses an explicit behavioral channel: diminishing marginal attention after panic impulse
- may help avoid buying every oversold dip in persistent bearish trends

### Minimal default parameters

- instrument: `BTC-USD`
- attention proxy: Google Trends or equivalent search-interest index for `Bitcoin`
- price condition: 3-day BTC return in bottom decile
- attention condition: search interest below its own 4-week average or decelerating versus prior spike
- entry timing: next day
- holding horizon: 3 days

This is slightly more complex than the first two ideas, so it ranks below them.

### Cheap-screen logic

Test one simple form only:

- identify 3-day BTC drawdowns in bottom decile
- among those, separate days with weak/fading attention versus elevated/rising attention
- check whether weak/fading-attention drawdowns have better forward 3-day rebound returns

The key Hunt question is not absolute performance alone, but whether the attention split improves the conditional rebound quality.

### Kill criteria

- attention data too coarse or revised
- no meaningful difference between fading-attention and rising-attention selloffs
- signal count too low for daily research
- effect obviously explained by one-off event periods only

### Data needs if advanced later

- BTC daily prices
- search-interest data at a reproducible sampling frequency
- documented handling for data revisions/sampling instability

---

## 4. Basis Dislocation Mean-Reversion After Panic

### Mechanism statement

> I expect deeply negative crypto futures basis to predict short-horizon spot rebounds because extreme discounting reflects urgent demand for downside hedging or deleveraging, which tends to mean-revert once panic hedges and forced unwinds clear.

This is conceptually adjacent to funding, but not identical:

- funding = ongoing positioning pressure in perpetuals
- basis = term-structure dislocation between futures and spot

That distinction is enough to justify testing later, but it should rank behind funding because funding is usually simpler and more continuously available.

### Why this is better than plain RSI

- directly measures stress in derivatives pricing
- links to hedging panic and leverage unwind
- more structurally tied to contrarian crowding than a raw price oscillator

### Minimal default parameters

- instrument: `BTC-USD`
- signal variable: front futures annualized basis versus spot
- default threshold: bottom 5% of basis observations
- entry timing: next day
- holding horizon: 1 to 3 days
- side: long only

### Cheap-screen logic

- buy after basis enters worst 5% tail
- compare next-day and 3-day spot returns to unconditional baseline
- sniff thresholds at 4%, 5%, 6%

### Kill criteria

- basis series too discontinuous or exchange-specific
- signal just duplicates funding with no cleaner behavior
- event count too sparse
- nearby thresholds unstable

### Data needs if advanced later

- spot BTC daily prices
- reproducible futures basis series
- roll/timestamp documentation

---

## Suggested Priority Order

1. **Perpetual funding-rate washout reversal**
   - best combination of mechanism clarity, sentiment relevance, and cheap testability

2. **Liquidation spike exhaustion rebound**
   - very strong mechanism if data quality is good

3. **Basis dislocation mean-reversion after panic**
   - good structure, but may overlap with funding and require messier data handling

4. **Attention crash divergence reversal**
   - behaviorally appealing but likely noisier and more fragile due to alt-data construction

---

## Hunt-Phase Default Workflow

Per the alpha-hunting framework, these should be screened cheaply and killed fast.

### Step 1: Economic mechanism test

Before code, require a one-sentence explanation that does not rely on "it worked before."

Pass examples:

- "extreme negative funding reflects crowded shorts likely to mean-revert after forced flow exhausts"
- "liquidation spikes indicate seller exhaustion from leverage unwind"

Fail example:

- "BTC gets oversold and usually bounces"

### Step 2: One-parameter existence test

For each candidate:

- one threshold
- one holding horizon
- one asset first (`BTC-USD`)
- one side first (long only)

Sharpe below the framework smell-test bar or too few trades: kill.

### Step 3: Not-obviously-broken test

Check immediately:

- enough events
- not all profits from one crash month
- time in market low enough to indicate real timing
- no lookahead in derivative or attention timestamps

### Step 4: Robustness sniff

Use only adjacent settings:

- 4%, 5%, 6% threshold tails
- 1-day versus 3-day hold only where justified

If best versus worst is too far apart, kill.

---

## What Should Happen If One Survives

If any candidate survives Hunt phase, the next step is **not** ad hoc backtest expansion. It should enter the documented lifecycle:

1. `mandate.yaml`
2. `hypothesis.yaml`
3. `data-contract.yaml`
4. `research-spec.yaml`

Only after the research spec is frozen should formal backtests start, with:

- explicit anti-lookahead timestamp policy
- append-only experiment logging
- DSR-aware trial discipline
- shuffled signal tests
- regime splits
- inversion checks where applicable
- CPCV / perturbation / cost review if the idea reaches robustness stage

Because these are rare-event contrarian setups, sample size and timestamp integrity will matter more than cosmetic backtest quality.

---

## Final Recommendation

Do **not** spend the next Family 7 cycle on plain BTC RSI.

The stronger crypto contrarian shortlist is:

1. **Perpetual funding-rate washout reversal**
2. **Liquidation spike exhaustion rebound**
3. **Basis dislocation mean-reversion after panic**
4. **Attention crash divergence reversal**

These are better because they are:

- more behaviorally specific
- more governance-aligned with Family 7
- less likely to blur into generic mean reversion
- easier to justify with a real mechanism before seeing results

If none of these survive a cheap Hunt-phase existence test, then the honest conclusion should be that **crypto Family 7 remains unproven**, not that RSI needs more tuning.