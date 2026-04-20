from datetime import UTC, datetime

from llm_quant.config import ExecutionConfig, RiskLimits
from llm_quant.trading.exits import (
    SyntheticExitContext,
    assess_eod_flatten,
    build_broker_exit_plan,
    build_exit_policy,
    build_exit_runtime,
    build_exit_telemetry_payload,
    evaluate_position_exits,
    evaluate_synthetic_exit,
    parse_eod_time,
    resolve_take_profit_price,
    synthetic_exit_execution_assumption,
    synthetic_exit_parity_mode,
)
from llm_quant.trading.intraday import IntradayPositionState
from llm_quant.trading.portfolio import Portfolio, Position


def _portfolio_with_position(symbol: str, shares: float, price: float) -> Portfolio:
    portfolio = Portfolio(initial_capital=100_000.0)
    portfolio.positions[symbol] = Position(
        symbol=symbol,
        shares=shares,
        avg_cost=price,
        current_price=price,
        stop_loss=price * 0.95,
    )
    return portfolio


def _portfolio_with_short_position(
    symbol: str, shares: float, price: float
) -> Portfolio:
    portfolio = Portfolio(initial_capital=100_000.0)
    portfolio.positions[symbol] = Position(
        symbol=symbol,
        shares=-shares,
        avg_cost=price,
        current_price=price,
        stop_loss=price * 1.05,
    )
    return portfolio


def test_build_exit_policy_prefers_risk_config():
    limits = RiskLimits(
        partial_take_profit_enabled=True,
        partial_take_profit_pct=0.03,
        partial_take_profit_size=0.4,
        trailing_stop_enabled=True,
        trailing_stop_pct=0.02,
        fail_on_unprotected_exits=True,
    )
    execution = ExecutionConfig(
        profit_take_partial_pct=0.01,
        profit_take_partial_size=0.25,
        trailing_stop_pct=0.01,
    )

    policy = build_exit_policy(limits, execution)

    assert policy.partial_take_profit_pct == 0.03
    assert policy.partial_take_profit_size == 0.4
    assert policy.trailing_stop_pct == 0.02
    assert policy.fail_on_unprotected_exits is True


def test_synthetic_exit_engine_generates_partial_tp():
    portfolio = _portfolio_with_position("SPY", 10, 100.0)
    states = {"SPY": IntradayPositionState(symbol="SPY", entry_price=100.0)}
    policy = build_exit_policy(
        RiskLimits(
            partial_take_profit_enabled=True,
            partial_take_profit_pct=0.02,
            partial_take_profit_size=0.5,
            trailing_stop_enabled=True,
            trailing_stop_pct=0.015,
        ),
        ExecutionConfig(),
    )
    runtime = build_exit_runtime(
        "paper",
        ExecutionConfig(intraday_enabled=True, intraday_use_oco=False),
    )

    signals, telemetry = evaluate_position_exits(
        portfolio,
        {"SPY": 102.0},
        states,
        policy,
        runtime,
    )

    assert len(signals) == 1
    assert signals[0].exit_reason == "tp_partial"
    assert telemetry[0].exit_mode == "synthetic"
    assert telemetry[0].partial_target_price == 102.0
    assert telemetry[0].synthetic_exit_parity_tier == "tier1_close_only"


def test_synthetic_exit_engine_short_stop_loss_triggers_on_price_rise():
    portfolio = _portfolio_with_short_position("SPY", 10, 100.0)
    states = {
        "SPY": IntradayPositionState(symbol="SPY", entry_price=100.0, peak_price=100.0)
    }
    policy = build_exit_policy(RiskLimits(), ExecutionConfig())
    runtime = build_exit_runtime(
        "paper",
        ExecutionConfig(intraday_enabled=True, intraday_use_oco=False),
    )

    signals, _telemetry = evaluate_position_exits(
        portfolio,
        {"SPY": 105.0},
        states,
        policy,
        runtime,
    )

    assert len(signals) == 1
    assert signals[0].action.value == "cover"
    assert signals[0].exit_reason == "stop_loss"


