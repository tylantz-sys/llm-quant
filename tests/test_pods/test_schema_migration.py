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


def test_schema_version_is_11(pod_db):
    """Verify schema_meta shows version 11 (pod-scoped rotation state)."""
    row = pod_db.execute(
        "SELECT value FROM schema_meta WHERE key = 'version'"
    ).fetchone()
    assert row is not None
    assert row[0] == "11"


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
