from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from llm_quant.brain.models import Action, Conviction, TradeSignal
from llm_quant.broker.intraday_orders import (
    IntradayOrderState,
    reconcile_orders,
)
from llm_quant.trading.runtime_controls import (
    apply_entry_halt_freeze,
    apply_harvest_governance_controls,
    apply_short_rollout_halt_freeze,
    load_latest_harvest_governance_result,
    log_harvest_governance_action,
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

    row = tmp_db.execute("""
        SELECT pod_id, context_json
        FROM decision_contexts
        ORDER BY timestamp DESC
        LIMIT 1
        """).fetchone()
    assert row is not None
    assert row[0] == "default"
    assert "allocation_shrink" in row[1]
    assert "capture_ratio" in row[1]


def test_apply_short_rollout_halt_freeze_blocks_entry_risk_actions() -> None:
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
            action=Action.SHORT,
            conviction=Conviction.MEDIUM,
            target_weight=0.10,
            stop_loss=410.0,
            reasoning="short",
        ),
        TradeSignal(
            symbol="IWM",
            action=Action.SELL,
            conviction=Conviction.MEDIUM,
            target_weight=0.05,
            stop_loss=0.0,
            reasoning="de-risk",
        ),
        TradeSignal(
            symbol="GLD",
            action=Action.COVER,
            conviction=Conviction.MEDIUM,
            target_weight=0.0,
            stop_loss=0.0,
            reasoning="de-risk",
        ),
    ]

    kept, blocked = apply_short_rollout_halt_freeze(
        signals,
        halt_detectors={"short_rollout"},
    )

    assert [signal.action for signal in kept] == [Action.SELL, Action.COVER]
    assert len(blocked) == 2
    assert {item["action"] for item in blocked} == {"buy", "short"}


def test_apply_short_rollout_halt_freeze_noop_without_short_rollout_halt() -> None:
    signals = [
        TradeSignal(
            symbol="SPY",
            action=Action.BUY,
            conviction=Conviction.MEDIUM,
            target_weight=0.20,
            stop_loss=95.0,
            reasoning="buy",
        )
    ]

    kept, blocked = apply_short_rollout_halt_freeze(
        signals,
        halt_detectors={"data_quality"},
    )

    assert len(kept) == 1
    assert kept[0].action == Action.BUY
    assert blocked == []


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
        match=r"Cannot compute replacement stop for BTCUSD: trailing_pct=0\.0, hwm=0\.0",
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

    row = tmp_db.execute("""
        SELECT pod_id, context_json
        FROM decision_contexts
        ORDER BY timestamp DESC
        LIMIT 1
        """).fetchone()
    assert row is not None
    assert row[0] == "crypto"
    assert '"active_mandate_name": "crypto"' in row[1]
    assert '"active_mandate_type": "crypto_synthetic_harvest"' in row[1]


# ---------------------------------------------------------------------------
# apply_entry_halt_freeze — new policy-driven tests
# ---------------------------------------------------------------------------


def _make_entry_signals() -> list[TradeSignal]:
    return [
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
            action=Action.SHORT,
            conviction=Conviction.MEDIUM,
            target_weight=0.10,
            stop_loss=410.0,
            reasoning="short",
        ),
        TradeSignal(
            symbol="IWM",
            action=Action.SELL,
            conviction=Conviction.MEDIUM,
            target_weight=0.05,
            stop_loss=0.0,
            reasoning="de-risk",
        ),
        TradeSignal(
            symbol="GLD",
            action=Action.COVER,
            conviction=Conviction.MEDIUM,
            target_weight=0.0,
            stop_loss=0.0,
            reasoning="cover short",
        ),
    ]


def test_apply_entry_halt_freeze_any_halt_mode_blocks_on_any_halt() -> None:
    """any_halt mode: any detector in halt_detectors triggers entry freeze."""
    kept, blocked = apply_entry_halt_freeze(
        _make_entry_signals(),
        halt_detectors={"regime_drift"},
        entry_freeze_mode="any_halt",
    )

    assert [s.action for s in kept] == [Action.SELL, Action.COVER]
    assert len(blocked) == 2
    assert {item["action"] for item in blocked} == {"buy", "short"}
    assert all(item["reason"] == "entry_halt_freeze" for item in blocked)


def test_apply_entry_halt_freeze_any_halt_mode_noop_when_no_halts() -> None:
    """any_halt mode: empty halt set means no freeze."""
    kept, blocked = apply_entry_halt_freeze(
        _make_entry_signals(),
        halt_detectors=set(),
        entry_freeze_mode="any_halt",
    )

    assert len(kept) == 4
    assert blocked == []


