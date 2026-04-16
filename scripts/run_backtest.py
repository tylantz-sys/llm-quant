#!/usr/bin/env python3
"""Run a backtest against a frozen research spec.

Usage:
    python scripts/run_backtest.py --slug test_sma --strategy sma_crossover \
        --symbols SPY,QQQ,TLT --years 3

The script:
1. Validates the frozen research spec exists
2. Fetches historical data
3. Computes indicators
4. Runs the backtest at 1x, 1.5x, 2x, 3x cost multipliers
5. Appends to experiment-registry.jsonl
6. Persists the experiment artifact
7. Outputs a markdown report
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Ensure src is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from llm_quant.backtest.artifacts import (
    ExperimentRegistry,
    ensure_frozen_spec,
    save_artifact,
    strategy_dir,
)
from llm_quant.backtest.engine import BacktestEngine, CostModel, MetaFilterConfig
from llm_quant.backtest.report import generate_backtest_report
from llm_quant.backtest.robustness import compute_min_trl
from llm_quant.backtest.strategies import STRATEGY_REGISTRY, create_strategy
from llm_quant.backtest.strategy import StrategyConfig
from llm_quant.data.fetcher import fetch_ohlcv
from llm_quant.data.indicators import compute_indicators

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


def _spec_backtest_symbols(spec: dict[str, Any]) -> list[str]:
    backtest_spec = spec.get("backtest_spec", {}) or {}
    tradable = backtest_spec.get("symbols", []) or []
    signal = backtest_spec.get("signal_symbols", []) or []
    ordered: list[str] = []
    seen: set[str] = set()
    for symbol in [*tradable, *signal]:
        symbol_str = str(symbol).strip()
        if symbol_str and symbol_str not in seen:
            ordered.append(symbol_str)
            seen.add(symbol_str)
    return ordered


def _spec_fill_delay(spec: dict[str, Any]) -> int:
    params = spec.get("parameters", {}) or {}
    return int(params.get("execution_lag_days", spec.get("fill_delay", 1)) or 1)


def _spec_warmup_days(spec: dict[str, Any]) -> int:
    backtest_spec = spec.get("backtest_spec", {}) or {}
    return int(backtest_spec.get("warmup_days", spec.get("warmup_days", 200)) or 200)


def _spec_backtest_years(spec: dict[str, Any], fallback_years: int) -> int:
    backtest_spec = spec.get("backtest_spec", {}) or {}
    return int(backtest_spec.get("years", fallback_years) or fallback_years)


def _build_policy_inputs(
    args: argparse.Namespace,
    spec: dict[str, Any],
    symbols: list[str],
    effective_years: int,
    years_overridden_by_spec: bool,
) -> dict[str, Any]:
    return {
        "initial_capital": float(args.initial_capital),
        "years_requested_cli": int(args.years),
        "years_effective": int(effective_years),
        "years_overridden_by_spec": bool(years_overridden_by_spec),
        "years": int(effective_years),
        "symbols": symbols,
        "spec_check_enforced": not bool(args.no_spec_check),
        "volatility_target": args.volatility_target,
        "vol_target_window": int(args.vol_target_window),
        "vol_target_max_scale": float(args.vol_target_max_scale),
        "regime_filter": bool(args.regime_filter),
        "vix_threshold": float(args.vix_threshold),
        "signal_strength": bool(args.signal_strength),
        "signal_strength_scale": float(args.signal_strength_scale),
        "signal_strength_cap": float(args.signal_strength_cap),
        "ensemble_vote": bool(args.ensemble_vote),
        "ensemble_min_votes": int(args.ensemble_min_votes),
        "fill_delay": _spec_fill_delay(spec),
        "warmup_days": _spec_warmup_days(spec),
    }


def _build_provenance(
    *,
    args: argparse.Namespace,
    spec: dict[str, Any],
    strategy_name: str,
    symbols: list[str],
    cost_model: CostModel,
    warmup_days: int,
    fill_delay: int,
    effective_years: int,
    years_overridden_by_spec: bool,
) -> dict[str, Any]:
    return {
        "created_at": _now_iso(),
        "runner": "scripts/run_backtest.py",
        "runner_identity": {
            "script": "scripts/run_backtest.py",
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "python_executable": sys.executable,
        },
        "strategy_slug": args.slug,
        "strategy_name": strategy_name,
        "frozen_spec_hash": spec.get("frozen_hash", ""),
        "spec_hash": spec.get("frozen_hash", ""),
        "spec_frozen": bool(spec.get("frozen", False)),
        "spec_frozen_at": spec.get("frozen_at"),
        "data_dir": str(Path(args.data_dir)),
        "symbols": symbols,
        "policy_inputs": {
            **_build_policy_inputs(
                args,
                spec,
                symbols,
                effective_years,
                years_overridden_by_spec,
            ),
            "warmup_days": int(warmup_days),
            "fill_delay": int(fill_delay),
            "cost_model": {
                "spread_bps": cost_model.spread_bps,
                "slippage_volatility_factor": cost_model.slippage_volatility_factor,
                "flat_slippage_bps": cost_model.flat_slippage_bps,
            },
        },
        "cli": {
            "argv": sys.argv,
        },
    }


def _build_strategy_config(strategy_name: str, spec: dict) -> StrategyConfig:
    """Build a StrategyConfig from frozen spec, mapping parameter names."""
    params = spec.get("parameters", {})

    # Map frozen-spec parameter names to what strategies expect
    mapped_params = dict(params)
    if "top_n_momentum" in params and "top_n" not in params:
        mapped_params["top_n"] = params["top_n_momentum"]
    if "momentum_lookback" in params and "lookback_days" not in params:
        mapped_params["lookback_days"] = params["momentum_lookback"]
    if "rebalance_frequency_days" in params and "rebalance_frequency" not in params:
        mapped_params["rebalance_frequency"] = params["rebalance_frequency_days"]

    # Multi-timeframe momentum parameters (TrendFollowingStrategy v3)
    spec_params = spec.get("parameters", {})
    if "lookback_short" in spec_params:
        mapped_params["lookback_short"] = spec_params["lookback_short"]
    if "lookback_long" in spec_params:
        mapped_params["lookback_long"] = spec_params["lookback_long"]
    if "lookback_medium" in spec_params:
        mapped_params["lookback_medium"] = spec_params["lookback_medium"]
    if "min_timeframes_positive" in spec_params:
        mapped_params["min_timeframes_positive"] = spec_params[
            "min_timeframes_positive"
        ]

    if strategy_name == "vix_regime":
        if "strategy_mode" in spec:
            strategy_mode = spec["strategy_mode"]
            if strategy_mode == "backwardation":
                strategy_mode = "term_structure"
            mapped_params["mode"] = strategy_mode
        if "signal_symbol_near" in params:
            mapped_params["vix_symbol"] = params["signal_symbol_near"]
        if "signal_symbol_medium" in params:
            mapped_params["vix3m_symbol"] = params["signal_symbol_medium"]
        if "trading_symbol_risk_on" in params:
            mapped_params["equity_symbol"] = params["trading_symbol_risk_on"]
        if "trading_symbol_risk_off" in params:
            mapped_params["risk_off_symbol"] = params["trading_symbol_risk_off"]
        if "backwardation_threshold" in params:
            mapped_params["vix_threshold"] = params["backwardation_threshold"]
        if "contango_threshold" in params:
            mapped_params["contango_threshold"] = params["contango_threshold"]
        if "weight_spy_risk_on" in params:
            mapped_params["target_weight"] = params["weight_spy_risk_on"]
        if "weight_spy_risk_off" in params:
            mapped_params["weight_spy_risk_off"] = float(params["weight_spy_risk_off"])
            if "risk_off_symbol_weight" not in params:
                mapped_params["risk_off_weight"] = 1.0 - float(
                    params["weight_spy_risk_off"]
                )
        if "risk_off_symbol_weight" in params:
            mapped_params["risk_off_symbol_weight"] = float(
                params["risk_off_symbol_weight"]
            )

    return StrategyConfig(
        name=strategy_name,
        rebalance_frequency_days=params.get(
            "rebalance_frequency_days",
            spec.get("rebalance_frequency_days", 5),
        ),
        max_positions=params.get(
            "top_n_momentum",
            spec.get("max_positions", 10),
        ),
        target_position_weight=params.get(
            "target_position_weight",
            spec.get("target_position_weight", 0.05),
        ),
        stop_loss_pct=params.get(
            "stop_loss_pct",
            spec.get("stop_loss_pct", 0.05),
        ),
        fractional_shares=bool(
            params.get(
                "fractional_shares",
                spec.get("fractional_shares", False),
            )
        ),
        parameters=mapped_params,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a backtest")
    parser.add_argument("--slug", required=True, help="Strategy slug")
    parser.add_argument(
        "--strategy",
        default=None,
        help=f"Strategy name. Available: {list(STRATEGY_REGISTRY.keys())}",
    )
    parser.add_argument(
        "--symbols",
        default="SPY,QQQ,TLT,GLD,IEF",
        help="Comma-separated symbols",
    )
    parser.add_argument("--years", type=int, default=3, help="Years of history")
    parser.add_argument(
        "--initial-capital",
        type=float,
        default=100_000.0,
        help="Initial capital",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Data directory",
    )
    parser.add_argument(
        "--no-spec-check",
        action="store_true",
        help="Skip frozen spec check (for quick testing only)",
    )
    parser.add_argument(
        "--volatility-target",
        type=float,
        default=None,
        metavar="VOL",
        help=(
            "Annualized volatility target for position scaling "
            "(e.g. 0.15 for 15%%). Default: disabled."
        ),
    )
    parser.add_argument(
        "--vol-target-window",
        type=int,
        default=20,
        help="Rolling window (days) for realized vol estimate. Default: 20.",
    )
    parser.add_argument(
        "--vol-target-max-scale",
        type=float,
        default=2.0,
        help="Maximum leverage multiplier for vol targeting. Default: 2.0.",
    )
    # Rule-based meta-filter flags
    parser.add_argument(
        "--regime-filter",
        action="store_true",
        default=False,
        help="Suppress BUY signals when VIX > vix-threshold (default 25).",
    )
    parser.add_argument(
        "--vix-threshold",
        type=float,
        default=25.0,
        help="VIX level above which regime_filter blocks BUY signals. Default: 25.",
    )
    parser.add_argument(
        "--signal-strength",
        action="store_true",
        default=False,
        help="Scale position size by leader-return magnitude.",
    )
    parser.add_argument(
        "--signal-strength-scale",
        type=float,
        default=0.01,
        help="Leader-return entry threshold divisor for signal_strength_weight. Default: 0.01.",
    )
    parser.add_argument(
        "--signal-strength-cap",
        type=float,
        default=2.0,
        help="Maximum multiplier cap for signal_strength_weight. Default: 2.0.",
    )
    parser.add_argument(
        "--ensemble-vote",
        action="store_true",
        default=False,
        help="Require 2+ BUY signals to agree before acting (ensemble gate).",
    )
    parser.add_argument(
        "--ensemble-min-votes",
        type=int,
        default=2,
        help="Minimum BUY vote count required when --ensemble-vote is set. Default: 2.",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    strat_dir = strategy_dir(data_dir, args.slug)
    cli_symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    symbols = list(cli_symbols)
    effective_years = int(args.years)
    years_overridden_by_spec = False

    # Load or create research spec
    spec: dict = {}
    strategy_name = args.strategy or "sma_crossover"

    if not args.no_spec_check:
        try:
            spec = ensure_frozen_spec(strat_dir)
            # Only use spec-defined strategy class/type if --strategy was not explicitly provided
            if args.strategy is None:
                strategy_name = spec.get(
                    "strategy_class",
                    spec.get("strategy_type", strategy_name),
                )
            spec_symbols = _spec_backtest_symbols(spec)
            if spec_symbols:
                symbols = spec_symbols
            spec_years = _spec_backtest_years(spec, args.years)
            years_overridden_by_spec = spec_years != int(args.years)
            effective_years = spec_years
            logger.info(
                "Loaded frozen research spec for %s (effective years=%d%s)",
                args.slug,
                effective_years,
                ", overridden by spec" if years_overridden_by_spec else "",
            )
        except (FileNotFoundError, ValueError):
            logger.exception("Spec check failed")
            sys.exit(1)
    else:
        logger.warning("Skipping spec check — results are exploratory only")

    lookback_days = effective_years * 365

    config = _build_strategy_config(strategy_name, spec)

    strategy = create_strategy(strategy_name, config)
    cost_model = CostModel.from_spec(spec)
    fill_delay = _spec_fill_delay(spec)
    warmup_days = _spec_warmup_days(spec)

    # Benchmark from spec or default
    benchmark_weights = {"SPY": 0.60, "TLT": 0.40}
    benchmark = spec.get("benchmark", {})
    if benchmark:
        benchmark_weights = benchmark.get("symbols", benchmark_weights)

    # Fetch data
    logger.info("Fetching %d symbols (%d days)...", len(symbols), lookback_days)
    prices_df = fetch_ohlcv(symbols, lookback_days=lookback_days)
    if len(prices_df) == 0:
        logger.error("No data fetched — aborting")
        sys.exit(1)

    # Compute indicators
    logger.info("Computing indicators...")
    indicators_df = compute_indicators(prices_df)

    # Build meta-filter config if any filter flags are set
    meta_filter: MetaFilterConfig | None = None
    if args.regime_filter or args.signal_strength or args.ensemble_vote:
        meta_filter = MetaFilterConfig(
            regime_filter_enabled=args.regime_filter,
            vix_threshold=args.vix_threshold,
            signal_strength_enabled=args.signal_strength,
            signal_strength_scale=args.signal_strength_scale,
            signal_strength_cap=args.signal_strength_cap,
            ensemble_vote_enabled=args.ensemble_vote,
            ensemble_min_votes=args.ensemble_min_votes,
        )
        active = [
            name
            for name, flag in [
                ("regime_filter", args.regime_filter),
                ("signal_strength", args.signal_strength),
                ("ensemble_vote", args.ensemble_vote),
            ]
            if flag
        ]
        logger.info("Meta-filters enabled: %s", ", ".join(active))

    # Run backtest with cost sensitivity
    logger.info("Running backtest with cost sensitivity...")
    if args.volatility_target is not None:
        logger.info(
            "Volatility targeting enabled: target=%.1f%%, window=%d days, max_scale=%.1fx",
            args.volatility_target * 100,
            args.vol_target_window,
            args.vol_target_max_scale,
        )
    engine = BacktestEngine(
        strategy=strategy,
        data_dir=str(data_dir),
        initial_capital=args.initial_capital,
        meta_filter=meta_filter,
        volatility_target=args.volatility_target,
        vol_target_window=args.vol_target_window,
        vol_target_max_scale=args.vol_target_max_scale,
        exit_policy_overrides=spec.get("exit_policy_overrides") or None,
    )

    result = engine.run_with_cost_sensitivity(
        prices_df=prices_df,
        indicators_df=indicators_df,
        slug=args.slug,
        cost_model=cost_model,
        fill_delay=fill_delay,
        warmup_days=warmup_days,
        benchmark_weights=benchmark_weights,
    )

    # Append to experiment registry
    registry = ExperimentRegistry(strat_dir)
    base_metrics = result.metrics.get("1.0x")

    provenance = _build_provenance(
        args=args,
        spec=spec,
        strategy_name=strategy_name,
        symbols=symbols,
        cost_model=cost_model,
        warmup_days=warmup_days,
        fill_delay=fill_delay,
        effective_years=effective_years,
        years_overridden_by_spec=years_overridden_by_spec,
    )

    registry_entry = {
        "experiment_id": result.experiment_id,
        "strategy_name": result.strategy_name,
        "slug": args.slug,
        "start_date": str(result.start_date),
        "end_date": str(result.end_date),
        "symbols": symbols,
        "total_return": base_metrics.total_return if base_metrics else 0,
        "sharpe_ratio": base_metrics.sharpe_ratio if base_metrics else 0,
        "max_drawdown": base_metrics.max_drawdown if base_metrics else 0,
        "dsr": base_metrics.dsr if base_metrics else 0,
        "total_trades": base_metrics.total_trades if base_metrics else 0,
        "spec_hash": spec.get("frozen_hash", ""),
        "frozen_spec_hash": spec.get("frozen_hash", ""),
        "parameters": config.parameters,
        "runner": provenance["runner"],
        "created_at": provenance["created_at"],
        "policy_inputs": provenance["policy_inputs"],
        "smoke_health": result.smoke_health,
        "smoke_health_reason": result.smoke_health_reason,
        "signal_count": result.signal_count,
        "executed_trade_count": result.executed_trade_count,
        "signal_noop_reasons": result.signal_noop_reasons,
    }
    trial_number = registry.append(registry_entry)
    result.trial_number = trial_number

    # Persist experiment artifact
    experiments_dir = strat_dir / "experiments"
    experiments_dir.mkdir(parents=True, exist_ok=True)

    if base_metrics:
        # Compute MinTRL from backtest returns
        min_trl_result = compute_min_trl(
            sharpe=base_metrics.sharpe_ratio,
            skew=0.0,
            kurtosis=0.0,
            n_observations=len(result.daily_returns),
        )
        # Re-compute with actual skew/kurtosis if returns available
        if result.daily_returns and len(result.daily_returns) >= 10:
            import numpy as np
            from scipy import stats as scipy_stats

            arr = np.array(result.daily_returns)
            min_trl_result = compute_min_trl(
                sharpe=base_metrics.sharpe_ratio,
                skew=float(scipy_stats.skew(arr, bias=False)),
                kurtosis=float(scipy_stats.kurtosis(arr, bias=False)),
                n_observations=len(result.daily_returns),
            )
        if not min_trl_result.min_trl_pass:
            logger.warning(
                "MinTRL WARNING: %.1f months available but %.1f months required "
                "for 95%% confidence (SR=%.3f)",
                min_trl_result.backtest_months,
                min_trl_result.min_trl_months,
                min_trl_result.sharpe,
            )

        artifact = {
            "experiment_id": result.experiment_id,
            "trial_number": trial_number,
            "strategy_slug": args.slug,
            "strategy_name": result.strategy_name,
            "start_date": str(result.start_date),
            "end_date": str(result.end_date),
            "created_at": provenance["created_at"],
            "runner": provenance["runner"],
            "runner_identity": provenance["runner_identity"],
            "provenance": provenance,
            "initial_capital": result.initial_capital,
            "symbols": result.symbols_used,
            "spec_hash": spec.get("frozen_hash", ""),
            "frozen_spec_hash": spec.get("frozen_hash", ""),
            "cost_model": {
                "spread_bps": cost_model.spread_bps,
                "slippage_volatility_factor": cost_model.slippage_volatility_factor,
                "flat_slippage_bps": cost_model.flat_slippage_bps,
            },
            "policy_inputs": provenance["policy_inputs"],
            "metrics_1x": {
                "total_return": base_metrics.total_return,
                "annualized_return": base_metrics.annualized_return,
                "sharpe_ratio": base_metrics.sharpe_ratio,
                "sortino_ratio": base_metrics.sortino_ratio,
                "calmar_ratio": base_metrics.calmar_ratio,
                "max_drawdown": base_metrics.max_drawdown,
                "dsr": base_metrics.dsr,
                "psr": base_metrics.psr,
                "total_trades": base_metrics.total_trades,
                "win_rate": base_metrics.win_rate,
            },
            "min_trl_months": round(min_trl_result.min_trl_months, 2),
            "min_trl_pass": min_trl_result.min_trl_pass,
            "min_trl_backtest_months": round(min_trl_result.backtest_months, 2),
            "volatility_target": args.volatility_target,
            "daily_returns": result.daily_returns,
            "data_warnings": result.data_warnings,
            "smoke_health": result.smoke_health,
            "smoke_health_reason": result.smoke_health_reason,
            "smoke_audit": result.smoke_audit,
            "signal_count": result.signal_count,
            "executed_trade_count": result.executed_trade_count,
            "signal_noop_reasons": result.signal_noop_reasons,
            "synthetic_exit_parity_mode": result.exit_parity_mode,
            "synthetic_exit_parity_tier": result.synthetic_exit_parity_tier,
            "synthetic_exit_execution_assumption": result.exit_execution_assumption,
        }
    else:
        artifact = {
            "experiment_id": result.experiment_id,
            "strategy_slug": args.slug,
            "strategy_name": result.strategy_name,
            "created_at": provenance["created_at"],
            "runner": provenance["runner"],
            "runner_identity": provenance["runner_identity"],
            "provenance": provenance,
            "spec_hash": spec.get("frozen_hash", ""),
            "frozen_spec_hash": spec.get("frozen_hash", ""),
            "policy_inputs": provenance["policy_inputs"],
            "error": "No metrics computed",
            "smoke_health": result.smoke_health,
            "smoke_health_reason": result.smoke_health_reason,
            "smoke_audit": result.smoke_audit,
            "signal_count": result.signal_count,
            "executed_trade_count": result.executed_trade_count,
            "signal_noop_reasons": result.signal_noop_reasons,
            "synthetic_exit_parity_mode": result.exit_parity_mode,
            "synthetic_exit_parity_tier": result.synthetic_exit_parity_tier,
            "synthetic_exit_execution_assumption": result.exit_execution_assumption,
        }

    save_artifact(experiments_dir / f"{result.experiment_id}.yaml", artifact)

    # Generate report
    report = generate_backtest_report(result)
    print(report)
    print(
        "SMOKE HEALTH:",
        result.smoke_health,
        "-",
        result.smoke_health_reason,
    )
    print("SMOKE AUDIT:", result.smoke_audit)

    logger.info(
        "Experiment %s saved (trial #%d)",
        result.experiment_id,
        trial_number,
    )


if __name__ == "__main__":
    main()
