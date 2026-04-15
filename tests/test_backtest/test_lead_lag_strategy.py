from datetime import date

import polars as pl

from llm_quant.backtest.strategies import LeadLagStrategy
from llm_quant.backtest.strategy import StrategyConfig
from llm_quant.trading.portfolio import Portfolio


def _build_df(symbol: str, closes: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [date(2024, 1, i + 1) for i in range(len(closes))],
            "symbol": [symbol] * len(closes),
            "close": closes,
        }
    )


def _portfolio_with_position(**position_fields):
    portfolio = Portfolio(cash=100000.0)
    portfolio.positions["QQQ"] = position_fields
    return portfolio


def test_lead_lag_v3_lower_tier_entry():
    strategy = LeadLagStrategy(
        StrategyConfig(
            name="lead_lag",
            rebalance_frequency_days=1,
            max_positions=1,
            target_position_weight=0.90,
            stop_loss_pct=0.05,
            parameters={
                "leader_symbol": "SOXX",
                "follower_symbol": "QQQ",
                "lag_days": 2,
                "signal_window": 3,
                "entry_threshold_lower": 0.03,
                "entry_threshold_upper": 0.05,
                "target_weight_lower": 0.35,
                "target_weight_upper": 0.60,
                "confirmation_window": 3,
                "confirmation_threshold": -0.005,
                "exit_threshold": 0.0,
            },
        )
    )

    indicators_df = pl.concat(
        [
            _build_df("SOXX", [100.0, 103.0, 106.0, 107.0, 108.0, 109.0]),
            _build_df("QQQ", [100.0, 99.8, 99.7, 99.6, 99.7, 99.8]),
        ]
    )
    signals = strategy.generate_signals(
        as_of_date=date(2024, 1, 6),
        indicators_df=indicators_df,
        portfolio=Portfolio(cash=100000.0),
        prices={"QQQ": 99.8},
    )

    assert len(signals) == 1
    signal = signals[0]
    assert signal.symbol == "QQQ"
    assert signal.target_weight == 0.35


def test_lead_lag_v3_upper_tier_entry():
    strategy = LeadLagStrategy(
        StrategyConfig(
            name="lead_lag",
            rebalance_frequency_days=1,
            max_positions=1,
            target_position_weight=0.90,
            stop_loss_pct=0.05,
            parameters={
                "leader_symbol": "SOXX",
                "follower_symbol": "QQQ",
                "lag_days": 2,
                "signal_window": 3,
                "entry_threshold_lower": 0.03,
                "entry_threshold_upper": 0.05,
                "target_weight_lower": 0.35,
                "target_weight_upper": 0.60,
                "confirmation_window": 3,
                "confirmation_threshold": -0.005,
                "exit_threshold": 0.0,
            },
        )
    )

    indicators_df = pl.concat(
        [
            _build_df("SOXX", [100.0, 106.0, 111.0, 112.0, 113.0, 114.0]),
            _build_df("QQQ", [100.0, 100.1, 100.0, 100.2, 100.3, 100.4]),
        ]
    )
    signals = strategy.generate_signals(
        as_of_date=date(2024, 1, 6),
        indicators_df=indicators_df,
        portfolio=Portfolio(cash=100000.0),
        prices={"QQQ": 100.4},
    )

    assert len(signals) == 1
    signal = signals[0]
    assert signal.symbol == "QQQ"
    assert signal.target_weight == 0.60


def test_lead_lag_v3_confirmation_filter_blocks_entry():
    strategy = LeadLagStrategy(
        StrategyConfig(
            name="lead_lag",
            rebalance_frequency_days=1,
            max_positions=1,
            target_position_weight=0.90,
            stop_loss_pct=0.05,
            parameters={
                "leader_symbol": "SOXX",
                "follower_symbol": "QQQ",
                "lag_days": 2,
                "signal_window": 3,
                "entry_threshold_lower": 0.03,
                "target_weight_lower": 0.35,
                "confirmation_window": 3,
                "confirmation_threshold": -0.005,
                "exit_threshold": 0.0,
            },
        )
    )

    indicators_df = pl.concat(
        [
            _build_df("SOXX", [100.0, 103.0, 106.0, 107.0, 108.0, 109.0]),
            _build_df("QQQ", [100.0, 98.0, 96.0, 95.0, 94.0, 93.0]),
        ]
    )
    signals = strategy.generate_signals(
        as_of_date=date(2024, 1, 6),
        indicators_df=indicators_df,
        portfolio=Portfolio(cash=100000.0),
        prices={"QQQ": 93.0},
    )

    assert signals == []


def test_lead_lag_v3_time_stop_exits_position():
    strategy = LeadLagStrategy(
        StrategyConfig(
            name="lead_lag",
            rebalance_frequency_days=1,
            max_positions=1,
            target_position_weight=0.90,
            stop_loss_pct=0.05,
            parameters={
                "leader_symbol": "SOXX",
                "follower_symbol": "QQQ",
                "lag_days": 2,
                "signal_window": 3,
                "entry_threshold_lower": 0.03,
                "target_weight_lower": 0.35,
                "max_holding_days": 8,
                "cooldown_days_after_exit": 2,
                "exit_threshold": -0.20,
            },
        )
    )

    indicators_df = pl.concat(
        [
            _build_df("SOXX", [100.0, 103.0, 106.0, 107.0, 108.0, 109.0]),
            _build_df("QQQ", [100.0, 100.1, 100.2, 100.3, 100.4, 100.5]),
        ]
    )
    portfolio = _portfolio_with_position(days_held=8)
    signals = strategy.generate_signals(
        as_of_date=date(2024, 1, 6),
        indicators_df=indicators_df,
        portfolio=portfolio,
        prices={"QQQ": 100.5},
    )

    assert len(signals) == 1
    signal = signals[0]
    assert signal.symbol == "QQQ"
    assert signal.target_weight == 0.0
    assert signal.metadata["exit_cooldown_days"] == 2


def test_lead_lag_v3_cooldown_blocks_reentry():
    strategy = LeadLagStrategy(
        StrategyConfig(
            name="lead_lag",
            rebalance_frequency_days=1,
            max_positions=1,
            target_position_weight=0.90,
            stop_loss_pct=0.05,
            parameters={
                "leader_symbol": "SOXX",
                "follower_symbol": "QQQ",
                "lag_days": 2,
                "signal_window": 3,
                "entry_threshold_lower": 0.03,
                "target_weight_lower": 0.35,
                "cooldown_days_after_exit": 2,
                "exit_threshold": 0.0,
            },
        )
    )

    indicators_df = pl.concat(
        [
            _build_df("SOXX", [100.0, 103.0, 106.0, 107.0, 108.0, 109.0]),
            _build_df("QQQ", [100.0, 100.1, 100.2, 100.1, 100.2, 100.3]),
        ]
    )
    portfolio = _portfolio_with_position(exit_cooldown_days=1)
    signals = strategy.generate_signals(
        as_of_date=date(2024, 1, 6),
        indicators_df=indicators_df,
        portfolio=portfolio,
        prices={"QQQ": 100.3},
    )

    assert signals == []
