# Broker causal-ingestion and lineage review

## Files reviewed
- `docs/governance/runtime-truth-table.md`
- `src/llm_quant/broker/event_ledger.py`
- `src/llm_quant/broker/reconciliation.py`

## Exact functions / branches needing change

### `src/llm_quant/broker/event_ledger.py`

#### `append_event`
- Needs pre-insert rollback-safe validation path.
- Today it calls `_validate_event_causality(...)`, inserts, commits, then calls `validate_event_causal_closure(...)`.
- That means a causally-invalid event can be durably written before closure validation fails.
- Under deterministic/no-silent-fallback rules, ingestion should reject invalid events atomically, not persist-then-fail.
- Minimum contract-preserving fix:
  - run full closure validation against the would-be ledger state before commit, or
  - wrap insert + closure validation in a transaction and rollback on any `CausalIntegrityError` / `OrderingError`.

#### `_validate_event_causality`
- Too permissive for out-of-order valid-timestamp replay and missing intermediate lineage.
- Current checks only verify parent existence by `order_id`, not parent event ordering or required registration semantics.
- Gaps:
  - allows causal child events if any row exists for `parent_event_order_id`, even if parent is only a terminal/fill row and no registration/submission anchor exists.
  - allows replay with earlier `event_time` than parent so long as `sequence_id` is increasing.
  - does not require `ORDER_SUBMITTED` anchor before fills/cancels for the same `order_id`.
  - does not detect duplicate `order_id` reused across divergent symbols/sides/intent chains.
- Branches to tighten:
  - `_CAUSAL_EVENT_TYPES` branch using `parent_event_order_id`
  - `_FILL_EVENT_TYPES` branch using `parent_order_id`
- Minimum fix themes:
  - require explicit parent registration anchor for fill/terminal/protection children,
  - reject child event whose `event_time` precedes its parent anchor/event,
  - reject same `order_id` appearing with incompatible lineage identity.

#### `_event_exists`
- Too weak as lineage primitive.
- It only answers “does any event row exist for this order_id?”
- This is insufficient for:
  - distinguishing registered/submitted orders from stray fill-first rows,
  - checking whether parent lineage is known vs merely observed,
  - detecting missing intermediate events.
- Likely needs companion helpers rather than direct replacement, e.g.:
  - lookup first event for order,
  - lookup whether order has `ORDER_SUBMITTED`,
  - lookup canonical order identity tuple `(symbol, side, parent_order_id, event_chain_id, intent_type)`.

#### `validate_event_causal_closure`
- Needs stronger graph invariants, not just reachability/cycle checks.
- Current logic only enforces seen-parent-before-child in ledger replay order.
- Missing validations:
  - no requirement that each non-root order chain has an `ORDER_SUBMITTED` node,
  - no detection of fill-before-registration within same order_id,
  - no detection of duplicate order IDs with divergent execution lineage,
  - no detection that intermediate parent event is missing when `parent_event_order_id` points to an order that exists only later-stage.
- Exact loop branches needing enhancement:
  - `if event.event_type in _CAUSAL_EVENT_TYPES and event.order_id != chain_id`
  - `if event.event_type in _FILL_EVENT_TYPES and event.parent_order_id ...`
  - per-event accumulation into `links`
- Add closure-time checks for:
  - first event for each `order_id` must be `ORDER_SUBMITTED`,
  - one canonical identity per `order_id`,
  - parent/child `event_time` monotonicity where lineage edge exists,
  - fills cannot appear before submission anchor for their own order.

#### `rebuild_position_state_from_events`
- Should not silently tolerate missing intermediate events once replay starts.
- It relies on prior validation, but current validation is weaker than required.
- No direct logic change necessarily needed if closure validation is strengthened; otherwise replay can produce superficially valid positions from invalid lineage histories.

### `src/llm_quant/broker/reconciliation.py`

#### `persist_submitted_orders`
- Needs duplicate lineage identity enforcement before `INSERT OR REPLACE`.
- Current behavior overwrites existing submitted-order row for same `order_id`, which can silently collapse divergent execution identities.
- This violates deterministic/no-best-effort merge.
- Exact branch:
  - before `INSERT OR REPLACE INTO broker_submitted_orders`
- Minimum fix:
  - fetch existing row for `(pod_id, order_id)`,
  - compare canonical identity fields such as `symbol`, `side`, `intent_type`, `parent_order_id`,
  - raise explicit failure on mismatch instead of replacing.

#### `_require_submitted_order_lineage`
- Good reusable anchor, but too narrow for replay anomaly classification.
- It already raises `UNKNOWN_SUBMITTED_ORDER_LINEAGE`.
- Can be reused as the authoritative registration check for:
  - fill-before-registration,
  - orphan open orders,
  - unknown parent lineage.
- Likely needs sibling helper(s) returning timestamps/identity so reconciliation can compare broker fill time vs submitted_at and detect temporal inversion.

#### `reconcile_broker_orders`
- Primary fill-before-registration replay gap lives here.
- Current flow:
  - fetch order status,
  - resolve fills,
  - require lineage by order id,
  - insert fill row,
  - append ledger fill event.
- Gaps:
  - `_require_submitted_order_lineage(...)` only proves row exists, not that `submitted_at <= fill.fill_time`.
  - if a submitted row was inserted late/backfilled, fill-before-registration is silently accepted.
  - if same order_id is reused with a different execution identity, reconciliation dedupes by execution identity but does not reject divergent order identity.
