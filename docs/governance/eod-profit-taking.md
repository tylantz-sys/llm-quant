# Canonical Exit Engine and EOD Flatten Policy

## Overview

This repo now uses a **canonical exit engine** to decide how open positions are protected and exited. The engine separates:

1. **Exit policy**
   - Defined by risk config.
   - Controls take-profit math, partial profit-taking, trailing stop behavior, EOD flatten, and whether missing protection should fail loudly.

2. **Broker realization path**
   - Determined by runtime mode (`intraday_enabled`, broker, and `intraday_use_oco`).
   - The same policy can be realized through:
     - synthetic monitoring and signals,
     - Alpaca bracket orders,
     - Alpaca partial TP + OCO remainder.

This makes the policy auditable even when the broker implementation differs by mode.

---

## Canonical Exit Policy

### Policy owner
Exit policy is owned by `config/risk.toml` via the active risk section (`[limits]`, `[track_b]`, etc.).

Key fields:

```toml
take_profit_mode = "pct"            # pct | rr
take_profit_pct = 0.03
take_profit_rr = 2.0

partial_take_profit_enabled = true
partial_take_profit_pct = 0.02
partial_take_profit_size = 0.50
remainder_take_profit_mult = 2.0

trailing_stop_enabled = true
trailing_stop_pct = 0.015

fail_on_unprotected_exits = true

eod_flatten_enabled = true
eod_flatten_time = "15:55"
```

### Take-profit modes

#### Fixed-percent mode
When `take_profit_mode = "pct"`:

```text
TP = entry_price * (1 + take_profit_pct)
```

Example:
- Entry: `127.00`
- `take_profit_pct = 0.03`
- Full TP target: `130.81`

#### Risk-reward mode
When `take_profit_mode = "rr"`:

```text
risk = entry_price - stop_loss
TP = entry_price + (take_profit_rr * risk)
```

Example:
- Entry: `100.00`
- Stop: `95.00`
- `take_profit_rr = 2.0`
- Full TP target: `110.00`

---

## Runtime Realization Paths

The canonical exit engine decides the policy once. Runtime mode decides how that policy is realized.

### Daily + paper
- Broker path: paper executor
- Exit realization: synthetic / simulated
- State tracking: portfolio + decision telemetry
- Backtest parity: the backtest engine now uses the same canonical synthetic exit rules for stop-loss, partial TP, trailing stop, and EOD flatten timing instead of maintaining a separate profit-taking implementation

### Daily + Alpaca
- Broker path: Alpaca bracket orders
- Exit realization: native bracket TP/SL
- If the bracket is invalid and `fail_on_unprotected_exits = true`, the run fails instead of silently degrading to an unprotected order.

### Intraday + paper
- Broker path: paper executor
- Exit realization: synthetic monitoring
- Supports:
  - partial TP signals,
  - trailing stop signals,
  - stop-loss exits.

### Intraday + Alpaca + `intraday_use_oco = true`
- Broker path: Alpaca market entry + partial TP limit + OCO remainder
- Exit realization: native/resting orders where available
- Order state is tracked in `intraday_order_state`
- If protective legs cannot be resolved and `fail_on_unprotected_exits = true`, the run fails loudly.

### Intraday + Alpaca + `intraday_use_oco = false`
- Broker path: Alpaca market/limit orders
- Exit realization: synthetic monitoring by the canonical exit engine
- This is the intended path for sleeves where broker OCO parity is not trusted or not desired.

---

## Partial TP and Trailing Stop Semantics

When partial profit-taking is enabled:

1. A first profit target is calculated from `partial_take_profit_pct`
2. `partial_take_profit_size` determines how much of the position is reduced
3. The remainder can be managed by:
   - synthetic trailing logic, or
   - native OCO / stop management depending on runtime

When trailing stops are enabled:
- trailing behavior only applies after the partial exit has been taken
- the stop follows `peak_price * (1 - trailing_stop_pct)`

This is now canonical behavior rather than an implementation detail buried in one broker path.

---

## EOD Flatten

### What it is
`pq eod-flat` is the **operational EOD flatten override**. It is governed by the canonical exit policy.

