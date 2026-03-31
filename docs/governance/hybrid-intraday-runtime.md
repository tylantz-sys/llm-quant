# Hybrid Strategy Runtime (Claude Overlay + Intraday Profit-Taking)

## What Changed
- Promoted strategy specs in `data/strategies/*` now generate live signals.
- Claude acts as a **risk/size overlay** when `claude_overlay_only = true`.
- Intraday bars (Alpaca) are stored in `market_data_intraday` and used for
  real-time context + profit-taking.
- Profit-taking logic can trigger:
  - **Partial take-profit** (default +2% for 50%)
  - **Trailing stop** after partial (updated via order replace)
  - **Scale-in** entries
  - **1-bar re-entry cooldown**

## RTH Guard + Native Orders
- Intraday runs **skip** when Alpaca clock reports market closed.
- Profit-taking is implemented via **native Alpaca orders**:
  - Partial TP limit order (50% by default)
  - **OCO order for the remainder** (TP leg + stop leg)
  - Trailing stop updates the **OCO stop leg** via order replace
- The remainder TP is raised above the partial TP using
  `profit_take_remainder_tp_mult` so trailing has room to work.
- Intraday order state + statuses persist in `intraday_order_state` so reports
  can prove partial TP + trailing behavior.
- Overlay runs are tagged with `decision_type = overlay` in `llm_decisions`.

## Config (Revertable)
Edit `config/default.toml`:

```
[execution]
intraday_enabled = true
intraday_timeframe_minutes = 5
intraday_lookback_days = 10
claude_overlay_only = true
profit_take_partial_pct = 0.02
profit_take_partial_size = 0.50
profit_take_remainder_tp_mult = 2.0
trailing_stop_pct = 0.015
scale_in_tranches = 3
reentry_cooldown_bars = 1
```

**Rollback:**
- Set `claude_overlay_only = false` to return to Claude-only trading.
- Set `intraday_enabled = false` to disable intraday runs.
- Set `profit_take_partial_pct = 0` and `trailing_stop_pct = 0` to disable
  profit-taking exits.

## Operational Notes
- Intraday runs disable bracket orders and rely on native OCO/limit orders.
- Strategy signals are merged and capped by `risk.max_position_weight`.
- Intraday runs are de-duped per 5‑minute slot via `data/locks/intraday_{pod}.lock`.
- See `docs/governance/runtime-truth-table.md` for mode-by-mode behavior.

## Data Upsert Guardrails
Daily and intraday fetch/upsert paths are protected so E2E runs don’t hang when
the DuckDB file is busy:
- A file lock gates upserts, with a timeout and retry loop.
- Upserts prefer a **bulk insert** (`INSERT OR REPLACE … SELECT`) and fall back to
  row-by-row inserts if needed.
- If the lock or upsert times out, the run logs a warning and continues without
  blocking the rest of the pipeline.

Config lives under `[data]` in `config/default.toml`:

```
db_lock_timeout_seconds = 30.0
db_lock_retry_seconds = 0.5
db_upsert_timeout_seconds = 30.0
db_upsert_max_retries = 2
db_upsert_retry_seconds = 1.0
```
