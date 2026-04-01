from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from llm_quant.brain.models import Action, Conviction, TradeSignal
from llm_quant.trading.runtime_controls import (
    apply_harvest_governance_controls,
    load_latest_harvest_governance_result,
    log_harvest_governance_action,
)
from llm_quant.broker.intraday_orders import (
    IntradayOrderState,
    reconcile_orders,
)


def test_load_latest_harvest_governance_result_returns_defaults_when_missing(tmp_db):
    result = load_latest_harvest_governance_result(tmp_db)
    assert result.allocation_scale == 1.0
    assert result.force_flatten is False
    assert result.actions == []
    assert result.breached_rules == []


def test_load_latest_harvest_governance_result_parses_actions(tmp_db):
    payload = [
        {
            "detector": "harvest_governance",
            "severity": "halt",
            "message": "breached",
            "metric_name": "harvest_governance_breach_count",
            "current_value": 2.0,
            "threshold_value": 0.0,
            "details": {
                "breached_metrics": [{"metric": "capture_ratio"}],
                "observed_metrics": {"capture_ratio": 0.2},
                "recommended_actions": [
                    {"action": "allocation_shrink", "scale": 0.4},
                    {"action": "apply_conservative_mandate", "mandate_name": "default"},
                    {"action": "temporary_eod_flatten", "enabled": True},
                    {"action": "demote_strategy", "enabled": True},
                ],
            },
        }
    ]
    tmp_db.execute(
        """
        INSERT INTO surveillance_scans (
            scan_timestamp, overall_severity, total_checks,
            halt_count, warning_count, checks_json
        ) VALUES (?, 'halt', 1, 1, 0, ?)
        """,
        [datetime.now(tz=UTC), json.dumps(payload)],
    )
    tmp_db.commit()

    result = load_latest_harvest_governance_result(tmp_db)
    assert result.allocation_scale == 0.4
    assert result.force_flatten is True
    assert result.conservative_mandate_name == "default"
    assert result.lifecycle_recommendation == "demote_strategy"
    assert result.metrics["capture_ratio"] == 0.2


def test_apply_harvest_governance_controls_scales_only_buys(tmp_db):
    payload = [
        {
            "detector": "harvest_governance",
            "severity": "halt",
            "details": {
                "breached_metrics": [{"metric": "capture_ratio"}],
                "observed_metrics": {},
                "recommended_actions": [
                    {"action": "allocation_shrink", "scale": 0.5},
                ],
            },
        }
    ]
    tmp_db.execute(
        """
        INSERT INTO surveillance_scans (
            scan_timestamp, overall_severity, total_checks,
            halt_count, warning_count, checks_json
        ) VALUES (?, 'halt', 1, 1, 0, ?)
        """,
        [datetime.now(tz=UTC), json.dumps(payload)],
    )
    tmp_db.commit()

    result = load_latest_harvest_governance_result(tmp_db)
    signals = [
        TradeSignal(
            symbol="SPY",
            action=Action.BUY,
            conviction=Conviction.MEDIUM,
            target_weight=0.20,
            stop_loss=95.0,
            reasoning="buy",
        ),
        TradeSignal(
            symbol="QQQ",
            action=Action.SELL,
            conviction=Conviction.MEDIUM,
            target_weight=0.10,
            stop_loss=90.0,
            reasoning="sell",
        ),
    ]

    adjusted = apply_harvest_governance_controls(signals, result)
    assert adjusted[0].target_weight == 0.10
    assert adjusted[1].target_weight == 0.10
    assert adjusted[1].action == Action.SELL


def test_apply_harvest_governance_controls_adds_close_signals_for_flatten(tmp_db):
    payload = [
        {
            "detector": "harvest_governance",
            "severity": "halt",
            "details": {
                "breached_metrics": [{"metric": "giveback_ratio"}],
                "observed_metrics": {},
                "recommended_actions": [
                    {"action": "temporary_eod_flatten", "enabled": True},
                ],
            },
        }
    ]
    tmp_db.execute(
        """
        INSERT INTO surveillance_scans (
            scan_timestamp, overall_severity, total_checks,
            halt_count, warning_count, checks_json
        ) VALUES (?, 'halt', 1, 1, 0, ?)
        """,
        [datetime.now(tz=UTC), json.dumps(payload)],
    )
    tmp_db.commit()

    result = load_latest_harvest_governance_result(tmp_db)
    adjusted = apply_harvest_governance_controls(
        [],
        result,
        portfolio_symbols={"SPY", "QQQ"},
    )
    assert len(adjusted) == 2
    assert all(signal.action == Action.CLOSE for signal in adjusted)
    assert {signal.symbol for signal in adjusted} == {"SPY", "QQQ"}


