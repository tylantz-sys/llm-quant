# Crypto Family 2 mean-reversion shortlist: SOL/BTC vs ETH/SOL

Date: 2026-04-16  
Owner: branch3-crypto-family2-shortlist  
Scope: Hunt-phase roadmap note only. No backtest claims, no fabricated results, no lifecycle artifacts created beyond this note.

## Why this note exists

Per `docs/governance/alpha-hunting-framework.md`, Family 2 mean reversion is the next priority because it adds a mechanism family that is more orthogonal to the repo's credit-led information-flow cluster. Per `docs/governance/universe-expansion-plan.md`, the approved crypto expansion universe already includes `BTC`, `ETH`, and `SOL`, and explicitly lists both `SOL/BTC` and `ETH/SOL` as candidate relationships.

Existing repo pattern review suggests two useful anchors:

- `eth-btc-ratio-mean-reversion-*` shows the crypto ratio family has already been researched deeply enough to justify disciplined, parameter-light successor selection rather than random pair sprawl.
- `gold-silver-ratio-mr` shows the expected framing for a mean-reversion hypothesis: shared structural drivers, temporary relative-value dislocations, simple z-score/Bollinger screens, and explicit falsification criteria.

This note is therefore a ranked shortlist for the next Hunt-phase crypto Family 2 screens after ETH/BTC.

## Ranking

### 1) SOL/BTC — test first

**Why rank it first**
- Cleanest extension of the existing ETH/BTC relative-value logic.
- BTC remains the dominant crypto benchmark/risk anchor, while SOL is a higher-beta smart-contract asset that can overshoot BTC during risk-on bursts and underperform sharply during de-risking.
- Likely to produce larger ratio dislocations than ETH/BTC, which is useful for a cheap Hunt-phase existence test.
- More aligned with the universe plan's Tier 1 treatment of `SOL/BTC` than `ETH/SOL`.

**Main risk**
- SOL may not behave as a stable "paired cousin" to BTC across the full sample. If the ratio is mostly trend plus regime breaks, this is not a mean-reversion candidate.

### 2) ETH/SOL — test second

**Why rank it second**
- Strong within-sector relationship: both are large smart-contract layer-1 ecosystems, so the pair has a more direct substitution narrative than BTC vs alt.
- Could capture relative rotation between "quality/liquidity incumbent" (ETH) and "higher-beta challenger" (SOL).
- If it works, it may be a purer crypto-relative-value signal than BTC-anchored pairs.

**Why not first**
- Higher narrative fragility than SOL/BTC because the relationship can be dominated by secular adoption share shifts rather than short-horizon reversion.
- More exposed to structural winner/loser regime changes, making the mean assumption less defensible.
- Universe plan places `ETH/SOL` after `SOL/BTC`.

## Minimal defensible hypotheses

## Candidate A: SOL/BTC

**One-sentence mechanism**
When SOL materially outperforms or underperforms BTC over a short rolling window, the relative move often overshoots because SOL carries higher beta and more reflexive retail/speculative flow than BTC, creating temporary ratio dislocations that partially mean-revert.

**Minimal defensible hypothesis**
The `SOL/BTC` ratio exhibits tradeable short-horizon mean reversion after statistically large deviations from its rolling mean, because BTC acts as the sector anchor while SOL experiences larger temporary overshoots during risk-on and risk-off bursts.

**What would count as success in Hunt**
- One default parameter set clears the Family 2 existence bar from the framework:
  - Sharpe > 0.6
  - enough trades for credibility
  - no obviously broken drawdown/time-in-market profile
- Nearby parameter settings are directionally similar rather than one isolated spike.

## Candidate B: ETH/SOL

**One-sentence mechanism**
When ETH meaningfully lags or leads SOL over a short horizon, part of the move can reflect transient crowd rotation between two closely related layer-1 ecosystems rather than durable fundamental repricing, allowing the ratio to mean-revert.

**Minimal defensible hypothesis**
The `ETH/SOL` ratio shows short-horizon mean reversion after extreme rolling deviations because capital rotates between two substitute smart-contract ecosystems in an overshooting way before relative valuation normalizes.

**What would count as success in Hunt**
- Same baseline existence bar:
  - Sharpe > 0.6
  - non-trivial trade count
  - acceptable drawdown
  - no extreme parameter brittleness

## Cheap Hunt-phase screens

These are intentionally simple and should be run before any full lifecycle artifact package is created.

### Common screen design for both pairs

Use the cheapest repo-consistent default first:

1. Build daily close ratio:
   - `SOL-USD / BTC-USD`
   - `ETH-USD / SOL-USD`

2. Compute one rolling deviation measure:
   - 20-day rolling mean and standard deviation
   - z-score of the ratio

3. Default entry/exit template:
   - enter when `|z| >= 2.0`
   - exit when `|z| <= 0.5` or `|z| <= 1.0`
   - rebalance daily
   - one-bar fill delay in later formal specs; for raw Hunt scan, still avoid same-bar lookahead in interpretation

