# Research/Review Documentation Synthesis — 2026-03-31

Scope reviewed:
- `docs/research/implementation-gaps.md`
- `docs/research/extreme-sharpe-playbook.md`
- `docs/research/institutional-quant-guide.md`
- `docs/research/library-evaluations-2026-03-30.md`
- `docs/research/reference-repo-findings-2026-03-30.md`
- `docs/research/track-d-review-2026-03-30.md`
- `docs/research/niche-arb-research.md`
- `docs/research/kalshi-arb-scan-2026-03-30.md`
- `docs/reviews/arb-module-review-2026-03-30.md`
- `reports/profit-taking-behavior-review.md`

## Executive takeaways

The research docs already contain a fairly coherent answer to “how do we get more profitable trades without dropping controls?” The strongest repeated theme is **not** “loosen risk,” but rather:

1. **Add more genuinely distinct alpha families**, because current gains are too concentrated in correlated credit/equity lead-lag ideas.
2. **Improve portfolio construction and dynamic sizing** via HRP/FDM/vol targeting instead of equal/static weights.
3. **Operationalize ideas already documented** in Track D, niche arbitrage, factor IC analysis, and rolling/walk-forward task generation.
4. **Expand validation, especially scenario testing and walk-forward robustness**, before promoting any high-return or higher-turnover ideas.

The docs also repeatedly warn that backtests with very high Sharpe/CAGR are suspicious unless supported by infrastructure, diversification, and robust OOS evidence. That directly argues against simply relaxing gates to force more trades.

## 1) Where current logic can be expanded

### A. Move beyond the current correlated Family 1 concentration
The clearest documented weakness is concentration in one mechanism family:
- `docs/research/extreme-sharpe-playbook.md` emphasizes that portfolio Sharpe comes from many low-correlation strategies, not endless variants of one edge.
- `docs/research/implementation-gaps.md` notes current portfolio construction is equal-weighted despite high intra-family correlations.
- `docs/research/track-d-review-2026-03-30.md` explicitly says D1/D6/D7 mostly add CAGR via leveraged TQQQ expression but **do not add mechanism diversity** because they are still credit-family signals.

Most defensible expansion areas already documented:
- trend following / momentum across new markets
- mean reversion / pairs / cointegration
- carry and macro signals
- volatility and risk-premium signals
- alternative data / text / sentiment
- niche arbitrage / prediction-market / funding basis / CEF discount strategies

### B. Portfolio construction and sizing are the highest-leverage logic upgrades
Several docs converge on this:
- `implementation-gaps.md`: HRP, volatility targeting, correlation gate, marginal SR contribution gate are missing.
- `institutional-quant-guide.md`: HRP over equal-weight; vol targeting is a “free lunch.”
- `library-evaluations-2026-03-30.md`: Riskfolio-Lib is called a high-priority allocation upgrade.
- `reference-repo-findings-2026-03-30.md`: Carver-style FDM/IDM and continuous risk overlay are specifically recommended.

This is important because it can increase profitable trade production **without** lowering governance standards:
- better cross-strategy weighting
- more capital to lower-correlation edges
- adaptive exposure when realized vol changes
- reduced redundancy from highly correlated variants

### C. Add breadth through more hypothesis generation, not weaker filters
The research docs repeatedly cite breadth as the real path to better portfolio performance:
- `extreme-sharpe-playbook.md`: breadth over IC, many modest edges.
- `institutional-quant-guide.md`: the research pipeline matters more than any single strategy.

Documented expansion channels:
- `reference-repo-findings-2026-03-30.md` points to specific notebooks for:
  - factor libraries / formulaic alphas
  - earnings-call NLP
  - sentiment pipelines
  - conditional autoencoders
  - stochastic volatility / regime proxies
- `library-evaluations-2026-03-30.md` highlights Alphalens-style IC analysis as a missing upstream filter for signal quality.

### D. Track D provides the nearest-term trade expansion path
`docs/research/track-d-review-2026-03-30.md` documents a live research lane with actual candidates:
- D1, D2, D6, D7 are passing Track D gates.
- D7 has the highest CAGR but still needs full CPCV + perturbation.
- D4 has an explicit retry spec.
- D8 has documented next hypotheses:
  - yield-curve momentum on TQQQ
  - VIX term-structure risk-on signal

