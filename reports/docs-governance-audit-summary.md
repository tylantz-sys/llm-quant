# Documentation Governance Audit Summary

## Scope reviewed

Primary documents reviewed:

- `README.md`
- `docs/governance/eod-profit-taking.md`
- `docs/governance/strategy-artifact-status-matrix.md`
- `docs/governance/quant-lifecycle.md`
- `docs/governance/runtime-truth-table.md`
- `docs/governance/control-matrix.md`
- `docs/governance/model-promotion-policy.md`
- `docs/governance/crypto-strategy-promotion.md`
- `docs/governance/crypto-paper-promotion-checklist.md`
- `docs/governance/research-tracks.md`
- `docs/governance/alpha-hunting-framework.md`

This summary is documentation-only. It describes the documented target state, explicit gates, anti-curve-fit controls, stated profit-taking / EOD policy, internal ambiguities, and the strategies/assets that the docs themselves say are still missing validation or promotion gates.

---

## 1. Documented target state

The docs describe a fairly strict institutional-style quant lifecycle with three distinct concepts that should not be conflated:

1. **Lifecycle/governance state**
   - idea → mandate → hypothesis → data contract → frozen research spec → backtest → robustness → paper → promotion → evaluation / retirement
   - Source: `docs/governance/quant-lifecycle.md`

2. **Artifact-backed validation state**
   - Promotion readiness depends on on-disk artifacts, not narrative claims.
   - Source: `docs/governance/strategy-artifact-status-matrix.md`

3. **Runtime-enabled state**
   - A strategy can be runtime-enabled without being governance-complete, and governance-complete without being live.
   - Source: `docs/governance/strategy-artifact-status-matrix.md`, `docs/governance/runtime-truth-table.md`

The intended operating model is:
- a research program with Track A / B as main alpha programs,
- explicit Track C and D extensions,
- optional pod-based runtime deployment,
- canonical exit policy across daily/intraday and synthetic/native broker modes,
- conservative promotion only after artifact-backed backtest, robustness, walk-forward, paper, and promotion review.

The docs repeatedly emphasize that **narrative confidence is not evidence** and that **promotion requires preserved artifacts plus runtime controls**.

---

## 2. Required documented gates

### 2.1 Lifecycle and artifact gates

From `quant-lifecycle.md`, a strategy is supposed to have:
- `mandate.yaml`
- `hypothesis.yaml`
- `data-contract.yaml`
- `research-spec.yaml`
- experiment entries in `data/strategies/experiment-registry.jsonl`
- `robustness.yaml`
- `paper-trading.yaml`
- promotion record in `strategy_changelog`

Additionally:
- spec must be frozen before backtest
- backtest requires frozen spec
- robustness requires at least 2 experiments
- paper requires robustness pass
- promotion requires paper pass

### 2.2 Robustness gates

Documented robustness gate in `quant-lifecycle.md`:

Common integrity gates:
- **DSR >= 0.95** for Track A/B/C, relaxed to 0.90 for Track D
- **PBO <= 0.10**
- **CPCV mean OOS Sharpe > 0**
- **CPCV median OOS Sharpe > 0**
- **2x cost survival with Sharpe > 0**
- **Parameter stability > 50%** / perturbation pass

Risk gates vary by track:
- Track A: Sharpe >= 0.80, MaxDD < 15%
- Track B: Sharpe >= 1.00, MaxDD < 30%
- Track C: Sharpe >= 1.50, MaxDD < 10%, beta to SPY < 0.15, min trades >= 50
- Track D: Sharpe >= 0.80, MaxDD < 40%, DSR >= 0.90, MAR >= 1.0 after 90d paper, max holding period <= 5 days

### 2.3 Promotion gates

From `model-promotion-policy.md`, promotion is a 5-stage sequence:

1. **Hard vetoes**
   - DSR >= 0.95
   - PBO <= 10%
   - SPA p-value <= 0.05
   - MinTRL >= 1 OOS period

2. **Weighted scorecard**
   - Composite >= 85
   - Dimensions: risk-adjusted returns, drawdown, trade stats, robustness, operational feasibility

3. **Paper trading**
   - >= 50 trades
   - >= 30 calendar days
   - paper Sharpe >= 0.60
   - all operational systems tested

4. **Canary**
   - 10% allocation
   - >= 14 days
   - drawdown < 10%
   - canary Sharpe >= 0.50
   - no kill switch trigger

5. **Full deployment**
   - kill switches active
   - baseline metrics recorded
   - enhanced surveillance active

### 2.4 Crypto-specific promotion gates

From `crypto-strategy-promotion.md` and `crypto-paper-promotion-checklist.md`:

