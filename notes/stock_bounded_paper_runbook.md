# Stock Bounded Paper Runbook

## Purpose

This runbook defines a small, supervised, bounded-risk paper session for the
new stock pod and the checks required before, during, and immediately after the
run.

It is intentionally separate from the existing `default` and `crypto` lanes so
we can validate a stock-focused paper pod under explicit capital, exposure, and
operational limits without depending on promoted strategy sets.

The goal is not throughput or profitability. The goal is to confirm that the
stock-focused candidate lane can:

- load governed strategy supply successfully
- build market context cleanly
- run end-to-end through the paper execution path
- preserve decision, order, and position auditability
- respect bounded capital and position risk constraints

## Pod

- Pod id: `stock-bounded-paper`
- Config overlay: `config/strategies/stock-bounded-paper.toml`

### Current pod assumptions

- Uses `strategy_overlay`
- Uses `candidate_stocks`
- Uses equity-only scope
- Uses daily paper mode first (`intraday_enabled = false`)
- Uses bounded configured capital
- Uses `initial_capital_source = "config"`
- Uses strict governor behavior
- Disables strategy rotation for deterministic interpretation
- Caps the pod to a single tiny position and a single trade per session
- Keeps most configured capital idle
- Is intended for supervised runtime validation, not unattended operation

## Important constraint

This pod is **bounded stock paper validation**, not a production promotion.

It closes the stock-lane paper validation gap by exercising a governed,
stock-focused candidate set with explicit limits. It does **not** by itself
promote any strategy set, relax lifecycle gates, or replace the full validation
requirements for promotion.

The current `candidate_stocks` set is intentionally narrow:

- `soxx-qqq-lead-lag`

That narrow set is deliberate. It keeps runtime behavior easier to interpret
while the pod itself is being validated.

## Pre-run checks

### 1. Confirm the config overlay exists and matches intent

Review `config/strategies/stock-bounded-paper.toml` before creating or reusing
the pod.

Expected characteristics:

- `signal_source = "strategy_overlay"`
- `strategy_set = "candidate_stocks"`
- `asset_class_filter = ["equity"]`
- `intraday_enabled = false`
- `initial_capital_source = "config"`
- strict bounded risk caps are enabled
- rotation is disabled for determinism

### 2. Register the pod

```bash
python -m llm_quant.cli pods create stock-bounded-paper --strategy custom --capital 1000
```

If it already exists, skip creation.

### 3. Confirm the pod is present

```bash
python -m llm_quant.cli pods list
```

Expected result:

- `stock-bounded-paper` appears in the pod list
- capital and strategy mode are consistent with the bounded overlay intent

### 4. Confirm stock candidate supply is non-empty

Verify `config/strategies/catalog.toml` contains:

- `candidate_stocks = ["soxx-qqq-lead-lag"]`

If `candidate_stocks` is empty, stop here and classify the session as not ready
to run.

### 5. Confirm market-data and runtime readiness

Before any supervised paper run, verify:

- the selected strategy set resolves cleanly
- required equity symbols for `soxx-qqq-lead-lag` are available and fresh
- the market session is open or the expected RTH behavior is understood
- there is no accidental dependency on crypto-only runtime assumptions

### 6. Confirm bounded-risk intent before execution

Before running, explicitly verify that the pod is configured so that:

- total configured capital is small
- only a tiny fraction of capital can be deployed in a single decision cycle
- concurrent positions are tightly capped
- most cash remains reserved
- no unattended session is planned

## Dry-run rehearsal

### Command

```bash
python -m llm_quant.cli run --pod stock-bounded-paper --broker paper --dry-run
```

### Expected dry-run outcome

- the pod loads successfully
- stock candidate strategy supply is non-empty
- market context builds
- the decision path executes cleanly
- if no trade is proposed, that is still a valid rehearsal result

### Dry-run is a failure if

- the pod cannot load
- the strategy set is unexpectedly empty
- market-context construction fails
- a governance or config mismatch prevents the run from starting

## Supervised live paper run

Run only during a supervised low-risk market window.

```bash
python -m llm_quant.cli run --pod stock-bounded-paper --broker paper
```

### Live-run expectations

- this is a paper-only run
- the run is supervised from start to finish
- any routed order should be small relative to configured capital
- the pod should not fan out into many symbols or positions
- if no order is emitted, that can still be a valid validation result

## Post-run checks

Use the repository's normal paper-trading inspection surfaces to verify:

- fresh decisions were written for `stock-bounded-paper`
- any paper trades or fills are attributable to `stock-bounded-paper`
- resulting positions remain within configured caps
- no unexpected symbol fan-out occurred
- any exits remain explainable under the configured canonical policy

## What must be true

### Decision-path correctness

- the pod identity is clear
- the active config intent is explainable
- no-trade states can be distinguished from config or data failure
- decisions are fresh for the run window

### Stock-lane sanity

- routed symbols, if any, are equity symbols expected from `candidate_stocks`
- no unexpected crypto-only behavior appears in the stock pod
- the pod remains within the intended stock-focused scope

### Bounded-risk behavior

- at most one tiny position is opened
- at most one trade is routed in the session
- position sizing remains tiny relative to configured capital
- most cash remains idle after the run
- no repeated re-entry loop or broad expansion occurs

### Exit-policy sanity

- stop-loss / take-profit / trailing behavior remains consistent with the
  configured paper-mode canonical exit policy
- no unprotected or unexplained position state appears
- end-of-day flatten behavior, if triggered, is explainable from config

## Result classification

### PASS

- pod runs successfully
- `candidate_stocks` is valid and non-empty
- decision path is fresh
- any paper trades are auditable and attributable to the pod
- bounded-risk checks hold
- any routed exposure remains small and within configured intent

### SOFT PASS

- pod runs successfully
- no trade occurs
- decision path is fresh
- stock candidate supply is valid
- runtime path appears healthy
- bounded-risk intent is preserved because no routeable order was emitted

### FAIL

- pod cannot load
- `candidate_stocks` is unexpectedly empty
- market-context construction fails
- paper execution path cannot start cleanly
- routed exposure exceeds bounded intent
- unexpected multi-position expansion or symbol fan-out occurs
- runtime behavior cannot be explained from config and telemetry

## Follow-up after first clean pass

If the first bounded paper pass is clean:

1. record actual runtime timestamps and evidence from the paper session
2. summarize whether any order was proposed, submitted, filled, or fully idle
3. confirm that bounded capital and position controls behaved as intended
4. decide whether to repeat with a slightly broader but still bounded stock set
5. keep promotion decisions separate from this runbook and governed by the
   normal lifecycle and validation path

## EOD flatten operational coverage

For packaging and audit purposes, this pod should use its own explicit equity EOD
flatten unit rather than implicitly inheriting `default` coverage.

Expected systemd assets:

- `scripts/systemd/llm-quant-eod-flat-stock-bounded-paper.service`
- `scripts/systemd/llm-quant-eod-flat-stock-bounded-paper.timer`

Expected command:

```bash
pq eod-flat --pod stock-bounded-paper
```

This pod's EOD behavior is stock-session based. Do not treat the stock bounded
paper timer as covering the crypto pod.
