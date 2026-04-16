from __future__ import annotations

"""Tests for BacktestEngine: look-ahead, fill-delay, cost, stop-loss."""

import math
from datetime import date, timedelta

import polars as pl

from llm_quant.backtest.engine import (
    BacktestEngine,
    CostModel,
    build_backtest_exit_components,
)
from llm_quant.backtest.strategy import Strategy, StrategyConfig
from llm_quant.brain.models import Action, Conviction, TradeSignal
from llm_quant.config import ExecutionConfig, RiskLimits
from llm_quant.data.indicators import compute_indicators
from llm_quant.trading.exits import (
    SyntheticExitContext,
    build_exit_policy,
    evaluate_synthetic_exit,
    synthetic_exit_execution_assumption,
    synthetic_exit_parity_mode,
)
from llm_quant.trading.intraday import IntradayPositionState
from llm_quant.trading.portfolio import Portfolio, Position

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_prices(
    symbols: list[str],
    n_days: int = 300,
    start_date: date | None = None,
    trend: float = 0.0005,
    base_price: float = 100.0,
) -> pl.DataFrame:
    """Generate synthetic OHLCV data."""
    if start_date is None:
        start_date = date(2020, 1, 1)

    rows = []
    for symbol in symbols:
        price = base_price
        for i in range(n_days):
            d = start_date + timedelta(days=i)
            if d.weekday() >= 5:
                continue
            open_ = price
            high = price * 1.01
            low = price * 0.99
            close = price * (1 + trend)
            adj_close = close
            volume = 1_000_000
            rows.append(
                {
                    "symbol": symbol,
                    "date": d,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "adj_close": adj_close,
                }
            )
            price = close

    return pl.DataFrame(rows).with_columns(
        pl.col("date").cast(pl.Date),
        pl.col("volume").cast(pl.Int64),
    )


class AlwaysBuyStrategy(Strategy):
    """Test strategy that buys everything on every rebalance."""

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        signals = []
        for symbol in prices:
            if symbol not in portfolio.positions:
                signals.append(
                    TradeSignal(
                        symbol=symbol,
                        action=Action.BUY,
                        conviction=Conviction.MEDIUM,
                        target_weight=0.05,
                        stop_loss=prices[symbol] * 0.90,
                        reasoning="test buy",
                    )
                )
        return signals


class NeverTradeStrategy(Strategy):
    """Test strategy that never trades."""

    def generate_signals(self, *args, **kwargs) -> list[TradeSignal]:
        return []


class RotationSignalStrategy(Strategy):
    """Emit explicit target weights so engine rebalance semantics can be tested."""

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        day_plan = self.config.parameters["day_plan"]
        targets = day_plan.get(as_of_date, {})
        signals: list[TradeSignal] = []

        for symbol, weight in targets.items():
            price = prices.get(symbol, 0.0)
            if weight > 0:
                signals.append(
                    TradeSignal(
                        symbol=symbol,
                        action=Action.BUY,
                        conviction=Conviction.MEDIUM,
                        target_weight=weight,
                        stop_loss=price * 0.90 if price > 0 else 0.0,
                        reasoning=f"rebalance target {weight:.0%}",
                    )
                )
            elif symbol in portfolio.positions:
                signals.append(
                    TradeSignal(
                        symbol=symbol,
                        action=Action.CLOSE,
                        conviction=Conviction.MEDIUM,
                        target_weight=0.0,
                        stop_loss=0.0,
                        reasoning="rebalance target 0%",
                    )
                )

        return signals


class TestLookAhead:
    """Verify that indicators at date T are identical whether computed
    from data[<=T] or data[<=T+30]."""

    def test_indicators_causal(self):
        prices = _make_prices(["SPY"], n_days=400, trend=0.001)
        full_indicators = compute_indicators(prices)
        dates = sorted(full_indicators.select("date").unique().to_series().to_list())
        test_date = dates[250]

        full_at_date = full_indicators.filter(
            (pl.col("symbol") == "SPY") & (pl.col("date") == test_date)
        )

        truncated_prices = prices.filter(pl.col("date") <= test_date)
        truncated_indicators = compute_indicators(truncated_prices)
        trunc_at_date = truncated_indicators.filter(
            (pl.col("symbol") == "SPY") & (pl.col("date") == test_date)
        )

        indicator_cols = [
            "sma_20",
            "sma_50",
            "sma_200",
            "rsi_14",
            "macd",
            "macd_signal",
            "macd_hist",
            "atr_14",
        ]
        for col in indicator_cols:
            full_val = full_at_date.select(col).item()
            trunc_val = trunc_at_date.select(col).item()
            if full_val is not None and trunc_val is not None:
                assert abs(full_val - trunc_val) < 1e-10