The same policy vocabulary now also applies in synthetic backtests, where EOD flatten is evaluated against a canonical end-of-day timestamp for parity review.

- `eod_flatten_enabled`
- `eod_flatten_time`

### What it does
If the Alpaca market clock reports:
- market open, and
- current ET time >= `eod_flatten_time`

then the command:
1. Cancels open orders
2. Submits market orders to flatten open positions
3. Logs close trades in DuckDB
4. Saves a portfolio snapshot

### Early-close days
On early-close sessions, Alpaca may report the market closed before the configured cutoff. In that case, `pq eod-flat` skips unless the configured cutoff is adjusted or the command is run earlier.

---

## Failure Policy

`fail_on_unprotected_exits = true` is the preferred production posture.

When enabled, the runtime fails loudly if it cannot confirm expected protection, for example:
- invalid live bracket parameters,
- unresolved OCO stop legs,
- live native mode with unprotected positions detected by exit telemetry.

This is intentional. A failed run is safer than silently carrying unprotected live exposure.

---

## Telemetry and Auditability

The runtime now logs canonical exit-engine context into intraday context snapshots, including:
- policy fields,
- runtime mode,
- broker realization path,
- per-position protection metadata,
- whether any position is considered unprotected.

This gives one audit vocabulary across synthetic and native modes.

For parity review, the repo should now be read as having three aligned layers:
- runtime synthetic exits,
- runtime native broker realization,
- backtest synthetic exit simulation.

The microstructure can still differ, but the policy semantics should not.

---

## Operational Checklist

- Ensure Alpaca API keys are set in `.env`:
  - `ALPACA_API_KEY`
  - `ALPACA_SECRET_KEY`
  - `ALPACA_PAPER_URL`
- Verify `config/risk.toml` contains the intended exit policy fields for the active sleeve.
- If using EOD flatten automation, verify the service/timer paths for your host:
  - repository root contains `llm-quant-eod-flat.service`
  - additional systemd assets may live under `scripts/systemd/`
- Confirm the scheduled command runs `pq eod-flat` at the desired ET cutoff.
- Check logs for:
  - bracket validation failures,
  - OCO leg resolution failures,
  - unprotected position detection.

### Explicit pod coverage

Current repo evidence supports stock-session EOD flatten coverage for equity pods only:

- `default` via `scripts/systemd/llm-quant-eod-flat.service`
- `stock-bounded-paper` via `scripts/systemd/llm-quant-eod-flat-stock-bounded-paper.service`

This coverage is justified because:

- `stock-bounded-paper` explicitly sets `asset_class_filter = ["equity"]`
- `stock-bounded-paper` explicitly enables `eod_flatten_enabled = true`
- the stock bounded runbook describes supervised stock-lane validation with explainable EOD behavior
- `pq eod-flat` itself skips crypto semantics when flatten assessment returns `disabled_for_crypto`

Operationally, do not imply crypto coverage from the stock EOD units. If crypto ever needs a distinct flatten control, define and audit it separately.

---

## Parity Matrix

| Surface | Stop-loss | Partial TP | Trailing stop | EOD flatten | Notes |
|---|---|---|---|---|---|
| Intraday synthetic runtime | Canonical | Canonical | Canonical | Operational override | `trading/intraday.py` now delegates to canonical exit evaluation |
| Intraday native Alpaca runtime | Canonical policy, broker-realized | Canonical policy, broker-realized | Canonical policy, broker-realized | Operational override | Telemetry remains the audit source of truth |
| Daily / paper runtime | Canonical | N/A or synthetic by policy | N/A or synthetic by policy | Policy-governed | Daily path is policy-aligned even when no intraday loop exists |
| Backtest engine | Canonical synthetic | Canonical synthetic | Canonical synthetic | Canonical synthetic | Backtest now shares the same synthetic exit vocabulary instead of separate TP logic |

## Notes

- The exit engine standardizes policy; it does **not** guarantee profitable exits.
- Synthetic and native paths can still differ in market microstructure and fill quality.
- The important invariant is that the same exit policy vocabulary governs all modes.
- Backtest parity reduces logic drift risk, but it does **not** eliminate the need for live-vs-paper and native-vs-synthetic behavior review.
