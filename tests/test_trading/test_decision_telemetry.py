from datetime import date

from llm_quant.brain.engine import SignalEngine
from llm_quant.brain.models import MarketContext, MarketRegime, TradingDecision
from llm_quant.trading.telemetry import log_decision_context


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
    log_decision_context(tmp_db, decision_id=1, pod_id="default", context=context)
    row = tmp_db.execute(
        "SELECT decision_id FROM decision_contexts WHERE decision_id = 1"
    ).fetchone()
    assert row is not None
