# Near-Close Broker Smoke Runbook

## Purpose

This runbook closes the currently identified broker-validation gaps by defining a
small, supervised, broker-authoritative paper session and the exact checks to
run immediately afterward.

It is intentionally separate from the existing `default` and `crypto` pods
because the promoted sets are empty by governance and may legitimately produce
no trades.

## Smoke Pod

- Pod id: `nearclose-broker-smoke`
- Config overlay: `config/strategies/nearclose-broker-smoke.toml`

### Current smoke-pod assumptions

- Uses `strategy_overlay`
- Uses `candidate_crypto`
- Uses crypto-only scope
- Uses tiny configured capital
- Uses `initial_capital_source = "config"`
- Disables strategy rotation
- Uses `intraday_use_oco = false` for the first reconciliation pass
- Uses strict governor behavior
- Caps risk to a single tiny trade

## Important constraint

This pod is **minimal-candidate**, not truly single-symbol.

The current non-empty candidate supply available in governed config is
`candidate_crypto`, which resolves to the frozen strategy:

- `eth-btc-ratio-mean-reversion-v5`

That strategy requires both:

- `ETH-USD`
- `BTC-USD`

So this closes the “empty promoted set” gap and the “tiny bounded broker test”
gap, but not the stricter “single-symbol” gap.

If strict one-symbol scope is required later, add a dedicated temporary
single-symbol strategy set or a separate one-symbol smoke pod.

## Pre-run steps

### 1. Register the pod

```bash
python -m llm_quant.cli pods create nearclose-broker-smoke --strategy custom --capital 1000
```

If it already exists, skip creation.

### 2. Confirm the pod is present

```bash
python -m llm_quant.cli pods list
```

### 3. Rehearse with dry-run

```bash
python -m llm_quant.cli run --pod nearclose-broker-smoke --broker alpaca --dry-run
```

Expected dry-run outcome:

- the pod loads successfully
- candidate crypto strategy supply is non-empty
- market context builds
- no broker tables are required yet for dry-run success
- if no trade is proposed, that is still a valid runtime rehearsal result

## Live supervised smoke run

Run during a supervised low-risk window.

```bash
python -m llm_quant.cli run --pod nearclose-broker-smoke --broker alpaca
```

## Broker tables to inspect afterward

The first successful broker-authoritative run should initialize and/or populate:

- `broker_submitted_orders`
- `broker_fill_events`
- `broker_event_ledger`
- `broker_position_lifecycle`

## Post-run SQL checks

### Submitted orders

```sql
SELECT
  pod_id,
  order_id,
  symbol,
  side,
  qty,
  intent_type,
  parent_order_id,
  exit_reason,
  status,
  submitted_at,
  updated_at
FROM broker_submitted_orders
WHERE pod_id = 'nearclose-broker-smoke'
ORDER BY submitted_at DESC, order_id DESC
LIMIT 20;
```

### Fill events

```sql
SELECT
  pod_id,
  order_id,
  symbol,
  side,
  fill_qty,
  fill_price,
  fill_time,
  execution_id,
  execution_ref,
  broker_fill_key,
  lifecycle_state,
  is_correction,
  is_reversal
FROM broker_fill_events
WHERE pod_id = 'nearclose-broker-smoke'
ORDER BY fill_time DESC, order_id DESC
LIMIT 50;
```

### Event-ledger lineage

```sql
SELECT
  pod_id,
  sequence_id,
  event_time,
  order_id,
  parent_order_id,
  event_chain_id,
  parent_event_order_id,
  symbol,
  event_type,
  status,
  filled_qty,
  remaining_qty
FROM broker_event_ledger
WHERE pod_id = 'nearclose-broker-smoke'
ORDER BY sequence_id DESC
LIMIT 50;
```

### Position lifecycle

```sql
SELECT
  pod_id,
  symbol,
  state,
  quantity,
  avg_price,
  opened_at,
  closed_at,
  last_order_id,
  parent_order_id,
  updated_at
FROM broker_position_lifecycle
WHERE pod_id = 'nearclose-broker-smoke'
ORDER BY updated_at DESC, symbol;
```

## What must be true

### Order origin / lineage

- fills reference known submitted orders
- exit orders reference valid parent orders where applicable
- event-ledger `sequence_id` is monotonic for the pod
- `event_chain_id` and `parent_event_order_id` preserve lineage

### Reconciliation correctness

- no duplicate fill identities
- no quantity mismatch between lifecycle state and event-ledger rebuild
- no position invariant failure
- no ordering invariant failure

### Bounded-risk behavior

- at most one tiny trade is routed
- most cash remains idle
- no unexpected multi-position expansion occurs

## Result classification

### PASS

- pod runs successfully
- broker tables are created
- at least the order/reconciliation surfaces initialize cleanly
- invariants hold
- lineage is auditable

### SOFT PASS

- pod runs successfully
- no trade occurs
- decision path is fresh and candidate supply is valid
- broker tables may remain empty if no routeable order was emitted

### FAIL

- pod cannot load
- candidate supply is unexpectedly empty
- broker client init fails
- broker tables fail to initialize after a routeable run
- reconciliation raises ordering or invariant errors

## Follow-up after first pass

If the first smoke pass is clean:

1. expand from minimal-candidate to stricter broker coverage
2. test `intraday_use_oco = true`
3. consider a dedicated one-symbol smoke path if still required
4. write a short final audit note with actual timestamps and table evidence
