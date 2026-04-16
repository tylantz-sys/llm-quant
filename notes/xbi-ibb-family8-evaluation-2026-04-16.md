# XBI -> IBB Family 8 Evaluation

## Scope

Assess whether `XBI -> IBB` can be framed as a repo-consistent **Family 8: non-credit cross-market lead-lag** mechanism hypothesis under:

- `docs/governance/alpha-hunting-framework.md`
- `docs/governance/universe-expansion-plan.md`
- `docs/governance/quant-lifecycle.md`

This is an analytical note only, not a promotion artifact or implementation recommendation.

---

## Bottom Line

`XBI -> IBB` is **admissible but weak** as a Family 8 candidate.

It can be framed as a non-credit information-flow hypothesis if the story is:

- `XBI` represents equal-weight / smaller-cap / higher-beta biotech breadth
- `IBB` represents more cap-weighted, large-cap biotech exposure
- changes in biotech risk appetite may appear in `XBI` first and diffuse into `IBB` with a short lag

That is a legitimate mechanism statement, but it is materially weaker than the repo's passing `SOXX -> QQQ` example because `XBI` and `IBB` are extremely close economic exposures. The distinction comes mostly from index construction, not from a clearly upstream market leading a downstream market.

My recommendation is **weak no-go as a priority candidate**, but **maybe worth a cheap smell-test scan** if the goal is to see whether IBB can survive only inside Family 8.

---

## Mechanism Quality

### Best defensible mechanism

The strongest non-hand-wavy explanation is:

> Equal-weight / smaller-cap biotech (`XBI`) may react faster to changes in biotech breadth and speculative risk appetite than cap-weight biotech (`IBB`), so unusually large `XBI` moves may contain next-day information for `IBB`.

This is better than saying "XBI moves first because it historically does." It gives an actual causal channel tied to:

- composition differences
- breadth versus mega-cap concentration
- differential sensitivity to speculative biotech flows

### Why the mechanism is still weak

Relative to the governance standard, the mechanism is only **moderately credible** because:

- both ETFs are biotech-sector wrappers
- both are driven by very similar underlying sector news
- the hypothesized lead is mostly a weighting / beta-composition effect rather than a clearly separate market transmitting information

That makes the mechanism plausible enough to test, but not strong enough to prioritize without very clean empirical support.

---

## Uniqueness vs Current Portfolio

### Positives

- It is **non-credit**, so it is more useful than adding another credit-equity lead-lag variant.
- It fits the framework's request to expand beyond the current credit-heavy concentration.

### Negatives

- It is still an **ETF information-flow** trade and likely shares portfolio behavior with other equity risk-appetite signals.
- Because `XBI` and `IBB` are very close relatives, any strategy built from this pair is likely to have high embedded exposure to biotech/equity sector risk.
- Even if the signal works statistically, it may fail the framework's practical diversification tests:
  - correlation to the existing portfolio may still be too high
  - marginal portfolio Sharpe lift may be too small

So this is more unique than Family 1, but probably not unique enough to become a high-value portfolio component unless the edge is unusually strong.

---

## Likely Fit with Family 8

### Why it fits

The alpha-hunting framework defines Family 8 as **Non-Credit Cross-Market Lead-Lag**, with `SOXX -> QQQ` as the example. `XBI -> IBB` can fit this family if the hypothesis is explicitly framed as:

- information flow
- leader/follower structure
- non-credit
- short-horizon diffusion

### Why the fit is weaker than the example

`SOXX -> QQQ` has a clearer upstream/downstream logic:

- semiconductors often lead broader technology and growth sentiment

`XBI -> IBB` is much closer to:

- one biotech index construction leading another biotech index construction

That means it belongs in Family 8 in form, but likely sits at the weaker end of that family in substance.

---

## Minimal Defensible Hypothesis

A minimal hypothesis consistent with repo governance would be:

> I expect large daily moves in `XBI` to predict next-day directional continuation in `IBB` because `XBI`'s equal-weight and smaller-cap composition reacts faster to changes in biotech breadth and speculative risk appetite than `IBB`'s cap-weight construction.

Keep the first test narrow:

- daily frequency
- one-bar lag
- threshold based on large absolute `XBI` move or abnormal `XBI` move
- next-day `IBB` directional response
- minimal free parameters

Avoid:

- many thresholds
- many holding windows
- complex filters
- regime-tuned variants before a frozen spec

Because the mechanism is already somewhat weak, parameter sprawl would make the idea look data-mined immediately.

---

## Data and Validation Needed

Per the governance docs, this should only advance if it survives a cheap screen and then the normal lifecycle.

### Minimum data

- daily adjusted OHLCV for `XBI`
- daily adjusted OHLCV for `IBB`
- long enough sample to cover multiple biotech and equity regimes

### Specific validation questions

1. **Asymmetry check**
   - Does `XBI -> IBB` exist more strongly than `IBB -> XBI`?
   - If not, there may be no real leader/follower structure.

2. **Regime split**
   - Does it work across at least 2 of 3 distinct regimes?
   - Biotech mania, biotech drawdown, broader market risk-on/risk-off periods should be separated.

3. **Signal concentration**
   - Does the effect show up specifically after unusual `XBI` moves, or is this just market drift?

4. **Alternative lens**
   - Can the same mechanism be seen through a related but not identical expression?
   - This matters because the framework explicitly warns against one-pair/one-mechanism artifacts.

### Required governance tests if the cheap scan passes

From the framework and lifecycle:

- one default-parameter existence test
- shuffled signal test
- mechanism inversion test
- perturbation stability
- out-of-sample holdout discipline
- CPCV
- DSR / PBO discipline once experiments begin
- cost sensitivity
- portfolio correlation and marginal SR contribution review

This is important because even a "passing" backtest is not enough if the strategy adds little diversification value.

---

## Honest Recommendation

## Recommendation: Weak No-Go as Priority Research

### Why not a full no-go

- It is not nonsense.
- It can be stated in a mechanism-consistent way.
- It is more aligned with repo priorities than another credit-equity variant.

### Why still mostly no-go

- The mechanism is thin compared with stronger Family 8 structures.
- The pair is economically too close.
- It is unlikely to score well on the "genuinely orthogonal" spirit of the alpha framework.
- It likely ranks below:
  - Family 2 mean reversion opportunities
  - stronger Family 8 upstream/downstream relationships
  - broader non-equity mechanism families like vol regime or sentiment contrarian

### Practical verdict

- **As a cheap screen:** yes, maybe
- **As a serious next-priority candidate:** no
- **As the main way to "save" IBB in the repo:** probably not

---

## Final Assessment

`XBI -> IBB` can survive governance review only as a **narrow, explicitly framed Family 8 information-diffusion hypothesis**:

- leader: equal-weight / smaller-cap biotech breadth (`XBI`)
- follower: cap-weight biotech (`IBB`)
- horizon: short, likely next-day

But the mechanism is only moderately credible and likely not distinct enough to justify high-priority research attention unless the empirical evidence is unexpectedly clean, asymmetric, and diversifying.

So the honest answer is:

- **repo-consistent:** yes
- **compelling:** no
- **priority go decision:** no