# SOXX/QQQ Lead-Lag v3 Governance and Research Note

**Date:** 2026-04-01  
**Strategy slug:** `soxx-qqq-lead-lag-v3`  
**Predecessor strategy slug:** `soxx-qqq-lead-lag-v2`  
**Document type:** governance / successor research note  
**Status:** recommendation to open a new unfrozen research line; no v3 performance claims

## Recommendation

Create `soxx-qqq-lead-lag-v3` as a new successor research line and treat it as a **structural redesign**, not a parameter-retune continuation of v2.

The recommendation is to keep the same broad thesis — that semiconductor leadership can transmit into the broader Nasdaq complex — while changing the implementation emphasis from a single sharp trigger into a more controlled state-based rule set. The explicit objective is to address the failure pattern now visible across v1 and v2:

- preserve the encouraging out-of-sample and CPCV evidence that suggests the core idea is not random
- reduce max drawdown to within the 15% mandate limit
- improve perturbation stability beyond the current 2/5 result
- improve resilience at higher trading costs, especially at 2x cost
- avoid another narrow re-optimization around one lag or one threshold

This note does **not** claim that v3 already achieves those outcomes. It only records why a v3 line is justified and how it should be validated.

## Why v3 exists

v3 exists because the research sequence has now established three important facts at once:

1. **v1 was not promotable.**  
   The original frozen strategy retained some signal evidence, but robustness was insufficient and the implementation was too brittle for promotion.

2. **v2 improved some important evidence, but still failed the governance bar.**  
   v2 produced stronger CPCV and a passing walk-forward summary, which means the successor work was not pointless. However, the key promotion blockers were not closed.

3. **The remaining failures are structural, not obviously solved by another small threshold move.**  
   The current failure pattern is not “one parameter slightly off.” It is a combination of:
   - DSR still below threshold
   - perturbation stability still failing
   - drawdown now above mandate
   - 2x-cost Sharpe still near zero

That combination is exactly why v3 should not be framed as “v2, but with one more lag/threshold tweak.”

## What v1 failed on

The frozen original strategy `soxx-qqq-lead-lag` was paper-only and promotion-blocked. Its robustness artifact shows:

- **DSR failure:** `0.5675` vs required `>= 0.95`
- **Perturbation failure:** `2/5` stable vs required `>= 60%`
- **CPCV pass:** mean OOS Sharpe `0.7789`
- **Max drawdown pass:** `6.84%` vs threshold `< 15%`
- **Cost sensitivity weakness:** Sharpe falls from `0.607` at 1x costs to `0.153` at 2x costs and negative at 3x

Interpretation for governance purposes:

- v1 did show some evidence that the concept may contain a real effect, because CPCV was positive.
- But promotion was correctly blocked because the implementation depended too heavily on exact trigger geometry.
- The perturbation results were especially important: nearby changes in `lag_days` and `entry_threshold` materially changed outcomes, including a negative result at `lag_days=4`.
- The cost curve also suggested a fragile edge after realistic friction.

So v1’s problem was not that the thesis was certainly false. The problem was that the tradable specification was too brittle and too cost-sensitive to deserve advancement.

## What v2 failed on

The successor `soxx-qqq-lead-lag-v2` was a conservative attempt to improve the weak points seen in v1. It succeeded in some respects:

- **CPCV pass improved:** mean OOS Sharpe `1.1075`
- **Walk-forward passed:**  
  - mean OOS Sharpe `1.217112`
  - median OOS Sharpe `0.708903`
  - max OOS drawdown `0.029802`
- The existence of these passes is important because it means the SOXX->QQQ lead-lag idea still has live research value.

However, v2 still failed robustness and remains HOLD:

- **DSR failure:** `0.9080` vs required `>= 0.95`
- **Perturbation failure:** `2/5` stable
- **Max drawdown failure:** `17.76%` vs required `< 15%`
- **2x-cost Sharpe weakness:** `0.086`
- **3x-cost Sharpe negative:** `-0.539`

Interpretation:

- v2 improved statistical credibility relative to v1, but did not become promotion-safe.
- The design changes helped CPCV and walk-forward, yet they did not solve robustness in a governance-complete way.
- More importantly, the strategy now fails on a **portfolio-control dimension** as well: drawdown.
- Perturbation remains weak in the immediate neighborhood of the chosen settings, especially around lag and trigger behavior.
- This is evidence against another narrow retune. If a first conservative retune improved some diagnostics but left the same family of stability problems intact, the next step should be a design change in controls, not another tiny move from 6 to 7 days or from 3% to 2.5%.

## Why v3 should use structural controls

The evidence now supports a governance view that the **signal thesis may be real, but the execution wrapper is under-specified**.

In plain terms:

- v1 looked too dependent on one sharp threshold and immediate full exposure
- v2 moderated that idea, but still behaved too sharply under perturbation and still did not protect the downside enough
- therefore v3 should focus on **how exposure is earned, scaled, held, and removed**, not only on exactly where one threshold is set

### Structural controls are preferred because they target the observed failure pattern

The observed failures suggest four design problems:

1. **Drawdown is not sufficiently bounded by the current entry/exit logic.**  
   A mandate breach at `17.76%` says the strategy needs a stronger risk-shaping layer than a modest parameter shift.

2. **Perturbation sensitivity implies over-reliance on exact trigger placement.**  
   If nearby lag and threshold variants still destabilize outcomes, then the decision surface is too sharp.

3. **2x-cost Sharpe near zero implies the edge is too dependent on turnover or marginal signals.**  
   A better answer is to reduce low-conviction trading structurally, not just hope one new threshold does it.