For a crypto strategy candidate such as `eth-btc-ratio-mean-reversion-v5`, expected artifacts include:
- `mandate.yaml`
- `hypothesis.yaml`
- `data-contract.yaml`
- `research-spec.yaml`
- `experiment-registry.jsonl`
- `robustness.yaml`
- `walk-forward.yaml`
- `paper-trading.yaml`

Crypto strategy-level gates:
- frozen spec
- baseline backtest: Sharpe > 0, DSR >= 0.95 when available, MaxDD <= 0.25
- passed robustness artifact
- passed walk-forward artifact
- paper gate: days_observed >= 30, closed_trades >= 50, sharpe >= 0.60, max_drawdown <= 0.25, operational checks healthy

Crypto operator/pod gate also requires:
- scheduler health
- fresh crypto runtime status
- deterministic governor/risk pipeline visibility
- strict set validation for `candidate_crypto` / `promoted_crypto`

### 2.5 Overnight walk-forward governance gates

Both `quant-lifecycle.md` and `model-promotion-policy.md` now require unattended overnight walk-forward evidence to preserve machine-readable artifacts:
- `overnight-manifest.json`
- `overnight_run_state.json`
- `overnight-status.jsonl`
- run summary artifact(s)
- per-candidate metadata artifact(s)
- baseline artifact
- walk-forward artifact

And require explicit handling/visibility for:
- lock ownership
- resume behavior
- timeout outcomes
- artifact freshness / missing-artifact states

---

## 3. Explicit anti-curve-fit / anti-overfitting controls documented

The documentation is unusually explicit here. The main documented defenses are:

- **Spec freeze before backtest**
- **Hypothesis before results**
- **Append-only experiment registry**
- **DSR trial counting**
- **PBO via CSCV**
- **CPCV with purge and embargo**
- **Parameter perturbation / stability testing**
- **2x and higher cost sensitivity**
- **Holdout / walk-forward expectations**
- **Paper trading minimum sample**
- **SPA p-value hard veto in promotion**
- **Overnight artifact governance for unattended WFO**
- **Shuffled signal fraud detector**
- **Mechanism inversion**
- **Economic regime split**
- **Alternative instrument checks**
- **Portfolio correlation and marginal SR contribution thinking**
- **Requirement for multiple mechanism families, not many correlated variants**

Important nuance from the docs:
- Track A and Track B have **different risk gates** but **the same integrity gates**.
- DSR/CPCV/PBO/perturbation are presented as anti-overfitting controls, not risk controls.
- The alpha-hunting framework is even more severe than the formal lifecycle docs; it adds practical fraud detectors beyond the baseline 5-gate framework.

---

## 4. Profit-taking / EOD flatten policy as documented

From `docs/governance/eod-profit-taking.md` and reinforced by `README.md` and `runtime-truth-table.md`:

### Canonical exit policy
Owned by risk config, with explicit fields for:
- take-profit mode (`pct` or `rr`)
- take-profit target
- partial take-profit enablement and size
- trailing stop enablement and percentage
- fail-on-unprotected-exits
- EOD flatten enablement and cutoff time

### Runtime behavior
Same exit policy should apply across:
- daily paper via synthetic/simulated exits
- daily Alpaca via native brackets
- intraday paper via synthetic monitoring
- intraday Alpaca with OCO via partial TP + OCO remainder
- intraday Alpaca without OCO via synthetic monitoring

### Partial TP semantics
If enabled:
- first profit target is computed
- position is partially reduced
- remainder is managed by trailing stop or native/synthetic equivalent

### EOD flatten
`pq eod-flat` is documented as an operational override:
- checks market open and ET time >= configured cutoff
- cancels open orders
- flattens positions with market orders
- logs close trades
- stores snapshot

### Safety posture
`fail_on_unprotected_exits = true` is explicitly preferred, and live runs should fail loudly if protection cannot be verified.

In documentation terms, the profit-taking story is coherent: exits are supposed to be policy-driven, runtime-agnostic in vocabulary, auditable, and safety-first.

---

## 5. Governance/process coherence strengths in the docs

The docs are strongest in these areas:

- clear separation between **target state**, **artifact-backed state**, and **runtime state**
- strong articulation of anti-overfitting doctrine
- repeated insistence that backtests alone do not justify promotion
- explicit paper and canary gates before live deployment
- explicit distinction between synthetic vs native exit realization while keeping one policy vocabulary
- good operator-focused runtime language for “working but no signals” vs “broken”
- useful conservative wording in the strategy status matrix: unknown remains unknown unless verified

Overall, the documentation is not casual or promotional; it is intentionally skeptical.

---

## 6. Document conflicts, ambiguities, or unclear areas

