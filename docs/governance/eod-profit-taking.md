# EOD Profit-Taking Policy (Revertable)

## Overview
This repo now supports **daily profit taking** via two configurable policies:

1. **Fixed % take-profit override**
   - When enabled, every BUY uses a fixed take-profit price (default +3%).
   - This **overrides any LLM-provided take_profit** and standardizes exits.

2. **End-of-day (EOD) flatten**
   - At **3:55pm US/Eastern**, all positions are flattened via market orders.
   - This removes overnight exposure and forces daily resets.

Both policies are controlled entirely by config flags in `config/risk.toml` and can
be reverted without code changes.

---

## Fixed % Take-Profit (Override)

### How it’s calculated
When `take_profit_mode = "pct"`, the take-profit is computed as:

```
TP = entry_price * (1 + take_profit_pct)
```

Example:
- Entry: `127.00`
- `take_profit_pct = 0.03`
- Target: `130.81`

### Where it applies
- Only **BUY** orders submitted to Alpaca.
- If the bracket TP is invalid (<= entry or <= stop), the system falls back to a
  plain market order and logs a warning.

---

## EOD Flatten (3:55pm ET)

### How it works
- A dedicated command `pq eod-flat` checks the Alpaca market clock.
- If market is open and time is **>= 15:55 ET**, it:
  1. Cancels open orders
  2. Submits market orders to close all positions
  3. Logs trades with reason `eod_flatten`

### Early close days
On early-close sessions (e.g., 1pm ET), Alpaca reports `is_open = false` by 3:55pm,
so the EOD flatten **will skip** unless you run it earlier manually.

---

## Configuration (Revertable)

`config/risk.toml` (Track A and Track B):

```toml
# Take-profit (override LLM) + end-of-day flatten controls
+take_profit_mode = "pct"          # pct | rr
+take_profit_pct = 0.03             # fixed take-profit percent (3%)
+take_profit_rr = 2.0               # risk-reward multiple (if mode = rr)
+eod_flatten_enabled = true         # force flat at 3:55pm ET
+eod_flatten_time = "15:55"         # US/Eastern
```

### Rollback (no code change)
To revert to LLM-based or RR take-profit and disable EOD flatten:

```toml
take_profit_mode = "rr"
eod_flatten_enabled = false
```

---

## Operational Checklist
- Ensure Alpaca API keys are set in `.env`:
  - `ALPACA_API_KEY`
  - `ALPACA_SECRET_KEY`
  - `ALPACA_PAPER_URL`
- Install the provided systemd units in `scripts/systemd/` and update paths:
  - `scripts/systemd/llm-quant-eod-flat.service`
  - `scripts/systemd/llm-quant-eod-flat.timer`
  (Edit `WorkingDirectory`, `EnvironmentFile`, and `ExecStart` for your host.)
- Verify the timer runs `pq eod-flat` at 3:55pm ET.
- Check logs after EOD flatten to confirm order submission.

---

## Notes
- This policy does **not** guarantee profits; it enforces earlier exits and
  reduces overnight risk.
- For partial profit-taking or intraday re-entry logic, extend the broker
  execution layer to split bracket orders and add a cooldown policy.