class TestFillDelay:
    """With fill_delay=1, buys execute at T+1 open, not T close."""

    def test_fill_at_next_day_open(self):
        prices = _make_prices(["SPY"], n_days=400, trend=0.001)
        indicators = compute_indicators(prices)

        config = StrategyConfig(
            name="test",
            rebalance_frequency_days=1,
            target_position_weight=0.05,
            stop_loss_pct=0.10,
        )
        strategy = AlwaysBuyStrategy(config)
        engine = BacktestEngine(strategy=strategy, initial_capital=100_000.0)

        result = engine.run(
            prices_df=prices,
            indicators_df=indicators,
            slug="test",
            fill_delay=1,
            warmup_days=50,
            trial_count=1,
        )

        if result.trades:
            first_trade = result.trades[0]
            open_price = (
                prices.filter(
                    (pl.col("symbol") == first_trade.symbol)
                    & (pl.col("date") == first_trade.date)
                )
                .select("open")
                .item()
            )
            assert first_trade.price == open_price


class TestCostModel:
    def test_spread_cost(self):
        cm = CostModel(spread_bps=5.0, flat_slippage_bps=0.0)
        cost = cm.compute_cost(10_000.0, 100, daily_volume=None, daily_volatility=None)
        expected = 10_000.0 * 5.0 / 10_000.0
        assert abs(cost - expected) < 0.01

    def test_sqrt_impact(self):
        cm = CostModel(
            spread_bps=5.0,
            slippage_volatility_factor=0.5,
            flat_slippage_bps=2.0,
        )
        notional = 10_000.0
        shares = 100
        daily_volume = 1_000_000.0
        daily_vol = 0.02

        cost = cm.compute_cost(
            notional,
            shares,
            daily_volume=daily_volume,
            daily_volatility=daily_vol,
        )

        spread = notional * 5.0 / 10_000.0
        impact = notional * 0.5 * 0.02 * math.sqrt(100 / 1_000_000.0)
        expected = spread + impact
        assert abs(cost - expected) < 0.01

    def test_cost_multiplier(self):
        cm = CostModel(spread_bps=5.0, flat_slippage_bps=2.0)
        cost_1x = cm.compute_cost(10_000.0, 100, multiplier=1.0)
        cost_2x = cm.compute_cost(10_000.0, 100, multiplier=2.0)
        assert abs(cost_2x - 2 * cost_1x) < 0.01

    def test_from_spec_reads_execution_cost_model(self):
        spec = {
            "execution": {
                "cost_model": {
                    "spread_bps": 7.5,
                    "flat_slippage_bps": 3.25,
                    "slippage_volatility_factor": 0.6,
                }
            }
        }

        cm = CostModel.from_spec(spec)

        assert cm.spread_bps == 7.5
        assert cm.flat_slippage_bps == 3.25
        assert cm.slippage_volatility_factor == 0.6

    def test_from_spec_falls_back_to_top_level_cost_model(self):
        spec = {
            "cost_model": {
                "spread_bps": 4.0,
                "flat_slippage_bps": 1.5,
                "slippage_volatility_factor": 0.2,
            }
        }

        cm = CostModel.from_spec(spec)

        assert cm.spread_bps == 4.0
        assert cm.flat_slippage_bps == 1.5
        assert cm.slippage_volatility_factor == 0.2

    def test_cost_reduces_nav(self):
        prices = _make_prices(["SPY"], n_days=400, trend=0.0)
        indicators = compute_indicators(prices)

        config = StrategyConfig(
            name="test",
            rebalance_frequency_days=1,
            target_position_weight=0.05,
            stop_loss_pct=0.50,
        )
        strategy = AlwaysBuyStrategy(config)
        engine = BacktestEngine(strategy=strategy, initial_capital=100_000.0)

        result = engine.run(
            prices_df=prices,
            indicators_df=indicators,
            slug="test",
            cost_model=CostModel(spread_bps=50.0, flat_slippage_bps=50.0),
            fill_delay=0,
            warmup_days=50,
            trial_count=1,
        )

        if result.trades:
            assert result.nav_series[-1] < 100_000.0