This is one of the most concrete places where “expand logic” is already written down.

### E. Niche arbitrage is a documented diversification engine
`docs/research/niche-arb-research.md` presents several decorrelated opportunities:
- crypto perp funding-rate arbitrage
- CEF discount mean reversion
- crypto cash-and-carry basis
- merger arbitrage
- tightly capped VIX contango overlay

These are especially relevant because the docs frame them as **genuinely decorrelated** to the current core credit/equity logic, which is exactly what the portfolio math documents want.

### F. Profit-taking review implies room to improve realized trade yield, not just signal count
`reports/profit-taking-behavior-review.md` does not present a new alpha family, but it does document a path to improve realized profitable trade output:
- harvest-first metrics are proposed
- there are explicit monetization-quality gates
- runtime behavior is fragmented across bracket, intraday synthetic TP, OCO, and EOD flatten
- backtest/live validation of exit behavior appears weak

That means part of “more profitable trades” may be getting more value from existing winners through clearer, tested exit logic rather than only increasing entry frequency.

## 2) Ideas documented but not operationalized

### A. Explicit missing implementation items already acknowledged
From `implementation-gaps.md`, the main not-yet-implemented items with upside are:
- MinTRL computation and enforcement
- t-stat reporting / threshold visibility
- complete strategy graveyard / scan registry
- HRP portfolio construction
- volatility targeting
- alpha decay / generalization ratio monitoring
- portfolio correlation gate for new strategies
- marginal portfolio Sharpe contribution gate
- corrected combined Sharpe with correlation
- capacity estimation
- CUSUM regime-change detection
- Kelly / fractional Kelly sizing

Among these, the docs themselves rate **HRP**, **vol targeting**, **correlation gating**, and **marginal SR contribution** as high-impact.

### B. Factor IC / signal-quality layer is described but not yet wired in
`library-evaluations-2026-03-30.md` says Alphalens-style IC analysis is missing and should live before research spec freeze. That means the research program has documented a way to reject weak signals earlier, but it is not yet operationalized.

This matters because more hypotheses can be explored without weakening controls if weak signals are filtered earlier by:
- IC mean
- IC t-stat
- IC decay
- turnover analysis
- quantile spread behavior

### C. Rolling walk-forward task generation is identified but not adopted
`reference-repo-findings-2026-03-30.md` specifically calls out qlib’s rolling task generator as a good scaffold for auditable walk-forward validation, but this is still just a note.

### D. Track D high-return ideas are not fully advanced through robustness
Documented but incomplete:
- D7 needs full CPCV and perturbation.
- D4 has a retry spec but is not yet rerun.
- D8 is only a hypothesis.
- D6 is viable but considered subsumed by D7.

### E. Arbitrage research ideas are documented at the research level, not all production-ready
From `niche-arb-research.md` and `kalshi-arb-scan-2026-03-30.md`:
- funding-rate arb is documented with stack and economics
- CEF mean reversion is documented with external support
- merger arb is documented conceptually
- Kalshi NegRisk has only one paper-trade candidate and infrastructure still needs simultaneous-fill handling

### F. Arb module research is ahead of production readiness
`docs/reviews/arb-module-review-2026-03-30.md` says the arb module has sound structure but still has critical and major issues before live use. So some diversification ideas are conceptually ready but operationally blocked.

### G. Profit-taking governance ideas are documented but not fully connected to runtime/research
`reports/profit-taking-behavior-review.md` documents:
- a Phase 1 harvest-first scorecard
- new metrics like harvest_ratio, giveback, TP1 hit rate, and runner retention quality
- proposed promotion gates tied to monetization quality

But the same file states these are governance/configuration concepts not yet fully instrumented or tested in runtime and backtests. That is a documented but unoperationalized improvement path.

## 3) Whether more scenario testing / walk-forwarding is explicitly warranted

Yes — very explicitly.

### A. The docs repeatedly require more robust OOS validation
Direct evidence:
- `institutional-quant-guide.md`: test across at least 3 distinct regimes; must work in 2 of 3.
- `institutional-quant-guide.md`: CPCV should produce distributions, not point estimates.
- `extreme-sharpe-playbook.md`: OOS performance should retain >60% of IS.
- `implementation-gaps.md`: regime split validation is only informal; MinTRL is missing; generalization-ratio monitoring is missing.
- `track-d-review-2026-03-30.md`: D7 explicitly needs full CPCV and perturbation.
- `reference-repo-findings-2026-03-30.md`: qlib rolling generator recommended as walk-forward scaffold.