def test_synthetic_exit_engine_short_partial_tp_triggers_on_price_drop():
    portfolio = _portfolio_with_short_position("SPY", 10, 100.0)
    states = {
        "SPY": IntradayPositionState(symbol="SPY", entry_price=100.0, peak_price=100.0)
    }
    policy = build_exit_policy(
        RiskLimits(
            partial_take_profit_enabled=True,
            partial_take_profit_pct=0.02,
            partial_take_profit_size=0.5,
        ),
        ExecutionConfig(),
    )
    runtime = build_exit_runtime(
        "paper",
        ExecutionConfig(intraday_enabled=True, intraday_use_oco=False),
    )

    signals, telemetry = evaluate_position_exits(
        portfolio,
        {"SPY": 98.0},
        states,
        policy,
        runtime,
    )

    assert len(signals) == 1
    assert signals[0].action.value == "cover"
    assert signals[0].exit_reason == "tp_partial"
    assert telemetry[0].partial_target_price == 98.0


def test_synthetic_exit_engine_short_trailing_stop_after_partial():
    portfolio = _portfolio_with_short_position("SPY", 10, 100.0)
    states = {
        "SPY": IntradayPositionState(
            symbol="SPY",
            entry_price=100.0,
            peak_price=96.0,
            partial_exit_taken=True,
        )
    }
    policy = build_exit_policy(
        RiskLimits(
            trailing_stop_enabled=True,
            trailing_stop_pct=0.015,
        ),
        ExecutionConfig(),
    )
    runtime = build_exit_runtime(
        "paper",
        ExecutionConfig(intraday_enabled=True, intraday_use_oco=False),
    )

    signals, telemetry = evaluate_position_exits(
        portfolio,
        {"SPY": 97.5},
        states,
        policy,
        runtime,
    )

    assert len(signals) == 1
    assert signals[0].action.value == "cover"
    assert signals[0].exit_reason == "trailing_stop"
    assert telemetry[0].trailing_stop_price == 97.44


def test_tier2_short_same_bar_stop_and_tp_resolves_to_cover_stop():
    portfolio = _portfolio_with_short_position("SPY", 10, 100.0)
    states = {"SPY": IntradayPositionState(symbol="SPY", entry_price=100.0)}
    policy = build_exit_policy(
        RiskLimits(
            partial_take_profit_enabled=True,
            partial_take_profit_pct=0.02,
            partial_take_profit_size=0.5,
        ),
        ExecutionConfig(),
    )

    signal = evaluate_synthetic_exit(
        SyntheticExitContext(
            position=portfolio.positions["SPY"],
            price=99.5,
            nav=portfolio.nav,
            state=states["SPY"],
            bar_high=105.5,
            bar_low=97.5,
            parity_tier="tier2_ohlc_conservative",
        ),
        policy,
    )

    assert signal is not None
    assert signal.exit_reason == "stop_loss"
    assert signal.action.value == "cover"


def test_tier2_ohlc_partial_tp_can_trigger_without_close_reaching_target():
    portfolio = _portfolio_with_position("SPY", 10, 100.0)
    states = {"SPY": IntradayPositionState(symbol="SPY", entry_price=100.0)}
    policy = build_exit_policy(
        RiskLimits(
            partial_take_profit_enabled=True,
            partial_take_profit_pct=0.02,
            partial_take_profit_size=0.5,
        ),
        ExecutionConfig(),
    )
    runtime = build_exit_runtime(
        "paper",
        ExecutionConfig(intraday_enabled=True, intraday_use_oco=False),
    )

    signals, telemetry = evaluate_position_exits(
        portfolio,
        {"SPY": 101.0},
        states,
        policy,
        runtime,
        bar_highs={"SPY": 102.5},
        bar_lows={"SPY": 99.0},
        parity_tier="tier2_ohlc_conservative",
    )

    assert len(signals) == 1
    assert signals[0].exit_reason == "tp_partial"
    assert telemetry[0].synthetic_exit_parity_tier == "tier2_ohlc_conservative"
    assert (
        "conservative daily OHLC reachability"
        in telemetry[0].synthetic_exit_execution_assumption
    )


