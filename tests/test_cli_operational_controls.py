from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb

from llm_quant import cli
from llm_quant.db.schema import init_schema
from llm_quant.trading.portfolio import Portfolio, Position


class _FakeConfig:
    def __init__(self, db_path: Path, *, intraday_enabled: bool = True, strategy_overlay: bool = True):
        self.general = SimpleNamespace(
            db_path=str(db_path),
            initial_capital=1000.0,
        )
        self.execution = SimpleNamespace(
            signal_source="strategy_overlay" if strategy_overlay else "llm",
            claude_overlay_only=False,
            overlay_auth_required=False,
            strategy_set="candidate_crypto",
            intraday_enabled=intraday_enabled,
            intraday_timeframe_minutes=5,
            asset_class_filter=["crypto"],
            intraday_use_oco=False,
            intraday_rth_guard=False,
            skip_daily_fetch_when_intraday=True,
            log_decisions_when_rth_closed=False,
            crypto_symbol_map={},
            intraday_lookback_days=10,
            overlay_governor_strict=True,
            overlay_max_upscale=1.0,
            overlay_max_downscale=0.0,
            scale_in_tranches=1,
            reentry_cooldown_bars=0,
            expectancy_gate_enabled=False,
            expectancy_lookback_closed_trades=10,
            expectancy_negative_scale=1.0,
            trailing_stop_pct=0.0,
            profit_take_partial_pct=0.0,
            profit_take_partial_size=0.0,
            profit_take_remainder_tp_mult=0.0,
        )
        self.risk = SimpleNamespace(
            take_profit_mode="pct",
            take_profit_pct=0.03,
            take_profit_rr=2.0,
            partial_take_profit_enabled=False,
            partial_take_profit_pct=0.0,
            partial_take_profit_size=0.0,
            remainder_take_profit_mult=0.0,
            trailing_stop_enabled=False,
            trailing_stop_pct=0.0,
            eod_flatten_enabled=True,
            eod_flatten_time="15:55",
            fail_on_unprotected_exits=True,
            max_position_weight=1.0,
            min_cash_reserve=0.0,
            default_stop_loss_pct=0.05,
        )
        self.universe = SimpleNamespace(assets=[])
        self.data = SimpleNamespace(
            db_lock_timeout_seconds=0.01,
            db_lock_retry_seconds=0.01,
            db_upsert_max_retries=0,
            db_upsert_retry_seconds=0.01,
            db_upsert_timeout_seconds=0.01,
            fetch_timeout=1.0,
            lookback_days=10,
        )
        self.strategy_rotation = SimpleNamespace(
            enabled=False,
            window_days=0,
            top_n=0,
            min_trades=0,
            cooldown_days=0,
        )
        self.allocation = SimpleNamespace(
            regime_weight_mult={},
            strategy_group_caps={},
        )


def test_run_single_pod_logs_skip_context_for_already_executed_slot(
    monkeypatch: Any, tmp_path: Path
) -> None:
    db_path = tmp_path / "test.duckdb"
    conn = init_schema(db_path)
    conn.close()

    config = _FakeConfig(db_path)

    monkeypatch.setattr(cli, "_get_config_for_pod", lambda pod_id="default": config)
    monkeypatch.setattr(cli, "_get_db_path", lambda cfg=None: db_path)

    import llm_quant.trading.run_lock as run_lock_module

    slot = run_lock_module.slot_for_time(datetime.now(tz=UTC), 5)
    monkeypatch.setattr(
        run_lock_module,
        "acquire_run_lock",
        lambda pod_id, run_slot, lock_dir=None: None
        if pod_id == "crypto-ethbtc-paper" and run_slot == slot
        else run_lock_module.acquire_run_lock(pod_id, run_slot, lock_dir),
    )
    cli._run_single_pod("crypto-ethbtc-paper", dry_run=False, broker="paper")

    check = duckdb.connect(str(db_path), read_only=True)
    row = check.execute(
        """
        SELECT timestamp, context_json
        FROM intraday_context_snapshots
        WHERE pod_id = ?
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        ["crypto-ethbtc-paper"],
    ).fetchone()
    check.close()

    assert row is not None
    assert "already_executed" in row[1]
    assert '"skip_status": "skipped"' in row[1]


def test_eod_flat_crypto_runtime_returns_before_clock_lookup(
    monkeypatch: Any, tmp_path: Path
) -> None:
    db_path = tmp_path / "test.duckdb"
    config = _FakeConfig(db_path)

    monkeypatch.setattr(cli, "_get_config_for_pod", lambda pod_id="default": config)
    monkeypatch.setattr(cli, "_get_db_path", lambda cfg=None: db_path)

    import llm_quant.broker.alpaca as alpaca_module

    def _boom_from_env() -> None:
        raise AssertionError("Alpaca clock path should not be touched for crypto EOD flatten")

    monkeypatch.setattr(alpaca_module.AlpacaClient, "from_env", staticmethod(_boom_from_env))

    cli.eod_flat(pod="crypto-ethbtc-paper")


def test_rollback_rejected_entry_order_unwinds_long_ghost_position() -> None:
    portfolio = Portfolio(initial_capital=1_000.0)
    portfolio.cash = 800.0
    portfolio.positions["SPY"] = Position(
        symbol="SPY",
        shares=2,
        avg_cost=100.0,
        current_price=100.0,
    )
    order = SimpleNamespace(
        intent_type="entry",
        symbol="SPY",
        status="rejected",
        notional=None,
    )

    rolled_back = cli._rollback_rejected_entry_order(portfolio, order)

    assert rolled_back is True
    assert portfolio.cash == 1_000.0
    assert "SPY" not in portfolio.positions


def test_rollback_rejected_entry_order_unwinds_short_ghost_position() -> None:
    portfolio = Portfolio(initial_capital=1_000.0)
    portfolio.cash = 1_200.0
    portfolio.positions["SPY"] = Position(
        symbol="SPY",
        shares=-2,
        avg_cost=100.0,
        current_price=100.0,
    )
    order = SimpleNamespace(
        intent_type="entry_short",
        symbol="SPY",
        status="rejected",
        notional=None,
    )

    rolled_back = cli._rollback_rejected_entry_order(portfolio, order)

    assert rolled_back is True
    assert portfolio.cash == 1_000.0
    assert "SPY" not in portfolio.positions
