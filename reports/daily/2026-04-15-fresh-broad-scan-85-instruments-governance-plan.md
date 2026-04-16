# Fresh Broad Scan 85 Instruments — Governance Plan
Date: 2026-04-15  
Analysis type: `fresh_broad_scan_85_instruments`  
Status: `research_only`  
Clearance snapshot: `HALT`  
Primary blocker: `alpha_decay — rolling Sharpe 0.01 vs full-period 0.54. NO new positions. Sells only.`

## Purpose
Convert the provided JSON scan into a governance-first, auditable decision artifact aligned with repository conventions:

- preserve research evidence
- keep research separate from execution
- respect current halt status
- prevent off-universe candidates from being treated as immediately tradable
- provide a primary branch and fallback branches if the first path is blocked

## Source summary
The provided scan states:

- 85 instruments scanned
- 2 new setups identified: `IBB`, `EWY`
- previously identified setups still valid: `GLD`, `XLI`, `XLP`, `XLU`, `SOXX`
- long disqualifications noted for leveraged and overextended names
- short-vol / inverse-volatility style idea noted via `UVXY`, but flagged as operationally complex
- current governance status is `HALT`, so no new buys are permitted

## Governance interpretation
Under the current halt:

- all proposed `buy` actions are downgraded to `queued_research_candidate`
- no new position may be opened
- all price levels, target ladders, and stop levels should be treated as research-time snapshots rather than executable instructions
- any future promotion requires both governance clearance and freshness revalidation

## Candidate classification

### Existing confirmed names
These remain part of the research backdrop, but are not executable while halt persists:

- `GLD`
- `XLI`
- `XLP`
- `XLU`
- `SOXX`

### New candidates
#### IBB
- Rank: 1
- Asset class: US Sector ETF — Biotech / Healthcare
- Conviction: high
- Current classification: `candidate_pending_governance_and_mandate`
- Blocking conditions:
  - current system-wide `HALT`
  - outside core 39-asset universe
  - mandate amendment or Track B expansion note required before allocation
- Research rationale retained:
  - above-average volume confirmation
  - fresh MACD cross
  - RSI below overbought threshold
  - bullish multi-SMA alignment
  - near 20-day high breakout zone

#### EWY
- Rank: 2
- Asset class: International Equity ETF — South Korea
- Conviction: medium-high
- Current classification: `candidate_pending_governance_and_mandate`
- Blocking conditions:
  - current system-wide `HALT`
  - outside core universe
  - mandate amendment or Track B expansion note required before allocation
- Research rationale retained:
  - strongest 60-day momentum in scan
  - macro tailwind from USD weakness and semiconductor cycle
  - fresh MACD bull cross
  - strong long-term trend structure
- Additional caution:
  - geopolitical risk
  - wider ATR / wider stop requirement
  - short-term extension above SMA20 suggests better handled with refreshed entry logic later

### Watchlist only
#### ARKG
- Current classification: `watch_only`
- Reason:
  - above Bollinger upper band
  - weaker 60-day structural trend than IBB
  - not suitable to chase at current levels
- Required future trigger:
  - pullback back within bands with technical confirmation

### Complex / out-of-scope candidate
#### UVXY
- Current classification: `research_only_complex_short`
- Reason:
  - short-vol structure is operationally complex
  - margin / roll-decay / event-spike risk exceeds normal simple ETF treatment
  - not appropriate for Track A promotion
- Use:
  - optional Track B / Track D discussion only

## Primary branch
## Branch A — Governance-first promotion branch
This is the recommended path.

### Objective
Preserve the scan, but prevent any accidental transition from research commentary to executable trade intent.

### Steps
1. Preserve the scan as immutable research evidence.
2. Treat all buys as queued candidates, not approvals.
3. Investigate the halt root cause:
   - alpha decay
   - rolling Sharpe collapse versus full-period Sharpe
4. Require governance clearance before any promotion.
5. Require mandate review for off-universe symbols:
   - `IBB`
   - `EWY`
6. On clearance, re-run freshness validation before using any price-specific fields:
   - entry
   - stop
   - target ladder
   - ATR-derived distances
   - Bollinger thresholds
7. Only after both governance and mandate checks pass, convert a candidate into a properly documented next-step artifact.

