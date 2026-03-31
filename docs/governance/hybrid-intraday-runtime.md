# Hybrid Strategy Runtime (Multi-Sleeve + Intraday Profit-Taking)

## What Changed
- Promoted strategy specs in `data/strategies/*` now generate live signals for
  strategy-overlay sleeves.
- Claude acts as a **risk/size overlay** when `claude_overlay_only = true`
  and can be configured as a strict governor.
- Runtime source selection is explicit per pod via
  `execution.signal_source = llm | strategy_overlay`.
- Strategy sets are catalog-driven via `config/strategies/catalog.toml`.
- Strategy rotation can enable only the **Top‑N** performers (configurable).
- Intraday bars (Alpaca) are stored in `market_data_intraday` and used for
  real-time context + profit-taking.
- Profit-taking logic can trigger:
  - **Partial take-profit** (default +2% for 50%)
  - **Trailing stop** after partial (updated via order replace)
  - **Scale-in** entries
  - **1-bar re-entry cooldown**
- Expectancy gate throttles new BUY sizes when recent realized expectancy is
  negative (pod-level).

## Sleeve Mandates

- `default`: promoted equity/fixed-income overlay only, RTH guard on.
- `commodities`: dedicated commodity sleeve (`DBA`, `GLD`, `SLV`, `USO`),
  independent Claude signals, RTH guard on.
- `crypto`: 24/7 **strategy-first** sleeve (`signal_source=strategy_overlay`,
  `strategy_set=promoted_crypto`) with Claude as strict governor and synthetic
  intraday exits (`intraday_use_oco=false`).

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
signal_source = "strategy_overlay"
strategy_set = "promoted_default"
overlay_governor_strict = true
overlay_max_upscale = 1.25
overlay_max_downscale = 0.0
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
- Or set `signal_source = "llm"` to bypass strategy-overlay mode.
- Set `intraday_enabled = false` to disable intraday runs.
- Set `profit_take_partial_pct = 0` and `trailing_stop_pct = 0` to disable
  profit-taking exits.

## Operational Notes
- Intraday runs disable bracket orders and rely on native OCO/limit orders.
- Strategy signals are merged and capped by `risk.max_position_weight`.
- Strategy group caps + regime multipliers can scale weights before execution.
- Intraday runs are de-duped per 5‑minute slot via `data/locks/intraday_{pod}.lock`.
- Overlay starvation guard skips overlay model calls when promoted-required bars
  are missing/stale during RTH and logs a deterministic no-trade overlay
  decision with explicit reason.
- Strict governor invariants are enforced post-LLM:
  symbol subset only, no side flips, no stop/take-profit drift, bounded sizing.
- On governor policy violation or overlay failure, run falls back to all-HOLD
  for candidate signals and logs policy violations in context telemetry.
- Drawdown uses persisted `peak_nav` from `portfolio_snapshots`.
- Expectancy gate telemetry is written into intraday context snapshots:
  `expectancy_gate_active`, `expectancy_value`, `expectancy_sample_size`,
  `buy_scale_applied`.
- Validate crypto promotion readiness with:
  `python scripts/validate_crypto_promotion.py --set promoted_crypto`.
- Promotion criteria checklist:
  `docs/governance/crypto-strategy-promotion.md`.
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
