from datetime import UTC, datetime

from llm_quant.broker.alpaca import AlpacaError
from llm_quant.config import ExecutionConfig, RiskLimits
from llm_quant.trading.exits import (
    assess_eod_flatten,
    build_broker_exit_plan,
    build_exit_policy,
    build_exit_runtime,
    build_exit_telemetry_payload,
    evaluate_position_exits,
    parse_eod_time,
    resolve_take_profit_price,
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


def test_resolve_take_profit_price_rr_mode():
    policy = build_exit_policy(
        RiskLimits(take_profit_mode="rr", take_profit_rr=3.0),
        ExecutionConfig(),
    )
    assert resolve_take_profit_price(100.0, 95.0, policy) == 115.0


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

    assert payload["exit_engine"]["policy"]["take_profit_mode"] == policy.take_profit_mode
    assert payload["exit_engine"]["runtime"]["broker_exit_kind"] == runtime.broker_exit_kind
    assert payload["exit_engine"]["positions"][0]["symbol"] == "SPY"
