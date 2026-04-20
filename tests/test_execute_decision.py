from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from llm_quant.brain.models import (
    Action,
    Conviction,
    MarketRegime,
    TradeSignal,
    TradingDecision,
)
from llm_quant.trading.portfolio import Portfolio
from scripts import execute_decision


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def execute(self, query, params=None):
        del params
        if "SELECT close FROM market_data_daily" in query:
            return _Cursor((500.0,))
        raise AssertionError(f"Unexpected query: {query}")

    def close(self):
        return None


class _AlpacaClient:
    def get_asset(self, symbol: str):
        assert symbol == "SPY"
        return {"shortable": True, "easy_to_borrow": True}


class _Scanner:
    def __init__(self, _config):
        pass

    def run_full_scan(self, _conn):
        return SimpleNamespace(
            overall_severity=SimpleNamespace(value="ok"),
            halt_checks=[],
            warning_checks=[],
        )

    def persist_scan(self, _conn, _report):
        return None


def test_execute_decision_dry_run_passes_broker_locate_lookup_to_risk_manager(
    monkeypatch: pytest.MonkeyPatch,
    sample_config,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sample_config.general.db_path = "test.duckdb"

    monkeypatch.setattr(execute_decision, "load_config_for_pod", lambda _pod: sample_config)
    monkeypatch.setattr(execute_decision, "get_connection", lambda _db: _Conn())
    monkeypatch.setattr(
        execute_decision.AlpacaClient,
        "from_env",
        classmethod(lambda cls: _AlpacaClient()),
    )
    monkeypatch.setattr(execute_decision, "SurveillanceScanner", _Scanner)
    monkeypatch.setattr(
        execute_decision,
        "parse_trading_decision",
        lambda _raw, today: TradingDecision(
            date=today,
            market_regime=MarketRegime.RISK_OFF,
            regime_confidence=0.7,
            regime_reasoning="test",
            signals=[
                TradeSignal(
                    symbol="SPY",
                    action=Action.SHORT,
                    conviction=Conviction.MEDIUM,
                    target_weight=0.02,
                    stop_loss=505.0,
                    reasoning="test short",
                )
            ],
            portfolio_commentary="test",
        ),
    )
    monkeypatch.setattr(
        execute_decision.Portfolio,
        "from_db",
        classmethod(lambda cls, *_args, **_kwargs: Portfolio(initial_capital=100_000.0)),
    )
    monkeypatch.setattr(execute_decision, "build_exit_policy", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(execute_decision, "build_exit_runtime", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(execute_decision, "evaluate_position_exits", lambda **_kwargs: ([], []))
    monkeypatch.setattr(
        execute_decision,
        "load_latest_harvest_governance_result",
        lambda *_args, **_kwargs: SimpleNamespace(
            active_mandate_name=None,
            active_mandate_type=None,
            allocation_scale=1.0,
            force_flatten=False,
            conservative_mandate_name=None,
            lifecycle_recommendation=None,
            breached_rules=[],
            actions=[],
            metrics={},
        ),
    )
    monkeypatch.setattr(
        execute_decision,
        "apply_harvest_governance_controls",
        lambda signals, *_args, **_kwargs: signals,
    )
    monkeypatch.setattr(
        execute_decision,
        "apply_entry_halt_freeze",
        lambda signals, *_args, **_kwargs: (signals, []),
    )
    monkeypatch.setattr(
        execute_decision,
        "normalize_crypto_basket_weights",
        lambda approved, *_args, **_kwargs: approved,
    )

    class _RiskManager:
        def __init__(self, _config):
            pass

        def filter_signals(self, signals, portfolio, prices, locate_lookup=None):
            del signals, portfolio, prices
            assert callable(locate_lookup)
            assert locate_lookup("SPY") is True
            return [], []

    monkeypatch.setattr(execute_decision, "RiskManager", _RiskManager)
    monkeypatch.setattr("sys.argv", ["execute_decision.py", "--broker", "alpaca", "--dry-run"])
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"ok": True})))

    execute_decision.main()

    output = json.loads(capsys.readouterr().out)
    assert output["dry_run"] is True
    assert output["broker"] == "alpaca"
