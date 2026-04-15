# OCO / Broker-Managed Protection Audit

## Scope
Reviewed:
- `src/llm_quant/broker/intraday_orders.py`
- `src/llm_quant/cli.py`
- `src/llm_quant/config.py`
- `config/strategies/crypto.toml`
- relevant test search under `tests/`

## File-specific findings

### `src/llm_quant/broker/intraday_orders.py`
This file already implements the intraday broker-managed protection path.

Key functions:
- `place_oco_exits_for_buys(...)`
  - For new buy trades:
    - submits optional partial take-profit via `client.submit_limit_order(...)`
    - submits remainder protection via `client.submit_oco_order(...)`
  - resolves OCO leg ids with `_resolve_oco_legs(...)`
  - when `fail_on_unprotected=True`, raises if the remainder position does not end up with a stop
- `reconcile_orders(...)`
  - re-resolves missing OCO legs
  - after repeated leg-resolution failure, submits a standalone stop order fallback
  - replaces protection after partial fills / OCO TP fills
  - can also raise when `fail_on_unprotected=True`
- `update_trailing_stops(...)`
  - updates the live stop via `client.replace_order(...)` only when `state.oco_stop_order_id` exists

Conclusion:
- The OCO / protection order helper path is present and reasonably defensive already.
- This does **not** look like the blocker.

### `src/llm_quant/cli.py`
The actual blocker is in `_run_single_pod(...)`.

Relevant early guard:
- after `evaluate_position_exits(...)`
- if:
  - `broker.lower() == "alpaca"`
  - `exit_policy.fail_on_unprotected_exits`
  - `has_unprotected_crypto_positions(portfolio.positions, asset_class_map, exit_runtime)`
- then it aborts with:
  - `"Live crypto execution requires broker-managed protection."`

Later in the same function, the actual intraday OCO maintenance flow only runs when:
- `config.execution.intraday_enabled`
- `alpaca_client`
- `not log_only`
- `config.execution.intraday_use_oco`

That means:
- crypto Alpaca intraday can fail at the guard
- even though the broker-managed protection path exists
- because strategy config disables `intraday_use_oco`

Conclusion:
- The mismatch is in runtime/config wiring, not Alpaca helper logic.

### `src/llm_quant/config.py`
Key setting:
- `ExecutionConfig.intraday_use_oco: bool = True`

Conclusion:
- framework default already prefers broker-managed intraday protection
- no schema/model change appears necessary

### `config/strategies/crypto.toml`
Repo context already established:
- `intraday_use_oco = false`

Conclusion:
- this override disables the later intraday OCO workflow in CLI
- it directly conflicts with the live crypto guard requiring broker-managed protection

## Test audit
Searches under `tests/` found no direct matches for:
- `intraday_use_oco`
- `"Live crypto execution requires broker-managed protection."`
- `place_oco_exits_for_buys`
- `reconcile_orders`
- `update_trailing_stops`
- `submit_oco_order`

Conclusion:
- there does not appear to be existing direct regression coverage for this mismatch
- parent should likely add one focused test

## Recommended minimal fix

### Best fix: config-only
Change:
- `config/strategies/crypto.toml`

Set:
- `execution.intraday_use_oco = true`

Why this is the cleanest fix:
- aligns the crypto pod with the existing safety invariant
- activates already-implemented broker-managed protection
- avoids weakening the live safety guard
- avoids Alpaca client/order helper changes

### Optional small CLI improvement
In `src/llm_quant/cli.py`, function `_run_single_pod(...)`:
- keep the guard
- but improve its message and/or condition so the config mismatch is clearer

Minimal improvement example:
- current message:
  - `"Live crypto execution requires broker-managed protection."`
- suggested message:
  - `"Live crypto execution requires broker-managed protection; enable execution.intraday_use_oco for intraday Alpaca runs."`

I would **not** recommend removing the guard entirely.

## Exact functions / conditions parent should update

### Primary
- `config/strategies/crypto.toml`
  - set `execution.intraday_use_oco = true`

### Optional
- `src/llm_quant/cli.py`
- function:
  - `_run_single_pod`
- exact block:
  - the `has_unprotected_crypto_positions(...)` failure immediately after `evaluate_position_exits(...)`
- if parent adjusts logic, ensure consistency with the later OCO path:
  - `if config.execution.intraday_enabled and alpaca_client and not log_only and config.execution.intraday_use_oco: ...`

## Suggested tests
Add one or more focused tests for the parent branch:

1. Config regression:
- load crypto pod config
- assert `config.execution.intraday_use_oco is True`

2. CLI/runtime guard regression:
- Alpaca + intraday + crypto + `fail_on_unprotected_exits=true`
- with OCO disabled, expect the broker-managed-protection failure
- with OCO enabled, do not fail solely due to that guard

## Bottom line
- Cleanest minimal safe fix: **config-only**
- specifically: enable `intraday_use_oco` in `config/strategies/crypto.toml`
- no Alpaca client/order helper changes recommended from this audit