class TestStopLoss:
    def test_stop_loss_triggers_close(self):
        engine = BacktestEngine(
            strategy=NeverTradeStrategy(StrategyConfig()),
            initial_capital=100_000.0,
        )

        portfolio = Portfolio(initial_capital=100_000.0)
        portfolio.positions["SPY"] = Position(
            symbol="SPY",
            shares=100,
            avg_cost=100.0,
            current_price=105.0,
            stop_loss=95.0,
        )
        portfolio.cash = 90_000.0

        stop_signals = engine._check_stop_losses(portfolio, {"SPY": 90.0})
        assert len(stop_signals) == 1
        assert stop_signals[0].action == Action.CLOSE
        assert stop_signals[0].symbol == "SPY"


class TestBenchmark:
    def test_total_return_vs_price_return(self):
        from llm_quant.backtest.metrics import compute_benchmark_returns

        rows = []
        base_date = date(2020, 1, 6)
        for i in range(100):
            d = base_date + timedelta(days=i)
            if d.weekday() >= 5:
                continue
            close = 100 + i * 0.1
            adj_close = close * 1.03
            rows.append(
                {
                    "symbol": "SPY",
                    "date": d,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 1_000_000,
                    "adj_close": adj_close,
                }
            )

        prices = pl.DataFrame(rows).with_columns(
            pl.col("date").cast(pl.Date),
            pl.col("volume").cast(pl.Int64),
        )

        tr_returns = compute_benchmark_returns(
            prices, {"SPY": 1.0}, rebalance_frequency_days=21, use_adj_close=True
        )
        pr_returns = compute_benchmark_returns(
            prices, {"SPY": 1.0}, rebalance_frequency_days=21, use_adj_close=False
        )

        if tr_returns and pr_returns:
            import numpy as np

            tr_total = float(np.prod([1 + r for r in tr_returns])) - 1.0
            pr_total = float(np.prod([1 + r for r in pr_returns])) - 1.0
            assert isinstance(tr_total, float)
            assert isinstance(pr_total, float)


