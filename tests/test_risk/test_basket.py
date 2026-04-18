"""Tests for crypto basket equal-weight sizing normalizer."""

import pytest

from llm_quant.brain.models import Action, Conviction, TradeSignal
from llm_quant.config import RiskLimits
from llm_quant.risk.basket import normalize_crypto_basket_weights


def _signal(symbol: str, action: Action, target_weight: float) -> TradeSignal:
    return TradeSignal(
        symbol=symbol,
        action=action,
        conviction=Conviction.MEDIUM,
        target_weight=target_weight,
        stop_loss=0.0,
        reasoning="test",
    )


@pytest.fixture
def limits_enabled() -> RiskLimits:
    lim = RiskLimits()
    lim.crypto_basket_equal_weight = True
    lim.crypto_basket_target_weight = 0.03
    return lim


@pytest.fixture
def limits_disabled() -> RiskLimits:
    lim = RiskLimits()
    lim.crypto_basket_equal_weight = False
    lim.crypto_basket_target_weight = 0.03
    return lim


@pytest.fixture
def asset_map() -> dict[str, str]:
    return {
        "XRPUSD": "crypto",
        "ETHUSD": "crypto",
        "SOLUSD": "crypto",
        "SPY": "equity",
    }


class TestNormalizeCryptoBasketWeights:
    def test_clamps_oversized_crypto_buy(self, limits_enabled, asset_map):
        signals = [_signal("XRPUSD", Action.BUY, 0.07)]
        result = normalize_crypto_basket_weights(signals, limits_enabled, asset_map)
        assert result[0].target_weight == pytest.approx(0.03)

    def test_leaves_undersized_crypto_buy_unchanged(self, limits_enabled, asset_map):
        signals = [_signal("ETHUSD", Action.BUY, 0.02)]
        result = normalize_crypto_basket_weights(signals, limits_enabled, asset_map)
        assert result[0].target_weight == pytest.approx(0.02)

    def test_does_not_clamp_crypto_sell(self, limits_enabled, asset_map):
        signals = [_signal("SOLUSD", Action.SELL, 0.08)]
        result = normalize_crypto_basket_weights(signals, limits_enabled, asset_map)
        assert result[0].target_weight == pytest.approx(0.08)

    def test_does_not_clamp_crypto_close(self, limits_enabled, asset_map):
        signals = [_signal("XRPUSD", Action.CLOSE, 0.0)]
        result = normalize_crypto_basket_weights(signals, limits_enabled, asset_map)
        assert result[0].target_weight == pytest.approx(0.0)

    def test_does_not_clamp_equity_buy(self, limits_enabled, asset_map):
        signals = [_signal("SPY", Action.BUY, 0.10)]
        result = normalize_crypto_basket_weights(signals, limits_enabled, asset_map)
        assert result[0].target_weight == pytest.approx(0.10)

    def test_disabled_flag_leaves_all_weights_unchanged(
        self, limits_disabled, asset_map
    ):
        signals = [
            _signal("XRPUSD", Action.BUY, 0.07),
            _signal("ETHUSD", Action.BUY, 0.05),
        ]
        result = normalize_crypto_basket_weights(signals, limits_disabled, asset_map)
        assert result[0].target_weight == pytest.approx(0.07)
        assert result[1].target_weight == pytest.approx(0.05)

    def test_mixed_basket_all_crypto_buys_clamped(self, limits_enabled, asset_map):
        signals = [
            _signal("XRPUSD", Action.BUY, 0.07),
            _signal("ETHUSD", Action.BUY, 0.05),
            _signal("SOLUSD", Action.BUY, 0.015),
        ]
        result = normalize_crypto_basket_weights(signals, limits_enabled, asset_map)
        assert result[0].target_weight == pytest.approx(0.03)  # clamped
        assert result[1].target_weight == pytest.approx(0.03)  # clamped
        assert result[2].target_weight == pytest.approx(0.015)  # already under target

    def test_unknown_symbol_treated_as_equity(self, limits_enabled, asset_map):
        signals = [_signal("UNKNOWN", Action.BUY, 0.08)]
        result = normalize_crypto_basket_weights(signals, limits_enabled, asset_map)
        # No entry in asset_map → defaults to "equity" → not clamped
        assert result[0].target_weight == pytest.approx(0.08)

    def test_returns_same_list_object(self, limits_enabled, asset_map):
        signals = [_signal("XRPUSD", Action.BUY, 0.07)]
        result = normalize_crypto_basket_weights(signals, limits_enabled, asset_map)
        assert result is signals
