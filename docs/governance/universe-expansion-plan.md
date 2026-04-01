# Universe Expansion Plan

This document defines the next execution move for expanding the `llm-quant` research universe in a controlled way before scaling deeper validation across more strategies.

It is designed to work alongside:

- `README.md`
- `docs/research/institutional-quant-guide.md`
- `docs/governance/strategy-thorough-testing-plan.md`
- `docs/governance/strategy-promotion-execution-plan.md`
- `docs/governance/strategy-promotion-gap-closure-plan.md`
- `docs/governance/strategy-artifact-status-matrix.md`

If this plan conflicts with a canonical governance requirement, the canonical requirement wins. This document defines execution order and research expansion structure, not a lower standard.

---

## Purpose

The purpose of this plan is to answer three practical questions:

1. Which additional assets should enter the research universe next?
2. How should those assets be grouped into candidate strategy relationships?
3. In what order should universe expansion feed the testing pipeline?

This plan exists to prevent random ticker sprawl.

The goal is not to add more symbols just because they are available. The goal is to:

- expand coverage intentionally
- create better candidate relationships
- increase the number of testable strategies
- preserve research discipline and promotion integrity

---

## Current Execution Order

The cleanest current execution order is:

1. keep `eth-btc-ratio-mean-reversion-v5` running in paper
2. start auditing `d7-tqqq-stacked-credit`
3. create and rank the next shortlist of symbols and relationships for expansion

This means universe expansion starts now, while the strongest existing candidate continues collecting paper evidence.

---

## Phase 1 — Expand the Research Universe

Phase 1 is about defining the next approved asset pool for research.

### Crypto starter universe

The first approved crypto expansion set is:

- `BTC`
- `ETH`
- `SOL`
- `AVAX`
- `DOGE`

These were chosen because they are:

- liquid
- widely followed
- already aligned with the current crypto research direction
- suitable for pair, spread, momentum, and rotation research

### ETF starter universe

The first approved ETF expansion set is:

- `QQQ`
- `TLT`
- `SOXX`
- `XLE`
- `XLU`
- `GLD`
- `SLV`
- `LQD`
- `HYG`

These were chosen because they provide coverage across:

- growth / technology
- duration / rates
- semiconductors
- cyclicals vs defensives
- commodities / metals
- investment-grade credit
- high-yield credit

Together, these ETFs provide a compact but expressive macro and cross-asset research set.

---

## Why These Universes Come First

This universe is intentionally small enough to manage but broad enough to generate multiple strong research families.

It supports:

- crypto relative value
- crypto trend / momentum
- equity-duration rotation
- cyclicals vs defensives
- metals spread relationships
- credit spread / risk appetite relationships

This is the correct next step because it gives the system more high-quality building blocks without forcing immediate promotion-track work on every idea.

---

## Phase 2 — Convert the Universe into Candidate Relationships

Once the universe is defined, the next step is to turn it into candidate research relationships.

### Crypto candidate relationships

The first crypto shortlist should include:

- `ETH/BTC`
- `SOL/BTC`
- `ETH/SOL`
- `AVAX/ETH`
- `DOGE/BTC`

These can support research in:

- ratio mean reversion
- relative momentum
- risk-on / beta rotation
- spread dislocation setups

### ETF candidate relationships

The first ETF shortlist should include:

- `QQQ/TLT`
- `SOXX/QQQ`
- `XLE/XLU`
- `GLD/SLV`
- `LQD/HYG`

These can support research in:

- macro risk-on / risk-off rotation
- semiconductor leadership vs broad tech
- cyclicals vs defensives
- precious metals spread behavior
- credit spread / stress sensitivity

---

## Phase 3 — Map Candidates to Strategy Families

Each candidate relationship should be assigned to a strategy family before testing begins.

Approved candidate family buckets:

- pairs / ratio mean reversion
- momentum
- rotation
- spread / relative value
- risk-on / risk-off regime expression