### 6.1 Track counts and deployment claims are somewhat inconsistent
`README.md` presents:
- 11 Track A strategies active
- 11 currently in paper trading
- 0 promoted to live capital

But `strategy-artifact-status-matrix.md` says:
- default sleeve currently disabled
- crypto sleeve disabled
- many Track A optimizer-linked artifacts incomplete
- only some strategies have artifact-backed evidence
- runtime history exists, but current runtime verification may not

This is not a direct contradiction if “active” means “research-active / paper-active historically,” but it is potentially confusing.

### 6.2 Track A “11 passing strategies” vs artifact completeness debt
`README.md` presents the 11 passing Track A strategies with detailed metrics, while the status matrix says many underlying lifecycle artifacts are incomplete and that 14 registered strategies have no experiment artifacts on disk. That suggests:
- research pass status may be documented at summary level,
- but full artifact chain is not uniformly present or verified.

### 6.3 Walk-forward emphasis is uneven
The lifecycle and promotion docs treat walk-forward and overnight WFO governance as important, but:
- the formal lifecycle schema lists `robustness.yaml` and paper artifacts,
- while the crypto strategy promotion standard explicitly requires `walk-forward.yaml`,
- and the status matrix tracks walk-forward as its own field.

This implies walk-forward is practically mandatory, but the artifact expectations are not completely uniform across all tracks in one single place.

### 6.4 Promotion criteria differ by general vs crypto docs
The general promotion policy includes:
- SPA p-value
- MinTRL
- scorecard
- canary
- full deployment

The crypto promotion docs focus more on:
- strategy-local artifacts
- passed baseline / robustness / walk-forward / paper gate
- catalog movement from `candidate_crypto` to `promoted_crypto`

So crypto docs read like a narrower operational subset rather than a full restatement of the promotion pipeline. Parent response should note that crypto docs complement, not replace, the general promotion policy.

### 6.5 README research claims exceed what the status matrix is willing to certify
The README communicates mature research results and paper state. The matrix is more conservative and repeatedly says many items are `unknown`, `missing`, or only narrative-backed. For an audit answer, the matrix should be treated as the more authoritative governance source.

### 6.6 Control matrix implementation status is mixed by design
`control-matrix.md` says some items are “implemented,” but also explicitly marks:
- crowding/capacity as deferred
- execution drift / TCA as deferred

So documented production safety is incomplete before live trading, even by the repo’s own standards.

---

## 7. Strategies/assets the docs themselves indicate are still missing gates

Below is the docs-only picture of gaps.

### 7.1 Default intraday sleeve
From the status matrix:
- current runtime disabled
- many Track A lifecycle artifacts incomplete
- walk-forward unknown
- paper unknown
- promotion unknown
- kill switches only partial
- telemetry partial

### 7.2 Crypto intraday sleeve
From the status matrix:
- current runtime disabled
- mixed artifact state
- mixed paper/evaluation candidate
- not currently runtime-verified
- promotion decision unknown

### 7.3 ETH/BTC ratio mean reversion v5
Docs say this remains incomplete pending verification of:
- mandate artifact
- hypothesis artifact
- data contract
- research spec verification/freeze evidence
- backtest artifact verification
- robustness artifact verification
- walk-forward artifact verification
- paper-trading artifact verification
- final promotion decision

Even though the crypto governance docs define the exact path, the status matrix conservatively leaves almost all fields unknown.

### 7.4 GLD/SLV mean reversion v4
Docs indicate:
- frozen spec: yes
- backtest: passed
- robustness: passed
- walk-forward: passed
- **paper trading: missing**
- **promotion decision: missing**
- canary unknown
- runtime verification not verified

So this is explicitly a promotion-pipeline candidate, not promotion-ready.

### 7.5 Track C arbitrage sleeve
Docs indicate missing/incomplete:
- live scan completion
- historical validation
- paper trading
- walk-forward/robustness unknown at sleeve level
- promotion unknown
- runtime disabled

### 7.6 Polymarket NegRisk + combinatorial arb
Docs say implementation exists, but still missing:
- first live scan
- historical validation
- paper trading
- passed backtest / robustness / walk-forward fields not yet evidenced
- promotion not ready

### 7.7 Track D family
Family-level docs indicate:
- candidate, not promoted
- walk-forward generally unknown
- paper unknown
- promotion unknown

### 7.8 D1 — TLT/TQQQ sprint
Docs indicate:
- backtest passed
- robustness unknown
- walk-forward unknown
- paper unknown
- promotion unknown
- upstream mandate/hypothesis/data-contract/spec artifacts not verified

