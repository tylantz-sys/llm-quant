# Phase 5a runtime field mapping

Scope reviewed:
- `src/llm_quant/trading/exits.py`
- `src/llm_quant/trading/telemetry.py`
- `src/llm_quant/trading/ledger.py`
- `scripts/execute_decision.py`

## What runtime data exists now

Current runtime has three relevant layers:

1. **Exit policy/config intent** from `ExitPolicy` and `ExitRuntime`
   - partial TP enabled/pct/size
   - remainder TP multiplier
   - trailing enabled/pct
   - exit mode (`native` vs `synthetic`)
   - broker exit kind (`none` / `bracket` / `oco` / `market_only`)
   - EOD flatten enabled/time

2. **Per-position point-in-time exit telemetry** from `ExitTelemetry`
   - `symbol`
   - `entry_price`
   - `current_price`
   - `stop_loss`
   - `partial_target_price`
   - `trailing_stop_price`
   - `peak_price`
   - `partial_exit_taken`
   - `exit_mode`
   - `broker_exit_kind`
   - `uses_partial_take_profit`
   - `uses_trailing_stop`
   - `unprotected`

3. **Executed trade / profit-take event persistence**
   - `trades.exit_reason` and `trades.profit_take_reason` can classify exit trades
   - `profit_take_events` can persist:
     - `timestamp`
     - `trade_id`
     - `entry_batch`
     - `shares`
     - `price`
     - `notional`
     - `trigger_price`
     - `peak_price`
     - `drawdown_pct`
     - `realized_pnl`
     - `return_pct`
     - `rule_name`
     - `reason`
     - `metadata_json`

## Important current limitations

- `scripts/execute_decision.py` calls `evaluate_position_exits(..., states={})`, so intraday exit state is currently **ephemeral for that run only** and starts empty every invocation.
- I found no runtime path in the reviewed files that currently calls `log_profit_take_event(...)`.
- I found no reviewed-path persistence of:
  - first partial-take timestamp
  - trailing activation timestamp
  - peak-before-first-reduction snapshot
  - cumulative realized-after-first-TP
  - trade-close capture ratio / runner outcome
- `ExitTelemetry` is a **snapshot**, not an event history.
- `trades` persistence captures trade-level classification, but not enough lifecycle state to derive all requested Phase 5a metrics reliably.

## Desired field mapping

