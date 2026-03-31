# Runtime Truth Table (Modes & Behaviors)

This page is the single source of truth for **what actually happens** at runtime.

## Truth Table (intraday_enabled × broker)

| intraday_enabled | broker | Data Source | Orders | Profit‑Taking | RTH Guard | Run Lock | Order State / Logs |
|---|---|---|---|---|---|---|---|
| false | paper | Yahoo daily bars (`market_data_daily`) | Paper portfolio updates only | Bracket TP/SL **simulated** in paper layer (no native orders) | No | No | Trades + decisions logged; no intraday order state |
| false | alpaca | Yahoo daily bars (`market_data_daily`) | Alpaca **bracket** orders (market entry + TP/SL) | TP/SL via bracket orders | No | No | Trades + decisions logged |
| true | paper | Alpaca 5‑min bars (`market_data_intraday`) + daily macro | Paper portfolio updates only | **No native OCO/TP/trailing**; only SELL/CLOSE signals change positions | Yes | Yes (per 5‑min slot) | Intraday context logged; no Alpaca order state |
| true | alpaca | Alpaca 5‑min bars (`market_data_intraday`) + daily macro | Alpaca **partial TP + OCO remainder** + trailing stop updates | Partial TP at +X%, remainder TP at +X%×mult, trailing stop replaces OCO stop | Yes | Yes (per 5‑min slot) | Intraday order state + leg statuses persisted |

## Drift / Gotchas

- **Intraday + paper does NOT execute OCO/TP/trailing**. It only honors explicit SELL/CLOSE signals. This is expected, but can look like “profit‑taking isn’t working.”
- Trailing stops only update when the OCO stop leg is resolved; if Alpaca doesn’t return leg IDs, trailing is disabled and logged.

## Related Docs

- `docs/governance/hybrid-intraday-runtime.md`
- `README.md` (Hybrid Runtime section)