### B. Scenario testing is especially needed where current docs show attractive returns
The docs themselves imply stronger testing is needed for:
- high-CAGR Track D levered-growth ideas
- vol-sensitive / risk-on risk-off logic
- niche arbitrage with fill, fee, and liquidity constraints
- crisis-correlation behavior, since the playbook warns correlation spikes can destroy diversification assumptions

### C. Live-vs-backtest degradation monitoring is explicitly underdeveloped
Both `implementation-gaps.md` and `institutional-quant-guide.md` call out the missing generalization-ratio framework. That is directly relevant if the runtime is currently producing repeated risk_off / 0-signal states: the docs support comparing current live behavior to expected backtest signal density and realized paper Sharpe, rather than merely loosening thresholds.

### D. Testing gaps in the arbitrage module are documented
`arb-module-review-2026-03-30.md` shows:
- critical bugs remain
- zero tests in several arb components
- network/retry and persistence issues exist
- fill mechanics remain a practical risk

So for arb ideas, more scenario testing is not optional; it is directly documented as necessary.

### E. Exit/profit-taking behavior also needs explicit scenario coverage
`reports/profit-taking-behavior-review.md` adds another testing gap:
- no direct tests were found for profit-taking / OCO / trailing / EOD flatten
- backtesting does not clearly model the operational exit stack
- one execution script bypasses the broker TP/OCO/EOD logic entirely

So even if entry logic is expanded, the docs support more scenario testing around monetization and exit behavior before claiming improved trade profitability.

## 4) Top opportunities to increase profitable trade production without weakening controls

### 1. Upgrade portfolio combination before loosening any signal gates
Most evidence-backed opportunity:
- adopt HRP / correlation-aware allocation
- add Carver-style diversification multipliers
- add volatility targeting
- enforce correlation and marginal SR contribution gates for new additions

Why this ranks first:
- it can improve realized portfolio efficiency even if raw signal count does not increase
- it aligns with multiple docs
- it does not require weaker promotion criteria

### 2. Prioritize low-correlation strategy families over more credit-family variants
Best documented expansion candidates:
- BTC / crypto momentum already appears as a diversifier in Track D
- CEF discount mean reversion
- merger arbitrage
- funding-rate / cash-and-carry arb
- pairs / cointegration / ratio mean reversion
- alternative data / text / sentiment
- trend / carry / macro / vol-regime signals

Why:
- `track-d-review-2026-03-30.md` says more Family 1 variants add less portfolio SR than genuinely different families.
- `extreme-sharpe-playbook.md` and `institutional-quant-guide.md` both stress low correlation over strategy count alone.

### 3. Advance Track D with full robustness, especially D7 and D2
Best near-term “more profitable trades” lane already in docs:
- D7 offers the strongest return potential but needs full robustness and paper gate.
- D2 adds genuine diversification.
- D4 has a specific retry recipe.
- D8 ideas are already documented.

This is likely the most practical growth path because it starts from tested strategies rather than net-new research.

### 4. Add earlier signal-quality screening with IC analytics
Using the documented Alphalens path would:
- surface stronger hypotheses faster
- reduce wasted backtests
- preserve governance while increasing research throughput

This is a trade-production opportunity indirectly: better upstream filtering means more time spent on promising ideas.

### 5. Operationalize niche arb selectively, starting with the cleanest implementations
Most promising by documentation quality:
- funding-rate arb
- CEF discount mean reversion
- cash-and-carry basis

Why not Kalshi first:
- `kalshi-arb-scan-2026-03-30.md` shows only one paper-trade candidate
- `arb-module-review-2026-03-30.md` documents production issues
- prediction-market arb appears real but thin and execution-sensitive

### 6. Add regime-aware and continuous risk scaling instead of binary on/off behavior
The docs suggest a better answer to repeated risk_off states than simply relaxing guards:
- `reference-repo-findings-2026-03-30.md` recommends continuous risk multipliers
- `institutional-quant-guide.md` recommends volatility targeting and dynamic leverage concepts
- `implementation-gaps.md` calls out CUSUM and generalization tracking

