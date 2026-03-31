# Runtime Truth Table (Modes, Sleeves, and Guards)

This page is the runtime source of truth for what executes, what is skipped, and why.

## Core Mode Matrix (`intraday_enabled x broker`)

| intraday_enabled | broker | Data Source | Order Path | Canonical Exit Policy | Broker Realization Path | RTH Guard | Run Lock | Exit State / Audit |
|---|---|---|---|---|---|---|---|---|
| `false` | `paper` | Yahoo daily (`market_data_daily`) | Paper executor | Canonical exit engine | Synthetic / simulated | No | No | Decision telemetry |
| `false` | `alpaca` | Yahoo daily (`market_data_daily`) | Alpaca native entry | Canonical exit engine | Native bracket TP/SL | No | No | Broker state + telemetry |
| `true` | `paper` | Alpaca 5m (`market_data_intraday`) + daily macro | Paper executor | Canonical exit engine | Synthetic partial TP / trailing / stop-loss | Configurable (`intraday_rth_guard`) | Yes (5m slot) | `intraday_position_state` + context snapshots |
| `true` | `alpaca` + `intraday_use_oco=true` | Alpaca 5m (`market_data_intraday`) + daily macro | Alpaca market entry + native exit orders | Canonical exit engine | Partial TP limit + OCO remainder + trailing stop management | Configurable (`intraday_rth_guard`) | Yes (5m slot) | `intraday_position_state` + `intraday_order_state` + context snapshots |
| `true` | `alpaca` + `intraday_use_oco=false` | Alpaca 5m (`market_data_intraday`) + daily macro | Alpaca market/limit orders | Canonical exit engine | Synthetic partial TP / trailing / stop-loss | Configurable (`intraday_rth_guard`) | Yes (5m slot) | `intraday_position_state` + context snapshots |

## Canonical Exit Vocabulary

Use these terms consistently:

- **canonical exit engine** — the single policy layer that decides exit behavior
- **exit policy** — thresholds and flags loaded from risk config
- **broker realization path** — how the active runtime expresses that policy
- **synthetic monitoring** — runtime-generated exit signals rather than broker-resting orders
- **native resting orders** — bracket / OCO / stop orders maintained at the broker
- **exit state** — persisted position/order state plus telemetry that explains current protection
- **EOD flatten override** — operational command that enforces end-of-day flatten when enabled

## Sleeve Mandates

| Pod | Asset Filter | Signal Source | Claude Role | Hours | Broker Realization Path | Scale-In |
|---|---|---|---|---|---|---|
| `default` | `["equity","fixed_income"]` | `strategy_overlay` (`strategy_set=promoted_default`) | Strict governor (scale/reject only) | RTH-only (`intraday_rth_guard=true`) | Native OCO intraday, native brackets daily | 3 |
| `commodities` | `["commodity"]` | `llm` | Primary signal generator | RTH-only (`intraday_rth_guard=true`) | Native OCO intraday | 2 |
| `crypto` | `["crypto"]` | `strategy_overlay` (`strategy_set=promoted_crypto`) | Strict governor (scale/reject only) | 24/7 (`intraday_rth_guard=false`) | Synthetic monitoring (`intraday_use_oco=false`) | 2 |
| `crypto-ethbtc-paper` | `["crypto"]` | `strategy_overlay` (`strategy_set=candidate_crypto`) | Strict governor (scale/reject only) | 24/7 (`intraday_rth_guard=false`) | Synthetic monitoring (`intraday_use_oco=false`) | 2 |

## Cross-Cutting Guards

- Overlay starvation guard: in overlay mode, promoted-required symbols must be fresh during RTH or the overlay call is skipped and logged as a no-trade overlay decision.
- Governor invariants (strict mode): no new symbols, no side flips, no stop/take-profit drift, bounded weight scaling.
- Governor fallback: strict policy violations convert the full candidate set to `HOLD` for that run.
- Drawdown correctness: `peak_nav` is computed from persisted `portfolio_snapshots` each run and injected into risk checks.
- Expectancy gate: if realized expectancy over the configured closed-trade window is negative, BUY target weights are scaled by `expectancy_negative_scale`.
- Intraday de-dup: one run per pod per 5-minute slot via `data/locks/intraday_{pod}.lock`.
- Exit protection guard: if `fail_on_unprotected_exits = true`, the runtime fails loudly when native live protection cannot be verified.
- Exit telemetry guardrail: intraday context snapshots record policy, runtime mode, broker realization path, and per-position protection metadata.

## Drift / Gotchas

- Synthetic monitoring and native broker orders can still diverge in market microstructure and fill sequence even when governed by the same canonical exit policy.
- `intraday_use_oco=true` does not mean “different policy”; it means the same policy is realized through native broker orders instead of synthetic monitoring.
- If OCO legs cannot be resolved in live native mode and `fail_on_unprotected_exits = true`, the run fails instead of silently degrading.
- If intraday bars are missing/stale for promoted-required symbols, overlay intentionally emits a no-trade decision for that slot.
- Crypto uses synthetic monitoring by design (`intraday_use_oco=false`) to avoid broker OCO parity issues while still using the canonical exit engine.

## Related References

- `docs/governance/eod-profit-taking.md`
- `docs/governance/hybrid-intraday-runtime.md`
- `README.md`
