from datetime import date, timedelta

import polars as pl
import pytest

from llm_quant.backtest.strategies import VixRegimeStrategy
from llm_quant.backtest.strategy import StrategyConfig
from llm_quant.brain.models import Action
from llm_quant.trading.portfolio import Portfolio, Position


def _build_term_structure_df(
    *,
    near_values: list[float],
    medium_values: list[float],
    equity_symbol: str = "SPY",
    defensive_symbol: str = "SHY",
) -> pl.DataFrame:
    start = date(2026, 1, 1)
    rows: list[dict[str, object]] = []
    for idx, (near_value, medium_value) in enumerate(zip(near_values, medium_values, strict=True)):
        current_date = start + timedelta(days=idx)
        rows.extend(
            [
                {"date": current_date, "symbol": "^VIX9D", "close": near_value},
                {"date": current_date, "symbol": "^VIX", "close": medium_value},
                {"date": current_date, "symbol": equity_symbol, "close": 500.0 + idx},
                {"date": current_date, "symbol": defensive_symbol, "close": 100.0 + 0.1 * idx},
            ]
        )
    return pl.DataFrame(rows)


def _make_strategy() -> VixRegimeStrategy:
    return VixRegimeStrategy(
        StrategyConfig(
            name="vix_regime",
            parameters={
                "mode": "term_structure",
                "vix_symbol": "^VIX9D",
                "vix3m_symbol": "^VIX",
                "equity_symbol": "SPY",
                "risk_off_symbol": "SHY",
                "vix_threshold": 1.05,
                "contango_threshold": 0.95,
                "target_weight": 0.80,
                "weight_spy_risk_off": 0.20,
            },
        )
    )


def test_vix_term_structure_contango_generates_risk_on_signal():
    strategy = _make_strategy()
    indicators_df = _build_term_structure_df(
        near_values=[20.0, 20.0, 20.0, 20.0, 20.0],
        medium_values=[18.0, 18.0, 18.0, 18.0, 18.0],
    )
    portfolio = Portfolio()
    signals = strategy.generate_signals(
        as_of_date=date(2026, 1, 5),
        indicators_df=indicators_df,
        portfolio=portfolio,
        prices={"SPY": 505.0, "SHY": 100.4},
    )

    assert len(signals) == 1
    signal = signals[0]
    assert signal.symbol == "SPY"
    assert signal.action == Action.BUY
    assert signal.target_weight == 0.80
    assert "contango ratio=0.900" in signal.reasoning


def test_vix_term_structure_backwardation_rotates_to_defensive_weights():
    strategy = _make_strategy()
    indicators_df = _build_term_structure_df(
        near_values=[20.0, 20.0, 20.0, 20.0, 20.0],
        medium_values=[22.0, 22.0, 22.0, 22.0, 22.0],
    )
    portfolio = Portfolio()
    portfolio.positions["SPY"] = Position(
        symbol="SPY",
        shares=10.0,
        avg_cost=500.0,
        current_price=505.0,
    )

    signals = strategy.generate_signals(
        as_of_date=date(2026, 1, 5),
        indicators_df=indicators_df,
        portfolio=portfolio,
        prices={"SPY": 505.0, "SHY": 100.4},
    )

    assert len(signals) == 2
    by_symbol = {signal.symbol: signal for signal in signals}
    assert by_symbol["SPY"].action == Action.BUY
    assert by_symbol["SPY"].target_weight == pytest.approx(0.20)
    assert by_symbol["SHY"].action == Action.BUY
    assert by_symbol["SHY"].target_weight == pytest.approx(0.80)
    assert "backwardation ratio=1.100" in by_symbol["SHY"].reasoning


def test_vix_term_structure_neutral_holds_current_allocation():
    strategy = _make_strategy()
    indicators_df = _build_term_structure_df(
        near_values=[20.0, 20.0, 20.0, 20.0, 20.0],
        medium_values=[20.0, 20.0, 20.0, 20.0, 20.0],
    )
    portfolio = Portfolio()
    portfolio.positions["SPY"] = Position(
        symbol="SPY",
        shares=10.0,
        avg_cost=500.0,
        current_price=505.0,
    )

    signals = strategy.generate_signals(
        as_of_date=date(2026, 1, 5),
        indicators_df=indicators_df,
        portfolio=portfolio,
        prices={"SPY": 505.0, "SHY": 100.4},
    )

    assert signals == []


def test_vix_term_structure_crossed_thresholds_do_not_degenerate_to_no_signal():
    strategy = _make_strategy()
    portfolio = Portfolio()

    contango_df = _build_term_structure_df(
        near_values=[20.0, 20.0, 20.0, 20.0, 20.0],
        medium_values=[18.0, 18.0, 18.0, 18.0, 18.0],
    )
    contango_signals = strategy.generate_signals(
        as_of_date=date(2026, 1, 5),
        indicators_df=contango_df,
        portfolio=portfolio,
        prices={"SPY": 505.0, "SHY": 100.4},
    )
    assert contango_signals
    assert any(
        signal.symbol == "SPY" and signal.action == Action.BUY
        for signal in contango_signals
    )

    backwardation_df = _build_term_structure_df(
        near_values=[20.0, 20.0, 20.0, 20.0, 20.0],
        medium_values=[22.0, 22.0, 22.0, 22.0, 22.0],
    )
    backwardation_signals = strategy.generate_signals(
        as_of_date=date(2026, 1, 5),
        indicators_df=backwardation_df,
        portfolio=portfolio,
        prices={"SPY": 505.0, "SHY": 100.4},
    )
    assert backwardation_signals
    assert any(
        signal.symbol == "SHY" and signal.action == Action.BUY
        for signal in backwardation_signals
    )
