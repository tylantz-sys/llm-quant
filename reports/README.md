# Reports

Auto-generated Markdown reports from the llm-quant DuckDB portfolio database.

## Directory Structure

```
reports/
  daily/YYYY-MM-DD.md     # Daily portfolio snapshot, trades, and metrics
  weekly/YYYY-WNN.md      # Weekly aggregate with daily breakdown
  monthly/YYYY-MM.md      # Full monthly dashboard with YTD and benchmarks
```

## Generation

```bash
cd E:/llm-quant && PYTHONPATH=src python scripts/generate_report.py [daily|weekly|monthly] [--date YYYY-MM-DD]
```

If `--date` is omitted, today's date is used.

## Report Contents

### Daily
- Market regime (from LLM decisions)
- Portfolio summary: NAV, cash, exposure, P&L
- Current positions with weights and stop losses
- Trades executed that day with conviction and reasoning
- Performance metrics: Sharpe, Sortino, Calmar, max drawdown, win rate
- Harvest Metrics section summarizing realized profit-take activity for the selected window when telemetry exists
- Benchmark comparison vs 60/40 SPY/TLT

### Weekly
- Start/end NAV with weekly return
- Daily NAV breakdown
- All trades for the week
- Position weight changes (start vs end of week)
- Regime history
- Harvest Metrics rollup with aggregate ratios and profit-take breakdowns when profit_take_events are available

### Monthly
- Monthly and YTD returns
- Full performance metrics dashboard
- Trade statistics grouped by conviction level
- Top/bottom performers by P&L %
- Regime breakdown (days per regime)
- Harvest Metrics rollup for the month, including realized harvest efficiency details when available
- Benchmark comparison

## Harvest Metrics Notes

The report generator now reads `profit_take_events` telemetry and appends a `## Harvest Metrics` section to daily, weekly, and monthly reports.

Typical items include:
- event counts and realized P&L totals
- capture and efficiency ratios derived from profit-take lifecycle fields
- per-symbol or per-conviction breakdowns when enough telemetry is present

If no profit-take telemetry exists for the requested period, the section is still rendered with a no-data message so report structure remains consistent.

## Integrity

Every report footer includes the hash-chain verification status (`PASS` or `FAIL`) confirming the trade ledger has not been tampered with.