| Desired field | Current available source | Populate now? | Notes |
| --- | --- | --- | --- |
| `exit_taxonomy` / canonical profit-take reason | `trade.exit_reason` normalized by `normalize_profit_take_reason`; `profit_take_events.reason` normalized likewise | **Yes** | Already available for executed exit trades/events. Canonical values currently reviewed: `take_profit_partial`, `trailing_stop`. |
| `first_tp_timestamp` | Could use `profit_take_events.timestamp` **if** first partial TP event is explicitly logged; otherwise not stored | **Not now** | Runtime review shows no current call path writing profit-take events. Needs explicit event emission when first reduction executes. |
| `first_tp_size_shares` | Executed partial-sell trade `shares`; `profit_take_events.shares` if event logged | **Partially / not first-class now** | The partial trade row has shares, but “first TP size” is not persisted as a dedicated lifecycle field and cannot be isolated robustly without reliable first-event capture. |
| `first_tp_size_pct` | Config intent in `ExitPolicy.partial_take_profit_size`; can infer target fraction, not actual filled fraction | **Needs future capture** | Current runtime exposes intended partial size, not guaranteed realized first-reduction percentage of original position. |
| `first_tp_trigger_price` | `ExitTelemetry.partial_target_price`; `profit_take_events.trigger_price` column exists | **Needs event capture** | Target price exists in snapshot/config, but first TP trigger at execution time is not currently persisted in reviewed path. |
| `first_tp_fill_price` | Executed trade `price`; `profit_take_events.price` if logged | **Partially / not first-class now** | Fill exists on the trade row, but only as generic trade data, not attached as “first TP” lifecycle milestone. |
| `peak_unrealized_before_first_reduction` | `ExitTelemetry.peak_price` and `state.peak_price` exist | **Not now** | Only peak price snapshot is available, not locked to the moment immediately before first reduction, and not persisted across runs. Unrealized P&L value itself is not stored for that milestone. |
| `peak_price_before_first_reduction` | `ExitTelemetry.peak_price` / `state.peak_price` | **Not reliably now** | Available only as current in-memory snapshot. Because state is ephemeral in execute path, cannot rely on it as a trade-lifecycle metric. |
| `realized_after_first_tp` | `profit_take_events.realized_pnl` column exists; trade rows have notional/price/shares | **Needs future capture / aggregation logic** | Could be populated only if runtime emits follow-on events and/or stores cumulative realized P&L after first TP. Reviewed files do not currently do that. |
| `trailing_activation_timestamp` | Implied when `state.partial_exit_taken` becomes true and trailing becomes active; no persisted timestamp field | **Not now** | Current code can know trailing is active in the moment (`uses_trailing_stop` + `partial_exit_taken`), but does not record when activation first occurred. |
| `trailing_stop_price_at_activation` | `ExitTelemetry.trailing_stop_price` when `partial_exit_taken` is true | **Not reliably now** | Snapshot value exists, but activation-time value is not persisted as a milestone. |
| `giveback_after_peak` | Potential ingredients: `peak_price`, `price`, `drawdown_pct` column on `profit_take_events` | **Needs future capture** | Current event table has a place for drawdown/giveback-like data, but reviewed runtime does not compute/persist it. |
| `giveback_pct_after_peak` | Same as above | **Needs future capture** | No current computation/persistence in reviewed files. |
| `end_of_trade_capture_ratio` | Could theoretically derive from entry price, peak price, and final exit price(s) | **Needs future capture / explicit close aggregation** | Not computed anywhere now. Also requires durable peak-over-trade lifecycle and final realized exit aggregation. |
| `runner_outcome` | Could be inferred from final exit taxonomy on remainder after first TP | **Needs future capture / classification rule** | No explicit runner concept persisted now. Partial TP plus trailing/full close may exist as trade reasons, but no durable trade-level “runner outcome” field. |
| `remainder_exit_reason` | `trades.exit_reason` on later closing trade; `profit_take_events.reason` if emitted | **Partially now** | Possible to inspect later exit trade reason, but not tied together as a first-class “remainder outcome” on the trade lifecycle. |
| `eod_flatten_timestamp` | `assess_eod_flatten()` can determine due state from `now_et` and target time | **Not now** | Decision logic exists, but reviewed execution path does not persist an EOD profit-take/runner milestone event. |
| `exit_mode` | `ExitTelemetry.exit_mode`; payload from `build_exit_telemetry_payload()` | **Yes** | Available now as point-in-time runtime context. |
| `broker_exit_kind` | `ExitTelemetry.broker_exit_kind`; `BrokerExitPlan.kind` | **Yes** | Available now as point-in-time runtime context. |
| `partial_tp_enabled` / `partial_tp_pct` / `partial_tp_size` | `ExitPolicy` and telemetry payload policy section | **Yes** | Config intent is available now. |
| `trailing_enabled` / `trailing_stop_pct` | `ExitPolicy` and telemetry payload policy section | **Yes** | Config intent is available now. |
| `remainder_take_profit_mult` | `ExitPolicy.remainder_take_profit_mult` | **Yes** | Config intent available now; not an observed outcome metric. |
| `partial_exit_taken_flag` | `ExitTelemetry.partial_exit_taken`; `IntradayPositionState.partial_exit_taken` | **Snapshot only now** | Available in-memory / summary payload, but not durably stored as a trade lifecycle milestone in reviewed path. |

## Smallest practical conclusion for Phase 5a

### Can be made first-class immediately from current runtime without inventing new capture logic
- canonical `exit_taxonomy` / profit-take reason
- `exit_mode`
- `broker_exit_kind`
- config-intent fields:
  - partial TP enabled/pct/size
  - trailing enabled/pct
  - remainder TP multiplier
- generic event execution fields already supported by `profit_take_events` if runtime starts emitting rows:
  - timestamp
  - shares
  - price
  - trigger_price
  - peak_price
  - drawdown_pct
  - realized_pnl
  - return_pct
  - rule_name
  - reason

### Needs new runtime event capture, not just schema
- `first_tp_timestamp`
- `first_tp_size_pct` as realized rather than configured
- `peak_unrealized_before_first_reduction`
- `realized_after_first_tp`
- `trailing_activation_timestamp`
- `giveback_after_peak`
- `end_of_trade_capture_ratio`
- `runner_outcome`

## Recommended interpretation for parent/schema agent

If Phase 5a wants the **smallest correct first-class field set now**, fields should favor:
1. values already present in runtime at the moment an exit event occurs, and
2. values that can be written on `profit_take_events` rows without requiring historical reconstruction.

The reviewed runtime supports event-style capture of execution-time facts, but **not** post-hoc lifecycle metrics unless new state tracking is added across the trade lifecycle.