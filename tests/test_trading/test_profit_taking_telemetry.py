from datetime import date

from llm_quant.trading.executor import ExecutedTrade
from llm_quant.trading.ledger import log_trades
from llm_quant.trading.telemetry import (
    is_profit_take_reason,
    log_profit_take_event,
    normalize_profit_take_reason,
)


def test_log_trades_persists_profit_take_attribution_fields(tmp_db):
    trade = ExecutedTrade(
        symbol="SPY",
        action="sell",
        shares=2.0,
        price=505.0,
        notional=1010.0,
        conviction="medium",
        reasoning="Take partial profits",
        strategy_id="trend_v1",
        entry_batch=3,
        exit_reason="tp_partial",
    )

    trade_ids = log_trades(
        tmp_db,
        [trade],
        date(2026, 3, 31),
        decision_id=42,
        pod_id="default",
        decision_source="strategy_overlay",
        sleeve="promoted_default",
        source_decision_id=41,
    )

    row = tmp_db.execute(
        """
        SELECT
            llm_decision_id,
            source_decision_id,
            decision_source,
            sleeve,
            is_profit_take,
            profit_take_reason
        FROM trades
        WHERE trade_id = ?
        """,
        [trade_ids[0]],
    ).fetchone()

    assert row is not None
    assert row[0] == 42
    assert row[1] == 41
    assert row[2] == "strategy_overlay"
    assert row[3] == "promoted_default"
    assert row[4] is True
    assert row[5] == "take_profit_partial"


def test_log_trades_uses_decision_id_as_source_decision_id_fallback(tmp_db):
    trade = ExecutedTrade(
        symbol="QQQ",
        action="sell",
        shares=1.0,
        price=450.0,
        notional=450.0,
        conviction="low",
        reasoning="Trim",
        strategy_id=None,
        entry_batch=1,
        exit_reason="trailing_stop",
    )

    trade_ids = log_trades(
        tmp_db,
        [trade],
        date(2026, 3, 31),
        decision_id=77,
        pod_id="default",
        decision_source="llm",
        sleeve=None,
        source_decision_id=None,
    )

    row = tmp_db.execute(
        """
        SELECT source_decision_id, is_profit_take, profit_take_reason
        FROM trades
        WHERE trade_id = ?
        """,
        [trade_ids[0]],
    ).fetchone()

    assert row is not None
    assert row[0] == 77
    assert row[1] is True
    assert row[2] == "trailing_stop"


def test_normalize_profit_take_reason_maps_legacy_values():
    assert normalize_profit_take_reason("tp_partial") == "take_profit_partial"
    assert normalize_profit_take_reason("trailing_stop") == "trailing_stop"
    assert normalize_profit_take_reason(None) is None


def test_is_profit_take_reason_uses_canonical_allowlist():
    assert is_profit_take_reason("tp_partial") is True
    assert is_profit_take_reason("take_profit_partial") is True
    assert is_profit_take_reason("trailing_stop") is True
    assert is_profit_take_reason("stop_loss") is False
    assert is_profit_take_reason(None) is False


def test_profit_take_event_can_link_to_logged_trade_and_decision(tmp_db):
    trade = ExecutedTrade(
        symbol="IWM",
        action="sell",
        shares=4.0,
        price=210.0,
        notional=840.0,
        conviction="medium",
        reasoning="Exit winner",
        strategy_id="mean_revert",
        entry_batch=5,
        exit_reason="tp_partial",
    )

    trade_ids = log_trades(
        tmp_db,
        [trade],
        date(2026, 3, 31),
        decision_id=100,
        pod_id="default",
        decision_source="llm",
        sleeve="sleeve_a",
        source_decision_id=100,
    )

    event_id = log_profit_take_event(
        tmp_db,
        pod_id="default",
        symbol="IWM",
        event_type="executed",
        decision_source="llm",
        sleeve="sleeve_a",
        source_decision_id=100,
        decision_id=100,
        trade_id=trade_ids[0],
        entry_batch=5,
        reduction_sequence=1,
        position_fraction=0.5,
        action="sell",
        shares=4.0,
        price=210.0,
        notional=840.0,
        peak_price=214.0,
        pre_reduction_peak_unrealized_pnl=120.0,
        pre_reduction_peak_return_pct=0.08,
        trailing_stop_activated_at=date(2026, 3, 31),
        peak_to_reduction_drawdown_pct=0.02,
        realized_pnl=80.0,
        return_pct=0.05,
        reason="tp_partial",
        metadata={"reasoning": "Exit winner"},
    )

    row = tmp_db.execute(
        """
        SELECT
            p.trade_id,
            p.decision_id,
            p.reason,
            p.reduction_sequence,
            p.position_fraction,
            p.pre_reduction_peak_unrealized_pnl,
            p.pre_reduction_peak_return_pct,
            p.peak_to_reduction_drawdown_pct,
            p.realized_pnl,
            p.return_pct,
            t.symbol,
            t.profit_take_reason
        FROM profit_take_events p
        JOIN trades t ON t.trade_id = p.trade_id
        WHERE p.event_id = ?
        """,
        [event_id],
    ).fetchone()

    assert row is not None
    assert row[0] == trade_ids[0]
    assert row[1] == 100
    assert row[2] == "take_profit_partial"
    assert row[3] == 1
    assert row[4] == 0.5
    assert row[5] == 120.0
    assert row[6] == 0.08
    assert row[7] == 0.02
    assert row[8] == 80.0
    assert row[9] == 0.05
    assert row[10] == "IWM"
    assert row[11] == "take_profit_partial"