### 7.9 D2 — BTC momentum v2
Docs indicate:
- backtest passed
- paper gate still required
- robustness/walk-forward not verified in the matrix
- promotion not complete

### 7.10 D4 — sector sprint top-1 retry
Docs indicate:
- current research spec not frozen
- backtest failed / conditional retry
- not ready for robustness, paper, or promotion

### 7.11 D6 — LQD/TQQQ sprint
Docs indicate:
- backtest passed
- robustness unknown
- walk-forward unknown
- paper unknown
- promotion unknown

### 7.12 D7 — TQQQ stacked credit
Docs indicate:
- preliminary pass only
- robustness still draft/incomplete
- specifically still needs CPCV + perturbation
- then needs 30-day paper gate
- not promotion ready

### 7.13 Track B near-pass candidates
From `research-tracks.md`, these remain non-promoted research candidates:
- `K1 QUAL factor rotation` — failed Track A MaxDD, re-examine with regime filter
- `O3 commodity rotation` — failed Track A MaxDD, needs V2 with lookback/VIX overlay
- `C7 window=7` — not pre-specified, must be re-specified as v2

These are clearly not past full governance gates.

### 7.14 Mechanism-family coverage still missing / untested
From `alpha-hunting-framework.md`, the docs themselves say these areas are still missing or incomplete:
- Family 2 Mean Reversion: GLD/SLV needs full lifecycle
- Family 3 Momentum/Trend: redesign needed
- Family 4 Vol regime harvesting: untested
- Family 5 Calendar/structural flows: partially tested, several ideas falsified, others need re-test
- Family 6 Macro regime rotation: untested
- Family 7 Sentiment contrarian: untested
- Family 8 Non-credit cross-market lead-lag: only 1 passing strategy, should expand

This matters because the docs repeatedly admit the current portfolio is still concentrated in one mechanism family.

---

## 8. Asset coverage and portfolio-level reality documented by docs

The docs claim broad universe access:
- 39 liquid US ETFs
- crypto
- equity / fixed income / commodity / crypto sleeves
- Track B adds leveraged ETFs and BTC/ETH
- Track C aims at niche arb
- Track D adds leveraged re-expression

But the docs also admit:
- Track A passers are mostly credit-equity lead-lag variants
- genuine mechanism diversification is still insufficient
- portfolio-level effective independent N is low
- correlation concentration is a known weakness
- many non-credit families remain untested or incomplete

So the documented target state is broad, but the documented realized validated edge is still comparatively narrow.

---

## 9. Biggest documented weaknesses

Purely from the docs, the biggest weaknesses are:

1. **Artifact completeness is behind narrative maturity**
   - Especially for Track A and some sleeve/runtime claims.

2. **Mechanism concentration**
   - 10 of 11 Track A passers share the same credit-equity lead-lag family.

3. **Walk-forward / promotion evidence not uniformly surfaced**
   - Strongly required in governance language, but not uniformly visible as complete across strategies.

4. **Runtime trust and governance trust are not yet aligned**
   - Matrix repeatedly distinguishes runtime history from current verified safety.

5. **Live-trading controls are knowingly incomplete**
   - Crowding/capacity and execution drift/TCA are explicitly deferred before live trading.

6. **Crypto promotion path is defined, but evidence still mostly unverified**
   - Especially for `eth-btc-ratio-mean-reversion-v5`.

7. **Track C and Track D are still candidate-stage**
   - Research and implementation exist, but paper/promotion gates remain incomplete.

---

## 10. Bottom-line docs-only conclusion

Documentation paints a **high-discipline target-state governance model** with serious anti-curve-fit doctrine:
- frozen specs,
- append-only registry,
- DSR/PBO/CPCV,
- perturbation,
- walk-forward governance,
- paper and canary gates,
- explicit promotion review,
- and canonical exit / EOD flatten policy.

However, the docs also openly admit that **documented process maturity exceeds uniformly verified artifact maturity**.

Most defensible docs-only conclusion:
- **The intended governance model is strong and coherent.**
- **The portfolio/research program is not yet fully artifact-complete or promotion-complete across sleeves and strategies.**
- **Crypto, Track C, and Track D still have major remaining gates.**
- **Even Track A’s reported success is accompanied by artifact debt and concentration risk.**
- **The docs do describe anti-curve-fit controls in detail, but the status matrix suggests those controls are not yet uniformly evidenced as passed for every claimed strategy/runtime path.**

For parent-agent synthesis, the safest phrasing is:
- the repo docs **do define** logic and policy to avoid curve fitting,
- but the docs themselves also say many strategies are still short of full backtest / WFO / paper / promotion evidence,
- and no strategy should be treated as promotion-ready unless the status matrix shows artifact-backed completion across all required stages.