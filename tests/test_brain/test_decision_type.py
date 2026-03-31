from datetime import date

from llm_quant.brain.engine import SignalEngine
from llm_quant.brain.models import MarketRegime, TradingDecision
from llm_quant.config import load_config
from llm_quant.db.schema import init_schema


def test_decision_type_logged(tmp_path):
    config = load_config()
    db_path = tmp_path / "test.duckdb"
    config.general.db_path = str(db_path)
    conn = init_schema(db_path)

    engine = SignalEngine(config)
    decision = TradingDecision(
        date=date(2026, 3, 30),
        market_regime=MarketRegime.RISK_ON,
        regime_confidence=0.5,
        regime_reasoning="test",
        signals=[],
        portfolio_commentary="",
        decision_type="overlay",
        pod_id="alpha",
    )

    decision_id = engine.log_decision(conn, decision)
    row = conn.execute(
        "SELECT decision_type, pod_id FROM llm_decisions WHERE decision_id = ?",
        [decision_id],
    ).fetchone()
    assert row is not None
    assert row[0] == "overlay"
    assert row[1] == "alpha"
    conn.close()