4. **DSR remains below the required bar even after v2 improvements.**  
   This argues for reducing model fragility and multiple-testing style sensitivity by simplifying the effective state logic and making trade qualification more robust.

### What “structural controls” means for v3

For governance purposes, v3 should prefer controls such as:

- **exposure scaling rather than single-step full exposure**
- **explicit trend/regime filters** to avoid taking lead-lag signals into hostile broader conditions
- **cooldown or persistence requirements** so one isolated threshold cross does not immediately force a trade
- **clearer de-risking rules** when the leader impulse decays or when the follower weakens
- **state-based participation** that distinguishes high-conviction from marginal setups
- **trade-frequency reduction by design**, rather than only by raising thresholds

These are examples of design direction, not approved final parameters. The point is that v3 should smooth the strategy’s behavior and reduce dependence on one exact coordinate in parameter space.

## Governance position on v1 -> v2 -> v3

The appropriate governance reading is:

- **v1:** concept not dead, but implementation not robust enough
- **v2:** concept still worth researching, because CPCV and walk-forward improved, but still not promotable due to DSR, perturbation, drawdown, and cost sensitivity
- **v3:** justified only as a new unfrozen successor with a different control architecture

This means v3 should:

- use slug `soxx-qqq-lead-lag-v3`
- name `soxx-qqq-lead-lag-v2` as predecessor
- remain **unfrozen** until its own artifacts and evidence exist
- inherit **no pass status**
- make **no claims** based on v1 or v2 beyond the documented predecessor evidence

## Explicit validation plan for v3

v3 must earn advancement through a fresh validation chain. Because the rationale for v3 is structural redesign, validation should test whether the redesign closes the exact known gaps rather than merely preserving isolated Sharpe.

### 1. Canonical backtest validation
Required purpose: determine whether the redesign remains economically viable before robustness work.

Minimum questions:
- Does the redesign preserve positive economic edge after the standard cost model?
- Is trade count still sufficient to evaluate?
- Is turnover lower or at least more efficient than v2?
- Does max drawdown move back toward or below the 15% mandate line?

Governance note:
- A strong backtest alone is not enough for promotion.
- If backtest improves only by adding complexity while cost-adjusted efficiency worsens, v3 should not advance.

### 2. Robustness validation
This is the primary gate for v3 because v1 and v2 both failed here.

Required focus areas:
- **DSR must reach or exceed 0.95**
- **Perturbation stability must exceed the current 2/5 outcome and clear policy threshold**
- **Max drawdown must be below 15%**
- **2x-cost Sharpe must improve materially from v2’s 0.086**
- **CPCV must remain positive**

Recommended perturbation emphasis:
- vary any persistence / filter / scaling controls locally
- vary lag settings locally
- vary activation thresholds locally
- vary exposure-cap settings locally
- verify that acceptable behavior is not confined to a single narrow setting

Governance criterion:
- v3 should be rejected if it merely swaps one fragile threshold for one fragile filter.

### 3. Walk-forward validation
Because v2 walk-forward was encouraging, v3 must show that any new controls do not destroy out-of-sample behavior.

Required checks:
- mean OOS Sharpe > 0
- median OOS Sharpe > 0
- OOS drawdown comfortably below policy threshold
- no evidence that apparent improvement is concentrated in only one or two folds
- inspect whether new controls reduce catastrophic fold behavior without over-pruning the signal

Interpretation rule:
- walk-forward should be read as support, not as a substitute for robustness.

### 4. Cost-sensitivity validation
This should be elevated for v3 because cost fragility is a repeated weakness across versions.

Required comparisons:
- 1x, 1.5x, 2x, and 3x cost ladders
- change in Sharpe and turnover versus v2
- whether any trade-frequency reduction improves net efficiency rather than just shrinking activity

Success condition:
- v3 should show a materially healthier degradation profile than v2, especially at 2x cost.

### 5. Simplicity and governance validation
Because v3 is justified as a structural redesign, there is a risk of adding too many moving parts.

Required review:
- each new control must have a direct governance reason tied to a known v1/v2 failure
- controls should be interpretable and implementable
- do not add complexity that cannot be defended as reducing drawdown, perturbation sensitivity, or cost drag
- do not turn v3 into an unrestricted optimization exercise

A practical governance rule is:
> every added rule in v3 should map to a documented predecessor failure mode.

## Recommendation to the parent research flow

Approve creation of v3 research artifacts only under the following framing:

- v3 is a **successor hypothesis**, not a promotion candidate
- v3 is a **structural control redesign**
- v3 is intended to fix:
  - drawdown control
  - perturbation stability
  - cost resilience
- v3 must also preserve:
  - positive CPCV behavior
  - credible walk-forward behavior
- no freezing until the new artifacts are written and its own evidence exists

## What should not be claimed

Until v3 is actually tested, governance language should avoid saying any of the following:

- that v3 has solved v2’s drawdown problem
- that v3 has passed DSR
- that v3 has better perturbation stability
- that v3 is promotion-ready
- that v3 inherits v2 walk-forward success

Only the design intent may be claimed at this stage.

## Bottom line

The evidence supports continuing the SOXX->QQQ research line, but not by making another tiny parameter adjustment.

- v1 failed because the tradable implementation was too brittle and too cost-sensitive.
- v2 improved CPCV and walk-forward, but still failed DSR, perturbation stability, max drawdown, and 2x-cost robustness.
- Therefore v3 is justified only if it is built as a **new, unfrozen, structurally controlled successor** aimed at smoothing decision logic and reducing downside and turnover fragility.

That is the conservative governance path consistent with the current evidence.