def test_log_harvest_governance_action_persists_payload(tmp_db):
    payload = [
        {
            "detector": "harvest_governance",
            "severity": "halt",
            "details": {
                "breached_metrics": [{"metric": "capture_ratio"}],
                "observed_metrics": {"capture_ratio": 0.2},
                "recommended_actions": [
                    {"action": "allocation_shrink", "scale": 0.4},
                ],
            },
        }
    ]
    tmp_db.execute(
        """
        INSERT INTO surveillance_scans (
            scan_timestamp, overall_severity, total_checks,
            halt_count, warning_count, checks_json
        ) VALUES (?, 'halt', 1, 1, 0, ?)
        """,
        [datetime.now(tz=UTC), json.dumps(payload)],
    )
    tmp_db.commit()

    result = load_latest_harvest_governance_result(tmp_db)
    log_harvest_governance_action(tmp_db, pod_id="default", runtime_result=result)

    row = tmp_db.execute(
        """
        SELECT pod_id, context_json
        FROM decision_contexts
        ORDER BY timestamp DESC
        LIMIT 1
        """
    ).fetchone()
    assert row is not None
    assert row[0] == "default"
    assert "allocation_shrink" in row[1]
    assert "capture_ratio" in row[1]


def test_reconcile_orders_raises_when_protective_stop_cannot_be_restored():
    class StubClient:
        def get_order(self, order_id):
            return {"status": "filled" if order_id == "tp-leg" else "new"}

        def cancel_order(self, order_id):
            return None

        def submit_stop_order(self, **kwargs):
            raise AssertionError("submit_stop_order should not be called")

    state = IntradayOrderState(
        symbol="BTCUSD",
        oco_order_id="oco-parent",
        oco_tp_order_id="tp-leg",
        oco_stop_order_id="stop-leg",
        hwm=0.0,
        remaining_qty=1.5,
    )

    with pytest.raises(
        Exception,
        match="Cannot compute replacement stop for BTCUSD: trailing_pct=0.0, hwm=0.0",
    ):
        reconcile_orders(
            StubClient(),
            {"BTCUSD": state},
            {"BTCUSD": 0.5},
            trailing_pct=0.0,
            fail_on_unprotected=True,
        )


def test_log_harvest_governance_action_includes_active_mandate_context(tmp_db):
    payload = [
        {
            "detector": "harvest_governance",
            "severity": "halt",
            "details": {
                "breached_metrics": [{"metric": "capture_ratio"}],
                "observed_metrics": {"capture_ratio": 0.2},
                "recommended_actions": [
                    {"action": "allocation_shrink", "scale": 0.4},
                    {"action": "apply_conservative_mandate", "mandate_name": "default"},
                ],
            },
        }
    ]
    tmp_db.execute(
        """
        INSERT INTO surveillance_scans (
            scan_timestamp, overall_severity, total_checks,
            halt_count, warning_count, checks_json
        ) VALUES (?, 'halt', 1, 1, 0, ?)
        """,
        [datetime.now(tz=UTC), json.dumps(payload)],
    )
    tmp_db.commit()

    result = load_latest_harvest_governance_result(tmp_db)
    result.active_mandate_name = "crypto"
    result.active_mandate_type = "crypto_synthetic_harvest"

    log_harvest_governance_action(tmp_db, pod_id="crypto", runtime_result=result)

    row = tmp_db.execute(
        """
        SELECT pod_id, context_json
        FROM decision_contexts
        ORDER BY timestamp DESC
        LIMIT 1
        """
    ).fetchone()
    assert row is not None
    assert row[0] == "crypto"
    assert '"active_mandate_name": "crypto"' in row[1]
    assert '"active_mandate_type": "crypto_synthetic_harvest"' in row[1]