- Exact branches needing checks:
  - inside `for decision in decisions:`
  - after `_require_submitted_order_lineage(conn, ..., order_id=fill.order_id)`
  - parent validation branch `if fill.parent_order_id and fill.parent_order_id not in fallback_records: ...`
- Minimum fixes:
  - compare fill time against submitted lineage timestamp,
  - validate parent lineage timestamp/identity before accepting child fill,
  - reject duplicate order-id / divergent identity cases before `INSERT OR IGNORE`.

#### `_resolve_fill_decisions`
- Needs explicit failure on duplicate order IDs with divergent execution identities if broker reports contradictory fills for same order.
- Current behavior keeps first non-correction per execution identity and ignores duplicate identities.
- Good for exact duplicates, but not for contradictory execution streams attached to same order_id lineage.
- Candidate enhancement:
  - if same `order_id` emits fills with conflicting normalized attributes under different identities that cannot be explained by correction/reversal semantics, raise explicit reconciliation error rather than accept both/ignore one.

#### `_validate_reconciliation_invariants`
- Best place to map several required failure modes, but missing temporal and registration-order checks.
- Existing checks already cover:
  - `FILL_WITHOUT_KNOWN_SUBMITTED_ORDER`
  - `FILL_WITHOUT_PARENT_ORDER`
  - `FILL_WITH_UNKNOWN_PARENT_ORDER`
  - `POSITION_SUM_MISMATCH`
- Missing:
  - fill-before-registration temporal violation,
  - open order / fill lineage identity divergence,
  - missing intermediate event between submitted parent and applied child in ledger.
- Exact sections to extend:
  - `for order_id, symbol, side, fill_qty, intent_type, parent_order_id, is_reversal in fill_rows:`
  - `for status in statuses:`
- Suggested additions:
  - join fill rows against `broker_submitted_orders.submitted_at` and fail if `fill_time < submitted_at`,
  - compare fill symbol/side/intent/parent against submitted lineage for same order_id,
  - compare ledger-first-event type per order and fail if non-submission anchor is observed.

## Candidate failure-mode mappings

### Ingestion / event-ledger side
- orphan causal child with no `parent_event_order_id` resolvable:
  - existing: `EVENT_CAUSAL_ORPHAN`
- child references nonexistent parent event order:
  - existing: `EVENT_CAUSAL_MISSING_PARENT_EVENT`
- fill references nonexistent parent order:
  - existing: `EVENT_CAUSAL_ORPHAN_FILL`
- child earlier than parent in causal time:
  - new explicit mapping needed, likely parallel to existing causal errors
- first event for an order is not `ORDER_SUBMITTED`:
  - new mapping needed; likely lineage/causal registration violation
- duplicate `order_id` with divergent `(symbol, side, parent_order_id, event_chain_id, intent_type)`:
  - new mapping needed
- missing intermediate event / parent anchor exists only as later-stage event:
  - currently partially collapses into `EVENT_CAUSAL_CHAIN_GAP`; should be made explicit or enforced through stronger anchor checks

### Reconciliation side
- fill/order references unknown submitted lineage:
  - existing: `UNKNOWN_SUBMITTED_ORDER_LINEAGE`
- applied fill row with no corresponding submitted order:
  - existing: `FILL_WITHOUT_KNOWN_SUBMITTED_ORDER`
- exit fill with no parent:
  - existing: `FILL_WITHOUT_PARENT_ORDER`
- exit fill with unknown parent order:
  - existing: `FILL_WITH_UNKNOWN_PARENT_ORDER`
- partial fill aggregate mismatch:
  - existing: `PARTIAL_FILL_RECONCILIATION_MISMATCH`
- position conservation mismatch:
  - existing: `POSITION_SUM_MISMATCH`
- ledger replay divergence:
  - existing: `EVENT LEDGER STATE DIVERGENCE`
- fill before submitted lineage timestamp:
  - new explicit mapping needed
- duplicate order id with divergent broker identity / lineage fields:
  - new explicit mapping needed
- open exit order with missing lineage:
  - existing: `OPEN_EXIT_ORDER_WITHOUT_PARENT_INTENT`
- open exit order with unknown parent:
  - existing: `OPEN_EXIT_ORDER_WITH_UNKNOWN_PARENT_INTENT`

## Existing helpers that can be reused

### From `event_ledger.py`
- `_coerce_event(...)`
  - useful for centralized normalization before any stricter causal checks.
- `_validate_replay_sequence_order(...)`
  - already enforces replay ordering invariants by `(event_time, sequence_id)`.
- `validate_event_causal_closure(...)`
  - best existing place to concentrate stronger full-ledger lineage invariants.
- `ledger_ordering_digest(...)`
  - helpful diagnostic surface if parent wants divergence reporting/tests.
- `_row_to_event(...)`
  - reusable for richer per-order scans.

### From `reconciliation.py`
- `_require_submitted_order_lineage(...)`
  - strongest current authoritative lineage lookup; should be reused rather than duplicated.
- `_execution_identity(...)`
  - reusable when checking duplicate order IDs with divergent execution identities.
- `_resolve_fill_decisions(...)`
  - already centralizes correction/reversal semantics; duplicate-identity rejection should extend here.
- `_validate_partial_fill_consistency(...)`
  - already enforces deterministic fill aggregation.
- `_validate_reconciliation_invariants(...)`
  - best place to add post-persistence hard failures for temporal/identity mismatches without widening public APIs.