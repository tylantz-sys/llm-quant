#!/usr/bin/env python3
"""Governed robustness analysis runner."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, "src")

from llm_quant.backtest.engine import BacktestEngine, CostModel
from llm_quant.backtest.strategies import create_strategy
from llm_quant.backtest.strategy import StrategyConfig
from llm_quant.data.fetcher import fetch_ohlcv
from llm_quant.data.indicators import compute_indicators

DEFAULT_INITIAL_CAPITAL = 100000.0
DEFAULT_WARMUP_DAYS = 30
DEFAULT_LOOKBACK_BUFFER_DAYS = 30
DEFAULT_COST_MULTIPLIERS = [1.0, 1.5, 2.0, 3.0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run governed robustness analysis.")
    parser.add_argument("--slug", required=True, help="Strategy slug under data/strategies/")
    parser.add_argument("--data-dir", default="data", help="Base data directory")
    return parser.parse_args()


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping YAML in {path}")
    return data


def load_research_spec(strategy_dir: Path) -> dict[str, Any]:
    spec_path = strategy_dir / "research-spec.yaml"
    if not spec_path.exists():
        raise FileNotFoundError(f"Missing research spec: {spec_path}")
    spec = load_yaml(spec_path)
    if not spec.get("frozen"):
        raise ValueError(f"Research spec is not frozen: {spec_path}")
    return spec


def load_latest_experiment(strategy_dir: Path) -> tuple[dict[str, Any], str]:
    registry_path = strategy_dir / "experiment-registry.jsonl"
    if not registry_path.exists():
        raise FileNotFoundError(f"Missing experiment registry: {registry_path}")
    latest: dict[str, Any] | None = None
    with registry_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            latest = json.loads(line)
    if latest is None:
        raise ValueError(f"No experiments found in {registry_path}")
    experiment_id = latest.get("experiment_id")
    if not experiment_id:
        raise ValueError("Latest registry entry missing experiment_id")
    artifact_path = strategy_dir / "experiments" / f"{experiment_id}.yaml"
    if not artifact_path.exists():
        raise FileNotFoundError(f"Missing experiment artifact: {artifact_path}")
    experiment = load_yaml(artifact_path)
    return experiment, experiment_id


def get_strategy_identity(spec: dict[str, Any]) -> tuple[str, str]:
    strategy_name = spec.get("strategy_type")
    strategy_slug = spec.get("strategy_slug")
    if not strategy_name or not strategy_slug:
        raise ValueError("Spec missing strategy_type or strategy_slug")
    return strategy_name, strategy_slug


def build_base_params(spec: dict[str, Any]) -> dict[str, Any]:
    params = dict(spec.get("parameters") or {})
    if not params:
        raise ValueError("Spec missing parameters")
    return params


def build_config(strategy_name: str, params: dict[str, Any]) -> StrategyConfig:
    rebalance_frequency_days = int(params.get("rebalance_frequency_days", 1))
    target_weight = float(params.get("target_weight", 0.90))
    return StrategyConfig(
        name=strategy_name,
        rebalance_frequency_days=rebalance_frequency_days,
        max_positions=1,
        target_position_weight=target_weight,
        stop_loss_pct=0.07,
        parameters=dict(params),
    )


def make_cost_model(spec: dict[str, Any], multiplier: float = 1.0) -> CostModel:
    costs = dict(spec.get("cost_model") or {})
    spread_bps = float(costs.get("spread_bps", 5.0)) * multiplier
    flat_slippage_bps = float(costs.get("flat_slippage_bps", 2.0)) * multiplier
    slippage_volatility_factor = float(costs.get("slippage_volatility_factor", 0.1))
    commission_per_share = float(costs.get("commission_per_share", 0.0))
    min_commission = float(costs.get("min_commission", 0.0))
    return CostModel(
        spread_bps=spread_bps,
        flat_slippage_bps=flat_slippage_bps,
        slippage_volatility_factor=slippage_volatility_factor,
        commission_per_share=commission_per_share,
        min_commission=min_commission,
    )


def fetch_data(spec: dict[str, Any]) -> tuple[Any, Any]:
    backtest_spec = dict(spec.get("backtest_spec") or {})
    symbols = backtest_spec.get("symbols") or []
    years = int(backtest_spec.get("years", 5))
    if not symbols:
        raise ValueError("Spec backtest_spec.symbols missing")
    lookback_days = years * 365 + DEFAULT_LOOKBACK_BUFFER_DAYS
    prices_df = fetch_ohlcv(symbols, lookback_days=lookback_days)
    indicators_df = compute_indicators(prices_df)
    return prices_df, indicators_df


def run_single(
    strategy_name: str,
    strategy_slug: str,
    spec: dict[str, Any],
    params: dict[str, Any],
    prices_df: Any,
    indicators_df: Any,
    cost_multiplier: float = 1.0,
) -> dict[str, Any]:
    backtest_spec = dict(spec.get("backtest_spec") or {})
    initial_capital = float(backtest_spec.get("initial_capital", DEFAULT_INITIAL_CAPITAL))
    warmup_days = int(backtest_spec.get("warmup_days", DEFAULT_WARMUP_DAYS))
    config = build_config(strategy_name, params)
    strategy = create_strategy(strategy_name, config)
    engine = BacktestEngine(strategy, initial_capital=initial_capital)
    result = engine.run(
        prices_df=prices_df,
        indicators_df=indicators_df,
        slug=strategy_slug,
        cost_model=make_cost_model(spec, multiplier=cost_multiplier),
        warmup_days=warmup_days,
        cost_multiplier=cost_multiplier,
    )
    metric_key = f"{cost_multiplier:.1f}x"
    metrics = result.metrics.get(metric_key)
    if metrics is None:
        if len(result.metrics) == 1:
            metrics = next(iter(result.metrics.values()))
        elif cost_multiplier == 1.0:
            metrics = result.metrics.get("1.0x")
    if metrics is None:
        raise ValueError(
            f"Missing metrics for cost multiplier {cost_multiplier:.1f}x; "
            f"available keys: {sorted(result.metrics.keys())}"
        )
    return {
        "sharpe": float(metrics.sharpe_ratio),
        "max_dd": float(metrics.max_drawdown),
        "daily_returns": result.daily_returns or [],
        "dsr": float(getattr(metrics, "dsr", 0.0) or 0.0),
        "psr": float(getattr(metrics, "psr", 0.0) or 0.0),
        "total_return": float(getattr(metrics, "total_return", 0.0) or 0.0),
    }


def cpcv_sharpe(returns: list[float], n_groups: int = 6, k: int = 2, purge: int = 5) -> tuple[float, float]:
    n = len(returns)
    if n < n_groups:
        return 0.0, 0.0
    group_size = n // n_groups
    oos_sharpes: list[float] = []
    for test_idx in combinations(range(n_groups), k):
        test_rets: list[float] = []
        for i in test_idx:
            start = i * group_size + purge
            end = (i + 1) * group_size - purge
            if start < end:
                test_rets.extend(returns[start:end])
        if len(test_rets) < 20:
            continue
        mean = sum(test_rets) / len(test_rets)
        std = (sum((r - mean) ** 2 for r in test_rets) / len(test_rets)) ** 0.5
        if std > 0:
            oos_sharpes.append(mean / std * math.sqrt(252))
    if not oos_sharpes:
        return 0.0, 0.0
    mean_oos = sum(oos_sharpes) / len(oos_sharpes)
    std_oos = (sum((x - mean_oos) ** 2 for x in oos_sharpes) / len(oos_sharpes)) ** 0.5
    return mean_oos, std_oos


def stable_result(base_sharpe: float, variant_sharpe: float, threshold_pct: float = 30.0) -> tuple[float, bool]:
    pct = (variant_sharpe - base_sharpe) / (abs(base_sharpe) + 1e-8) * 100
    return pct, abs(pct) <= threshold_pct


def build_perturbations(base_params: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    perturbations: list[tuple[str, dict[str, Any]]] = []
    lag_days = int(base_params.get("lag_days", 5))
    entry_threshold = float(base_params.get("entry_threshold", 0.02))
    target_weight = float(base_params.get("target_weight", 0.90))

    if lag_days > 1:
        perturbations.append((f"lag_days={lag_days - 1}", {**base_params, "lag_days": lag_days - 1}))
    perturbations.append((f"lag_days={lag_days + 1}", {**base_params, "lag_days": lag_days + 1}))
    perturbations.append(
        (
            f"entry_threshold={max(0.0, entry_threshold - 0.01):.2f}",
            {**base_params, "entry_threshold": max(0.0, entry_threshold - 0.01)},
        )
    )
    perturbations.append(
        (
            f"entry_threshold={entry_threshold + 0.02:.2f}",
            {**base_params, "entry_threshold": entry_threshold + 0.02},
        )
    )
    perturbations.append(
        (
            f"target_weight={max(0.1, target_weight - 0.20):.2f}",
            {**base_params, "target_weight": max(0.1, target_weight - 0.20)},
        )
    )
    return perturbations[:5]


def format_percent(value: float) -> float:
    return round(value * 100, 2)


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    with path.open("w") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def main() -> int:
    args = parse_args()
    data_dir = Path(args.data_dir)
    strategy_dir = data_dir / "strategies" / args.slug

    spec = load_research_spec(strategy_dir)
    experiment, experiment_id = load_latest_experiment(strategy_dir)
    strategy_name, strategy_slug = get_strategy_identity(spec)
    base_params = build_base_params(spec)
    prices_df, indicators_df = fetch_data(spec)

    base_result = run_single(strategy_name, strategy_slug, spec, base_params, prices_df, indicators_df, 1.0)
    cpcv_mean, cpcv_std = cpcv_sharpe(base_result["daily_returns"])

    perturbation_results: list[dict[str, Any]] = []
    for name, params in build_perturbations(base_params):
        result = run_single(strategy_name, strategy_slug, spec, params, prices_df, indicators_df, 1.0)
        pct_change, stable = stable_result(base_result["sharpe"], result["sharpe"])
        perturbation_results.append(
            {
                "name": name,
                "sharpe": round(result["sharpe"], 4),
                "pct_change": round(pct_change, 1),
                "stable": stable,
            }
        )
        print(
            f"  {name}: sharpe={result['sharpe']:.4f} "
            f"max_dd={result['max_dd']:.4f} ({pct_change:+.1f}%) {'STABLE' if stable else 'UNSTABLE'}"
        )

    cost_runs = {
        multiplier: run_single(strategy_name, strategy_slug, spec, base_params, prices_df, indicators_df, multiplier)
        for multiplier in DEFAULT_COST_MULTIPLIERS
    }

    dsr_value = float(
        experiment.get("metrics_1x", {}).get("dsr")
        or experiment.get("metrics_1x", {}).get("psr")
        or base_result["dsr"]
    )
    print(f"\nDSR: {dsr_value:.4f}")

    perturbation_stable_count = sum(1 for item in perturbation_results if item["stable"])
    perturbation_total = len(perturbation_results)
    perturbation_pass = perturbation_total > 0 and perturbation_stable_count / perturbation_total >= 0.6
    dsr_pass = dsr_value >= 0.95
    cpcv_pass = cpcv_mean > 0.0
    max_dd_pass = cost_runs[1.0]["max_dd"] < 0.15

    gate_results = {
        "dsr": {
            "dsr_value": round(dsr_value, 4),
            "threshold": ">= 0.95",
            "result": "PASS" if dsr_pass else "FAIL",
            "notes": f"DSR from canonical experiment {experiment_id} under frozen repaired lineage.",
        },
        "pbo": {
            "value": "not_computed",
            "threshold": "<= 0.10",
            "result": "SKIP",
            "notes": "PBO not computed by this runner; family variant set not yet modeled here.",
        },
        "cpcv": {
            "mean_oos_sharpe": round(cpcv_mean, 4),
            "std_oos_sharpe": round(cpcv_std, 4),
            "threshold": "> 0",
            "result": "PASS" if cpcv_pass else "FAIL",
            "notes": "CPCV computed from canonical repaired-lineage daily returns with 6 groups, 2 test groups, 5-day purge.",
        },
        "perturbation": {
            "n_stable": perturbation_stable_count,
            "n_total": perturbation_total,
            "result": "PASS" if perturbation_pass else "FAIL",
            "threshold": ">= 60% stable",
            "details": perturbation_results,
        },
    }

    overall_passed = dsr_pass and cpcv_pass and perturbation_pass and max_dd_pass
    gates_passed = sum(1 for key in ("dsr", "cpcv", "perturbation") if gate_results[key]["result"] == "PASS")
    gates_passed += 1 if max_dd_pass else 0
    promotion_notes = (
        f"{'PASS' if overall_passed else 'HOLD'}: "
        f"Sharpe={base_result['sharpe']:.3f} "
        f"DSR={dsr_value:.4f} "
        f"MaxDD={format_percent(cost_runs[1.0]['max_dd']):.2f}% "
        f"CPCV_OOS={cpcv_mean:.4f}."
    )

    output = {
        "strategy_slug": strategy_slug,
        "created_at": now_utc_iso(),
        "runner": "scripts/run_a7_robustness.py",
        "runner_identity": {
            "script": "scripts/run_a7_robustness.py",
            "pid": Path("/proc/self").resolve().name if Path("/proc/self").exists() else None,
            "hostname": Path("/etc/hostname").read_text().strip() if Path("/etc/hostname").exists() else None,
            "python_executable": sys.executable,
        },
        "frozen_spec_hash": spec.get("frozen_hash"),
        "provenance": {
            "created_at": now_utc_iso(),
            "runner": "scripts/run_a7_robustness.py",
            "runner_identity": {
                "script": "scripts/run_a7_robustness.py",
                "pid": Path("/proc/self").resolve().name if Path("/proc/self").exists() else None,
                "hostname": Path("/etc/hostname").read_text().strip() if Path("/etc/hostname").exists() else None,
                "python_executable": sys.executable,
            },
            "strategy_slug": strategy_slug,
            "strategy_name": strategy_name,
            "frozen_spec_hash": spec.get("frozen_hash"),
            "spec_frozen": bool(spec.get("frozen")),
            "spec_frozen_at": spec.get("frozen_at"),
            "data_dir": str(data_dir),
            "symbols": spec.get("backtest_spec", {}).get("symbols", []),
            "canonical_experiment_id": experiment_id,
            "canonical_metrics_1x": experiment.get("metrics_1x", {}),
            "policy_inputs": {
                "years": spec.get("backtest_spec", {}).get("years"),
                "initial_capital": spec.get("backtest_spec", {}).get("initial_capital"),
                "warmup_days": spec.get("backtest_spec", {}).get("warmup_days"),
                "cost_model": spec.get("cost_model", {}),
                "parameters": base_params,
            },
        },
        "trial_family": spec.get("family"),
        "trial_count": int(spec.get("family_trial_number", 1)),
        "overall_result": "PASS" if overall_passed else "HOLD",
        "gates_passed": gates_passed,
        "gates_failed": 4 - gates_passed,
        "gates_total": 4,
        "gate_results": gate_results,
        "cost_sensitivity": {
            "sharpe_1x": round(cost_runs[1.0]["sharpe"], 3),
            "sharpe_1_5x": round(cost_runs[1.5]["sharpe"], 3),
            "sharpe_2x": round(cost_runs[2.0]["sharpe"], 3),
            "sharpe_3x": round(cost_runs[3.0]["sharpe"], 3),
            "max_dd_1x": format_percent(cost_runs[1.0]["max_dd"]),
            "notes": "Cost sensitivity computed from repaired frozen canonical spec.",
        },
        "max_drawdown_check": {
            "value": format_percent(cost_runs[1.0]["max_dd"]),
            "threshold": "< 15.0",
            "result": "PASS" if max_dd_pass else "FAIL",
            "notes": "Base 1x-cost drawdown must remain inside mandate threshold.",
        },
        "overall_passed": overall_passed,
        "rejection_reason": None if overall_passed else "Refreshed robustness gates did not fully pass under corrected lineage.",
        "promotion_notes": promotion_notes,
    }

    output_path = strategy_dir / "robustness.yaml"
    write_yaml(output_path, output)
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