4. Position expression:
   - long the laggard, short the leader in ratio terms if the backtest framework supports paired legs
   - if only single-leg proxy screening is available, use a clearly labeled approximation and do not treat proxy results as promotion-grade evidence

5. Test only 3 nearby variants for robustness sniff:
   - window: 20, 30
   - entry z: 2.0, 2.5
   - exit z: 0.5, 1.0  
   Keep the combinatorics small; the goal is smell-test robustness, not optimization.

### SOL/BTC cheap screen

**Default first shot**
- window: 20
- entry z: 2.0
- exit z: 0.5

**Reason**
This is the most direct analogue to a basic Bollinger-style ratio reversion test and maximizes comparability to prior ETH/BTC and GLD/SLV style scans.

**Helpful diagnostic splits**
- pre-2023 vs post-2023
- high-crypto-mania months vs calmer periods
- long-SOL/short-BTC trades vs long-BTC/short-SOL trades separately

The side split matters because the pair may only "revert" after SOL melt-ups but not after SOL collapses.

### ETH/SOL cheap screen

**Default first shot**
- window: 20
- entry z: 2.0
- exit z: 0.5

**Reason**
Keep identical defaults initially so ranking reflects the relationship, not parameter differences.

**Helpful diagnostic splits**
- pre-SOL-breakout era vs post-breakout era
- long-ETH/short-SOL and long-SOL/short-ETH separately
- trending bull periods vs choppy/range periods

If one side dominates all apparent profits, treat that as a warning that the "mean reversion" may really be one-directional structural drift.

## Fast kill criteria

Apply the framework's kill chain strictly.

### Kill immediately if the mechanism fails the smell test
Kill if the best explanation becomes:
- "it used to work historically"
- "alts are correlated"
- "crypto is noisy so maybe it snaps back"

That is not enough. The mechanism must be specific about anchor asset vs higher-beta overshoot or substitution/rotation overshoot.

### Kill on existence test failure
Kill if the default parameter set does not clear:
- Sharpe > 0.6
- sufficient trades over the test window
- no obviously excessive drawdown
- no near-always-in-market behavior

### Kill on fragility
Kill if 3 nearby parameter sets show a best/worst outcome ratio above roughly 2x, consistent with the framework's robustness sniff rule.

### Pair-specific kill criteria

#### SOL/BTC
Kill or demote if:
- results only come from a narrow SOL mania pocket
- one trade direction works and the opposite direction is flat-to-positive instead of clearly worse
- the ratio behaves like a regime-trending series with no stable reversion center

#### ETH/SOL
Kill or demote if:
- apparent profits depend almost entirely on one secular leadership era
- the relationship looks like slow structural repricing between ecosystems rather than short-horizon overshoot/reversion
- turnover is too low after requiring truly extreme deviations

## Preferred ranking interpretation after Hunt

### Promote SOL/BTC first if:
- it passes the basic existence test
- both trade directions are at least plausible
- results are not concentrated in one speculative episode
- nearby settings remain qualitatively similar

### Promote ETH/SOL first instead only if:
- SOL/BTC fails the regime-stability smell test
- ETH/SOL shows cleaner two-sided reversion and less dependence on one crypto cycle
- the mechanism reads as substitution-driven relative value rather than trend masquerading as reversion

## Lifecycle discipline if a candidate survives

Per `docs/governance/quant-lifecycle.md`, surviving Hunt is not enough for robustness or paper. If either candidate passes the cheap screen, create only the normal pre-backtest artifact stack first:

1. `mandate.yaml`
2. `hypothesis.yaml`
3. `data-contract.yaml`
4. `research-spec.yaml`

Important constraints for those future artifacts:
- keep the spec parameter-light
- freeze the research spec before any formal backtest
- include explicit crypto regime-risk language
- document anti-lookahead execution assumptions
- define falsification criteria before reviewing results
- do not create robustness, paper-trading, or promotion artifacts unless the frozen-spec backtest earns that progression

## Recommended next execution order

1. Run Hunt-phase `SOL/BTC` scan with one default and three nearby variants.
2. If `SOL/BTC` fails quickly, run identical first-pass `ETH/SOL` scan.
3. If one survives, create a new strategy slug with only:
   - `mandate.yaml`
   - `hypothesis.yaml`
   - `data-contract.yaml`
   - `research-spec.yaml`
4. Only after spec freeze, run formal backtests and then the normal robustness ladder.

## Bottom line

- **First priority:** `SOL/BTC`
- **Second priority:** `ETH/SOL`

`SOL/BTC` is the better next Family 2 exploration because it is the cleanest BTC-anchored extension of the existing crypto ratio research and most likely to show detectable overshoot/reversion if the mechanism is real. `ETH/SOL` remains worth screening, but it should be treated as a more regime-fragile substitute/rotation hypothesis rather than the default first successor.