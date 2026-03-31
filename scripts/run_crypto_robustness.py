"""Run strict robustness gates for a crypto strategy slug.

Gates:
- DSR >= 0.95
- Sharpe > 0
- Max drawdown <= 0.25
- CPCV mean OOS Sharpe > 0
- Perturbation stability >= 60% (|Sharpe delta| <= 30%)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

# Ensure src/ is importable when run as a script from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from llm_quant.backtest.artifacts import ensure_frozen_spec, strategy_dir
from llm_quant.backtest.engine import BacktestEngine, CostModel
from llm_quant.backtest.robustness import run_cpcv
from llm_quant.backtest.strategies import create_strategy
from llm_quant.backtest.strategy import StrategyConfig
from llm_quant.data.fetcher import fetch_ohlcv
from llm_quant.data.indicators import compute_indicators


def _load_latest_registry_entry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    latest = {}
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            stripped = raw.strip()
            if stripped:
                latest = json.loads(stripped)
    return latest


def _load_experiment_daily_returns(strat_dir: Path, experiment_id: str) -> list[float]:
    artifact_path = strat_dir / "experiments" / f"{experiment_id}.yaml"
    if not artifact_path.exists():
        return []
    with artifact_path.open("r", encoding="utf-8") as f:
        payload = yaml.safe_load(f) or {}
    raw = payload.get("daily_returns", [])
    if not isinstance(raw, list):
        return []
    return [float(v) for v in raw]


def _resolve_symbols(spec: dict[str, Any]) -> list[str]:
    params = spec.get("parameters", {}) or {}
    symbols = spec.get("backtest_spec", {}).get("symbols", [])
    if isinstance(symbols, list) and symbols:
        return [str(s) for s in symbols]
    resolved: list[str] = []
    for key in ("symbol", "leader_symbol", "follower_symbol", "symbol_a", "symbol_b"):
        value = params.get(key)
        if value:
            resolved.append(str(value))
    return sorted({s for s in resolved if s})


def _build_strategy_config(
    spec: dict[str, Any], params: dict[str, Any]
) -> StrategyConfig:
    if "rebalance_frequency_days" in params and "rebalance_frequency" not in params:
        params["rebalance_frequency"] = params["rebalance_frequency_days"]
    return StrategyConfig(
        name=str(spec.get("strategy_slug", spec.get("strategy_type", "strategy"))),
        rebalance_frequency_days=int(params.get("rebalance_frequency_days", 1)),
        max_positions=10,
        target_position_weight=float(params.get("target_weight", 0.25)),
        stop_loss_pct=0.10,
        parameters=params,
    )


def _run_variant(
    spec: dict[str, Any],
    slug: str,
    prices_df,
    indicators_df,
    params: dict[str, Any],
) -> dict[str, float]:
    strategy_name = str(spec.get("strategy_type", "pairs_ratio"))
    config = _build_strategy_config(spec, params)
    strategy = create_strategy(strategy_name, config)
    engine = BacktestEngine(strategy, initial_capital=100_000.0)
    result = engine.run(
        prices_df=prices_df,
        indicators_df=indicators_df,
        slug=slug,
        cost_model=CostModel.from_spec(spec),
        fill_delay=1,
        warmup_days=int(spec.get("backtest_spec", {}).get("warmup_days", 30)),
        cost_multiplier=1.0,
        trial_count=1,
    )
    metrics = result.metrics.get("1.0x")
    return {
        "sharpe": float(metrics.sharpe_ratio if metrics else 0.0),
        "max_drawdown": float(metrics.max_drawdown if metrics else 0.0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run crypto robustness gates for a slug."
    )
    parser.add_argument("--slug", required=True, help="Strategy slug")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    parser.add_argument("--maxdd-threshold", type=float, default=0.25)
    parser.add_argument("--dsr-threshold", type=float, default=0.95)
    parser.add_argument("--stability-threshold", type=float, default=0.60)
    args = parser.parse_args()

    strat_dir = strategy_dir(Path(args.data_dir), args.slug)
    spec = ensure_frozen_spec(strat_dir)
    base_params = dict(spec.get("parameters", {}) or {})
    latest = _load_latest_registry_entry(strat_dir / "experiment-registry.jsonl")
    if not latest:
        msg = (
            "No experiment-registry entry found. Run backtest first with "
            "scripts/run_backtest.py."
        )
        raise SystemExit(msg)

    base_sharpe = float(latest.get("sharpe_ratio", 0.0) or 0.0)
    base_maxdd = abs(float(latest.get("max_drawdown", 1.0) or 1.0))
    base_dsr = float(latest.get("dsr", 0.0) or 0.0)
    experiment_id = str(latest.get("experiment_id", ""))

    daily_returns = _load_experiment_daily_returns(strat_dir, experiment_id)
    cpcv = run_cpcv(daily_returns, strategy_fn=None, n_groups=6, k_test=2, purge_days=5)
    cpcv_mean = float(cpcv.mean_oos_sharpe or 0.0)
    cpcv_std = float(cpcv.std_oos_sharpe or 0.0)

    symbols = _resolve_symbols(spec)
    years = int(spec.get("backtest_spec", {}).get("years", 5))
    lookback_days = max(years * 365, 365)
    prices_df = fetch_ohlcv(symbols, lookback_days=lookback_days)
    indicators_df = compute_indicators(prices_df)

    bb_window = int(base_params.get("bb_window", 20) or 20)
    bb_std = float(base_params.get("bb_std", 2.0) or 2.0)
    target_weight = float(base_params.get("target_weight", 0.25) or 0.25)

    variants: list[tuple[str, dict[str, Any]]] = [
        ("bb_window_minus_5", {**base_params, "bb_window": max(10, bb_window - 5)}),
        ("bb_window_plus_5", {**base_params, "bb_window": bb_window + 5}),
        ("bb_std_minus_0_5", {**base_params, "bb_std": max(1.0, bb_std - 0.5)}),
        ("bb_std_plus_0_5", {**base_params, "bb_std": bb_std + 0.5}),
        (
            "target_weight_minus_20pct",
            {**base_params, "target_weight": target_weight * 0.8},
        ),
    ]

    perturbations: list[dict[str, Any]] = []
    stable_count = 0
    for name, params in variants:
        metrics = _run_variant(
            spec=spec,
            slug=f"{args.slug}-perturb-{name}",
            prices_df=prices_df,
            indicators_df=indicators_df,
            params=params,
        )
        sharpe = metrics["sharpe"]
        if math.isclose(base_sharpe, 0.0):
            pct_change = 0.0 if math.isclose(sharpe, 0.0) else 999.0
        else:
            pct_change = ((sharpe - base_sharpe) / abs(base_sharpe)) * 100.0
        stable = abs(pct_change) <= 30.0
        if stable:
            stable_count += 1
        perturbations.append(
            {
                "name": name,
                "sharpe": round(sharpe, 6),
                "max_drawdown": round(metrics["max_drawdown"], 6),
                "pct_change": round(pct_change, 2),
                "stable": stable,
            }
        )

    stability_ratio = stable_count / len(variants) if variants else 0.0
    dsr_pass = base_dsr >= float(args.dsr_threshold)
    sharpe_pass = base_sharpe > 0.0
    maxdd_pass = base_maxdd <= float(args.maxdd_threshold)
    cpcv_pass = cpcv_mean > 0.0
    perturb_pass = stability_ratio >= float(args.stability_threshold)
    overall = dsr_pass and sharpe_pass and maxdd_pass and cpcv_pass and perturb_pass

    payload = {
        "strategy_slug": args.slug,
        "created_at": datetime.now(tz=UTC).replace(microsecond=0).isoformat(),
        "base_experiment_id": experiment_id,
        "base_metrics": {
            "sharpe_ratio": round(base_sharpe, 6),
            "max_drawdown": round(base_maxdd, 6),
            "dsr": round(base_dsr, 6),
            "total_trades": int(latest.get("total_trades", 0) or 0),
        },
        "thresholds": {
            "sharpe_gt": 0.0,
            "max_drawdown_lte": float(args.maxdd_threshold),
            "dsr_gte": float(args.dsr_threshold),
            "cpcv_mean_oos_sharpe_gt": 0.0,
            "perturbation_stability_gte": float(args.stability_threshold),
        },
        "cpcv": {
            "mean_oos_sharpe": round(cpcv_mean, 6),
            "std_oos_sharpe": round(cpcv_std, 6),
            "n_combinations": int(cpcv.n_combinations),
            "n_paths": int(cpcv.n_paths),
        },
        "perturbation": {
            "stable_count": stable_count,
            "total": len(variants),
            "stability_ratio": round(stability_ratio, 6),
            "variants": perturbations,
        },
        "gate_results": {
            "sharpe": sharpe_pass,
            "max_drawdown": maxdd_pass,
            "dsr": dsr_pass,
            "cpcv": cpcv_pass,
            "perturbation": perturb_pass,
        },
        "overall_passed": overall,
    }

    out_path = strat_dir / "robustness.yaml"
    with out_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False)

    print(
        "Robustness complete: "
        f"sharpe={base_sharpe:.3f} maxdd={base_maxdd:.3f} dsr={base_dsr:.3f} "
        f"cpcv={cpcv_mean:.3f} stability={stability_ratio:.2%} overall={overall}"
    )
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