def test_tier2_same_bar_stop_and_tp_resolves_pessimistically_to_stop():
    portfolio = _portfolio_with_position("SPY", 10, 100.0)
    states = {"SPY": IntradayPositionState(symbol="SPY", entry_price=100.0)}
    policy = build_exit_policy(
        RiskLimits(
            partial_take_profit_enabled=True,
            partial_take_profit_pct=0.02,
            partial_take_profit_size=0.5,
        ),
        ExecutionConfig(),
    )

    signal = evaluate_synthetic_exit(
        SyntheticExitContext(
            position=portfolio.positions["SPY"],
            price=100.5,
            nav=portfolio.nav,
            state=states["SPY"],
            bar_high=102.5,
            bar_low=94.5,
            parity_tier="tier2_ohlc_conservative",
        ),
        policy,
    )

    assert signal is not None
    assert signal.exit_reason == "stop_loss"
    assert signal.action.value == "close"
    assert "pessimistically resolving to stop-loss" in signal.reasoning


def test_tier1_close_only_does_not_trigger_intrabar_partial_tp():
    portfolio = _portfolio_with_position("SPY", 10, 100.0)
    states = {"SPY": IntradayPositionState(symbol="SPY", entry_price=100.0)}
    policy = build_exit_policy(
        RiskLimits(
            partial_take_profit_enabled=True,
            partial_take_profit_pct=0.02,
            partial_take_profit_size=0.5,
        ),
        ExecutionConfig(),
    )
    runtime = build_exit_runtime(
        "paper",
        ExecutionConfig(intraday_enabled=True, intraday_use_oco=False),
    )

    signals, telemetry = evaluate_position_exits(
        portfolio,
        {"SPY": 101.0},
        states,
        policy,
        runtime,
        bar_highs={"SPY": 102.5},
        bar_lows={"SPY": 99.0},
        parity_tier="tier1_close_only",
    )

    assert signals == []
    assert telemetry[0].synthetic_exit_parity_tier == "tier1_close_only"
    assert (
        telemetry[0].synthetic_exit_execution_assumption
        == synthetic_exit_execution_assumption()
    )


def test_native_exit_engine_emits_telemetry_without_signals():
    portfolio = _portfolio_with_position("SPY", 10, 100.0)
    states = {"SPY": IntradayPositionState(symbol="SPY", entry_price=100.0)}
    policy = build_exit_policy(RiskLimits(), ExecutionConfig())
    runtime = build_exit_runtime(
        "alpaca",
        ExecutionConfig(intraday_enabled=False, intraday_use_oco=True),
    )

    signals, telemetry = evaluate_position_exits(
        portfolio,
        {"SPY": 103.0},
        states,
        policy,
        runtime,
    )

    assert signals == []
    assert len(telemetry) == 1
    assert telemetry[0].broker_exit_kind == "bracket"


def test_broker_exit_plan_for_oco_runtime_contains_partial_and_trailing():
    policy = build_exit_policy(
        RiskLimits(
            partial_take_profit_enabled=True,
            partial_take_profit_pct=0.02,
            partial_take_profit_size=0.5,
            remainder_take_profit_mult=2.0,
            trailing_stop_enabled=True,
            trailing_stop_pct=0.015,
        ),
        ExecutionConfig(),
    )
    runtime = build_exit_runtime(
        "alpaca",
        ExecutionConfig(intraday_enabled=True, intraday_use_oco=True),
    )

    plan = build_broker_exit_plan("SPY", 100.0, 95.0, policy, runtime)

    assert plan.kind == "oco"
    assert plan.partial_take_profit_price == 102.0
    assert plan.remainder_take_profit_price == 104.0
    assert plan.trailing_stop_pct == 0.015


def test_broker_exit_plan_for_short_runtime_places_targets_below_entry():
    policy = build_exit_policy(
        RiskLimits(
            take_profit_mode="pct",
            take_profit_pct=0.03,
            partial_take_profit_enabled=True,
            partial_take_profit_pct=0.02,
            partial_take_profit_size=0.5,
            remainder_take_profit_mult=2.0,
            trailing_stop_enabled=True,
            trailing_stop_pct=0.015,
        ),
        ExecutionConfig(),
    )
    runtime = build_exit_runtime(
        "alpaca",
        ExecutionConfig(intraday_enabled=True, intraday_use_oco=True),
    )

    plan = build_broker_exit_plan(
        "SPY",
        100.0,
        105.0,
        policy,
        runtime,
        is_short=True,
    )

    assert plan.take_profit == 97.0
    assert plan.partial_take_profit_price == 98.0
    assert plan.remainder_take_profit_price == 96.0
    assert plan.stop_loss == 105.0