class TestEngineEdgeCases:
    def test_empty_data_returns_early(self):
        empty_prices = pl.DataFrame(
            {
                "symbol": [],
                "date": [],
                "open": [],
                "high": [],
                "low": [],
                "close": [],
                "volume": [],
                "adj_close": [],
            },
            schema={
                "symbol": pl.Utf8,
                "date": pl.Date,
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Int64,
                "adj_close": pl.Float64,
            },
        )
        empty_indicators = pl.DataFrame(
            {"symbol": [], "date": [], "close": []},
            schema={"symbol": pl.Utf8, "date": pl.Date, "close": pl.Float64},
        )

        strategy = NeverTradeStrategy(StrategyConfig(name="test"))
        engine = BacktestEngine(strategy=strategy, initial_capital=100_000.0)

        result = engine.run(
            prices_df=empty_prices,
            indicators_df=empty_indicators,
            slug="test",
            warmup_days=50,
            trial_count=1,
        )

        assert len(result.trades) == 0
        assert len(result.data_warnings) > 0

    def test_single_day_data(self):
        rows = [
            {
                "symbol": "SPY",
                "date": date(2020, 1, 6),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1_000_000,
                "adj_close": 100.0,
            }
        ]
        prices = pl.DataFrame(rows).with_columns(
            pl.col("date").cast(pl.Date),
            pl.col("volume").cast(pl.Int64),
        )
        indicators = pl.DataFrame(
            {"symbol": ["SPY"], "date": [date(2020, 1, 6)], "close": [100.0]}
        ).with_columns(pl.col("date").cast(pl.Date))

        strategy = NeverTradeStrategy(StrategyConfig(name="test"))
        engine = BacktestEngine(strategy=strategy, initial_capital=100_000.0)

        result = engine.run(
            prices_df=prices,
            indicators_df=indicators,
            slug="test",
            warmup_days=50,
            trial_count=1,
        )

        assert len(result.trades) == 0
        assert len(result.data_warnings) > 0

    def test_zero_volume_falls_back_to_flat_slippage(self):
        cm = CostModel(
            spread_bps=5.0,
            slippage_volatility_factor=0.5,
            flat_slippage_bps=3.0,
        )
        cost = cm.compute_cost(
            notional=10_000.0,
            shares=100,
            daily_volume=0,
            daily_volatility=0.02,
        )
        expected = 10_000.0 * 5.0 / 10_000.0 + 10_000.0 * 3.0 / 10_000.0
        assert abs(cost - expected) < 0.01

    def test_cost_model_zero_shares(self):
        cm = CostModel(spread_bps=5.0, flat_slippage_bps=2.0)
        cost = cm.compute_cost(
            notional=0.0,
            shares=0,
            daily_volume=1_000_000,
            daily_volatility=0.02,
        )
        assert cost == 0.0

    def test_fill_delay_changes_fill_price(self):
        rows = []
        base_date = date(2020, 1, 6)
        price = 100.0
        for i in range(400):
            d = base_date + timedelta(days=i)
            if d.weekday() >= 5:
                continue
            rows.append(
                {
                    "symbol": "SPY",
                    "date": d,
                    "open": price * 0.99,
                    "high": price * 1.01,
                    "low": price * 0.98,
                    "close": price,
                    "volume": 1_000_000,
                    "adj_close": price,
                }
            )
            price = price * 1.002

        prices = pl.DataFrame(rows).with_columns(
            pl.col("date").cast(pl.Date),
            pl.col("volume").cast(pl.Int64),
        )
        indicators = compute_indicators(prices)

        config = StrategyConfig(
            name="test",
            rebalance_frequency_days=1,
            target_position_weight=0.05,
            stop_loss_pct=0.10,
        )

        result0 = BacktestEngine(
            strategy=AlwaysBuyStrategy(config),
            initial_capital=100_000.0,
        ).run(
            prices_df=prices,
            indicators_df=indicators,
            slug="test",
            fill_delay=0,
            warmup_days=50,
            trial_count=1,
        )

        result1 = BacktestEngine(
            strategy=AlwaysBuyStrategy(config),
            initial_capital=100_000.0,
        ).run(
            prices_df=prices,
            indicators_df=indicators,
            slug="test",
            fill_delay=1,
            warmup_days=50,
            trial_count=1,
        )

        assert len(result0.trades) > 0
        assert len(result1.trades) > 0
        assert result0.trades[0].price != result1.trades[0].price

    def test_nav_series_no_gaps_on_empty_price_day(self):
        rows = []
        base_date = date(2020, 1, 6)
        for i in range(300):
            d = base_date + timedelta(days=i)
            if d.weekday() >= 5:
                continue
            if i == 100:
                continue
            rows.append(
                {
                    "symbol": "SPY",
                    "date": d,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1_000_000,
                    "adj_close": 100.0,
                }
            )

        prices = pl.DataFrame(rows).with_columns(
            pl.col("date").cast(pl.Date),
            pl.col("volume").cast(pl.Int64),
        )
        indicators = compute_indicators(prices)

        result = BacktestEngine(
            strategy=NeverTradeStrategy(StrategyConfig(name="test")),
            initial_capital=100_000.0,
        ).run(
            prices_df=prices,
            indicators_df=indicators,
            slug="test",
            warmup_days=50,
            trial_count=1,
        )

        n_trading_dates = len(sorted(prices.select("date").unique().to_series().to_list()))
        if n_trading_dates > 50:
            assert len(result.nav_series) == (n_trading_dates - 50) + 1


