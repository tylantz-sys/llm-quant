from datetime import date, datetime

from llm_quant.trading.harvest_metrics import (
    compute_harvest_metrics,
    compute_harvest_metrics_from_db,
)


def _insert_profit_take_event(tmp_db, **overrides):
    event = {
        "timestamp": datetime(2026, 3, 31, 10, 0, 0),
        "pod_id": "default",
        "symbol": "SPY",
        "event_type": "executed",
        "decision_source": "llm",
        "sleeve": "promoted_default",
        "source_decision_id": 10,
        "decision_id": 11,
        "trade_id": 12,
        "entry_batch": 1,
        "reduction_sequence": 1,
        "position_fraction": 0.5,
        "action": "sell",
        "shares": 5.0,
        "price": 505.0,
        "notional": 2525.0,
        "trigger_price": 507.0,
        "peak_price": 510.0,
        "drawdown_pct": 0.01,
        "pre_reduction_peak_unrealized_pnl": 100.0,
        "pre_reduction_peak_return_pct": 0.10,
        "trailing_stop_activated_at": None,
        "peak_to_reduction_drawdown_pct": 0.02,
        "realized_pnl": 60.0,
        "return_pct": 0.06,
        "rule_name": "tp_rule",
        "reason": "take_profit_partial",
        "metadata_json": "{}",
    }
    event.update(overrides)
    columns = list(event.keys())
    placeholders = ", ".join(["?"] * len(columns))
    tmp_db.execute(
        f"INSERT INTO profit_take_events ({', '.join(columns)}) VALUES ({placeholders})",
        [event[column] for column in columns],
    )


def test_compute_harvest_metrics_aggregates_representative_profit_take_rows():
    events = [
        {
            "event_type": "executed",
            "symbol": "SPY",
            "reduction_sequence": 1,
            "position_fraction": 0.5,
            "pre_reduction_peak_unrealized_pnl": 100.0,
            "realized_pnl": 60.0,
            "pre_reduction_peak_return_pct": 0.10,
            "return_pct": 0.06,
            "trailing_stop_activated_at": None,
            "peak_to_reduction_drawdown_pct": 0.02,
            "reason": "take_profit_partial",
        },
        {
            "event_type": "executed",
            "symbol": "QQQ",
            "reduction_sequence": 2,
            "position_fraction": 0.25,
            "pre_reduction_peak_unrealized_pnl": 80.0,
            "realized_pnl": 20.0,
            "pre_reduction_peak_return_pct": 0.08,
            "return_pct": 0.02,
            "trailing_stop_activated_at": datetime(2026, 3, 31, 14, 30, 0),
            "peak_to_reduction_drawdown_pct": 0.05,
            "reason": "trailing_stop",
        },
        {
            "event_type": "planned",
            "symbol": "IWM",
            "reduction_sequence": 1,
            "position_fraction": 0.5,
            "pre_reduction_peak_unrealized_pnl": 50.0,
            "realized_pnl": 0.0,
            "pre_reduction_peak_return_pct": 0.05,
            "return_pct": 0.0,
            "trailing_stop_activated_at": None,
            "peak_to_reduction_drawdown_pct": 0.01,
            "reason": "take_profit_partial",
        },
    ]

    metrics = compute_harvest_metrics(events)

    assert metrics["profit_take_events"] == 3
    assert metrics["executed_profit_take_events"] == 2
    assert metrics["symbols_harvested"] == 2
    assert metrics["realized_harvest_pnl"] == 80.0
    assert metrics["peak_unrealized_pnl_reference"] == 180.0
    assert metrics["capture_ratio"] == 80.0 / 180.0
    assert metrics["capture_ratio_return_pct"] == 0.08 / 0.18
    assert metrics["giveback_ratio"] == 100.0 / 180.0
    assert metrics["giveback_ratio_return_pct"] == 0.10 / 0.18
    assert metrics["tp1_effectiveness"] == 0.6
    assert metrics["tp1_effectiveness_return_pct"] == 0.6
    assert metrics["runner_retention_proxy"] == 0.5
    assert metrics["trailing_salvage_proxy"] == 0.25
    assert metrics["trailing_salvage_proxy_return_pct"] == 0.25
    assert metrics["realized_to_peak_ratio"] == metrics["capture_ratio"]
    assert metrics["realized_to_peak_ratio_return_pct"] == metrics["capture_ratio_return_pct"]
    assert metrics["avg_peak_to_reduction_drawdown_pct"] == 0.035
    assert metrics["exit_reason_breakdown"] == {
        "take_profit_partial": 1,
        "trailing_stop": 1,
    }


def test_compute_harvest_metrics_handles_no_events():
    metrics = compute_harvest_metrics([])

    assert metrics["profit_take_events"] == 0
    assert metrics["executed_profit_take_events"] == 0
    assert metrics["symbols_harvested"] == 0
    assert metrics["realized_harvest_pnl"] == 0.0
    assert metrics["peak_unrealized_pnl_reference"] == 0.0
    assert metrics["capture_ratio"] is None
    assert metrics["giveback_ratio"] is None
    assert metrics["tp1_effectiveness"] is None
    assert metrics["runner_retention_proxy"] is None
    assert metrics["trailing_salvage_proxy"] is None
    assert metrics["avg_peak_to_reduction_drawdown_pct"] is None
    assert metrics["exit_reason_breakdown"] == {}


def test_compute_harvest_metrics_from_db_filters_by_date_bounds(tmp_db):
    _insert_profit_take_event(
        tmp_db,
        timestamp=datetime(2026, 3, 30, 15, 0, 0),
        symbol="SPY",
        pre_reduction_peak_unrealized_pnl=120.0,
        realized_pnl=72.0,
        pre_reduction_peak_return_pct=0.12,
        return_pct=0.072,
        reason="take_profit_partial",
        trailing_stop_activated_at=None,
    )
    _insert_profit_take_event(
        tmp_db,
        timestamp=datetime(2026, 3, 31, 15, 0, 0),
        symbol="QQQ",
        reduction_sequence=2,
        position_fraction=0.25,
        pre_reduction_peak_unrealized_pnl=80.0,
        realized_pnl=20.0,
        pre_reduction_peak_return_pct=0.08,
        return_pct=0.02,
        reason="trailing_stop",
        trailing_stop_activated_at=datetime(2026, 3, 31, 14, 30, 0),
        peak_to_reduction_drawdown_pct=0.05,
    )

    metrics = compute_harvest_metrics_from_db(
        tmp_db,
        start=date(2026, 3, 31),
        end=date(2026, 3, 31),
    )

    assert metrics["profit_take_events"] == 1
    assert metrics["executed_profit_take_events"] == 1
    assert metrics["symbols_harvested"] == 1
    assert metrics["realized_harvest_pnl"] == 20.0
    assert metrics["capture_ratio"] == 0.25
    assert metrics["exit_reason_breakdown"] == {"trailing_stop": 1}
    assert len(metrics["events"]) == 1
    assert metrics["events"][0]["symbol"] == "QQQ"