def test_resolve_take_profit_price_rr_mode():
    policy = build_exit_policy(
        RiskLimits(take_profit_mode="rr", take_profit_rr=3.0),
        ExecutionConfig(),
    )
    assert resolve_take_profit_price(100.0, 95.0, policy) == 115.0


def test_resolve_take_profit_price_rr_mode_for_short():
    policy = build_exit_policy(
        RiskLimits(take_profit_mode="rr", take_profit_rr=3.0),
        ExecutionConfig(),
    )
    assert resolve_take_profit_price(100.0, 105.0, policy, is_short=True) == 85.0


def test_eod_flatten_due_after_cutoff():
    policy = build_exit_policy(
        RiskLimits(eod_flatten_enabled=True, eod_flatten_time="15:55"),
        ExecutionConfig(),
    )
    decision = assess_eod_flatten(
        policy,
        datetime(2026, 3, 31, 15, 56, tzinfo=UTC),
        market_is_open=True,
    )

    assert decision.due is True
    assert decision.reason == "due"


def test_eod_flatten_disabled_for_crypto_runtime():
    policy = build_exit_policy(
        RiskLimits(eod_flatten_enabled=True, eod_flatten_time="15:55"),
        ExecutionConfig(asset_class_filter=["crypto"]),
    )
    runtime = build_exit_runtime(
        "alpaca",
        ExecutionConfig(
            intraday_enabled=True, intraday_use_oco=True, asset_class_filter=["crypto"]
        ),
    )

    decision = assess_eod_flatten(
        policy,
        datetime(2026, 3, 31, 23, 56, tzinfo=UTC),
        market_is_open=True,
        runtime=runtime,
    )

    assert decision.enabled is False
    assert decision.due is False
    assert decision.reason == "disabled_for_crypto"


def test_parse_eod_time_rejects_invalid_value():
    try:
        parse_eod_time("bad")
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError for invalid EOD time")


def test_exit_telemetry_payload_contains_policy_and_position_data():
    portfolio = _portfolio_with_position("SPY", 10, 100.0)
    states = {"SPY": IntradayPositionState(symbol="SPY", entry_price=100.0)}
    policy = build_exit_policy(RiskLimits(), ExecutionConfig())
    runtime = build_exit_runtime("alpaca", ExecutionConfig(intraday_enabled=False))
    _signals, telemetry = evaluate_position_exits(
        portfolio,
        {"SPY": 101.0},
        states,
        policy,
        runtime,
    )

    payload = build_exit_telemetry_payload(telemetry, policy, runtime)

    assert (
        payload["exit_engine"]["policy"]["take_profit_mode"] == policy.take_profit_mode
    )
    assert (
        payload["exit_engine"]["runtime"]["broker_exit_kind"]
        == runtime.broker_exit_kind
    )
    assert payload["exit_engine"]["positions"][0]["symbol"] == "SPY"
    assert (
        payload["exit_engine"]["parity"]["synthetic_exit_parity_mode"]
        == synthetic_exit_parity_mode()
    )
    assert (
        payload["exit_engine"]["parity"]["synthetic_exit_execution_assumption"]
        == synthetic_exit_execution_assumption()
    )


def test_exit_telemetry_marks_native_crypto_without_stop_as_unprotected():
    portfolio = _portfolio_with_position("BTC-USD", 0.25, 40000.0)
    portfolio.positions["BTC-USD"].stop_loss = 0.0
    states = {"BTC-USD": IntradayPositionState(symbol="BTC-USD", entry_price=40000.0)}
    policy = build_exit_policy(
        RiskLimits(), ExecutionConfig(asset_class_filter=["crypto"])
    )
    runtime = build_exit_runtime(
        "alpaca",
        ExecutionConfig(
            intraday_enabled=True, intraday_use_oco=True, asset_class_filter=["crypto"]
        ),
    )

    _signals, telemetry = evaluate_position_exits(
        portfolio,
        {"BTC-USD": 40500.0},
        states,
        policy,
        runtime,
    )

    assert telemetry[0].unprotected is True
    assert telemetry[0].broker_exit_kind == "oco"
