# Crypto Candidate Daily Promotion Checklist

Use this checklist for `eth-btc-ratio-mean-reversion-v5` in the
`crypto-ethbtc-paper` pod before moving from `candidate_crypto` to
`promoted_crypto`.

## 1) Scheduler Health (must pass)

- `systemctl --user status llm-quant-crypto-ethbtc-paper.timer --no-pager`
  - Pass: timer is `active (running)` and has a future `Trigger`.
- `journalctl --user -u llm-quant-crypto-ethbtc-paper.service -n 100 --no-pager`
  - Pass: no repeated `FAILURE`, no DuckDB lock crash loops.

## 2) Data / Runtime Health (must pass)

- `pq crypto status --pod crypto-ethbtc-paper`
  - Pass: bar age is fresh, Alpaca crypto status is active, no stale warning.
- Check latest run logs for deterministic behavior:
  - Strategy candidate generation visible.
  - Governor audit visible (`decision_type=overlay` path).
  - Risk filter executes after governor.

## 3) Paper Gate Metrics (must pass all)

From `data/strategies/eth-btc-ratio-mean-reversion-v5/paper-trading.yaml`:

- `days_observed >= 30`
- `closed_trades >= 50`
- `sharpe >= 0.60`
- `max_drawdown <= 0.25`
- `operational_checks_required == true` and all checks healthy

Refresh with:

- `.venv/bin/python scripts/update_crypto_paper_eval.py --slug eth-btc-ratio-mean-reversion-v5 --pod crypto-ethbtc-paper`

## 4) Governance Artifact Validation (must pass)

- `.venv/bin/python scripts/validate_crypto_promotion.py --set candidate_crypto --strict`
  - Pass: V5 shows `ready=True`.

## 5) Promotion Procedure (only after all passes)

1. Edit `config/strategies/catalog.toml`:
   - Remove `eth-btc-ratio-mean-reversion-v5` from `candidate_crypto`.
   - Add it to `promoted_crypto`.
2. Validate promoted set:
   - `.venv/bin/python scripts/validate_crypto_promotion.py --set promoted_crypto --strict`
3. Reload runtime:
   - `systemctl --user daemon-reload`
   - `systemctl --user restart llm-quant-crypto.timer`
4. Keep candidate paper pod running for 1 extra day as rollback safety.

## 6) Automatic Fail Conditions (do not promote)

- Repeated stale-bar warnings.
- Repeated run lock/DB lock crashes.
- Negative drift in paper Sharpe below gate.
- Drawdown breaches gate.