def test_apply_entry_halt_freeze_specific_detectors_mode() -> None:
    """specific_detectors mode: only listed detectors trigger the freeze."""
    # Matching detector in halt set → freeze
    kept, blocked = apply_entry_halt_freeze(
        _make_entry_signals(),
        halt_detectors={"alpha_decay"},
        entry_freeze_mode="specific_detectors",
        entry_freeze_detectors=["alpha_decay", "risk_drift"],
    )
    assert [s.action for s in kept] == [Action.SELL, Action.COVER]
    assert len(blocked) == 2

    # Non-matching detector → no freeze
    kept2, blocked2 = apply_entry_halt_freeze(
        _make_entry_signals(),
        halt_detectors={"regime_drift"},
        entry_freeze_mode="specific_detectors",
        entry_freeze_detectors=["alpha_decay", "risk_drift"],
    )
    assert len(kept2) == 4
    assert blocked2 == []


def test_apply_entry_halt_freeze_short_rollout_only_mode_unaffected_by_other_halts() -> (
    None
):
    """Default mode: regime_drift halt alone does NOT freeze entries."""
    kept, blocked = apply_entry_halt_freeze(
        _make_entry_signals(),
        halt_detectors={"regime_drift"},
        entry_freeze_mode="short_rollout_only",
    )

    assert len(kept) == 4
    assert blocked == []


def test_apply_short_rollout_halt_freeze_alias_still_works() -> None:
    """Backward-compat: old function name delegates to apply_entry_halt_freeze."""
    kept, blocked = apply_short_rollout_halt_freeze(
        _make_entry_signals(),
        halt_detectors={"short_rollout"},
    )
    assert [s.action for s in kept] == [Action.SELL, Action.COVER]
    assert len(blocked) == 2
    assert all(item["reason"] == "entry_halt_freeze" for item in blocked)


@pytest.mark.parametrize(
    ("entry_freeze_mode", "halt_detectors", "entry_freeze_detectors"),
    [
        ("short_rollout_only", {"short_rollout"}, None),
        ("any_halt", {"regime_drift"}, None),
        ("specific_detectors", {"risk_drift"}, ["risk_drift"]),
    ],
)
def test_apply_entry_halt_freeze_cover_always_allowed_in_all_modes(
    entry_freeze_mode: str,
    halt_detectors: set[str],
    entry_freeze_detectors: list[str] | None,
) -> None:
    kept, blocked = apply_entry_halt_freeze(
        _make_entry_signals(),
        halt_detectors=halt_detectors,
        entry_freeze_mode=entry_freeze_mode,
        entry_freeze_detectors=entry_freeze_detectors,
    )

    kept_actions = [signal.action for signal in kept]
    assert Action.COVER in kept_actions
    assert Action.SELL in kept_actions
    assert {item["action"] for item in blocked} == {"buy", "short"}


# ---------------------------------------------------------------------------
# Standalone named tests for COVER / SHORT halt invariants
# (complement the parametrize test above with clearly-named regression guards)
# ---------------------------------------------------------------------------

def test_cover_signal_always_passes_during_any_halt_freeze() -> None:
    """COVER is never blocked by entry_halt_freeze regardless of halt detector.

    Regression guard: COVER must remain in the 'kept' list even when every
    other entry action (BUY/SHORT) is frozen.  A naive implementation that
    filters by *omission* would pass COVER only incidentally; an explicit
    allow-list ensures it.
    """
    signals = _make_entry_signals()
    kept, blocked = apply_entry_halt_freeze(
        signals,
        halt_detectors={"regime_drift"},
        entry_freeze_mode="any_halt",
    )
    kept_actions = [s.action for s in kept]
    assert Action.COVER in kept_actions, (
        "COVER must always pass through an entry halt freeze."
    )
    # Sanity: the freeze DID fire (BUY blocked)
    blocked_actions = {item["action"] for item in blocked}
    assert "buy" in blocked_actions


def test_short_signal_is_blocked_during_any_halt_freeze() -> None:
    """SHORT is always blocked by entry_halt_freeze in any_halt mode.

    Regression guard: SHORT is an entry action and must be caught by the
    explicit ENTRY_HALT_BLOCKED_ACTIONS set, not accidentally allowed because
    a new signal type bypassed an omission-based filter.
    """
    signals = _make_entry_signals()
    kept, blocked = apply_entry_halt_freeze(
        signals,
        halt_detectors={"drawdown_breach"},
        entry_freeze_mode="any_halt",
    )
    blocked_actions = {item["action"] for item in blocked}
    assert "short" in blocked_actions, (
        "SHORT must be blocked by entry halt freeze."
    )
    # Sanity: COVER was NOT blocked
    kept_actions = [s.action for s in kept]
    assert Action.COVER in kept_actions
