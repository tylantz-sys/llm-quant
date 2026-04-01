# Exit Taxonomy Audit and Normalization Plan

## Scope reviewed
- `src/llm_quant/trading/exits.py`
- `src/llm_quant/trading/ledger.py`
- `src/llm_quant/trading/telemetry.py`
- `tests/test_trading/test_profit_taking_telemetry.py`
- `scripts/execute_decision.py`

## Current exit reasons observed

### Runtime / exit engine
Defined in `src/llm_quant/trading/exits.py` via `TradeSignal.exit_reason`:
- `stop_loss`
- `tp_partial`
- `trailing_stop`

Related non-trade exit status reasons also present in EOD assessment:
- `disabled`
- `market_closed`
- `before_cutoff`
- `due`

These EOD values are decision-status reasons, not trade exit reasons.

### Ledger persistence
`src/llm_quant/trading/ledger.py` persists:
- `exit_reason` directly from `ExecutedTrade.exit_reason`
- `is_profit_take = bool(trade.exit_reason)`
- `profit_take_reason = trade.exit_reason`

Observed implication:
- Any non-null exit reason, including `stop_loss`, is currently labeled as a profit-take.

### Profit-take telemetry
`src/llm_quant/trading/telemetry.py` writes `profit_take_events.reason` as a free-form string.
Test coverage uses:
- `tp_partial`

### Tests
`tests/test_trading/test_profit_taking_telemetry.py` asserts:
- `tp_partial`
- `trailing_stop`

and indirectly treats both as profit-taking reasons because:
- `is_profit_take` is expected to be `True`
- `profit_take_reason` equals the exit reason string

### Script/runtime wiring
`scripts/execute_decision.py`:
- evaluates canonical exit signals
- logs executed trades
- includes exit telemetry payload in output summary

It does not currently normalize or classify exit reasons before persistence.

## Inconsistencies

1. **Profit-taking vs generic exit reasons are conflated**
   - `ledger.py` marks any `exit_reason` as `is_profit_take=True`.
   - This incorrectly classifies `stop_loss` as profit-taking.

2. **No canonical taxonomy boundary**
   - Runtime uses specific reasons (`stop_loss`, `tp_partial`, `trailing_stop`).
   - Telemetry accepts arbitrary `reason` strings.
   - Ledger stores both `exit_reason` and `profit_take_reason` with no validation.

3. **EOD reason strings are in a separate semantic category**
   - `disabled`, `market_closed`, `before_cutoff`, `due` are EOD decision-state reasons.
   - They should not share the same namespace as executed trade exit reasons.

4. **`tp_partial` naming is narrow and abbreviated**
   - It is understandable internally, but less explicit than a canonical external taxonomy.
   - If more take-profit paths are added, abbreviation may become inconsistent.

5. **Trailing-stop classification is ambiguous**
   - In current tests, `trailing_stop` is treated as profit-taking.
   - Semantically, trailing stop is an exit mechanism that may realize profit, but it is not itself a take-profit target.
   - Phase 5a should decide this explicitly instead of inferring via non-null `exit_reason`.

## Recommended canonical taxonomy for Phase 5a

Use a single canonical **trade exit reason** taxonomy for executed exits:

- `stop_loss`
- `take_profit_partial`
- `trailing_stop`
- `eod_flatten`

Optional future-safe extension if full target exits are added later:
- `take_profit_full`

## Recommended normalization mapping

Map existing runtime values to canonical persisted values:

- `stop_loss` -> `stop_loss`
- `tp_partial` -> `take_profit_partial`
- `trailing_stop` -> `trailing_stop`

For future EOD-generated liquidation trades:
- executed EOD close -> `eod_flatten`

Keep EOD assessment status reasons separate under a distinct concept, e.g.:
- `eod_flatten_status`: `disabled | market_closed | before_cutoff | due`

## Recommended classification rule

Do not derive profit-taking attribution from `bool(exit_reason)`.

Instead classify profit-taking with an allowlist:
- profit-taking reasons:
  - `take_profit_partial`
  - `take_profit_full` if introduced
  - possibly `trailing_stop` **only if** the team intentionally defines trailing-stop exits as part of the profit-taking workstream

Recommended Phase 5a default:
- `stop_loss` -> not profit-taking
- `take_profit_partial` -> profit-taking
- `trailing_stop` -> profit-taking only if explicitly included by product/governance intent
- `eod_flatten` -> not profit-taking by default

## Practical normalization plan

1. **Canonicalize at source**
   - `src/llm_quant/trading/exits.py` should emit canonical strings rather than aliases.
   - Replace `tp_partial` with `take_profit_partial`.

2. **Preserve semantic separation**
   - Keep trade exit reasons separate from EOD assessment/status reasons.
   - Do not mix `disabled`, `due`, etc. into trade `exit_reason`.

3. **Enforce persistence consistency**
   - `src/llm_quant/trading/ledger.py` should treat:
     - `exit_reason` as the canonical trade exit reason
     - `profit_take_reason` as populated only for profit-taking-classified reasons
     - `is_profit_take` from explicit membership, not truthiness

4. **Align telemetry naming**
   - `src/llm_quant/trading/telemetry.py` should accept/store canonical reason values for executed profit-taking events.
   - If free-form reasons remain allowed, Phase 5a should still standardize all internal callers to canonical values.

5. **Update tests to canonical names**
   - `tests/test_trading/test_profit_taking_telemetry.py` should assert canonical values.
   - Existing `tp_partial` expectations should migrate to `take_profit_partial`.

6. **Keep summary/output identical in shape, normalized in values**
   - `scripts/execute_decision.py` likely needs no shape changes if upstream values are canonical.
   - Any surfaced exit reason strings should match the canonical taxonomy.

## Recommended canonical contract

### Trade exit reasons
- `stop_loss`
- `take_profit_partial`
- `trailing_stop`
- `eod_flatten`

### EOD flatten status reasons
- `disabled`
- `market_closed`
- `before_cutoff`
- `due`

## Key Phase 5a decision to lock
The main open policy question is whether `trailing_stop` belongs in profit-taking attribution.

Recommended answer for consistency with current tests and workstream intent:
- treat `trailing_stop` as a valid profit-taking reason for telemetry attribution,
- but still keep it distinct from target-hit exits in the canonical taxonomy.

That yields:
- profit-taking subset: `take_profit_partial`, `trailing_stop`
- non-profit-taking exit reasons: `stop_loss`, `eod_flatten`