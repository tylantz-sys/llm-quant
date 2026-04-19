"""Tests for v4 schema migration (multi-pod support)."""

import duckdb

from llm_quant.db.schema import init_schema


def test_init_schema_creates_pods_table(pod_db):
    """Verify the pods table exists after init_schema."""
    tables = {
        row[0]
        for row in pod_db.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    assert "pods" in tables


def test_default_pod_seeded(pod_db):
    """Verify 'default' pod row is inserted during migration."""
    row = pod_db.execute(
        "SELECT pod_id, display_name, strategy_type, initial_capital, status "
        "FROM pods WHERE pod_id = 'default'"
    ).fetchone()
    assert row is not None
    pod_id, display_name, strategy_type, initial_capital, status = row
    assert pod_id == "default"
    assert display_name == "Default Pod"
    assert strategy_type == "regime_momentum"
    assert initial_capital == 100_000.0
    assert status == "active"


def test_pod_id_columns_exist(pod_db):
    """Verify pod_id column exists on trades, portfolio_snapshots, llm_decisions."""
    for table in ("trades", "portfolio_snapshots", "llm_decisions"):
        cols = {
            row[0]
            for row in pod_db.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = ?",
                [table],
            ).fetchall()
        }
        assert "pod_id" in cols, f"pod_id column missing from {table}"


def test_indexes_created(pod_db):
    """Verify the 3 pod-related indexes exist."""
    indexes = {
        row[0]
        for row in pod_db.execute("SELECT index_name FROM duckdb_indexes()").fetchall()
    }
    assert "idx_trades_pod_id" in indexes
    assert "idx_snapshots_pod_date" in indexes
    assert "idx_decisions_pod_date" in indexes


def test_schema_version_is_14(pod_db):
    """Verify schema_meta shows version 14 (short-aware snapshot telemetry)."""
    row = pod_db.execute(
        "SELECT value FROM schema_meta WHERE key = 'version'"
    ).fetchone()
    assert row is not None
    assert row[0] == "14"


def test_strategy_rotation_state_table_exists(pod_db):
    tables = {
        row[0]
        for row in pod_db.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    assert "strategy_rotation_state" in tables
    cols = {
        row[0]
        for row in pod_db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'strategy_rotation_state'"
        ).fetchall()
    }
    assert "pod_id" in cols


def test_decision_telemetry_tables_exist(pod_db):
    tables = {
        row[0]
        for row in pod_db.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    assert "decision_contexts" in tables
    assert "llm_prompt_logs" in tables


def test_profit_take_events_table_and_columns_exist(pod_db):
    tables = {
        row[0]
        for row in pod_db.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    assert "profit_take_events" in tables

    cols = {
        row[0]
        for row in pod_db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'profit_take_events'"
        ).fetchall()
    }
    expected = {
        "event_id",
        "timestamp",
        "pod_id",
        "symbol",
        "event_type",
        "decision_source",
        "sleeve",
        "source_decision_id",
        "decision_id",
        "trade_id",
        "entry_batch",
        "reduction_sequence",
        "position_fraction",
        "action",
        "shares",
        "price",
        "notional",
        "trigger_price",
        "peak_price",
        "drawdown_pct",
        "pre_reduction_peak_unrealized_pnl",
        "pre_reduction_peak_return_pct",
        "trailing_stop_activated_at",
        "peak_to_reduction_drawdown_pct",
        "realized_pnl",
        "return_pct",
        "rule_name",
        "reason",
        "metadata_json",
        "created_at",
    }
    assert expected.issubset(cols)


def test_profit_take_indexes_and_attribution_columns_exist(pod_db):
    indexes = {
        row[0]
        for row in pod_db.execute("SELECT index_name FROM duckdb_indexes()").fetchall()
    }
    assert "idx_profit_take_events_pod_time" in indexes
    assert "idx_profit_take_events_symbol_time" in indexes
    assert "idx_profit_take_events_trade_id" in indexes
    assert "idx_profit_take_events_decision_id" in indexes

    trades_cols = {
        row[0]
        for row in pod_db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'trades'"
        ).fetchall()
    }
    assert {
        "source_decision_id",
        "decision_source",
        "sleeve",
        "is_profit_take",
        "profit_take_reason",
    }.issubset(trades_cols)

    decision_cols = {
        row[0]
        for row in pod_db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'llm_decisions'"
        ).fetchall()
    }
    assert {
        "source_decision_id",
        "decision_source",
        "sleeve",
        "is_profit_take",
        "trigger_symbol",
        "trigger_event_type",
    }.issubset(decision_cols)


def test_v11_repair_migrates_rotation_table_missing_pod_id(tmp_path):
    db_path = tmp_path / "legacy_v11_rotation.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        "CREATE TABLE schema_meta (key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL)"
    )
    conn.execute("INSERT INTO schema_meta VALUES ('version', '11')")
    conn.execute("""
        CREATE TABLE strategy_rotation_state (
            strategy_id VARCHAR NOT NULL PRIMARY KEY,
            disabled_until DATE,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
    conn.execute(
        "INSERT INTO strategy_rotation_state (strategy_id, disabled_until) "
        "VALUES ('legacy_strategy', '2026-04-01')"
    )
    conn.close()

    migrated = init_schema(db_path)
    cols = {
        row[0]
        for row in migrated.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'strategy_rotation_state'"
        ).fetchall()
    }
    assert "pod_id" in cols
    row = migrated.execute(
        "SELECT pod_id, strategy_id, disabled_until "
        "FROM strategy_rotation_state WHERE strategy_id = 'legacy_strategy'"
    ).fetchone()
    assert row is not None
    assert row[0] == "default"
    assert row[1] == "legacy_strategy"
    assert str(row[2]) == "2026-04-01"
    migrated.close()


def test_v12_migration_backfills_is_profit_take_defaults(pod_db):
    trade_default = pod_db.execute(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_name = 'trades' AND column_name = 'is_profit_take'"
    ).fetchone()
    decision_default = pod_db.execute(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_name = 'llm_decisions' AND column_name = 'is_profit_take'"
    ).fetchone()

    assert trade_default is not None
    assert decision_default is not None
    assert trade_default[0] in {"false", "FALSE", "CAST('f' AS BOOLEAN)"}
    assert decision_default[0] in {"false", "FALSE", "CAST('f' AS BOOLEAN)"}

    version_row = pod_db.execute(
        "SELECT value FROM schema_meta WHERE key = 'version'"
    ).fetchone()
    assert version_row is not None
    assert version_row[0] == "14"