This prevents a symbol pair from being treated as a strategy by itself.

For example:

- `ETH/BTC` can belong to pairs mean reversion and relative momentum
- `QQQ/TLT` can belong to rotation and macro regime expression
- `GLD/SLV` can belong to spread / relative value
- `LQD/HYG` can belong to credit risk appetite tracking

---

## Phase 4 — Rank Candidates Before Full Testing

Not every candidate should immediately enter the full testing ladder.

Each candidate should be placed into one of three tiers.

### Tier 1 — Test first
Use for candidates that are:

- economically interpretable
- strongly aligned with existing repo research
- likely to produce a clean frozen research spec
- operationally practical to validate

Initial Tier 1 candidates:

- `ETH/BTC`
- `SOL/BTC`
- `QQQ/TLT`
- `XLE/XLU`
- `LQD/HYG`

### Tier 2 — Test after Tier 1
Use for candidates that are interesting but slightly less urgent or less proven.

Initial Tier 2 candidates:

- `ETH/SOL`
- `SOXX/QQQ`
- `GLD/SLV`
- `AVAX/ETH`

### Tier 3 — Idea bank only
Use for candidates that are exploratory or currently too noisy to prioritize.

Initial Tier 3 candidates:

- `DOGE/BTC`

Tier 3 candidates may still be useful, but they should not consume early promotion-track attention unless stronger evidence appears.

---

## Ranking Rules

Candidates should be ranked using the following logic:

### Rank higher if:
- the relationship has a clear economic rationale
- the assets are liquid and observable
- the pair maps cleanly to an existing strategy family
- the candidate fits current repo mandates
- the runtime path is practical for later paper testing

### Rank lower if:
- the relationship is mostly narrative and weakly testable
- the assets are too noisy relative to expected signal quality
- the candidate requires a large amount of new operational support
- the candidate lacks a clean hypothesis boundary

---

## How Universe Expansion Feeds the Testing Ladder

Universe expansion does not replace the standard validation ladder. It feeds it.

The intended path is:

1. expand the universe
2. generate candidate relationships
3. assign strategy family
4. write and freeze research spec
5. run baseline backtest
6. run robustness
7. run walk-forward
8. move winners to paper
9. package only complete evidence stacks for promotion review

This keeps expansion fast but controlled.

---

## Active and Near-Term Work Queue

### Keep running
- `eth-btc-ratio-mean-reversion-v5` remains the active paper candidate

### Audit next
- `d7-tqqq-stacked-credit`

### Build next shortlist from expanded universe
Priority shortlist for next-wave exploration:

1. `ETH/BTC`
2. `SOL/BTC`
3. `QQQ/TLT`
4. `XLE/XLU`
5. `LQD/HYG`
6. `ETH/SOL`
7. `SOXX/QQQ`
8. `GLD/SLV`
9. `AVAX/ETH`
10. `DOGE/BTC`

---

## Immediate Deliverables From Phase 1

Phase 1 is complete when the following are true:

- the starter crypto universe is defined
- the starter ETF universe is defined
- the first candidate relationships are listed
- the ranking logic is defined
- the next-wave shortlist is visible
- the active paper candidate remains explicitly identified

---

## Immediate Next Step After Phase 1

After this universe plan is locked, the next move is:

1. audit `d7-tqqq-stacked-credit`
2. create strategy-candidate specs for the Tier 1 shortlist
3. start the normal testing ladder on the highest-conviction candidates

This preserves momentum while expanding what the system can test.

---

## Definition of Success

This plan is successful when:

- the research universe is broader without becoming random
- candidate generation becomes structured instead of ad hoc
- the system can test more stocks and cryptos through explicit strategy families
- existing strong candidates continue moving forward while expansion begins

At that point, the system is no longer limited to a narrow queue.
It has a controlled pipeline for expanding into more stocks, more crypto relationships, and more testable strategy ideas.
