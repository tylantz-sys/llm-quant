# Crypto Strategy Promotion Standard

Use this checklist before moving a slug from `candidate_crypto` to
`promoted_crypto` in `config/strategies/catalog.toml`.

## Required Gates

1. Frozen research spec exists with `frozen: true`.
2. Backtest record exists and passes minimum thresholds:
- `sharpe_ratio > 0`
- `dsr >= 0.95`
- `max_drawdown <= 0.25`
3. Walk-forward artifact exists at `data/strategies/<slug>/walk-forward.yaml`
with `passed: true`.
4. Robustness gate exists at `data/strategies/<slug>/robustness.yaml`
with `overall_passed: true`.
5. Paper shadow artifact exists at `data/strategies/<slug>/paper-trading.yaml`
and indicates pass/ready/complete status.

## Validation Utility

Candidate-stage validation (pre-paper, strict):

```bash
python scripts/validate_crypto_promotion.py --set candidate_crypto --strict
```

Promoted-stage validation (paper gate required):

```bash
python scripts/validate_crypto_promotion.py --set promoted_crypto
```

Strict CI-style check:

```bash
python scripts/validate_crypto_promotion.py --set promoted_crypto --strict
```

## Operator Checklist

1. Confirm crypto pod uses `signal_source = "strategy_overlay"`.
2. Confirm `strategy_set = "promoted_crypto"`.
3. Confirm strict governor flags are enabled:
- `overlay_governor_strict = true`
- `overlay_max_upscale = 1.25`
- `overlay_max_downscale = 0.0`
4. Run one paper smoke cycle and verify:
- candidate signals present,
- no policy violations,
- risk filters and profit-taking telemetry logged.

## Dedicated Candidate Pod

For ETH/BTC V2 paper shadow, use:
- pod config: `config/strategies/crypto-ethbtc-paper.toml`
- runtime mode: `signal_source = "strategy_overlay"`
- strategy set: `strategy_set = "candidate_crypto"`
- service/timer templates:
  `scripts/systemd/llm-quant-crypto-ethbtc-paper.service`,
  `scripts/systemd/llm-quant-crypto-ethbtc-paper.timer`
