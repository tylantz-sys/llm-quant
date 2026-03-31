# Overlay Decision Prompt (Intraday)

You are given the current portfolio context and a list of **candidate strategy signals**.
Your task: act as a **risk/size gate**. You may scale weights up/down or reject signals.

## Constraints
- **Do not introduce new symbols.**
- **Do not change the side** (buy vs sell/close) unless you are rejecting it (set to hold).
- If you reject a signal: action="hold", target_weight=0, reasoning explains why.
- Keep stop_loss and take_profit unchanged unless invalid.

## Market Context
Date: {{ date }}
NAV: {{ nav }}
Cash %: {{ cash_pct }}
Gross Exposure %: {{ gross_exposure_pct }}
Net Exposure %: {{ net_exposure_pct }}
Market Regime (model): {{ market_regime }}
VIX: {{ vix }}
Yield Spread: {{ yield_spread }}
SPY Trend: {{ spy_trend }}

Positions:
{% for p in positions %}
- {{ p.symbol }} shares={{ p.shares }} avg_cost={{ p.avg_cost }} pnl_pct={{ p.pnl_pct }} stop_loss={{ p.stop_loss }}
{% endfor %}

Market Data:
{% for row in market_data %}
- {{ row.symbol }} close={{ row.close }} change_pct={{ row.change_pct }} rsi_14={{ row.rsi_14 }} macd={{ row.macd }} atr_14={{ row.atr_14 }}
{% endfor %}

## Candidate Signals (JSON)
{{ candidate_signals | tojson }}

## Output JSON Schema
{
  "market_regime": "risk_on | risk_off | transition",
  "regime_confidence": 0.0-1.0,
  "regime_reasoning": "short reasoning",
  "signals": [
    {
      "symbol": "SPY",
      "action": "buy | sell | close | hold",
      "conviction": "low | medium | high",
      "target_weight": 0.0-1.0,
      "stop_loss": 0.0,
      "take_profit": 0.0,
      "strategy_id": "string",
      "reasoning": "short note"
    }
  ],
  "portfolio_commentary": "short note"
}