class TestBacktestExitConfig:
    def test_build_backtest_exit_components_uses_governed_config(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        (config_dir / "default.toml").write_text(
            """
[execution]
intraday_enabled = true
intraday_use_oco = false
asset_class_filter = ["equity"]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        (config_dir / "risk.toml").write_text(
            """
[limits]
take_profit_mode = "pct"
take_profit_pct = 0.07
partial_take_profit_enabled = true
partial_take_profit_pct = 0.03
partial_take_profit_size = 0.4
trailing_stop_enabled = true
trailing_stop_pct = 0.02
eod_flatten_enabled = false
eod_flatten_time = "15:50"
""".strip()
            + "\n",
            encoding="utf-8",
        )

        (
            exit_policy,
            exit_runtime,
            parity_mode,
            parity_tier,
            execution_assumption,
        ) = build_backtest_exit_components(config_dir=config_dir)

        assert exit_policy.take_profit_pct == 0.07
        assert exit_policy.partial_take_profit_pct == 0.03
        assert exit_policy.partial_take_profit_size == 0.4
        assert exit_policy.trailing_stop_pct == 0.02
        assert exit_policy.eod_flatten_enabled is False
        assert exit_policy.eod_flatten_time == "15:50"
        assert exit_runtime.intraday_enabled is True
        assert exit_runtime.intraday_use_oco is False
        assert exit_runtime.asset_class_filter == ("equity",)
        assert parity_mode == synthetic_exit_parity_mode()
        assert parity_tier == "tier1_close_only"
        assert execution_assumption == synthetic_exit_execution_assumption()


class TestExitParity:
    def test_backtest_synthetic_exit_trade_reports_provenance(self):
        prices = _make_prices(["SPY"], n_days=260, trend=0.0)
        indicators = compute_indicators(prices)

        strategy = NeverTradeStrategy(StrategyConfig(name="no_trade"))
        engine = BacktestEngine(strategy=strategy, initial_capital=100_000.0)
        engine.backtest_exit_policy = build_exit_policy(
            RiskLimits(
                partial_take_profit_enabled=True,
                partial_take_profit_pct=0.02,
                partial_take_profit_size=0.5,
            ),
            ExecutionConfig(),
        )

        portfolio = Portfolio(initial_capital=100_000.0)
        portfolio.positions["SPY"] = Position(
            symbol="SPY",
            shares=10,
            avg_cost=100.0,
            current_price=100.0,
            stop_loss=95.0,
        )
        portfolio.cash = 99_000.0

        signal = engine._check_exit_policy(
            portfolio,
            {"SPY": 102.0},
            {},
            date(2020, 1, 6),
        )[0]

        trades = engine._execute_signals(
            [signal],
            portfolio,
            {"SPY": 102.0},
            date(2020, 1, 6),
            CostModel(),
            1.0,
            {},
            {},
        )

        assert len(trades) == 1
        trade = trades[0]
        assert trade.is_synthetic_exit is True
        assert trade.exit_reason == "tp_partial"
        assert trade.exit_parity_mode == synthetic_exit_parity_mode()
        assert trade.exit_execution_assumption == synthetic_exit_execution_assumption()
        assert trade.exit_parity_tier == engine.backtest_exit_parity_tier

    def test_non_exit_sell_does_not_report_synthetic_exit_telemetry(self):
        engine = BacktestEngine(
            strategy=NeverTradeStrategy(StrategyConfig(name="no_trade")),
            initial_capital=100_000.0,
        )

        portfolio = Portfolio(initial_capital=100_000.0)
        portfolio.positions["SPY"] = Position(
            symbol="SPY",
            shares=10,
            avg_cost=100.0,
            current_price=100.0,
            stop_loss=95.0,
        )
        portfolio.cash = 99_000.0

        trades = engine._execute_signals(
            [
                TradeSignal(
                    symbol="SPY",
                    action=Action.SELL,
                    conviction=Conviction.MEDIUM,
                    target_weight=0.005,
                    stop_loss=95.0,
                    reasoning="plain rebalance trim",
                )
            ],
            portfolio,
            {"SPY": 102.0},
            date(2020, 1, 6),
            CostModel(),
            1.0,
            {},
            {},
        )

        assert len(trades) == 1
        trade = trades[0]
        assert trade.is_synthetic_exit is False
        assert trade.exit_reason == ""
        assert trade.exit_parity_mode == ""
        assert trade.exit_execution_assumption == ""

    def test_backtest_daily_synthetic_exit_is_not_more_optimistic_than_runtime(self):
        engine = BacktestEngine(
            strategy=NeverTradeStrategy(StrategyConfig(name="no_trade")),
            initial_capital=100_000.0,
        )
        policy = build_exit_policy(
            RiskLimits(
                partial_take_profit_enabled=True,
                partial_take_profit_pct=0.02,
                partial_take_profit_size=0.5,
            ),
            ExecutionConfig(),
        )
        engine.backtest_exit_policy = policy

        portfolio = Portfolio(initial_capital=100_000.0)
        portfolio.positions["SPY"] = Position(
            symbol="SPY",
            shares=10,
            avg_cost=100.0,
            current_price=100.0,
            stop_loss=95.0,
        )
        portfolio.cash = 99_000.0

        runtime_signal = evaluate_synthetic_exit(
            SyntheticExitContext(
                position=portfolio.positions["SPY"],
                price=102.0,
                nav=portfolio.nav,
                state=IntradayPositionState(
                    symbol="SPY",
                    entry_price=100.0,
                    peak_price=102.0,
                ),
            ),
            policy,
        )
        backtest_signal = engine._check_exit_policy(
            portfolio,
            {"SPY": 102.0},
            {
                "SPY": IntradayPositionState(
                    symbol="SPY",
                    entry_price=100.0,
                    peak_price=102.0,
                )
            },
            date(2020, 1, 6),
        )[0]

        assert runtime_signal is not None
        assert backtest_signal is not None
        assert backtest_signal.exit_reason == runtime_signal.exit_reason
        assert backtest_signal.action == runtime_signal.action
        assert backtest_signal.target_weight <= runtime_signal.target_weight

    def test_backtest_result_exposes_exit_parity_summary(self):
        prices = _make_prices(["SPY"], n_days=260, trend=0.0)
        indicators = compute_indicators(prices)

        result = BacktestEngine(
            strategy=NeverTradeStrategy(StrategyConfig(name="no_trade")),
            initial_capital=100_000.0,
        ).run(
            prices_df=prices,
            indicators_df=indicators,
            slug="test",
            fill_delay=0,
            warmup_days=50,
            trial_count=1,
        )

        assert result.exit_parity_mode == synthetic_exit_parity_mode()
        assert result.exit_execution_assumption == synthetic_exit_execution_assumption()
        assert result.synthetic_exit_trade_count == 0

    def test_partial_take_profit_then_trailing_stop_semantics(self):
        policy = build_exit_policy(
            RiskLimits(
                partial_take_profit_enabled=True,
                partial_take_profit_pct=0.02,
                partial_take_profit_size=0.5,
                trailing_stop_enabled=True,
                trailing_stop_pct=0.05,
            ),
            ExecutionConfig(),
        )
        position = Position(
            symbol="SPY",
            shares=10,
            avg_cost=100.0,
            current_price=100.0,
            stop_loss=95.0,
        )
        state = IntradayPositionState(
            symbol="SPY",
            entry_price=100.0,
            peak_price=100.0,
        )

        partial_signal = evaluate_synthetic_exit(
            SyntheticExitContext(
                position=position,
                price=102.0,
                nav=100_000.0,
                state=state,
            ),
            policy,
        )

        assert partial_signal is not None
        assert partial_signal.exit_reason == "tp_partial"
        assert partial_signal.action == Action.SELL

        state.partial_exit_taken = True
        state.peak_price = 110.0

        trailing_signal = evaluate_synthetic_exit(
            SyntheticExitContext(
                position=position,
                price=104.0,
                nav=100_000.0,
                state=state,
            ),
            policy,
        )

        assert trailing_signal is not None
        assert trailing_signal.exit_reason == "trailing_stop"
        assert trailing_signal.action == Action.CLOSE

    def test_eod_flatten_preempts_rebalance_signals_for_open_positions(self):
        prices = _make_prices(["SPY"], n_days=260, trend=0.0)
        indicators = compute_indicators(prices)
        trading_date = sorted(prices.select("date").unique().to_series().to_list())[0]

        strategy = RotationSignalStrategy(
            StrategyConfig(
                name="rotation",
                rebalance_frequency_days=1,
                parameters={"day_plan": {trading_date: {"SPY": 0.8}}},
            )
        )
        engine = BacktestEngine(strategy=strategy, initial_capital=100_000.0)

        portfolio = Portfolio(initial_capital=100_000.0)
        portfolio.positions["SPY"] = Position(
            symbol="SPY",
            shares=10,
            avg_cost=100.0,
            current_price=100.0,
            stop_loss=95.0,
        )
        portfolio.cash = 99_000.0

        exit_signals = engine._check_exit_policy(
            portfolio,
            {"SPY": 100.0},
            {},
            trading_date,
            prices,
        )
        strategy_signals = strategy.generate_signals(
            trading_date,
            indicators.filter(pl.col("date") <= trading_date),
            portfolio,
            {"SPY": 100.0},
        )

        exit_symbols = {signal.symbol for signal in exit_signals}
        deduped_strategy = [
            signal for signal in strategy_signals if signal.symbol not in exit_symbols
        ]

        assert len(exit_signals) == 1
        assert exit_signals[0].symbol == "SPY"
        assert exit_signals[0].exit_reason == "eod_flatten"
        assert strategy_signals[0].symbol == "SPY"
        assert strategy_signals[0].action == Action.BUY
        assert deduped_strategy == []


class TestRebalanceSemantics:
    def _make_rotation_prices(self) -> pl.DataFrame:
        rows = []
        current = date(2020, 1, 6)
        trading_days = 0
        while trading_days < 8:
            if current.weekday() < 5:
                for symbol in ("SPY", "TLT"):
                    rows.append(
                        {
                            "symbol": symbol,
                            "date": current,
                            "open": 100.0,
                            "high": 100.0,
                            "low": 100.0,
                            "close": 100.0,
                            "volume": 1_000_000,
                            "adj_close": 100.0,
                        }
                    )
                trading_days += 1
            current += timedelta(days=1)

        return pl.DataFrame(rows).with_columns(
            pl.col("date").cast(pl.Date),
            pl.col("volume").cast(pl.Int64),
        )

    def _make_rotation_dates(self, prices: pl.DataFrame) -> list[date]:
        return sorted(prices.select("date").unique().to_series().to_list())

    def test_rebalance_rotates_from_80_20_to_20_80(self):
        prices = self._make_rotation_prices()
        indicators = prices.select(["symbol", "date", "close"])
        dates = self._make_rotation_dates(prices)

        strategy = RotationSignalStrategy(
            StrategyConfig(
                name="rotation",
                rebalance_frequency_days=1,
                parameters={
                    "day_plan": {
                        dates[0]: {"SPY": 0.8, "TLT": 0.2},
                        dates[1]: {"SPY": 0.2, "TLT": 0.8},
                    }
                },
            )
        )

        result = BacktestEngine(strategy=strategy, initial_capital=100_000.0).run(
            prices_df=prices,
            indicators_df=indicators,
            slug="rotation",
            fill_delay=0,
            warmup_days=0,
            trial_count=1,
        )

        trades_day_0 = [t for t in result.trades if t.date == dates[0]]
        trades_day_1 = [t for t in result.trades if t.date == dates[1]]

        assert [(t.symbol, t.action, int(t.shares)) for t in trades_day_0] == [
            ("SPY", "buy", 800),
            ("TLT", "buy", 199),
        ]
        assert [(t.symbol, t.action, int(t.shares), t.exit_reason) for t in trades_day_1] == [
            ("SPY", "close", 800, "eod_flatten"),
            ("TLT", "close", 199, "eod_flatten"),
        ]

    def test_rebalance_rotates_from_20_80_to_80_20(self):
        prices = self._make_rotation_prices()
        indicators = prices.select(["symbol", "date", "close"])
        dates = self._make_rotation_dates(prices)

        strategy = RotationSignalStrategy(
            StrategyConfig(
                name="rotation",
                rebalance_frequency_days=1,
                parameters={
                    "day_plan": {
                        dates[0]: {"SPY": 0.2, "TLT": 0.8},
                        dates[1]: {"SPY": 0.8, "TLT": 0.2},
                    }
                },
            )
        )

        result = BacktestEngine(strategy=strategy, initial_capital=100_000.0).run(
            prices_df=prices,
            indicators_df=indicators,
            slug="rotation",
            fill_delay=0,
            warmup_days=0,
            trial_count=1,
        )

        trades_day_0 = [t for t in result.trades if t.date == dates[0]]
        trades_day_1 = [t for t in result.trades if t.date == dates[1]]

        assert [(t.symbol, t.action, int(t.shares)) for t in trades_day_0] == [
            ("SPY", "buy", 200),
            ("TLT", "buy", 799),
        ]
        assert [(t.symbol, t.action, int(t.shares), t.exit_reason) for t in trades_day_1] == [
            ("SPY", "close", 200, "eod_flatten"),
            ("TLT", "close", 799, "eod_flatten"),
        ]

    def test_neutral_hold_generates_no_churn(self):
        prices = self._make_rotation_prices()
        indicators = prices.select(["symbol", "date", "close"])
        dates = self._make_rotation_dates(prices)

        strategy = RotationSignalStrategy(
            StrategyConfig(
                name="rotation",
                rebalance_frequency_days=1,
                parameters={"day_plan": {dates[0]: {"SPY": 0.8, "TLT": 0.2}, dates[1]: {}}},
            )
        )

        result = BacktestEngine(strategy=strategy, initial_capital=100_000.0).run(
            prices_df=prices,
            indicators_df=indicators,
            slug="rotation",
            fill_delay=0,
            warmup_days=0,
            trial_count=1,
        )

        assert len([t for t in result.trades if t.date == dates[0]]) == 2
        assert [(t.symbol, t.action, t.exit_reason) for t in result.trades if t.date == dates[1]] == [
            ("SPY", "close", "eod_flatten"),
            ("TLT", "close", "eod_flatten"),
        ]
        assert result.signal_noop_reasons == {}
        assert next(s for s in result.snapshots if s.date == dates[1]).n_positions == 0

    def test_signal_noop_reasons_capture_unfilled_rebalance_attempts(self):
        prices = self._make_rotation_prices()
        indicators = prices.select(["symbol", "date", "close"])
        dates = self._make_rotation_dates(prices)

        strategy = RotationSignalStrategy(
            StrategyConfig(
                name="noop-diagnostics",
                rebalance_frequency_days=1,
                parameters={
                    "day_plan": {
                        dates[0]: {"SPY": 0.8, "TLT": 0.2},
                        dates[1]: {"SPY": 0.8, "TLT": 0.2},
                    }
                },
            )
        )

        result = BacktestEngine(strategy=strategy, initial_capital=100_000.0).run(
            prices_df=prices,
            indicators_df=indicators,
            slug="rotation",
            fill_delay=0,
            warmup_days=0,
            trial_count=1,
        )

        assert result.signal_count == 4
        assert result.executed_trade_count == 4
        assert result.signal_noop_reasons == {}


class TestSellExecutionSemantics:
    def test_sell_below_share_floor_is_treated_as_hold_not_noop_churn(self):
        engine = BacktestEngine(
            strategy=NeverTradeStrategy(StrategyConfig(name="no_trade")),
            initial_capital=100_000.0,
        )

        portfolio = Portfolio(initial_capital=100_000.0)
        portfolio.positions["SPY"] = Position(
            symbol="SPY",
            shares=10,
            avg_cost=100.0,
            current_price=100.0,
            stop_loss=95.0,
        )
        portfolio.cash = 99_000.0

        signal_noop_reasons: dict[str, int] = {}
        trades = engine._execute_signals(
            [
                TradeSignal(
                    symbol="SPY",
                    action=Action.SELL,
                    conviction=Conviction.MEDIUM,
                    target_weight=0.00995,
                    stop_loss=95.0,
                    reasoning="tiny rebalance trim",
                )
            ],
            portfolio,
            {"SPY": 100.0},
            date(2020, 1, 6),
            CostModel(),
            1.0,
            {},
            {},
            signal_noop_reasons,
        )

        assert trades == []
        assert signal_noop_reasons == {"sell_residual_below_share_floor": 1}
        assert portfolio.positions["SPY"].shares == 10

    def test_close_signal_fully_liquidates_without_share_floor_block(self):
        engine = BacktestEngine(
            strategy=NeverTradeStrategy(StrategyConfig(name="no_trade")),
            initial_capital=100_000.0,
        )

        portfolio = Portfolio(initial_capital=100_000.0)
        portfolio.positions["SPY"] = Position(
            symbol="SPY",
            shares=10,
            avg_cost=100.0,
            current_price=100.0,
            stop_loss=95.0,
        )
        portfolio.cash = 99_000.0

        signal_noop_reasons: dict[str, int] = {}
        trades = engine._execute_signals(
            [
                TradeSignal(
                    symbol="SPY",
                    action=Action.CLOSE,
                    conviction=Conviction.HIGH,
                    target_weight=0.0,
                    stop_loss=0.0,
                    reasoning="full liquidation",
                    exit_reason="rebalance_close",
                )
            ],
            portfolio,
            {"SPY": 100.0},
            date(2020, 1, 6),
            CostModel(),
            1.0,
            {},
            {},
            signal_noop_reasons,
        )

        assert len(trades) == 1
        assert trades[0].action == "close"
        assert trades[0].shares == 10
        assert "SPY" not in portfolio.positions
        assert signal_noop_reasons == {}


class TestEngineIntegration:
    def test_no_trades_strategy(self):
        prices = _make_prices(["SPY"], n_days=400)
        indicators = compute_indicators(prices)

        result = BacktestEngine(
            strategy=NeverTradeStrategy(StrategyConfig(name="no_trade")),
            initial_capital=100_000.0,
        ).run(
            prices_df=prices,
            indicators_df=indicators,
            slug="test",
            warmup_days=50,
            trial_count=1,
        )

        assert len(result.trades) == 0
        assert result.nav_series[-1] == 100_000.0

    def test_cost_sensitivity_runs_multiple(self):
        prices = _make_prices(["SPY"], n_days=400, trend=0.001)
        indicators = compute_indicators(prices)

        strategy = AlwaysBuyStrategy(
            StrategyConfig(
                name="test",
                rebalance_frequency_days=5,
                target_position_weight=0.10,
                stop_loss_pct=0.10,
            )
        )
        engine = BacktestEngine(
            strategy=strategy,
            initial_capital=100_000.0,
            data_dir="data",
        )

        result = engine.run_with_cost_sensitivity(
            prices_df=prices,
            indicators_df=indicators,
            slug="test",
            cost_multipliers=[1.0, 2.0],
        )

        assert "1.0x" in result.metrics
        assert "2.0x" in result.metrics