### Result if successful
- `GLD`, `XLI`, `XLP`, `XLU`, and other already-supported candidates can be re-evaluated for post-halt action under current governance.
- `IBB` and `EWY` can move from `candidate_pending_governance_and_mandate` to formal universe-expansion review or approved candidate state, depending on policy.

## Fallback branches

## Branch B — Research-only archive branch
Use this if halt resolution is delayed or governance review is blocked.

### Objective
Make progress without creating any execution pressure.

### Steps
1. Archive the scan as a dated research artifact.
2. Normalize outcomes:
   - `IBB` = pending universe review
   - `EWY` = pending universe review
   - `ARKG` = watch only
   - `UVXY` = complex research only
3. Stop before any mandate or execution-path work.

### Result
- evidence preserved
- no risk of overstepping halt
- useful for future reactivation

## Branch C — Universe-expansion branch
Use this if the question becomes “should the framework support `IBB` and `EWY` at all?”

### Objective
Separate symbol-admission review from immediate trading interest.

### Steps
1. Evaluate `IBB` and `EWY` as proposed universe additions.
2. For each symbol, require:
   - mandate fit
   - liquidity and data support
   - implementation supportability
   - diversification benefit
   - risk model compatibility
3. Produce one of:
   - admit to expanded universe
   - keep as off-universe research only
   - reject

### Result
A durable decision on symbol support, independent of the current scan timing.

## Branch D — In-universe-only salvage branch
Use this if off-universe additions are not allowed.

### Objective
Retain useful in-universe research while discarding unsupported additions from the actionable path.

### Steps
1. Keep `IBB` and `EWY` as informational only.
2. Restrict any future actionable follow-up to approved universe names.
3. Rebuild any future portfolio idea using only currently supported symbols.

### Result
The scan still contributes value even if universe expansion is denied.

## Branch E — Anti-staleness refresh branch
Use this if halt duration makes current price levels unreliable.

### Objective
Keep the theses, expire the stale levels.

### Steps
1. Preserve only durable narrative and structural observations:
   - biotech rotation thesis
   - USD weakness thesis
   - Korea semi-cycle thesis
   - diversification logic
2. Expire all point-in-time signal fields after the freshness window:
   - entry prices
   - stops
   - target ladders
   - ATR levels
   - Bollinger-based triggers
3. On future review, rescan the same names and regenerate technical fields from fresh data.

### Result
No stale levels survive into later decisions.

## Practical interpretation of current JSON
At the current time, the JSON supports the following conclusions:

- `IBB` is promising but not tradable now
- `EWY` is promising but not tradable now
- `ARKG` belongs on watch, not in execution
- `UVXY` is conceptually interesting but not appropriate for standard promotion
- existing names may remain research-valid, but all buys remain blocked by `HALT`

## Suggested normalized status table

| Symbol | Current status | Blocking reason | Next valid action |
|---|---|---|---|
| GLD | research-valid, non-executable | halt | re-check on halt clearance |
| XLI | research-valid, non-executable | halt | re-check on halt clearance |
| XLP | research-valid, non-executable | halt | re-check on halt clearance |
| XLU | research-valid, non-executable | halt | re-check on halt clearance |
| SOXX | research-valid, non-executable | halt | verify mandate support, then re-check |
| IBB | candidate pending governance and mandate | halt + off-universe | mandate review + fresh revalidation |
| EWY | candidate pending governance and mandate | halt + off-universe | mandate review + fresh revalidation |
| ARKG | watch only | overextended / lower structural quality | wait for pullback trigger |
| UVXY | research only complex short | strategy complexity / mandate mismatch | expansion discussion only |

## Required follow-ups
1. Diagnose the alpha-decay halt.
2. Confirm whether `SOXX` is in the currently supported mandate scope.
3. Decide whether `IBB` and `EWY` should enter a formal universe-expansion workflow.
4. If halt clears later, refresh all technical levels before acting on any thesis.
5. Keep the provided JSON as research evidence, not execution authority.

## Final recommendation
Follow **Branch A** first. If halted governance remains unresolved, fall back to **Branch B**. If the real decision is about adding `IBB` and `EWY` to supported coverage, shift to **Branch C**. If off-universe additions are denied, use **Branch D**. If delay makes current levels stale, enforce **Branch E** and regenerate the technical layer from fresh data.

## Traceability note
This artifact is derived from a user-provided JSON scan dated `2026-04-15` and intentionally reframes all trade-oriented language into governance-safe research language because the governing status in the source material is `HALT`.
