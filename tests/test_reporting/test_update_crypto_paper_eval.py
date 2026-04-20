from datetime import date

from llm_quant.db.schema import init_schema
from scripts.update_crypto_paper_eval import _load_db_telemetry


def test_load_db_telemetry_counts_cover_as_closed_trade(tmp_path) -> None:
    db_path = tmp_path / "paper_eval.duckdb"
    conn = init_schema(db_path)
    conn.execute(
        """
        INSERT INTO portfolio_snapshots (
            snapshot_id, date, pod_id, nav, cash, gross_exposure, net_exposure,
            long_exposure, short_exposure, total_pnl
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [1, date(2026, 4, 19), "crypto-pod", 100_500.0, 95_000.0, 5_500.0, 1_500.0, 0.0, 5_500.0, 500.0],
    )
    conn.execute(
        """
        INSERT INTO llm_decisions (
            decision_id, date, pod_id, model, market_regime, num_signals
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [1, date(2026, 4, 19), "crypto-pod", "test-model", "transition", 1],
    )
    conn.execute(
        """
        INSERT INTO trades (
            trade_id, date, pod_id, symbol, action, shares, price, notional,
            prev_hash, row_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [1, date(2026, 4, 19), "crypto-pod", "BTC-USD", "cover", 0.25, 80_000.0, 20_000.0, "", ""],
    )
    conn.commit()
    conn.close()

    snapshots, closed_trades, decisions = _load_db_telemetry(db_path, "crypto-pod")

    assert len(snapshots) == 1
    assert closed_trades == 1
    assert decisions == 1