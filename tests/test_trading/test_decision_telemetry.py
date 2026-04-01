from datetime import date

from llm_quant.brain.engine import SignalEngine
from llm_quant.brain.models import MarketContext, MarketRegime, TradingDecision
from llm_quant.trading.telemetry import log_decision_context, log_profit_take_event


def test_log_decision_writes_prompt_logs(tmp_db, sample_config):
    engine = SignalEngine(sample_config)
    decision = TradingDecision(
        date=date(2026, 1, 1),
        market_regime=MarketRegime.RISK_ON,
        regime_confidence=0.5,
        regime_reasoning="test",
        signals=[],
        portfolio_commentary="",
    )
    decision.model = "test-model"
    decision.prompt_tokens = 1
    decision.completion_tokens = 1
    decision.total_tokens = 2
    decision.cost_usd = 0.0
    decision.raw_response = "{}"
    decision.system_prompt = "system"
    decision.user_prompt = "user"

    decision_id = engine.log_decision(tmp_db, decision)
    rows = tmp_db.execute(
        "SELECT prompt_type, prompt_text FROM llm_prompt_logs WHERE decision_id = ?",
        [decision_id],
    ).fetchall()

    assert {row[0] for row in rows} == {"system", "user"}


def test_log_decision_context(tmp_db):
    context = MarketContext(
        date=date(2026, 1, 1),
        nav=100000.0,
        cash=50000.0,
        cash_pct=0.5,
        gross_exposure_pct=0.5,
        net_exposure_pct=0.5,
    )
    log_decision_context(
        tmp_db,
        decision_id=1,
        pod_id="default",
        context=context,
        extra={"governor_audit": {"candidate_count": 1}},
    )
    row = tmp_db.execute(
        "SELECT context_json FROM decision_contexts WHERE decision_id = 1"
    ).fetchone()
    assert row is not None
    assert "governor_audit" in row[0]


def test_log_decision_context_can_include_active_mandate(tmp_db):
    context = MarketContext(
        date=date(2026, 1, 1),
        nav=100000.0,
        cash=50000.0,
        cash_pct=0.5,
        gross_exposure_pct=0.5,
        net_exposure_pct=0.5,
    )
    log_decision_context(
        tmp_db,
        decision_id=2,
        pod_id="crypto",
        context=context,
        extra={
            "active_mandate_name": "crypto",
            "active_mandate_type": "crypto_synthetic_harvest",
        },
    )
    row = tmp_db.execute(
        "SELECT context_json FROM decision_contexts WHERE decision_id = 2"
    ).fetchone()
    assert row is not None
    assert '"active_mandate_name": "crypto"' in row[0]
    assert '"active_mandate_type": "crypto_synthetic_harvest"' in row[0]


def test_log_profit_take_event_persists_full_payload(tmp_db):
    event_id = log_profit_take_event(
        tmp_db,
        pod_id="default",
        symbol="SPY",
        event_type="executed",
        decision_source="llm",
        sleeve="promoted_default",
        source_decision_id=11,
        decision_id=12,
        trade_id=13,
        entry_batch=2,
        action="sell",
        shares=5.0,
        price=505.25,
        notional=2526.25,
        trigger_price=504.0,
        peak_price=510.0,
        drawdown_pct=-1.176,
        realized_pnl=125.5,
        return_pct=0.052,
        rule_name="partial_take_profit",
        reason="tp_partial",
        metadata={"strategy_id": "s1", "reasoning": "trim winner"},
    )

    row = tmp_db.execute(
        """
        SELECT
            event_id,
            pod_id,
            symbol,
            event_type,
            decision_source,
            sleeve,
            source_decision_id,
            decision_id,
            trade_id,
            entry_batch,
            action,
            shares,
            price,
            notional,
            trigger_price,
            peak_price,
            drawdown_pct,
            realized_pnl,
            return_pct,
            rule_name,
            reason,
            metadata_json
        FROM profit_take_events
        WHERE event_id = ?
        """,
        [event_id],
    ).fetchone()

    assert row is not None
    assert row[0] == event_id
    assert row[1] == "default"
    assert row[2] == "SPY"
    assert row[3] == "executed"
    assert row[4] == "llm"
    assert row[5] == "promoted_default"
    assert row[6] == 11
    assert row[7] == 12
    assert row[8] == 13
    assert row[9] == 2
    assert row[10] == "sell"
    assert row[11] == 5.0
    assert row[12] == 505.25
    assert row[13] == 2526.25
    assert row[14] == 504.0
    assert row[15] == 510.0
    assert row[16] == -1.176
    assert row[17] == 125.5
    assert row[18] == 0.052
    assert row[19] == "partial_take_profit"
    assert row[20] == "take_profit_partial"
    assert "trim winner" in row[21]