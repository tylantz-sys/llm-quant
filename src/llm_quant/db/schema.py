"""DuckDB schema creation and management."""

import logging
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 14

DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS universe (
        symbol VARCHAR PRIMARY KEY,
        name VARCHAR NOT NULL,
        category VARCHAR NOT NULL,
        sector VARCHAR NOT NULL,
        tradeable BOOLEAN DEFAULT TRUE,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_data_daily (
        symbol VARCHAR NOT NULL,
        date DATE NOT NULL,
        open DOUBLE,
        high DOUBLE,
        low DOUBLE,
        close DOUBLE,
        volume BIGINT,
        adj_close DOUBLE,
        sma_20 DOUBLE,
        sma_50 DOUBLE,
        rsi_14 DOUBLE,
        macd DOUBLE,
        macd_signal DOUBLE,
        macd_hist DOUBLE,
        atr_14 DOUBLE,
        PRIMARY KEY (symbol, date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pods (
        pod_id VARCHAR PRIMARY KEY,
        display_name VARCHAR NOT NULL,
        strategy_type VARCHAR NOT NULL,
        initial_capital DOUBLE NOT NULL DEFAULT 100000.0,
        status VARCHAR NOT NULL DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        retired_at TIMESTAMP,
        config_path VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_snapshots (
        snapshot_id INTEGER PRIMARY KEY,
        date DATE NOT NULL,
        pod_id VARCHAR NOT NULL DEFAULT 'default',
        nav DOUBLE NOT NULL,
        cash DOUBLE NOT NULL,
        gross_exposure DOUBLE NOT NULL,
        net_exposure DOUBLE NOT NULL,
        long_exposure DOUBLE NOT NULL DEFAULT 0.0,
        short_exposure DOUBLE NOT NULL DEFAULT 0.0,
        total_pnl DOUBLE NOT NULL,
        daily_pnl DOUBLE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE SEQUENCE IF NOT EXISTS seq_snapshot_id START 1
    """,
    """
    CREATE TABLE IF NOT EXISTS positions (
        snapshot_id INTEGER NOT NULL,
        symbol VARCHAR NOT NULL,
        shares DOUBLE NOT NULL,
        avg_cost DOUBLE NOT NULL,
        current_price DOUBLE NOT NULL,
        market_value DOUBLE NOT NULL,
        unrealized_pnl DOUBLE NOT NULL,
        weight DOUBLE NOT NULL,
        is_short BOOLEAN NOT NULL DEFAULT FALSE,
        short_proceeds DOUBLE NOT NULL DEFAULT 0.0,
        stop_loss DOUBLE,
        PRIMARY KEY (snapshot_id, symbol),
        FOREIGN KEY (snapshot_id) REFERENCES portfolio_snapshots(snapshot_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trades (
        trade_id INTEGER PRIMARY KEY,
        date DATE NOT NULL,
        pod_id VARCHAR NOT NULL DEFAULT 'default',
        symbol VARCHAR NOT NULL,
        action VARCHAR NOT NULL,
        shares DOUBLE NOT NULL,
        price DOUBLE NOT NULL,
        notional DOUBLE NOT NULL,
        conviction VARCHAR,
        reasoning TEXT,
        strategy_id VARCHAR,
        entry_batch INTEGER,
        exit_reason VARCHAR,
        llm_decision_id INTEGER,
        source_decision_id INTEGER,
        decision_source VARCHAR,
        sleeve VARCHAR,
        is_profit_take BOOLEAN DEFAULT FALSE,
        profit_take_reason VARCHAR,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        prev_hash VARCHAR NOT NULL DEFAULT '',
        row_hash VARCHAR NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE SEQUENCE IF NOT EXISTS seq_trade_id START 1
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_decisions (
        decision_id INTEGER PRIMARY KEY,
        date DATE NOT NULL,
        pod_id VARCHAR NOT NULL DEFAULT 'default',
        decision_type VARCHAR NOT NULL DEFAULT 'llm',
        model VARCHAR NOT NULL,
        prompt_tokens INTEGER,
        completion_tokens INTEGER,
        total_tokens INTEGER,
        cost_usd DOUBLE,
        market_regime VARCHAR,
        regime_confidence DOUBLE,
        num_signals INTEGER,
        raw_response TEXT,
        source_decision_id INTEGER,
        decision_source VARCHAR,
        sleeve VARCHAR,
        is_profit_take BOOLEAN DEFAULT FALSE,
        trigger_symbol VARCHAR,
        trigger_event_type VARCHAR,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decision_contexts (
        decision_id INTEGER NOT NULL,
        pod_id VARCHAR NOT NULL DEFAULT 'default',
        timestamp TIMESTAMP NOT NULL,
        context_json TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_prompt_logs (
        decision_id INTEGER NOT NULL,
        prompt_type VARCHAR NOT NULL,
        prompt_text TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_data_intraday (
        symbol VARCHAR NOT NULL,
        timestamp TIMESTAMP NOT NULL,
        open DOUBLE,
        high DOUBLE,
        low DOUBLE,
        close DOUBLE,
        volume BIGINT,
        vwap DOUBLE,
        sma_20 DOUBLE,
        sma_50 DOUBLE,
        rsi_14 DOUBLE,
        macd DOUBLE,
        macd_signal DOUBLE,
        macd_hist DOUBLE,
        atr_14 DOUBLE,
        PRIMARY KEY (symbol, timestamp)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS intraday_position_state (
        pod_id VARCHAR NOT NULL DEFAULT 'default',
        symbol VARCHAR NOT NULL,
        entry_batch INTEGER NOT NULL DEFAULT 0,
        entry_price DOUBLE,
        peak_price DOUBLE,
        partial_exit_taken BOOLEAN DEFAULT FALSE,
        last_entry_ts TIMESTAMP,
        last_exit_ts TIMESTAMP,
        cooldown_until_ts TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (pod_id, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS intraday_order_state (
        pod_id VARCHAR NOT NULL DEFAULT 'default',
        symbol VARCHAR NOT NULL,
        partial_tp_order_id VARCHAR,
        oco_order_id VARCHAR,
        oco_tp_order_id VARCHAR,
        oco_stop_order_id VARCHAR,
        oco_leg_missing_count INTEGER DEFAULT 0,
        hwm DOUBLE,
        remaining_qty DOUBLE,
        tp_status VARCHAR,
        oco_tp_status VARCHAR,
        stop_status VARCHAR,
        last_checked_at TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (pod_id, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS strategy_rotation_state (
        pod_id VARCHAR NOT NULL DEFAULT 'default',
        strategy_id VARCHAR NOT NULL,
        disabled_until DATE,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (pod_id, strategy_id)
    )
    """,
    """
    CREATE SEQUENCE IF NOT EXISTS seq_intraday_snapshot_id START 1
    """,
    """
    CREATE TABLE IF NOT EXISTS intraday_context_snapshots (
        snapshot_id INTEGER PRIMARY KEY,
        timestamp TIMESTAMP NOT NULL,
        pod_id VARCHAR NOT NULL DEFAULT 'default',
        context_json TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE SEQUENCE IF NOT EXISTS seq_decision_id START 1
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key VARCHAR PRIMARY KEY,
        value VARCHAR NOT NULL
    )
    """,
    """
    CREATE SEQUENCE IF NOT EXISTS seq_profit_take_event_id START 1
    """,
    """
    CREATE TABLE IF NOT EXISTS profit_take_events (
        event_id INTEGER PRIMARY KEY DEFAULT nextval('seq_profit_take_event_id'),
        timestamp TIMESTAMP NOT NULL,
        pod_id VARCHAR NOT NULL DEFAULT 'default',
        symbol VARCHAR NOT NULL,
        event_type VARCHAR NOT NULL,
        decision_source VARCHAR,
        sleeve VARCHAR,
        source_decision_id INTEGER,
        decision_id INTEGER,
        trade_id INTEGER,
        entry_batch INTEGER,
        reduction_sequence INTEGER,
        position_fraction DOUBLE,
        action VARCHAR,
        shares DOUBLE,
        price DOUBLE,
        notional DOUBLE,
        trigger_price DOUBLE,
        peak_price DOUBLE,
        drawdown_pct DOUBLE,
        pre_reduction_peak_unrealized_pnl DOUBLE,
        pre_reduction_peak_return_pct DOUBLE,
        trailing_stop_activated_at TIMESTAMP,
        peak_to_reduction_drawdown_pct DOUBLE,
        realized_pnl DOUBLE,
        return_pct DOUBLE,
        rule_name VARCHAR,
        reason VARCHAR,
        metadata_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # --- Governance / Surveillance tables (v3) ---
    """
    CREATE SEQUENCE IF NOT EXISTS seq_scan_id START 1
    """,
    """
    CREATE TABLE IF NOT EXISTS surveillance_scans (
        scan_id INTEGER PRIMARY KEY DEFAULT nextval('seq_scan_id'),
        scan_timestamp TIMESTAMP NOT NULL,
        overall_severity VARCHAR NOT NULL,
        total_checks INTEGER NOT NULL,
        halt_count INTEGER NOT NULL,
        warning_count INTEGER NOT NULL,
        checks_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS config_hashes (
        file_path VARCHAR NOT NULL,
        hash_sha256 VARCHAR NOT NULL,
        recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (file_path, hash_sha256)
    )
    """,
    """
    CREATE SEQUENCE IF NOT EXISTS seq_change_id START 1
    """,
    """
    CREATE TABLE IF NOT EXISTS strategy_changelog (
        change_id INTEGER PRIMARY KEY DEFAULT nextval('seq_change_id'),
        change_date DATE NOT NULL,
        change_type VARCHAR NOT NULL,
        description TEXT NOT NULL,
        config_diff TEXT,
        author VARCHAR DEFAULT 'system',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # --- COT (Commitments of Traders) weekly data (v5) ---
    """
    CREATE TABLE IF NOT EXISTS cot_weekly (
        symbol VARCHAR NOT NULL,
        report_date DATE NOT NULL,
        commercial_net DOUBLE,
        noncommercial_net DOUBLE,
        open_interest DOUBLE,
        cot_index DOUBLE,
        PRIMARY KEY (symbol, report_date)
    )
    """,
    # --- Pod indexes (v4) ---
    """
    CREATE INDEX IF NOT EXISTS idx_trades_pod_id
        ON trades (pod_id, trade_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_snapshots_pod_date
        ON portfolio_snapshots (pod_id, date DESC, snapshot_id DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_decisions_pod_date
        ON llm_decisions (pod_id, date DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_profit_take_events_pod_time
        ON profit_take_events (pod_id, timestamp DESC, event_id DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_profit_take_events_symbol_time
        ON profit_take_events (symbol, timestamp DESC, event_id DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_profit_take_events_trade_id
        ON profit_take_events (trade_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_profit_take_events_decision_id
        ON profit_take_events (decision_id)
    """,
]


def _migrate_v1_to_v2(conn: duckdb.DuckDBPyConnection) -> None:
    """Add hash-chain columns to trades and backfill existing rows."""
    cols = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'trades'"
        ).fetchall()
    }
    if "prev_hash" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN prev_hash VARCHAR DEFAULT ''")
    if "row_hash" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN row_hash VARCHAR DEFAULT ''")

    from llm_quant.db.integrity import backfill_hashes

    backfill_hashes(conn)
    logger.info("Migrated schema to v2: hash-chain columns added.")


def _migrate_v2_to_v3(conn: duckdb.DuckDBPyConnection) -> None:
    """Add governance/surveillance tables for production monitoring."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    if "surveillance_scans" not in tables:
        conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_scan_id START 1")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS surveillance_scans (
                scan_id INTEGER PRIMARY KEY DEFAULT nextval('seq_scan_id'),
                scan_timestamp TIMESTAMP NOT NULL,
                overall_severity VARCHAR NOT NULL,
                total_checks INTEGER NOT NULL,
                halt_count INTEGER NOT NULL,
                warning_count INTEGER NOT NULL,
                checks_json TEXT
            )
            """)
    if "config_hashes" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS config_hashes (
                file_path VARCHAR NOT NULL,
                hash_sha256 VARCHAR NOT NULL,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (file_path, hash_sha256)
            )
            """)
    if "strategy_changelog" not in tables:
        conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_change_id START 1")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_changelog (
                change_id INTEGER PRIMARY KEY DEFAULT nextval('seq_change_id'),
                change_date DATE NOT NULL,
                change_type VARCHAR NOT NULL,
                description TEXT NOT NULL,
                config_diff TEXT,
                author VARCHAR DEFAULT 'system',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)
    logger.info("Migrated schema to v3: governance/surveillance tables added.")


def _migrate_v3_to_v4(conn: duckdb.DuckDBPyConnection) -> None:
    """Add pods table and pod_id columns for multi-pod support."""
    # Create pods table
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    if "pods" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pods (
                pod_id VARCHAR PRIMARY KEY,
                display_name VARCHAR NOT NULL,
                strategy_type VARCHAR NOT NULL,
                initial_capital DOUBLE NOT NULL DEFAULT 100000.0,
                status VARCHAR NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                retired_at TIMESTAMP,
                config_path VARCHAR
            )
            """)

    # Insert default pod if not present
    existing = conn.execute(
        "SELECT pod_id FROM pods WHERE pod_id = 'default'"
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO pods "
            "(pod_id, display_name, strategy_type, "
            "initial_capital, status) "
            "VALUES ('default', 'Default Pod', "
            "'regime_momentum', 100000.0, 'active')"
        )

    # Add pod_id column to trades, portfolio_snapshots, llm_decisions
    for table in ("trades", "portfolio_snapshots", "llm_decisions"):
        cols = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = ?",
                [table],
            ).fetchall()
        }
        if "pod_id" not in cols:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN pod_id VARCHAR DEFAULT 'default'"
            )
            conn.execute(f"UPDATE {table} SET pod_id = 'default' WHERE pod_id IS NULL")

    # Create indexes
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_trades_pod_id ON trades (pod_id, trade_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_snapshots_pod_date "
        "ON portfolio_snapshots (pod_id, date DESC, snapshot_id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decisions_pod_date "
        "ON llm_decisions (pod_id, date DESC)"
    )

    logger.info("Migrated schema to v4: multi-pod support added.")


def _migrate_v4_to_v5(conn: duckdb.DuckDBPyConnection) -> None:
    """Add cot_weekly table for CFTC COT regime overlay."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    if "cot_weekly" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cot_weekly (
                symbol VARCHAR NOT NULL,
                report_date DATE NOT NULL,
                commercial_net DOUBLE,
                noncommercial_net DOUBLE,
                open_interest DOUBLE,
                cot_index DOUBLE,
                PRIMARY KEY (symbol, report_date)
            )
            """)
    logger.info("Migrated schema to v5: cot_weekly table added.")


def _migrate_v5_to_v6(conn: duckdb.DuckDBPyConnection) -> None:
    """Add intraday tables + trade metadata columns."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    if "market_data_intraday" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_data_intraday (
                symbol VARCHAR NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                vwap DOUBLE,
                sma_20 DOUBLE,
                sma_50 DOUBLE,
                rsi_14 DOUBLE,
                macd DOUBLE,
                macd_signal DOUBLE,
                macd_hist DOUBLE,
                atr_14 DOUBLE,
                PRIMARY KEY (symbol, timestamp)
            )
            """)
    if "intraday_position_state" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS intraday_position_state (
                pod_id VARCHAR NOT NULL DEFAULT 'default',
                symbol VARCHAR NOT NULL,
                entry_batch INTEGER NOT NULL DEFAULT 0,
                entry_price DOUBLE,
                peak_price DOUBLE,
                partial_exit_taken BOOLEAN DEFAULT FALSE,
                last_entry_ts TIMESTAMP,
                last_exit_ts TIMESTAMP,
                cooldown_until_ts TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (pod_id, symbol)
            )
            """)
    if "intraday_context_snapshots" not in tables:
        conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_intraday_snapshot_id START 1")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS intraday_context_snapshots (
                snapshot_id INTEGER PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL,
                pod_id VARCHAR NOT NULL DEFAULT 'default',
                context_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

    # Add columns to trades table if missing
    trade_cols = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'trades'"
        ).fetchall()
    }
    if "strategy_id" not in trade_cols:
        conn.execute("ALTER TABLE trades ADD COLUMN strategy_id VARCHAR")
    if "entry_batch" not in trade_cols:
        conn.execute("ALTER TABLE trades ADD COLUMN entry_batch INTEGER")
    if "exit_reason" not in trade_cols:
        conn.execute("ALTER TABLE trades ADD COLUMN exit_reason VARCHAR")

    logger.info("Migrated schema to v6: intraday tables + trade metadata added.")


def _migrate_v6_to_v7(conn: duckdb.DuckDBPyConnection) -> None:
    """Add intraday_order_state table for OCO/trailing management."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    if "intraday_order_state" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS intraday_order_state (
                pod_id VARCHAR NOT NULL DEFAULT 'default',
                symbol VARCHAR NOT NULL,
                tp_order_id VARCHAR,
                stop_order_id VARCHAR,
                hwm DOUBLE,
                remaining_qty DOUBLE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (pod_id, symbol)
            )
            """)
    logger.info("Migrated schema to v7: intraday_order_state added.")


def _migrate_v7_to_v8(conn: duckdb.DuckDBPyConnection) -> None:
    """Add decision_type + intraday order status columns."""
    # decision_type for llm_decisions
    decision_cols = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'llm_decisions'"
        ).fetchall()
    }
    if "decision_type" not in decision_cols:
        conn.execute(
            "ALTER TABLE llm_decisions "
            "ADD COLUMN decision_type VARCHAR DEFAULT 'llm'"
        )
        conn.execute(
            "UPDATE llm_decisions SET decision_type = 'llm' "
            "WHERE decision_type IS NULL"
        )

    # intraday_order_state columns
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    if "intraday_order_state" in tables:
        order_cols = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'intraday_order_state'"
            ).fetchall()
        }
        additions = [
            ("partial_tp_order_id", "VARCHAR"),
            ("oco_order_id", "VARCHAR"),
            ("oco_tp_order_id", "VARCHAR"),
            ("oco_stop_order_id", "VARCHAR"),
            ("tp_status", "VARCHAR"),
            ("oco_tp_status", "VARCHAR"),
            ("stop_status", "VARCHAR"),
            ("last_checked_at", "TIMESTAMP"),
        ]
        for col, col_type in additions:
            if col not in order_cols:
                conn.execute(
                    f"ALTER TABLE intraday_order_state ADD COLUMN {col} {col_type}"
                )

        # Backfill from legacy column names if present.
        if "tp_order_id" in order_cols:
            conn.execute(
                "UPDATE intraday_order_state "
                "SET partial_tp_order_id = tp_order_id "
                "WHERE partial_tp_order_id IS NULL"
            )
        if "stop_order_id" in order_cols:
            conn.execute(
                "UPDATE intraday_order_state "
                "SET oco_stop_order_id = stop_order_id "
                "WHERE oco_stop_order_id IS NULL"
            )

    logger.info(
        "Migrated schema to v8: decision_type + intraday order status columns added."
    )


def _migrate_v8_to_v9(conn: duckdb.DuckDBPyConnection) -> None:
    """Add strategy rotation state + OCO leg tracking fields."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    if "strategy_rotation_state" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_rotation_state (
                pod_id VARCHAR NOT NULL DEFAULT 'default',
                strategy_id VARCHAR NOT NULL,
                disabled_until DATE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (pod_id, strategy_id)
            )
            """)

    if "intraday_order_state" in tables:
        order_cols = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'intraday_order_state'"
            ).fetchall()
        }
        if "oco_leg_missing_count" not in order_cols:
            conn.execute(
                "ALTER TABLE intraday_order_state "
                "ADD COLUMN oco_leg_missing_count INTEGER DEFAULT 0"
            )

    logger.info("Migrated schema to v9: rotation state + OCO leg tracking added.")


def _migrate_v9_to_v10(conn: duckdb.DuckDBPyConnection) -> None:
    """Add decision context + prompt log tables."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    if "decision_contexts" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS decision_contexts (
                decision_id INTEGER NOT NULL,
                pod_id VARCHAR NOT NULL DEFAULT 'default',
                timestamp TIMESTAMP NOT NULL,
                context_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)
    if "llm_prompt_logs" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_prompt_logs (
                decision_id INTEGER NOT NULL,
                prompt_type VARCHAR NOT NULL,
                prompt_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)
    logger.info("Migrated schema to v10: decision contexts + prompt logs added.")


def _migrate_v10_to_v11(conn: duckdb.DuckDBPyConnection) -> None:
    """Scope strategy rotation state by pod_id."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    if "strategy_rotation_state" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_rotation_state (
                pod_id VARCHAR NOT NULL DEFAULT 'default',
                strategy_id VARCHAR NOT NULL,
                disabled_until DATE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (pod_id, strategy_id)
            )
            """)
        logger.info("Migrated schema to v11: created pod-scoped rotation state.")
        return

    cols = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'strategy_rotation_state'"
        ).fetchall()
    }
    if "pod_id" in cols:
        logger.info("Schema v11 rotation migration skipped; pod_id already present.")
        return

    conn.execute("""
        CREATE TABLE IF NOT EXISTS strategy_rotation_state_v11 (
            pod_id VARCHAR NOT NULL DEFAULT 'default',
            strategy_id VARCHAR NOT NULL,
            disabled_until DATE,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (pod_id, strategy_id)
        )
        """)
    conn.execute("""
        INSERT INTO strategy_rotation_state_v11
            (pod_id, strategy_id, disabled_until, updated_at)
        SELECT
            'default' AS pod_id,
            strategy_id,
            disabled_until,
            updated_at
        FROM strategy_rotation_state
        """)
    conn.execute("DROP TABLE strategy_rotation_state")
    conn.execute(
        "ALTER TABLE strategy_rotation_state_v11 RENAME TO strategy_rotation_state"
    )
    logger.info("Migrated schema to v11: pod-scoped rotation state enabled.")


def _rebuild_table_with_added_columns(
    conn: duckdb.DuckDBPyConnection,
    *,
    table_name: str,
    temp_table_name: str,
    final_columns: list[tuple[str, str]],
    copy_columns: list[str],
) -> None:
    """Rebuild a table with an expanded schema when ALTER TABLE is blocked."""
    conn.execute(f"DROP TABLE IF EXISTS {temp_table_name}")
    column_defs = ",\n            ".join(
        f"{column_name} {column_type}" for column_name, column_type in final_columns
    )
    conn.execute(f"""
        CREATE TABLE {temp_table_name} (
            {column_defs}
        )
        """)
    copy_column_sql = ", ".join(copy_columns)
    conn.execute(f"""
        INSERT INTO {temp_table_name} ({copy_column_sql})
        SELECT {copy_column_sql}
        FROM {table_name}
        """)
    conn.execute(f"DROP TABLE {table_name}")
    conn.execute(f"ALTER TABLE {temp_table_name} RENAME TO {table_name}")


def _migrate_v11_to_v12(conn: duckdb.DuckDBPyConnection) -> None:
    """Add profit-taking telemetry schema objects and attribution columns."""
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_profit_take_event_id START 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS profit_take_events (
            event_id INTEGER PRIMARY KEY DEFAULT nextval('seq_profit_take_event_id'),
            timestamp TIMESTAMP NOT NULL,
            pod_id VARCHAR NOT NULL DEFAULT 'default',
            symbol VARCHAR NOT NULL,
            event_type VARCHAR NOT NULL,
            decision_source VARCHAR,
            sleeve VARCHAR,
            source_decision_id INTEGER,
            decision_id INTEGER,
            trade_id INTEGER,
            entry_batch INTEGER,
            action VARCHAR,
            shares DOUBLE,
            price DOUBLE,
            notional DOUBLE,
            trigger_price DOUBLE,
            peak_price DOUBLE,
            drawdown_pct DOUBLE,
            realized_pnl DOUBLE,
            return_pct DOUBLE,
            rule_name VARCHAR,
            reason VARCHAR,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

    trade_cols = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'trades'"
        ).fetchall()
    }
    trade_additions = [
        ("source_decision_id", "INTEGER"),
        ("decision_source", "VARCHAR"),
        ("sleeve", "VARCHAR"),
        ("is_profit_take", "BOOLEAN DEFAULT FALSE"),
        ("profit_take_reason", "VARCHAR"),
    ]
    if "profit_take_reason" not in trade_cols:
        final_trade_columns = [
            ("trade_id", "INTEGER PRIMARY KEY"),
            ("date", "DATE NOT NULL"),
            ("pod_id", "VARCHAR NOT NULL DEFAULT 'default'"),
            ("symbol", "VARCHAR NOT NULL"),
            ("action", "VARCHAR NOT NULL"),
            ("shares", "DOUBLE NOT NULL"),
            ("price", "DOUBLE NOT NULL"),
            ("notional", "DOUBLE NOT NULL"),
            ("conviction", "VARCHAR"),
            ("reasoning", "TEXT"),
            ("strategy_id", "VARCHAR"),
            ("entry_batch", "INTEGER"),
            ("exit_reason", "VARCHAR"),
            ("llm_decision_id", "INTEGER"),
            ("source_decision_id", "INTEGER"),
            ("decision_source", "VARCHAR"),
            ("sleeve", "VARCHAR"),
            ("is_profit_take", "BOOLEAN DEFAULT FALSE"),
            ("profit_take_reason", "VARCHAR"),
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ("prev_hash", "VARCHAR NOT NULL DEFAULT ''"),
            ("row_hash", "VARCHAR NOT NULL DEFAULT ''"),
        ]
        copy_trade_columns = [col for col, _ in final_trade_columns if col in trade_cols]
        _rebuild_table_with_added_columns(
            conn,
            table_name="trades",
            temp_table_name="trades_v12_migration",
            final_columns=final_trade_columns,
            copy_columns=copy_trade_columns,
        )
    else:
        for col, col_type in trade_additions:
            if col not in trade_cols:
                conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_type}")
    if "is_profit_take" in {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'trades'"
        ).fetchall()
    }:
        conn.execute(
            "UPDATE trades SET is_profit_take = FALSE WHERE is_profit_take IS NULL"
        )

    decision_cols = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'llm_decisions'"
        ).fetchall()
    }
    decision_additions = [
        ("source_decision_id", "INTEGER"),
        ("decision_source", "VARCHAR"),
        ("sleeve", "VARCHAR"),
        ("is_profit_take", "BOOLEAN DEFAULT FALSE"),
        ("trigger_symbol", "VARCHAR"),
        ("trigger_event_type", "VARCHAR"),
    ]
    if "trigger_event_type" not in decision_cols:
        final_decision_columns = [
            ("decision_id", "INTEGER PRIMARY KEY"),
            ("date", "DATE NOT NULL"),
            ("pod_id", "VARCHAR NOT NULL DEFAULT 'default'"),
            ("decision_type", "VARCHAR NOT NULL DEFAULT 'llm'"),
            ("model", "VARCHAR NOT NULL"),
            ("prompt_tokens", "INTEGER"),
            ("completion_tokens", "INTEGER"),
            ("total_tokens", "INTEGER"),
            ("cost_usd", "DOUBLE"),
            ("market_regime", "VARCHAR"),
            ("regime_confidence", "DOUBLE"),
            ("num_signals", "INTEGER"),
            ("raw_response", "TEXT"),
            ("source_decision_id", "INTEGER"),
            ("decision_source", "VARCHAR"),
            ("sleeve", "VARCHAR"),
            ("is_profit_take", "BOOLEAN DEFAULT FALSE"),
            ("trigger_symbol", "VARCHAR"),
            ("trigger_event_type", "VARCHAR"),
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ]
        copy_decision_columns = [
            col for col, _ in final_decision_columns if col in decision_cols
        ]
        _rebuild_table_with_added_columns(
            conn,
            table_name="llm_decisions",
            temp_table_name="llm_decisions_v12_migration",
            final_columns=final_decision_columns,
            copy_columns=copy_decision_columns,
        )
    else:
        for col, col_type in decision_additions:
            if col not in decision_cols:
                conn.execute(f"ALTER TABLE llm_decisions ADD COLUMN {col} {col_type}")
    if "is_profit_take" in {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'llm_decisions'"
        ).fetchall()
    }:
        conn.execute(
            "UPDATE llm_decisions SET is_profit_take = FALSE "
            "WHERE is_profit_take IS NULL"
        )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_profit_take_events_pod_time "
        "ON profit_take_events (pod_id, timestamp DESC, event_id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_profit_take_events_symbol_time "
        "ON profit_take_events (symbol, timestamp DESC, event_id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_profit_take_events_trade_id "
        "ON profit_take_events (trade_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_profit_take_events_decision_id "
        "ON profit_take_events (decision_id)"
    )
    logger.info("Migrated schema to v12: profit-taking telemetry enabled.")


def _migrate_v12_to_v13(conn: duckdb.DuckDBPyConnection) -> None:
    """Add first-class profit-take lifecycle telemetry fields."""
    event_cols = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'profit_take_events'"
        ).fetchall()
    }
    additions = [
        ("reduction_sequence", "INTEGER"),
        ("position_fraction", "DOUBLE"),
        ("pre_reduction_peak_unrealized_pnl", "DOUBLE"),
        ("pre_reduction_peak_return_pct", "DOUBLE"),
        ("trailing_stop_activated_at", "TIMESTAMP"),
        ("peak_to_reduction_drawdown_pct", "DOUBLE"),
    ]
    for col, col_type in additions:
        if col not in event_cols:
            conn.execute(
                f"ALTER TABLE profit_take_events ADD COLUMN {col} {col_type}"
            )
    logger.info("Migrated schema to v13: profit-take lifecycle telemetry added.")


def _migrate_v13_to_v14(conn: duckdb.DuckDBPyConnection) -> None:
    """Add short-aware exposure telemetry to snapshots and positions."""
    snapshot_cols = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'portfolio_snapshots'"
        ).fetchall()
    }
    if "long_exposure" not in snapshot_cols:
        conn.execute(
            "ALTER TABLE portfolio_snapshots "
            "ADD COLUMN long_exposure DOUBLE DEFAULT 0.0"
        )
    if "short_exposure" not in snapshot_cols:
        conn.execute(
            "ALTER TABLE portfolio_snapshots "
            "ADD COLUMN short_exposure DOUBLE DEFAULT 0.0"
        )

    # Backfill where possible from gross/net decomposition:
    # gross = long + short, net = long - short.
    conn.execute(
        "UPDATE portfolio_snapshots "
        "SET long_exposure = (gross_exposure + net_exposure) / 2.0 "
        "WHERE long_exposure IS NULL"
    )
    conn.execute(
        "UPDATE portfolio_snapshots "
        "SET short_exposure = (gross_exposure - net_exposure) / 2.0 "
        "WHERE short_exposure IS NULL"
    )

    position_cols = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'positions'"
        ).fetchall()
    }
    if "is_short" not in position_cols:
        conn.execute(
            "ALTER TABLE positions ADD COLUMN is_short BOOLEAN DEFAULT FALSE"
        )
    if "short_proceeds" not in position_cols:
        conn.execute(
            "ALTER TABLE positions ADD COLUMN short_proceeds DOUBLE DEFAULT 0.0"
        )
    conn.execute("UPDATE positions SET is_short = FALSE WHERE is_short IS NULL")
    conn.execute("UPDATE positions SET short_proceeds = 0.0 WHERE short_proceeds IS NULL")
    logger.info("Migrated schema to v14: short-aware snapshot and position telemetry added.")


def _needs_v11_rotation_migration(
    conn: duckdb.DuckDBPyConnection,
    old_version: int,
) -> bool:
    if old_version < 11:
        return True

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    if "strategy_rotation_state" not in tables:
        return True

    rotation_cols = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'strategy_rotation_state'"
        ).fetchall()
    }
    return "pod_id" not in rotation_cols


def init_schema(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    """Create all tables in DuckDB. Returns the connection."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(str(db_path))
    for stmt in DDL_STATEMENTS:
        conn.execute(stmt)

    # Run migrations for existing databases
    old_ver = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'version'"
    ).fetchone()
    old_version = int(old_ver[0]) if old_ver else 0
    if old_version < 2:
        _migrate_v1_to_v2(conn)
    if old_version < 3:
        _migrate_v2_to_v3(conn)
    if old_version < 4:
        _migrate_v3_to_v4(conn)
    if old_version < 5:
        _migrate_v4_to_v5(conn)
    if old_version < 6:
        _migrate_v5_to_v6(conn)
    if old_version < 7:
        _migrate_v6_to_v7(conn)
    if old_version < 8:
        _migrate_v7_to_v8(conn)
    if old_version < 9:
        _migrate_v8_to_v9(conn)
    if old_version < 10:
        _migrate_v9_to_v10(conn)
    if _needs_v11_rotation_migration(conn, old_version):
        _migrate_v10_to_v11(conn)
    if old_version < 12:
        _migrate_v11_to_v12(conn)
    if old_version < 13:
        _migrate_v12_to_v13(conn)
    if old_version < 14:
        _migrate_v13_to_v14(conn)

    conn.execute(
        "INSERT OR REPLACE INTO schema_meta VALUES ('version', ?)",
        [str(SCHEMA_VERSION)],
    )
    conn.commit()
    logger.info("DuckDB schema initialized at %s (v%d)", db_path, SCHEMA_VERSION)
    return conn


def get_connection(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    """Open an existing DuckDB database."""
    # Ensure schema is up-to-date for existing DBs (migrations, new tables).
    # This is safe to call repeatedly and prevents missing-table crashes.
    return init_schema(db_path)
