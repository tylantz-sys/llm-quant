from llm_quant.broker.executor import bracket_prices_valid, resolve_take_profit
from llm_quant.config import RiskLimits


def test_resolve_take_profit_pct() -> None:
    limits = RiskLimits(take_profit_mode="pct", take_profit_pct=0.03)
    tp = resolve_take_profit(127.0, 120.0, limits)
    assert tp == 130.81


def test_resolve_take_profit_rr() -> None:
    limits = RiskLimits(take_profit_mode="rr", take_profit_rr=2.0)
    tp = resolve_take_profit(127.0, 120.0, limits)
    assert tp == 141.0


def test_bracket_prices_valid() -> None:
    assert bracket_prices_valid(127.0, 120.0, 130.0)
    assert not bracket_prices_valid(127.0, 0.0, 130.0)
    assert not bracket_prices_valid(127.0, 120.0, 120.0)
    assert not bracket_prices_valid(127.0, 130.0, 129.0)
