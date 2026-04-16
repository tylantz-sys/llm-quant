# GLD Structural Successor Family Shape Recommendation

Date: 2026-04-16  
Author: sub-agent `artifact-successor-shape`  
Status: discovery-only recommendation artifact for parent-agent consumption

## Recommendation summary

Create a new governed GLD family rather than extending `gld-regime-starter-*`.

### Recommended family root
`gld-structural-confirmation`

### Recommended first slug
`gld-structural-confirmation-v1`

### If multiple variants are needed
Use sibling slugs under the same new family rather than continuing the prior numbering lineage:

- `gld-structural-confirmation-v1`
- `gld-structural-confirmation-v2`
- `gld-structural-confirmation-v3`

Avoid `gld-regime-starter-v9` style naming because governance should treat this as a new structural thesis, not a minor continuation of an exhausted parameter family.

## Governed artifact layout to use later

For the first governed implementation candidate, the expected lifecycle layout should be:

- `data/strategies/gld-structural-confirmation-v1/mandate.yaml`
- `data/strategies/gld-structural-confirmation-v1/hypothesis.yaml`
- `data/strategies/gld-structural-confirmation-v1/data-contract.yaml`
- `data/strategies/gld-structural-confirmation-v1/research-spec.yaml`

Then, only after frozen-spec backtesting and validation:

- append experiment entries to `data/strategies/experiment-registry.jsonl`
- `data/strategies/gld-structural-confirmation-v1/robustness.yaml`
- `data/strategies/gld-structural-confirmation-v1/paper-trading.yaml`

A proposal/design note is governance-consistent and can be useful before implementation:

- `docs/governance/gld-structural-confirmation-successor-spec-v1-proposal-2026-04-16.md`

## How this family should differ from `gld-regime-starter`

### Old family framing
The `gld-regime-starter` line appears to have reached a frontier where:
- some variants improved hit rate but not profit factor
- some variants improved profit factor but degraded hit rate
- bridge variants did not resolve the tradeoff

That is evidence of a structural ceiling, not just a missing parameter tweak.

### New family framing
The new family should be framed as a gold-specific structural confirmation line whose goal is to trade GLD only when the move is supported by stronger causal or contextual evidence than the predecessor family required.

This means the family mandate and hypothesis should explicitly emphasize:
- breakout confirmation
- gold-specific cross-asset confirmation
- macro gating more specific than broad VIX-only risk filters
- better monetization / exit realization logic, if the execution architecture allows it

## Governance-safe mandate framing

Suggested mandate direction:

> Exploit GLD directional continuation only when price action is confirmed by gold-relevant structural evidence, with the goal of improving both trade quality and monetization relative to the `gld-regime-starter` family.

## Governance-safe hypothesis framing

Suggested hypothesis direction:

> GLD trends become more tradeable when entries require gold-specific structural confirmation rather than broad regime proxies alone; combining stronger confirmation quality with improved exit monetization may break the hit-rate versus profit-factor tradeoff observed in the `gld-regime-starter` family.

## Lineage and predecessor framing

This should be treated as a fresh governed line:
- no inherited pass status
- no inherited frozen-hash lineage
- no implicit carry-forward of robustness, walk-forward, paper, or promotion evidence

If predecessor context is helpful, keep it informational only, for example:
- `predecessor_strategy_family: "gld-regime-starter"`
- `design_goal: "address structural frontier observed in regime-starter variants"`

## Recommended family organization

Most governance-safe sequence:

1. `gld-structural-confirmation-v1`
   - base deterministic confirmation version
   - full-entry/full-exit if partial exits are not yet practical

2. `gld-structural-confirmation-v2`
   - same family, but with richer macro gate if symbol/data support is safe

3. `gld-structural-confirmation-v3`
   - same family, but only if justified, with staged realization / target-ladder behavior

This keeps each sibling version hypothesis-distinct and reduces the appearance of blind parameter sweeping.

## Evidence reviewed

- `docs/governance/quant-lifecycle.md`
- `docs/governance/spy-deterministic-strategy-governance-summary.md`
- `docs/governance/strategy-artifact-status-matrix.md`
- `docs/governance/soxx-qqq-lead-lag-successor-spec-v2-proposal-2026-04-01.md`

## Notes

This file is not a governed lifecycle artifact for a strategy slug. It is a discovery summary created only because the tool workflow required an on-disk file for completion. The recommendation itself remains implementation-neutral and does not claim any backtest, robustness, walk-forward, paper, or promotion status.