from datetime import date, datetime

from scripts.generate_report import (
    _get_trades_for_date,
    _semantic_action_label,
    generate_daily_report,
    generate_monthly_report,
    generate_weekly_report,
)


def _insert_snapshot(tmp_db, *, snapshot_id: int, snapshot_date: date, nav: float):
    tmp_db.execute(
        """
        INSERT INTO portfolio_snapshots (
            snapshot_id,
            date,
            pod_id,
            nav,
            cash,
            gross_exposure,
            net_exposure,
            total_pnl,
            daily_pnl
        )
        VALUES (?, ?, 'default', ?, ?, ?, ?, ?, ?)
        """,
        [
            snapshot_id,
            snapshot_date,
            nav,
            nav * 0.4,
            nav * 0.6,
            nav * 0.6,
            nav - 100000.0,
            100.0,
        ],
    )


def _insert_decision(tmp_db, *, decision_id: int, decision_date: date):
    tmp_db.execute(
        """
        INSERT INTO llm_decisions (
            decision_id,
            date,
            pod_id,
            decision_type,
            model,
            num_signals,
            market_regime,
            regime_confidence,
            created_at
        )
        VALUES (?, ?, 'default', 'llm', 'gpt-test', 2, 'bull', 0.8, ?)
        """,
        [decision_id, decision_date, datetime(2026, 3, 31, 9, 0, 0)],
    )


def _insert_profit_take_event(tmp_db, *, event_time: datetime, reason: str):
    tmp_db.execute(
        """
        INSERT INTO profit_take_events (
            timestamp,
            pod_id,
            symbol,
            event_type,
            decision_source,
            sleeve,
            source_decision_id,
            decision_id,
            trade_id,
            entry_batch,
            reduction_sequence,
            position_fraction,
            action,
            shares,
            price,
            notional,
            trigger_price,
            peak_price,
            drawdown_pct,
            pre_reduction_peak_unrealized_pnl,
            pre_reduction_peak_return_pct,
            trailing_stop_activated_at,
            peak_to_reduction_drawdown_pct,
            realized_pnl,
            return_pct,
            rule_name,
            reason,
            metadata_json
        )
        VALUES (?, 'default', 'SPY', 'executed', 'llm', 'promoted_default', 1, 1, 1, 1, 1, 0.5, 'sell', 5.0, 505.0, 2525.0, 507.0, 510.0, 0.01, 100.0, 0.10, ?, 0.02, 60.0, 0.06, 'tp_rule', ?, '{}')
        """,
        [
            event_time,
            event_time if reason == "trailing_stop" else None,
            reason,
        ],
    )


def _insert_trade(
    tmp_db,
    *,
    trade_id: int,
    trade_date: date,
    symbol: str,
    action: str,
    semantic_action: str | None,
    broker_side: str | None,
    intent_type: str | None,
):
    tmp_db.execute(
        """
        INSERT INTO trades (
            trade_id,
            date,
            pod_id,
            symbol,
            action,
            semantic_action,
            broker_side,
            intent_type,
            shares,
            price,
            notional,
            conviction,
            reasoning,
            llm_decision_id,
            prev_hash,
            row_hash
        )
        VALUES (?, ?, 'default', ?, ?, ?, ?, ?, 1.0, 100.0, 100.0, 'medium', 'test', 1, 'prev', 'row')
        """,
        [
            trade_id,
            trade_date,
            symbol,
            action,
            semantic_action,
            broker_side,
            intent_type,
        ],
    )


def test_generate_reports_include_harvest_metrics_section_when_telemetry_exists(tmp_db):
    report_date = date(2026, 3, 31)
    _insert_snapshot(tmp_db, snapshot_id=1, snapshot_date=report_date, nav=101000.0)
    _insert_snapshot(
        tmp_db, snapshot_id=2, snapshot_date=date(2026, 3, 30), nav=100500.0
    )
    _insert_snapshot(
        tmp_db, snapshot_id=3, snapshot_date=date(2026, 3, 1), nav=100000.0
    )
    _insert_decision(tmp_db, decision_id=1, decision_date=report_date)
    _insert_profit_take_event(
        tmp_db,
        event_time=datetime(2026, 3, 31, 15, 0, 0),
        reason="take_profit_partial",
    )

    daily_report = generate_daily_report(tmp_db, report_date, 100000.0)
    weekly_report = generate_weekly_report(tmp_db, report_date, 100000.0)
    monthly_report = generate_monthly_report(tmp_db, report_date, 100000.0)

    for report in (daily_report, weekly_report, monthly_report):
        assert "## Harvest Metrics" in report
        assert "| Executed Harvest Events | 1 |" in report
        assert "| take_profit_partial | 1 |" in report


def test_trade_reader_renders_short_lifecycle_actions(tmp_db):
    report_date = date(2026, 3, 31)
    _insert_trade(
        tmp_db,
        trade_id=1,
        trade_date=report_date,
        symbol="SPY",
        action="sell",
        semantic_action="short_entry",
        broker_side="sell_short",
        intent_type="entry",
    )
    _insert_trade(
        tmp_db,
        trade_id=2,
        trade_date=report_date,
        symbol="SPY",
        action="buy",
        semantic_action="short_cover",
        broker_side="buy_to_cover",
        intent_type="cover",
    )

    trades = _get_trades_for_date(tmp_db, report_date)

    labels = [_semantic_action_label(trade) for trade in trades]
    assert labels == ["SHORT_ENTRY", "SHORT_COVER"]
    assert trades[0]["broker_side"] == "sell_short"
    assert trades[1]["broker_side"] == "buy_to_cover"
