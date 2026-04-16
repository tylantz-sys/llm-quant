from collections.abc import Mapping
from datetime import date

import polars as pl
import pytest

from llm_quant.backtest.strategies import create_strategy
from llm_quant.backtest.strategy import Strategy, StrategyConfig
from llm_quant.brain.models import Action
from llm_quant.trading.portfolio import Portfolio, Position


def _make_strategy() -> Strategy:
    config = StrategyConfig(
        name="spy_regime_starter",
        rebalance_frequency_days=1,
        parameters={
            "trade_symbol": "SPY",
            "vix_symbol": "VIX",
            "starter_weight": 0.02,
            "max_weight": 0.05,
            "rsi_entry_threshold": 55.0,
            "rsi_exit_threshold": 40.0,
            "vix_entry_max": 19.2,
            "vix_add_max": 16.4,
            "vix_exit_min": 25.0,
            "macd_add_min": 0.0,
            "macd_exit_max": -0.20,
            "atr_stop_multiple": 1.75,
            "atr_stop_mode": "fixed_at_entry",
            "max_adds": 1,
            "cooldown_days_after_exit": 2,
            "missing_vix_policy": "block_new_entries_allow_risk_exits",
            "rebalance_frequency_days": 1,
            "execution_lag_days": 1,
        },
    )
    return create_strategy("spy_regime_starter", config)


