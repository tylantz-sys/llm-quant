"""Execute a trading decision from JSON on stdin.

Parses the JSON decision, runs risk checks, executes trades,
saves portfolio snapshot, and prints execution summary.

Usage:
    cd E:/llm-quant && PYTHONPATH=src \\
        python scripts/execute_decision.py --broker alpaca \\
        <<< '{"market_regime": "risk_on", ...}'
    cd E:/llm-quant && PYTHONPATH=src \\
        python scripts/execute_decision.py --pod momo --broker alpaca \\
        <<< '{"market_regime": "risk_on", ...}'

    # Dry-run: validate full pipeline without DB writes or Alpaca orders:
    cd E:/llm-quant && PYTHONPATH=src \\
        python scripts/execute_decision.py --broker alpaca --dry-run \\
        <<< '{"market_regime": "risk_on", ...}'

    # Paper-only (local simulation, no Alpaca):
    cd E:/llm-quant && PYTHONPATH=src \\
        python scripts/execute_decision.py \\
        <<< '{"market_regime": "risk_on", ...}'
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from llm_quant.brain.parser import parse_trading_decision
from llm_quant.broker.alpaca import AlpacaClient, AlpacaError
from llm_quant.broker.executor import submit_alpaca_orders
from llm_quant.config import load_config_for_pod
from llm_quant.db.schema import get_connection
from llm_quant.risk.basket import normalize_crypto_basket_weights
from llm_quant.risk.manager import RiskManager
from llm_quant.surveillance.scanner import SurveillanceScanner
from llm_quant.trading.executor import execute_signals
from llm_quant.trading.exits import (
    build_exit_policy,
    build_exit_runtime,
    build_exit_telemetry_payload,
    evaluate_position_exits,
)
from llm_quant.trading.ledger import log_trades, save_portfolio_snapshot
from llm_quant.trading.portfolio import Portfolio
from llm_quant.trading.runtime_controls import (
    apply_entry_halt_freeze,
    apply_harvest_governance_controls,
    load_latest_harvest_governance_result,
    log_harvest_governance_action,
)

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logger = logging.getLogger(__name__)


def _submit_to_alpaca(
    alpaca_client: AlpacaClient,
    executed: list,
    approved: list,
    conn: object,
    config: object,
    asset_class_map: dict,
    pod_id: str,
    today: object,
) -> list[dict]:
    """Submit executed trades to Alpaca and reconcile fills. Returns broker order records."""
    from llm_quant.broker.reconciliation import (
        log_broker_fills,
        persist_submitted_orders,
        reconcile_broker_orders,
    )

    stop_losses = {sig.symbol: sig.stop_loss for sig in approved}
    submitted_orders = submit_alpaca_orders(
        alpaca_client,
        executed,
        stop_losses,
        config.risk,
        use_brackets=True,
        asset_class_map=asset_class_map,
        execution=config.execution,
    )
    persist_submitted_orders(conn, submitted_orders, pod_id=pod_id)
    reconcile_broker_orders(
        conn,
        alpaca_client,
        portfolio=None,
        ledger_conn=conn,
        pod_id=pod_id,
        order_ids=[o.order_id for o in submitted_orders],
        broker_positions=alpaca_client.list_positions(),
        log_fills_fn=log_broker_fills,
        trade_date=today,
    )
    return [
        {
            "order_id": o.order_id,
            "symbol": o.symbol,
            "side": o.side,
            "qty": o.qty,
            "order_type": o.order_type,
            "status": o.status,
        }
        for o in submitted_orders
    ]


def _build_summary(
    *,
    pod_id: str,
    broker: str,
    today: object,
    decision: object,
    exit_signals: list,
    all_signals: list,
    governed_signals: list,
    exit_telemetry: object,
    exit_policy: object,
    exit_runtime: object,
    runtime_result: object,
    surveillance_status: dict,
    short_rollout_freeze_blocked: list[dict[str, str]],
    approved: list,
    rejected: list,
    executed: list,
    broker_orders: list,
    portfolio: object,
    snapshot_id: object,
    trade_ids: list,
) -> dict:
    """Build the execution summary dict."""
    return {
        "pod_id": pod_id,
        "broker": broker,
        "date": str(today),
        "decision": {
            "market_regime": decision.market_regime.value,
            "regime_confidence": decision.regime_confidence,
            "regime_reasoning": decision.regime_reasoning,
            "portfolio_commentary": decision.portfolio_commentary,
            "total_signals": len(decision.signals),
            "exit_engine_signals": len(exit_signals),
            "total_signals_after_exit_merge": len(all_signals),
            "total_signals_after_governance": len(governed_signals),
            "total_signals_after_short_rollout_freeze": (
                len(governed_signals) - len(short_rollout_freeze_blocked)
            ),
        },
        "surveillance": surveillance_status,
        "short_rollout_freeze": {
            "active": bool(short_rollout_freeze_blocked),
            "blocked_signals": short_rollout_freeze_blocked,
        },
        "exit_engine": build_exit_telemetry_payload(
            exit_telemetry, exit_policy, exit_runtime
        )["exit_engine"],
        "harvest_governance": {
            "active_mandate_name": runtime_result.active_mandate_name,
            "active_mandate_type": runtime_result.active_mandate_type,
            "allocation_scale": runtime_result.allocation_scale,
            "force_flatten": runtime_result.force_flatten,
            "conservative_mandate_name": runtime_result.conservative_mandate_name,
            "lifecycle_recommendation": runtime_result.lifecycle_recommendation,
            "breached_rules": runtime_result.breached_rules,
            "actions": runtime_result.actions,
            "metrics": runtime_result.metrics,
        },
        "risk_filter": {
            "approved": len(approved),
            "rejected": len(rejected),
            "rejected_details": [
                {
                    "symbol": sig.symbol,
                    "action": sig.action.value,
                    "failures": [c.message for c in checks if not c.passed],
                }
                for sig, checks in rejected
            ],
        },
        "executed_trades": [
            {
                "symbol": t.symbol,
                "action": t.action,
                "shares": t.shares,
                "price": round(t.price, 2),
                "notional": round(t.notional, 2),
                "conviction": t.conviction,
                "reasoning": t.reasoning,
            }
            for t in executed
        ],
        "broker_orders": broker_orders,
        "portfolio_after": {
            "nav": round(portfolio.nav, 2),
            "cash": round(portfolio.cash, 2),
            "positions": len(portfolio.positions),
            "total_pnl": round(portfolio.total_pnl, 2),
            "gross_exposure": round(portfolio.gross_exposure, 2),
        },
        "snapshot_id": snapshot_id,
        "trade_ids": trade_ids,
    }


def main():  # noqa: PLR0912, PLR0915 — orchestration entry point; decomposing further adds no clarity
    parser = argparse.ArgumentParser(description="Execute trading decision")
    parser.add_argument("--pod", default="default", help="Pod ID to execute for")
    parser.add_argument(
        "--broker",
        default="paper",
        choices=["paper", "alpaca"],
        help="Execution broker: 'paper' (default) or 'alpaca' to submit live orders",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate pipeline (parse, price-check, risk filter) without writing to DB "
            "or submitting orders to Alpaca. Use to smoke-test signals before trading."
        ),
    )
    args = parser.parse_args()
    pod_id = args.pod
    broker = args.broker.lower()
    dry_run: bool = args.dry_run

    # Read JSON from stdin
    raw_input = sys.stdin.read().strip()
    if not raw_input:
        print(json.dumps({"error": "No input received on stdin"}))
        sys.exit(1)

    config = load_config_for_pod(pod_id)
    db_path = config.general.db_path

    # Resolve relative db_path
    project_root = Path(__file__).resolve().parent.parent
    if not Path(db_path).is_absolute():
        db_path = str(project_root / db_path)

    # Initialise Alpaca client early so we fail fast on missing credentials
    alpaca_client: AlpacaClient | None = None
    if broker == "alpaca":
        try:
            alpaca_client = AlpacaClient.from_env()
        except AlpacaError as exc:
            print(json.dumps({"error": f"Alpaca client init failed: {exc}"}))
            sys.exit(1)

    conn = get_connection(db_path)

    try:
        # Parse the trading decision
        today = datetime.now(tz=UTC).date()
        decision = parse_trading_decision(raw_input, today)

        # Load portfolio
        portfolio = Portfolio.from_db(
            conn, config.general.initial_capital, pod_id=pod_id
        )

        asset_class_map = {
            asset.symbol: asset.asset_class for asset in config.universe.assets
        }

        # Get latest prices
        prices: dict[str, float] = {}
        symbols = set()
        for sig in decision.signals:
            symbols.add(sig.symbol)
        for sym in list(portfolio.positions.keys()):
            symbols.add(sym)

        for symbol in symbols:
            row = conn.execute(
                "SELECT close FROM market_data_daily"
                " WHERE symbol = ? ORDER BY date DESC"
                " LIMIT 1",
                [symbol],
            ).fetchone()
            if row and row[0] is not None:
                prices[symbol] = float(row[0])
            else:
                logger.warning(
                    "No price data in market_data_daily for %s — "
                    "trades for this symbol will be skipped",
                    symbol,
                )

        portfolio.update_prices(prices)
        nav_before = portfolio.nav

        exit_policy = build_exit_policy(config.risk, config.execution)
        exit_runtime = build_exit_runtime(broker, config.execution)
        exit_signals, exit_telemetry = evaluate_position_exits(
            portfolio=portfolio,
            prices=prices,
            states={},
            policy=exit_policy,
            runtime=exit_runtime,
        )

        runtime_result = load_latest_harvest_governance_result(
            conn,
            config=config,
            pod_id=pod_id,
        )

        surveillance_status = {
            "overall_severity": "ok",
            "halts": 0,
            "warnings": 0,
            "halt_details": [],
            "warning_details": [],
        }
        short_rollout_freeze_blocked: list[dict[str, str]] = []
        halt_detectors: set[str] = set()
        try:
            scanner = SurveillanceScanner(config)
            report = scanner.run_full_scan(conn)
            scanner.persist_scan(conn, report)
            surveillance_status = {
                "overall_severity": report.overall_severity.value,
                "halts": len(report.halt_checks),
                "warnings": len(report.warning_checks),
                "halt_details": [
                    {"detector": c.detector, "message": c.message}
                    for c in report.halt_checks
                ],
                "warning_details": [
                    {"detector": c.detector, "message": c.message}
                    for c in report.warning_checks
                ],
            }
            halt_detectors = {c.detector for c in report.halt_checks}
        except Exception as exc:
            logger.warning("Surveillance scan failed in execute_decision: %s", exc)

        all_signals = exit_signals + decision.signals
        governed_signals = apply_harvest_governance_controls(
            all_signals,
            runtime_result,
            portfolio_symbols=set(portfolio.positions.keys()),
        )
        governed_signals, short_rollout_freeze_blocked = apply_entry_halt_freeze(
            governed_signals,
            halt_detectors,
            entry_freeze_mode=config.governance.halt_policy.entry_freeze_mode,
            entry_freeze_detectors=config.governance.halt_policy.entry_freeze_detectors,
        )
        (
            log_harvest_governance_action(
                conn,
                pod_id=pod_id,
                runtime_result=runtime_result,
            )
            if not dry_run
            else None
        )

        # Risk filter
        risk_mgr = RiskManager(config)
        approved, rejected = risk_mgr.filter_signals(
            governed_signals, portfolio, prices
        )

        # Enforce crypto basket equal-weight sizing — clamp BUY crypto target_weights
        # to crypto_basket_target_weight so all basket constituents get flat allocation.
        approved = normalize_crypto_basket_weights(
            approved, config.risk, asset_class_map
        )

        # Dry-run: output preview and stop — no DB writes, no Alpaca calls
        if dry_run:
            preview = {
                "dry_run": True,
                "broker": broker,
                "date": str(today),
                "signals_received": len(decision.signals),
                "signals_after_governance": len(governed_signals),
                "signals_blocked_short_rollout_freeze": len(
                    short_rollout_freeze_blocked
                ),
                "surveillance": surveillance_status,
                "signals_approved": len(approved),
                "signals_rejected": len(rejected),
                "short_rollout_freeze_blocked": short_rollout_freeze_blocked,
                "approved_signals": [
                    {
                        "symbol": s.symbol,
                        "action": str(s.action),
                        "conviction": s.conviction,
                        "stop_loss": s.stop_loss,
                        "target_weight": s.target_weight,
                        "price": prices.get(s.symbol),
                        "asset_class": asset_class_map.get(s.symbol, "equity"),
                    }
                    for s in approved
                ],
                "rejected_signals": [
                    {"symbol": s.symbol, "action": str(s.action), "reason": r}
                    for s, r in rejected
                ],
                "alpaca_reachable": alpaca_client is not None,
                "portfolio_nav": portfolio.nav,
                "portfolio_cash": portfolio.cash,
            }
            print(json.dumps(preview, indent=2))
            return

        # Execute approved signals (paper simulation — updates local portfolio state)
        executed = execute_signals(portfolio, approved, prices, nav_before)

        # Submit to Alpaca if broker mode is active
        broker_orders: list[dict] = []
        if alpaca_client and executed:
            try:
                broker_orders = _submit_to_alpaca(
                    alpaca_client,
                    executed,
                    approved,
                    conn,
                    config,
                    asset_class_map,
                    pod_id,
                    today,
                )
            except AlpacaError as exc:
                print(json.dumps({"error": f"Alpaca order submission failed: {exc}"}))
                sys.exit(1)

        # Log trades and save snapshot
        decision_id = None
        trade_ids = (
            log_trades(conn, executed, today, decision_id, pod_id=pod_id)
            if executed
            else []
        )

        # Compute daily P&L (change from previous day's NAV)
        prev_snap = conn.execute(
            """
            SELECT nav FROM portfolio_snapshots
            WHERE date < ?
            ORDER BY date DESC, snapshot_id DESC
            LIMIT 1
            """,
            [today],
        ).fetchone()

        daily_pnl = None
        if prev_snap is not None:
            daily_pnl = portfolio.nav - float(prev_snap[0])

        snapshot_id = save_portfolio_snapshot(
            conn, portfolio, today, daily_pnl=daily_pnl, pod_id=pod_id
        )

        # Build summary
        summary = _build_summary(
            pod_id=pod_id,
            broker=broker,
            today=today,
            decision=decision,
            exit_signals=exit_signals,
            all_signals=all_signals,
            governed_signals=governed_signals,
            exit_telemetry=exit_telemetry,
            exit_policy=exit_policy,
            exit_runtime=exit_runtime,
            runtime_result=runtime_result,
            surveillance_status=surveillance_status,
            short_rollout_freeze_blocked=short_rollout_freeze_blocked,
            approved=approved,
            rejected=rejected,
            executed=executed,
            broker_orders=broker_orders,
            portfolio=portfolio,
            snapshot_id=snapshot_id,
            trade_ids=trade_ids,
        )

        print(json.dumps(summary, indent=2))

    except ValueError as e:
        print(json.dumps({"error": f"Failed to parse decision: {e}"}))
        sys.exit(1)
    except (OSError, RuntimeError) as e:
        print(json.dumps({"error": f"Execution failed: {e}"}))
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