This suggests a safe expansion path: keep hard halts, but add intermediate yellow-zone scaling so the system can still express smaller positions in stressed but not catastrophic conditions.

### 7. Improve realized profit capture on existing trades
From `reports/profit-taking-behavior-review.md`, a control-preserving opportunity is to improve monetization quality by:
- aligning governance docs with actual runtime exit paths
- instrumenting harvest metrics
- backtesting/testing the live exit stack
- removing execution-path inconsistencies where profit-taking is bypassed

This does not increase trade count directly, but it can increase profitable trade output and reduce giveback.

## File-specific evidence summary

### `docs/research/implementation-gaps.md`
Strongest evidence for missing institutional-grade pieces:
- HRP
- vol targeting
- MinTRL
- generalization ratio
- correlation and marginal SR gates

### `docs/research/extreme-sharpe-playbook.md`
Best strategic framing:
- breadth and decorrelation, not magical single-strategy Sharpe
- realistic Sharpe expectations
- strong reject criteria for redundant strategies

### `docs/research/institutional-quant-guide.md`
Best validation framework:
- regime testing
- CPCV / DSR / PBO
- kill criteria
- expected OOS decay
- portfolio construction doctrine

### `docs/research/library-evaluations-2026-03-30.md`
Best implementation map for near-term upgrades:
- Riskfolio for HRP
- Alphalens for IC
- Quantstats for evaluation/reporting

### `docs/research/reference-repo-findings-2026-03-30.md`
Best research-pipeline expansion map:
- rolling/walk-forward task generation
- FDM/IDM
- alternative data notebooks
- factor libraries

### `docs/research/track-d-review-2026-03-30.md`
Best near-term alpha expansion candidates:
- D7, D2
- D4 retry
- D8 new hypotheses
- clear warning that Track D mostly overlaps existing Family 1 logic

### `docs/research/niche-arb-research.md`
Best source of decorrelated strategy ideas outside the main stack:
- funding arb
- CEF discount arb
- merger arb
- basis trade

### `docs/research/kalshi-arb-scan-2026-03-30.md`
Shows PM arb is real but thin; useful as a paper-trade/execution-learning track, not yet a broad scalable source of profitable trades.

### `docs/reviews/arb-module-review-2026-03-30.md`
Important caution: arb expansion should wait for critical bug fixes and stronger test coverage.

### `reports/profit-taking-behavior-review.md`
Shows that improving realized profitability is partly an exit-quality problem:
- monetization scorecard is documented
- runtime exit logic is fragmented
- historical/live validation of the exit stack is weak
- profit-taking controls and governance are not yet fully aligned

## Recommended parent-level conclusions

1. **Do not interpret low live trade count as a simple signal to weaken controls.** The docs point more strongly toward missing diversification, weighting, regime-aware sizing, and exit-quality validation than toward overly strict governance alone.
2. **Highest-confidence expansion path:** improve portfolio construction and activate already-documented low-correlation families.
3. **Highest-confidence near-term candidate set:** Track D D7/D2 path, but only after documented CPCV/perturbation/paper-gate completion.
4. **Most important missing validation additions:** formal regime testing, walk-forward/rolling validation, MinTRL, and live/backtest generalization monitoring.
5. **Best “more trades without weaker controls” strategy:** add breadth via new families and better upstream signal-quality screening, not looser thresholds on the current family.
6. **Realized profitability likely also depends on better-tested exit logic.** The profit-taking review suggests part of the gap may be in monetization quality and consistency, not only entry generation.

## Action list ranked by evidence/impact

1. Implement HRP / diversification-multiplier / vol-target portfolio layer.
2. Enforce portfolio-level correlation and marginal-SR contribution checks for new strategies.
3. Complete robustness testing for D7 and advance D2/D7 only if CPCV + perturbation pass.
4. Add formal walk-forward task generation and regime-split validation.
5. Add IC/IC-decay analysis upstream in hypothesis vetting.
6. Prioritize niche-arb and non-credit families that improve decorrelation.
7. Add generalization-ratio monitoring to compare paper/live behavior against backtest expectations.
8. Align and test profit-taking / monetization behavior so existing winners are harvested more consistently.
9. Keep prediction-market arb in paper/experimental mode until module issues and fill mechanics are resolved.