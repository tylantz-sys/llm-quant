# SPY Deterministic Strategy Governance Summary

This note summarizes the repo's governing doctrine for taking a new deterministic single-symbol SPY daily strategy from idea to backtest, robustness, walk-forward, paper, and promotion.

Sources:
- `docs/governance/quant-lifecycle.md`
- `docs/governance/validation-requirements-matrix.md`
- `docs/governance/research-tracks.md`
- `docs/governance/model-promotion-policy.md`

## 1. Required lifecycle sequence

The lifecycle is strict and sequential. There are no shortcuts and no stage skipping:

`Idea -> Mandate -> Hypothesis -> Data Contract -> Research Spec (frozen) -> Backtest -> Robustness -> Paper Trading -> Promotion -> Evaluation -> Retirement`

A failure at a gate sends the strategy back to the appropriate earlier stage. A mandate change invalidates downstream work and requires re-doing from hypothesis onward.

## 2. Required repo artifacts

For a strategy slug at `data/strategies/{slug}/`:

1. **Mandate**
   - File: `data/strategies/{slug}/mandate.yaml`
   - Must define objective, benchmark, universe, constraints, target metrics, status, and `track`.

2. **Hypothesis**
   - File: `data/strategies/{slug}/hypothesis.yaml`
   - Must be written before looking at results.
   - Must include statement, expected outcome, measurement method, null hypothesis, falsification criteria, timeframe, conviction, rationale, and risks.

3. **Data contract**
   - File: `data/strategies/{slug}/data-contract.yaml`
   - Must define symbols, date range, frequency, required fields, quality grade, known issues, source, freshness requirements, and benchmark data.

4. **Research spec**
   - File: `data/strategies/{slug}/research-spec.yaml`
   - Must define parameters, indicators, signals, rules, validation setup, cost model, `fill_delay`, warmup, rebalance cadence, and frozen/hash fields.

5. **Experiment registry entries**
   - File: `data/strategies/experiment-registry.jsonl`
   - Global append-only log of every backtest run, including failures and diagnostics.

6. **Robustness artifact**
   - File: `data/strategies/{slug}/robustness.yaml`
   - Must contain PBO, CPCV, perturbation, DSR, and overall gate result.

7. **Paper trading artifact**
   - File: `data/strategies/{slug}/paper-trading.yaml`
   - Must record start date, status, performance, trades, incidents, slippage drift, operations tested, days active, and total trades.

8. **Promotion record**
   - Stored in `strategy_changelog` with the promotion template fields from the promotion policy.
   - Promotion packet should also rely on frozen artifacts, experiment registry evidence, robustness outputs, walk-forward outputs, paper evidence, and governance/runtime records.

## 3. Universal no-shortcuts rules

These are explicit repo doctrine:

- **No stage skipping.**
- **Hypothesis must exist before results are interpreted.**
- **Research spec must be frozen before backtesting.**
- **Every backtest run must be recorded** in the append-only experiment registry.
- **At least 2 completed experiments are required before robustness.**
- **Fill delay must be realistic:** minimum `fill_delay = 1` bar unless clearly diagnostic-only.
- **Walk-forward and robustness evidence must be directly reviewable** and tied back to the frozen spec and experiment record.
- Narrative claims are not evidence:
  - README claims do not count.
  - High CAGR alone does not count.
  - Runtime being active does not count.
  - A quiet or no-trade runtime does not count as proof of health.
- If any promotion-readiness item is unknown, the strategy is not promotion-ready.

## 4. Track decision for a single-symbol SPY daily strategy

For a deterministic SPY daily strategy, the track must be declared in `mandate.yaml`.

Most relevant choices:

### Track A
Use if the intent is a defensive, lower-drawdown SPY sleeve.
- Robustness Sharpe gate: `>= 0.80`
- Robustness MaxDD gate: `< 15%`
- Benchmark: `60/40 SPY/TLT`, monthly rebalanced, **total return**
- Paper Sharpe gate in track table: `>= 0.60`
- Position sizing doctrine is more conservative

### Track B
Use if the strategy is explicitly higher-volatility / higher-CAGR SPY alpha.
- Robustness Sharpe gate: `>= 1.00`
- Robustness MaxDD gate: `< 30%`
- Benchmark: `100% SPY`
- Research tracks doc also raises paper Sharpe expectation to `>= 0.80`, while universal promotion minimum remains `>= 0.60`
- Still must pass the same anti-overfitting integrity gates as Track A

For a **single-symbol SPY daily** deterministic strategy, Track A is the default fit if the sleeve is meant to be a conservative portfolio component; Track B only if the mandate explicitly accepts larger drawdowns for growth.

## 5. What must exist before backtest

Before any legitimate backtest:

- `data/strategies/{slug}/mandate.yaml`
- `data/strategies/{slug}/hypothesis.yaml`
- `data/strategies/{slug}/data-contract.yaml`
- `data/strategies/{slug}/research-spec.yaml`
- In the research spec:
  - `frozen: true`
  - recorded `frozen_hash`
  - validation method/configuration
  - cost model
  - `fill_delay >= 1`
- Benchmark definition must be explicit and use correct return treatment
  - If using the default mandate benchmark logic, benchmark return type must be `total_return`

## 6. What must exist before robustness

Before robustness can be run/reviewed:

- Frozen research spec and hash chain in place
- `>= 2` completed experiment runs recorded in `data/strategies/experiment-registry.jsonl`
- Each experiment entry should include:
  - `experiment_id`
  - `slug`
  - `spec_hash`
  - `trial_number`
  - date and tested period
  - metrics including Sharpe and DSR
  - cost sensitivity
  - benchmark comparison

Robustness then requires a machine-reviewable artifact at:
- `data/strategies/{slug}/robustness.yaml`

And all universal integrity checks must pass:
- `DSR >= 0.95`
- `PBO <= 0.10`
- CPCV mean OOS Sharpe `> 0`
- CPCV median OOS Sharpe `> 0`
- 2x cost survival with Sharpe `> 0`
- parameter stability `> 50%`

Track-specific risk gates also apply:
- Track A: Sharpe `>= 0.80`, MaxDD `< 15%`
- Track B: Sharpe `>= 1.00`, MaxDD `< 30%`

## 7. What must exist before walk-forward is considered adequate

The validation matrix treats walk-forward / regime validation as a distinct layer after robustness and before paper/promotion readiness.

Repo doctrine requires:
- **walk-forward evidence must exist and be directly reviewable**
- it must be tied to the frozen spec and experiment record
- promotion review must use the underlying artifacts, not screenshots or terminal output

Also relevant:
- promotion hard vetoes require `MinTRL >= 1` out-of-sample period
- the robustness scorecard dimension explicitly includes walk-forward consistency
- the minimum promotion checklist includes `Walk-forward passed`

So for a SPY daily strategy to be considered adequately validated for downstream gates, it should have:
- direct walk-forward artifact(s) or outputs linked to the same frozen spec/hash lineage
- at least one true OOS period
- evidence suitable for scorecard review and promotion packet review

## 8. What must exist before shadow/paper trading

Paper trading requires robustness to have passed first.

Before paper:
- robustness artifact exists and passes
- walk-forward evidence exists and is reviewable
- operationally testable implementation path exists end-to-end

Paper artifact:
- `data/strategies/{slug}/paper-trading.yaml`

Paper minimums:
- `>= 30` calendar days
- `>= 50` trades
- paper Sharpe `>= 0.60` universal minimum
- all operational systems tested

Operational systems expected by doctrine include:
- data fetching
- indicator computation
- signal generation
- risk enforcement
- trade execution path
- portfolio snapshot persistence
- hash chain integrity
- performance reporting
- incident logging

## 9. What must exist before promotion

Promotion is sequential and cannot begin on narrative confidence alone.

### Stage 1: Hard vetoes
Must have evidence for:
- `DSR >= 0.95`
- `PBO <= 10%`
- `SPA p <= 0.05`
- `MinTRL >= 1` OOS period

If walk-forward or robustness evidence feeds these checks, those artifacts must be directly reviewable and tied to the frozen spec and experiment record.

### Stage 2: Weighted scorecard
Need composite `>= 85` across:
- risk-adjusted returns
- drawdown characteristics
- trade statistics
- robustness
- operational

### Stage 3: Paper trading
Need:
- `>= 50` trades
- `>= 30` days
- paper Sharpe `>= 0.60`
- all operational systems tested

### Stage 4: Canary
Need:
- `10%` allocation
- `>= 14` calendar days
- canary drawdown `< 10%`
- canary Sharpe `>= 0.50`
- no material kill-switch events
- no harmful portfolio interaction effects

### Stage 5: Full deployment
Need:
- kill switches active and verified
- baseline metrics recorded
- promotion/changelog record written
- enhanced surveillance begins

## 10. Practical repo-specific takeaway for planning a deterministic SPY daily strategy

For this repo, a deterministic SPY daily strategy is not "ready for backtest" just because the rules are coded. The required planning path is:

1. choose slug and track in `mandate.yaml`
2. define pre-results hypothesis in `hypothesis.yaml`
3. lock data assumptions in `data-contract.yaml`
4. define a complete deterministic `research-spec.yaml`
5. freeze the spec and hash it
6. record every backtest in `experiment-registry.jsonl`
7. pass robustness with DSR/PBO/CPCV/cost/parameter-stability and track risk gates
8. produce direct walk-forward evidence with at least one OOS period
9. pass paper minimums in `paper-trading.yaml`
10. pass promotion hard vetoes, scorecard, canary, and deployment checks
11. write formal promotion record to `strategy_changelog`

Anything less is incomplete under repo governance.