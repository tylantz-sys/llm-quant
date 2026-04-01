"""Execute a trading decision from JSON on stdin.

Parses the JSON decision, runs risk checks, executes trades,
saves portfolio snapshot, and prints execution summary.

Usage:
    cd E:/llm-quant && PYTHONPATH=src \\
        python scripts/execute_decision.py \\
        <<< '{"market_regime": "risk_on", ...}'
    cd E:/llm-quant && PYTHONPATH=src \\
        python scripts/execute_decision.py --pod momo \\
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
from llm_quant.config import load_config_for_pod
from llm_quant.db.schema import get_connection
from llm_quant.risk.manager import RiskManager
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
    apply_harvest_governance_controls,
    load_latest_harvest_governance_result,
    log_harvest_governance_action,
)

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute trading decision")
    parser.add_argument("--pod", default="default", help="Pod ID to execute for")
    args = parser.parse_args()
    pod_id = args.pod

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

    conn = get_connection(db_path)

    try:
        # Parse the trading decision
        today = datetime.now(tz=UTC).date()
        decision = parse_trading_decision(raw_input, today)

        # Load portfolio
        portfolio = Portfolio.from_db(
            conn, config.general.initial_capital, pod_id=pod_id
        )

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
        exit_runtime = build_exit_runtime("paper", config.execution)
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
        all_signals = exit_signals + decision.signals
        governed_signals = apply_harvest_governance_controls(
            all_signals,
            runtime_result,
            portfolio_symbols=set(portfolio.positions.keys()),
        )
        log_harvest_governance_action(
            conn,
            pod_id=pod_id,
            runtime_result=runtime_result,
        )

        # Risk filter
        risk_mgr = RiskManager(config)
        approved, rejected = risk_mgr.filter_signals(
            governed_signals, portfolio, prices
        )

        # Execute approved signals
        executed = execute_signals(portfolio, approved, prices, nav_before)

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
        summary = {
            "pod_id": pod_id,
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