def _build_indicators(
    *,
    spy_rows: list[Mapping[str, float | None]],
    vix_rows: list[Mapping[str, float | None]] | None = None,
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for idx, spy_row in enumerate(spy_rows, start=1):
        rows.append(
            {
                "date": date(2026, 1, idx),
                "symbol": "SPY",
                **spy_row,
            }
        )
    if vix_rows is not None:
        for idx, vix_row in enumerate(vix_rows, start=1):
            rows.append(
                {
                    "date": date(2026, 1, idx),
                    "symbol": "VIX",
                    **vix_row,
                }
            )
    return pl.DataFrame(rows)


def _portfolio_with_spy_position(
    *,
    shares: float = 5.0,
    current_price: float = 1000.0,
    avg_cost: float = 100.0,
    stop_loss: float = 0.0,
    entry_price: float | None = None,
    entry_atr_14: float | None = None,
    add_count: int | None = None,
) -> Portfolio:
    portfolio = Portfolio(cash=100000.0)
    position = Position(
        symbol="SPY",
        shares=shares,
        avg_cost=avg_cost,
        current_price=current_price,
        stop_loss=stop_loss,
    )
    if entry_price is not None:
        setattr(position, "entry_price", entry_price)
    if entry_atr_14 is not None:
        setattr(position, "entry_atr_14", entry_atr_14)
    if add_count is not None:
        setattr(position, "add_count", add_count)
    portfolio.positions["SPY"] = position
    return portfolio


def test_spy_regime_starter_entry_requires_all_conditions() -> None:
    strategy = _make_strategy()
    indicators_df = _build_indicators(
        spy_rows=[
            {
                "close": 430.0,
                "sma_20": 420.0,
                "sma_50": 410.0,
                "rsi_14": 56.0,
                "macd": 0.05,
                "atr_14": 4.0,
            }
        ],
        vix_rows=[{"close": 18.0}],
    )

    signals = strategy.generate_signals(
        as_of_date=date(2026, 1, 1),
        indicators_df=indicators_df,
        portfolio=Portfolio(cash=100000.0),
        prices={"SPY": 430.0, "VIX": 18.0},
    )

    assert len(signals) == 1
    signal = signals[0]
    assert signal.symbol == "SPY"
    assert signal.action == Action.BUY
    assert signal.target_weight == pytest.approx(0.02)

    missing_rsi_df = _build_indicators(
        spy_rows=[
            {
                "close": 430.0,
                "sma_20": 420.0,
                "sma_50": 410.0,
                "rsi_14": 54.0,
                "macd": 0.05,
                "atr_14": 4.0,
            }
        ],
        vix_rows=[{"close": 18.0}],
    )
    blocked_signals = strategy.generate_signals(
        as_of_date=date(2026, 1, 1),
        indicators_df=missing_rsi_df,
        portfolio=Portfolio(cash=100000.0),
        prices={"SPY": 430.0, "VIX": 18.0},
    )

    assert blocked_signals == []


def test_spy_regime_starter_missing_vix_blocks_new_entry() -> None:
    strategy = _make_strategy()
    indicators_df = _build_indicators(
        spy_rows=[
            {
                "close": 430.0,
                "sma_20": 420.0,
                "sma_50": 410.0,
                "rsi_14": 56.0,
                "macd": 0.05,
                "atr_14": 4.0,
            }
        ],
        vix_rows=None,
    )

    signals = strategy.generate_signals(
        as_of_date=date(2026, 1, 1),
        indicators_df=indicators_df,
        portfolio=Portfolio(cash=100000.0),
        prices={"SPY": 430.0},
    )

    assert signals == []


@pytest.mark.parametrize(
    ("spy_overrides", "vix_close", "price", "entry_price", "entry_atr_14", "expected_fragment"),
    [
        ({"rsi_14": 39.0, "macd": 0.10}, 18.0, 430.0, 420.0, 4.0, "RSI"),
        ({"rsi_14": 56.0, "macd": -0.21}, 18.0, 430.0, 420.0, 4.0, "MACD"),
        ({"rsi_14": 56.0, "macd": 0.10}, 26.0, 430.0, 420.0, 4.0, "VIX"),
        ({"rsi_14": 56.0, "macd": 0.10}, 18.0, 412.0, 420.0, 4.0, "ATR"),
    ],
)
def test_spy_regime_starter_exit_conditions(
    spy_overrides: dict[str, float],
    vix_close: float,
    price: float,
    entry_price: float,
    entry_atr_14: float,
    expected_fragment: str,
) -> None:
    strategy = _make_strategy()
    spy_row = {
        "close": price,
        "sma_20": 420.0,
        "sma_50": 410.0,
        "rsi_14": 56.0,
        "macd": 0.10,
        "atr_14": 4.0,
    }
    spy_row.update(spy_overrides)
    indicators_df = _build_indicators(
        spy_rows=[spy_row],
        vix_rows=[{"close": vix_close}],
    )
    portfolio = _portfolio_with_spy_position(
        shares=5.0,
        current_price=price,
        avg_cost=420.0,
        entry_price=entry_price,
        entry_atr_14=entry_atr_14,
        add_count=0,
    )

    signals = strategy.generate_signals(
        as_of_date=date(2026, 1, 1),
        indicators_df=indicators_df,
        portfolio=portfolio,
        prices={"SPY": price, "VIX": vix_close},
    )

    assert len(signals) == 1
    signal = signals[0]
    assert signal.symbol == "SPY"
    assert signal.action == Action.CLOSE
    assert signal.target_weight == 0.0
    assert expected_fragment in signal.reasoning


def test_spy_regime_starter_add_signal_when_below_max_weight() -> None:
    strategy = _make_strategy()
    indicators_df = _build_indicators(
        spy_rows=[
            {
                "close": 430.0,
                "sma_20": 420.0,
                "sma_50": 410.0,
                "rsi_14": 56.0,
                "macd": 0.10,
                "atr_14": 4.0,
            }
        ],
        vix_rows=[{"close": 15.0}],
    )
    portfolio = _portfolio_with_spy_position(
        shares=5.0,
        current_price=430.0,
        avg_cost=420.0,
        entry_price=420.0,
        entry_atr_14=4.0,
        add_count=0,
    )

    assert portfolio.get_position_weight("SPY") < 0.05

    signals = strategy.generate_signals(
        as_of_date=date(2026, 1, 1),
        indicators_df=indicators_df,
        portfolio=portfolio,
        prices={"SPY": 430.0, "VIX": 15.0},
    )

    assert len(signals) == 1
    signal = signals[0]
    assert signal.action == Action.BUY
    assert signal.target_weight == pytest.approx(0.05)


def test_spy_regime_starter_returns_empty_when_indicator_data_insufficient() -> None:
    strategy = _make_strategy()
    indicators_df = _build_indicators(
        spy_rows=[
            {
                "close": 430.0,
                "rsi_14": 56.0,
            }
        ],
        vix_rows=[{"close": 18.0}],
    )

    signals = strategy.generate_signals(
        as_of_date=date(2026, 1, 1),
        indicators_df=indicators_df,
        portfolio=Portfolio(cash=100000.0),
        prices={"SPY": 430.0, "VIX": 18.0},
    )

    assert signals == []
