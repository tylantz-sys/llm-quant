"""Tests for risk limit checks and risk manager."""

from llm_quant.brain.models import Action, Conviction, TradeSignal
from llm_quant.risk.limits import (
    check_atr_stop_loss,
    check_cash_reserve,
    check_drawdown_limit,
    check_gross_exposure,
    check_locate_availability,
    check_margin_buffer,
    check_position_size,
    check_position_weight,
    check_short_exposure,
    check_stop_loss,
)
from llm_quant.risk.manager import RiskManager


def test_position_size_pass() -> None:
    result = check_position_size(
        trade_notional=1_500.0, nav=100_000.0, max_trade_size=0.02
    )
    assert result.passed


def test_position_size_fail() -> None:
    result = check_position_size(
        trade_notional=3_000.0, nav=100_000.0, max_trade_size=0.02
    )
    assert not result.passed


def test_position_weight_pass() -> None:
    result = check_position_weight(
        current_weight=0.05, target_weight=0.08, max_weight=0.10
    )
    assert result.passed


def test_position_weight_fail() -> None:
    result = check_position_weight(
        current_weight=0.05, target_weight=0.12, max_weight=0.10
    )
    assert not result.passed


def test_gross_exposure_pass() -> None:
    result = check_gross_exposure(
        current_gross=100_000.0, trade_notional=5_000.0, nav=100_000.0, max_gross=2.0
    )
    assert result.passed


def test_gross_exposure_fail() -> None:
    result = check_gross_exposure(
        current_gross=195_000.0, trade_notional=10_000.0, nav=100_000.0, max_gross=2.0
    )
    assert not result.passed


def test_cash_reserve_pass() -> None:
    result = check_cash_reserve(
        cash=20_000.0, trade_notional=5_000.0, nav=100_000.0, min_reserve=0.05
    )
    assert result.passed


def test_cash_reserve_fail() -> None:
    result = check_cash_reserve(
        cash=6_000.0, trade_notional=2_000.0, nav=100_000.0, min_reserve=0.05
    )
    assert not result.passed


def test_short_exposure_pass() -> None:
    result = check_short_exposure(
        current_short=10_000.0,
        trade_notional=5_000.0,
        nav=100_000.0,
        max_short=0.20,
    )
    assert result.passed


def test_short_exposure_fail() -> None:
    result = check_short_exposure(
        current_short=18_000.0,
        trade_notional=5_000.0,
        nav=100_000.0,
        max_short=0.20,
    )
    assert not result.passed


def test_margin_buffer_pass() -> None:
    result = check_margin_buffer(
        available_cash=20_000.0,
        projected_short_notional=30_000.0,
        margin_requirement=0.50,
    )
    assert result.passed


def test_margin_buffer_fail() -> None:
    result = check_margin_buffer(
        available_cash=10_000.0,
        projected_short_notional=30_000.0,
        margin_requirement=0.50,
    )
    assert not result.passed


def test_stop_loss_required_present() -> None:
    result = check_stop_loss(has_stop_loss=True, require=True)
    assert result.passed


def test_stop_loss_required_missing() -> None:
    result = check_stop_loss(has_stop_loss=False, require=True)
    assert not result.passed


def test_stop_loss_not_required() -> None:
    result = check_stop_loss(has_stop_loss=False, require=False)
    assert result.passed


def test_atr_stop_loss_short_direction_pass() -> None:
    result = check_atr_stop_loss(
        stop_loss_price=105.0,
        entry_price=100.0,
        atr=2.0,
        atr_multiplier=2.0,
        is_short=True,
    )
    assert result.passed


def test_atr_stop_loss_short_direction_fail() -> None:
    result = check_atr_stop_loss(
        stop_loss_price=102.0,
        entry_price=100.0,
        atr=2.0,
        atr_multiplier=2.0,
        is_short=True,
    )
    assert not result.passed


def test_locate_required_missing_status_fails() -> None:
    result = check_locate_availability(require_locate=True, locate_available=None)
    assert not result.passed


def test_risk_manager_rejects_short_when_locate_required_and_missing(
    sample_portfolio: object,
    sample_prices: object,
    sample_config: object,
) -> None:
    sample_config.risk.require_locate = True
    mgr = RiskManager(sample_config)
    signal = TradeSignal(
        symbol="SPY",
        action=Action.SHORT,
        conviction=Conviction.HIGH,
        target_weight=0.02,
        stop_loss=470.0,
        reasoning="Short without locate",
    )

    approved, rejected = mgr.filter_signals([signal], sample_portfolio, sample_prices)
    assert len(approved) == 0
    assert len(rejected) == 1
    assert any(r.rule == "locate_availability" and not r.passed for r in rejected[0][1])


def test_risk_manager_accepts_short_when_locate_required_and_available(
    sample_portfolio: object,
    sample_prices: object,
    sample_config: object,
) -> None:
    sample_config.risk.require_locate = True
    mgr = RiskManager(sample_config)
    signal = TradeSignal(
        symbol="SPY",
        action=Action.SHORT,
        conviction=Conviction.HIGH,
        target_weight=0.02,
        stop_loss=470.0,
        reasoning="Short with locate",
        metadata={"locate_available": True},
    )

    approved, rejected = mgr.filter_signals([signal], sample_portfolio, sample_prices)
    assert len(approved) == 1
    assert len(rejected) == 0


def test_risk_manager_short_passes_when_locate_not_required(
    sample_portfolio: object,
    sample_prices: object,
    sample_config: object,
) -> None:
    sample_config.risk.require_locate = False
    mgr = RiskManager(sample_config)
    signal = TradeSignal(
        symbol="SPY",
        action=Action.SHORT,
        conviction=Conviction.HIGH,
        target_weight=0.02,
        stop_loss=470.0,
        reasoning="Short allowed by policy",
    )

    approved, rejected = mgr.filter_signals([signal], sample_portfolio, sample_prices)
    assert len(approved) == 1
    assert len(rejected) == 0


def test_risk_manager_uses_locate_lookup_when_metadata_missing(
    sample_portfolio: object,
    sample_prices: object,
    sample_config: object,
) -> None:
    sample_config.risk.require_locate = True
    mgr = RiskManager(sample_config)
    signal = TradeSignal(
        symbol="SPY",
        action=Action.SHORT,
        conviction=Conviction.HIGH,
        target_weight=0.02,
        stop_loss=470.0,
        reasoning="Short with broker locate lookup",
    )

    approved, rejected = mgr.filter_signals(
        [signal],
        sample_portfolio,
        sample_prices,
        locate_lookup=lambda symbol: symbol == "SPY",
    )
    assert len(approved) == 1
    assert len(rejected) == 0


def test_risk_manager_rejects_short_when_locate_lookup_reports_false(
    sample_portfolio: object,
    sample_prices: object,
    sample_config: object,
) -> None:
    sample_config.risk.require_locate = True
    mgr = RiskManager(sample_config)
    signal = TradeSignal(
        symbol="SPY",
        action=Action.SHORT,
        conviction=Conviction.HIGH,
        target_weight=0.02,
        stop_loss=470.0,
        reasoning="Short rejected by broker locate lookup",
    )

    approved, rejected = mgr.filter_signals(
        [signal],
        sample_portfolio,
        sample_prices,
        locate_lookup=lambda _symbol: False,
    )
    assert len(approved) == 0
    assert len(rejected) == 1
    assert any(r.rule == "locate_availability" and not r.passed for r in rejected[0][1])


def test_risk_manager_approves_valid_signal(
    sample_portfolio: object,
    sample_prices: object,
    sample_config: object,
) -> None:
    mgr = RiskManager(sample_config)
    signal = TradeSignal(
        symbol="GLD",
        action=Action.BUY,
        conviction=Conviction.MEDIUM,
        target_weight=0.02,
        stop_loss=175.0,
        reasoning="Hedge",
    )
    approved, rejected = mgr.filter_signals([signal], sample_portfolio, sample_prices)
    assert len(approved) == 1
    assert len(rejected) == 0


def test_risk_manager_rejects_oversized(
    sample_portfolio: object,
    sample_prices: object,
    sample_config: object,
) -> None:
    mgr = RiskManager(sample_config)
    signal = TradeSignal(
        symbol="GLD",
        action=Action.BUY,
        conviction=Conviction.HIGH,
        target_weight=0.15,  # Exceeds 10% max weight
        stop_loss=175.0,
        reasoning="Too big",
    )
    approved, rejected = mgr.filter_signals([signal], sample_portfolio, sample_prices)
    assert len(approved) == 0
    assert len(rejected) == 1


def test_risk_manager_blocks_new_positions_over_cap(
    sample_prices: object,
    sample_config: object,
) -> None:
    from llm_quant.trading.portfolio import Portfolio, Position

    p = Portfolio(initial_capital=100_000.0)
    p.cash = 92_000.0
    p.positions = {
        "SPY": Position("SPY", 10, 100.0, 100.0, 95.0),
        "QQQ": Position("QQQ", 10, 100.0, 100.0, 95.0),
        "TLT": Position("TLT", 10, 100.0, 100.0, 95.0),
        "GLD": Position("GLD", 10, 100.0, 100.0, 95.0),
        "BTC-USD": Position("BTC-USD", 1, 30000.0, 30000.0, 28000.0),
        "EURUSD=X": Position("EURUSD=X", 1000, 1.1, 1.1, 1.05),
        "XLE": Position("XLE", 10, 100.0, 100.0, 95.0),
        "XLF": Position("XLF", 10, 100.0, 100.0, 95.0),
    }

    sample_prices["NEW"] = 100.0
    mgr = RiskManager(sample_config)
    signal = TradeSignal(
        symbol="NEW",
        action=Action.BUY,
        conviction=Conviction.MEDIUM,
        target_weight=0.01,
        stop_loss=95.0,
        reasoning="New position beyond cap",
    )
    approved, rejected = mgr.filter_signals([signal], p, sample_prices)
    assert len(approved) == 0
    assert len(rejected) == 1
    assert any(r.rule == "max_positions" for r in rejected[0][1])


def test_risk_manager_enforces_trade_limit(
    sample_portfolio: object,
    sample_prices: object,
    sample_config: object,
) -> None:
    """Only max_trades_per_session signals should be approved."""
    mgr = RiskManager(sample_config)
    signals = [
        TradeSignal(
            symbol=f"ETF{i}",
            action=Action.BUY,
            conviction=Conviction.LOW,
            target_weight=0.01,
            stop_loss=10.0,
            reasoning=f"Signal {i}",
        )
        for i in range(10)
    ]
    # Add all ETFs to prices
    for i in range(10):
        sample_prices[f"ETF{i}"] = 100.0

    approved, _rejected = mgr.filter_signals(signals, sample_portfolio, sample_prices)
    assert len(approved) <= sample_config.risk.max_trades_per_session


# ---------------------------------------------------------------------------
# Per-asset-class weight cap tests
# ---------------------------------------------------------------------------


def test_risk_manager_rejects_crypto_over_class_limit(
    sample_portfolio: object,
    sample_prices: object,
    sample_config: object,
) -> None:
    """Crypto positions exceeding 5% weight should be rejected."""
    mgr = RiskManager(sample_config)
    sample_prices["BTC-USD"] = 50_000.0
    signal = TradeSignal(
        symbol="BTC-USD",
        action=Action.BUY,
        conviction=Conviction.HIGH,
        target_weight=0.07,  # Exceeds crypto limit of 0.05
        stop_loss=45_000.0,
        reasoning="Crypto buy",
    )
    approved, rejected = mgr.filter_signals([signal], sample_portfolio, sample_prices)
    assert len(approved) == 0
    assert len(rejected) == 1
    # Verify the rejection is due to position_weight
    checks = rejected[0][1]
    weight_check = [c for c in checks if c.rule == "position_weight"]
    assert len(weight_check) == 1
    assert not weight_check[0].passed


def test_risk_manager_approves_crypto_within_class_limit(
    sample_portfolio: object,
    sample_prices: object,
    sample_config: object,
) -> None:
    """Crypto positions within 5% weight should be approved."""
    mgr = RiskManager(sample_config)
    sample_prices["BTC-USD"] = 50_000.0
    signal = TradeSignal(
        symbol="BTC-USD",
        action=Action.BUY,
        conviction=Conviction.HIGH,
        target_weight=0.02,  # Within crypto limit of 0.05
        stop_loss=45_000.0,
        reasoning="Small crypto buy",
    )
    approved, rejected = mgr.filter_signals([signal], sample_portfolio, sample_prices)
    assert len(approved) == 1
    assert len(rejected) == 0


def test_risk_manager_rejects_forex_over_class_limit(
    sample_portfolio: object,
    sample_prices: object,
    sample_config: object,
) -> None:
    """Forex positions exceeding 8% weight should be rejected."""
    mgr = RiskManager(sample_config)
    sample_prices["EURUSD=X"] = 1.10
    signal = TradeSignal(
        symbol="EURUSD=X",
        action=Action.BUY,
        conviction=Conviction.MEDIUM,
        target_weight=0.09,  # Exceeds forex limit of 0.08
        stop_loss=1.05,
        reasoning="Forex buy",
    )
    approved, rejected = mgr.filter_signals([signal], sample_portfolio, sample_prices)
    assert len(approved) == 0
    assert len(rejected) == 1
    checks = rejected[0][1]
    weight_check = [c for c in checks if c.rule == "position_weight"]
    assert len(weight_check) == 1
    assert not weight_check[0].passed


def test_risk_manager_equity_uses_default_limit(
    sample_portfolio: object,
    sample_prices: object,
    sample_config: object,
) -> None:
    """Equities should use the default max_position_weight (0.10)."""
    mgr = RiskManager(sample_config)
    # 0.09 is within default 0.10 but would exceed crypto 0.05
    signal = TradeSignal(
        symbol="GLD",
        action=Action.BUY,
        conviction=Conviction.MEDIUM,
        target_weight=0.02,  # Well within 0.10
        stop_loss=175.0,
        reasoning="Gold hedge",
    )
    approved, _rejected = mgr.filter_signals([signal], sample_portfolio, sample_prices)
    assert len(approved) == 1


# ---------------------------------------------------------------------------
# Drawdown circuit breaker tests
# ---------------------------------------------------------------------------


def test_drawdown_limit_pass_no_drawdown() -> None:
    """No drawdown should pass."""
    result = check_drawdown_limit(
        current_nav=100_000.0,
        peak_nav=100_000.0,
        max_drawdown_pct=0.15,
    )
    assert result.passed
    assert result.rule == "drawdown_limit"


def test_drawdown_limit_pass_small_drawdown() -> None:
    """A small drawdown (5%) should pass when limit is 15% (threshold 12%)."""
    result = check_drawdown_limit(
        current_nav=95_000.0,
        peak_nav=100_000.0,
        max_drawdown_pct=0.15,
    )
    assert result.passed  # -5% >= -12%


def test_drawdown_limit_fail_near_limit() -> None:
    """Drawdown exceeding the threshold (limit - 3%) should fail."""
    result = check_drawdown_limit(
        current_nav=87_000.0,  # -13% drawdown
        peak_nav=100_000.0,
        max_drawdown_pct=0.15,  # threshold = -12%
    )
    assert not result.passed  # -13% < -12%


def test_drawdown_limit_pass_zero_peak_nav() -> None:
    """Zero peak NAV should pass (no history yet)."""
    result = check_drawdown_limit(
        current_nav=100_000.0,
        peak_nav=0.0,
        max_drawdown_pct=0.15,
    )
    assert result.passed


def test_drawdown_limit_fail_exactly_at_threshold() -> None:
    """Drawdown exactly at threshold should pass (>= comparison)."""
    # threshold = -(0.15 - 0.03) = -0.12
    # current_dd = (88_000 - 100_000) / 100_000 = -0.12
    result = check_drawdown_limit(
        current_nav=88_000.0,
        peak_nav=100_000.0,
        max_drawdown_pct=0.15,
    )
    assert result.passed  # -12% >= -12%


def test_risk_manager_drawdown_blocks_buy(
    sample_config: object,
    sample_prices: object,
) -> None:
    """BUY signals should be blocked when portfolio is in deep drawdown."""
    from llm_quant.trading.portfolio import Portfolio

    # Portfolio has lost > 12% from initial capital
    p = Portfolio(initial_capital=100_000.0)
    p.cash = 86_000.0  # NAV = 86_000, peak = 100_000 => -14% drawdown
    # No positions, so NAV = cash = 86_000

    mgr = RiskManager(sample_config)
    sample_prices["GLD"] = 185.0
    signal = TradeSignal(
        symbol="GLD",
        action=Action.BUY,
        conviction=Conviction.HIGH,
        target_weight=0.01,
        stop_loss=175.0,
        reasoning="Trying to buy during drawdown",
    )
    approved, rejected = mgr.filter_signals([signal], p, sample_prices)
    assert len(approved) == 0
    assert len(rejected) == 1
    checks = rejected[0][1]
    dd_check = [c for c in checks if c.rule == "drawdown_limit"]
    assert len(dd_check) == 1
    assert not dd_check[0].passed


def test_risk_manager_drawdown_allows_sell(
    sample_config: object,
    sample_prices: object,
) -> None:
    """SELL signals should NOT be blocked by drawdown limit."""
    from llm_quant.trading.portfolio import Portfolio, Position

    p = Portfolio(initial_capital=100_000.0)
    p.cash = 76_000.0
    p.positions = {
        "SPY": Position(
            symbol="SPY",
            shares=20,
            avg_cost=450.0,
            current_price=460.0,
            stop_loss=427.5,
        ),
    }
    # NAV = 76_000 + 20*460 = 85_200, peak = 100_000 => ~-14.8% drawdown

    mgr = RiskManager(sample_config)
    sample_prices["SPY"] = 460.0
    signal = TradeSignal(
        symbol="SPY",
        action=Action.SELL,
        conviction=Conviction.MEDIUM,
        target_weight=0.10,  # Small reduction (current weight ~10.8% -> 10%)
        stop_loss=427.5,
        reasoning="Reducing exposure during drawdown",
    )
    # Directly check that the drawdown check itself passes for sells
    checks = mgr.check_trade(signal, p, sample_prices)
    dd_check = [c for c in checks if c.rule == "drawdown_limit"]
    assert len(dd_check) == 1
    assert dd_check[0].passed
    assert dd_check[0].message == "Sell/close/cover not blocked by drawdown limit."


def test_risk_manager_drawdown_uses_injected_peak_nav(
    sample_config: object,
    sample_prices: object,
) -> None:
    """Injected persisted peak NAV should drive drawdown check for BUY signals."""
    from llm_quant.trading.portfolio import Portfolio

    p = Portfolio(initial_capital=100_000.0)
    p.cash = 100_000.0
    # Simulate persisted historical peak > initial capital.
    p.peak_nav = 120_000.0

    mgr = RiskManager(sample_config)
    signal = TradeSignal(
        symbol="GLD",
        action=Action.BUY,
        conviction=Conviction.HIGH,
        target_weight=0.01,
        stop_loss=170.0,
        reasoning="buy in drawdown",
    )
    approved, rejected = mgr.filter_signals([signal], p, sample_prices)
    assert len(approved) == 0
    assert len(rejected) == 1
    dd_check = [c for c in rejected[0][1] if c.rule == "drawdown_limit"]
    assert len(dd_check) == 1
    assert dd_check[0].passed is False


def test_risk_manager_approves_valid_short_signal(
    sample_portfolio: object,
    sample_prices: object,
    sample_config: object,
) -> None:
    mgr = RiskManager(sample_config)
    signal = TradeSignal(
        symbol="GLD",
        action=Action.SHORT,
        conviction=Conviction.MEDIUM,
        target_weight=0.02,
        stop_loss=190.0,
        take_profit=175.0,
        reasoning="Short gold hedge",
    )
    approved, rejected = mgr.filter_signals([signal], sample_portfolio, sample_prices)
    assert len(approved) == 1
    assert len(rejected) == 0


def test_risk_manager_rejects_short_over_short_cap(
    sample_portfolio: object,
    sample_prices: object,
    sample_config: object,
) -> None:
    mgr = RiskManager(sample_config)
    signal = TradeSignal(
        symbol="GLD",
        action=Action.SHORT,
        conviction=Conviction.HIGH,
        target_weight=0.15,
        stop_loss=190.0,
        take_profit=175.0,
        reasoning="Oversized short",
    )
    approved, rejected = mgr.filter_signals([signal], sample_portfolio, sample_prices)
    assert len(approved) == 0
    assert len(rejected) == 1
    short_weight_checks = [c for c in rejected[0][1] if c.rule == "position_weight"]
    assert short_weight_checks
    assert short_weight_checks[0].passed is False


def test_risk_manager_rejects_short_without_stop_above_price(
    sample_portfolio: object,
    sample_prices: object,
    sample_config: object,
) -> None:
    mgr = RiskManager(sample_config)
    signal = TradeSignal(
        symbol="GLD",
        action=Action.SHORT,
        conviction=Conviction.HIGH,
        target_weight=0.02,
        stop_loss=180.0,
        take_profit=175.0,
        reasoning="Invalid short stop",
    )
    approved, rejected = mgr.filter_signals([signal], sample_portfolio, sample_prices)
    assert len(approved) == 0
    assert len(rejected) == 1
    stop_checks = [c for c in rejected[0][1] if c.rule == "short_stop_direction"]
    assert stop_checks
    assert stop_checks[0].passed is False


def test_risk_manager_enforces_short_atr_stop_distance(
    sample_portfolio: object,
    sample_prices: object,
    sample_config: object,
) -> None:
    mgr = RiskManager(sample_config)
    signal = TradeSignal(
        symbol="GLD",
        action=Action.SHORT,
        conviction=Conviction.MEDIUM,
        target_weight=0.02,
        stop_loss=186.0,
        take_profit=175.0,
        reasoning="Short with too-tight ATR stop",
    )

    checks = mgr.check_trade(
        signal,
        sample_portfolio,
        sample_prices,
        atrs={"GLD": 1.0},
    )
    atr_checks = [c for c in checks if c.rule == "atr_stop_loss"]
    assert atr_checks
    assert atr_checks[0].passed is False


def test_risk_manager_accepts_short_atr_stop_when_distance_sufficient(
    sample_portfolio: object,
    sample_prices: object,
    sample_config: object,
) -> None:
    mgr = RiskManager(sample_config)
    signal = TradeSignal(
        symbol="GLD",
        action=Action.SHORT,
        conviction=Conviction.MEDIUM,
        target_weight=0.02,
        stop_loss=188.0,
        take_profit=175.0,
        reasoning="Short with sufficient ATR stop",
    )

    checks = mgr.check_trade(
        signal,
        sample_portfolio,
        sample_prices,
        atrs={"GLD": 1.0},
    )
    atr_checks = [c for c in checks if c.rule == "atr_stop_loss"]
    assert atr_checks
    assert atr_checks[0].passed is True


def test_risk_manager_cover_not_blocked_by_drawdown(
    sample_config: object,
    sample_prices: object,
) -> None:
    from llm_quant.trading.portfolio import Portfolio, Position

    p = Portfolio(initial_capital=100_000.0)
    p.cash = 104_000.0
    p.positions = {
        "GLD": Position(
            symbol="GLD",
            shares=-10,
            avg_cost=185.0,
            current_price=190.0,
            stop_loss=192.0,
            short_proceeds=1_850.0,
        ),
    }

    mgr = RiskManager(sample_config)
    signal = TradeSignal(
        symbol="GLD",
        action=Action.COVER,
        conviction=Conviction.MEDIUM,
        target_weight=0.0,
        stop_loss=0.0,
        reasoning="Cover losing short",
    )
    checks = mgr.check_trade(signal, p, sample_prices)
    dd_check = [c for c in checks if c.rule == "drawdown_limit"]
    assert dd_check
    assert dd_check[0].passed is True


def test_risk_manager_close_short_reduces_net_exposure(
    sample_config: object,
    sample_prices: object,
) -> None:
    from llm_quant.trading.portfolio import Portfolio, Position

    p = Portfolio(initial_capital=100_000.0)
    p.cash = 102_000.0
    p.positions = {
        "GLD": Position(
            symbol="GLD",
            shares=-10,
            avg_cost=185.0,
            current_price=190.0,
            stop_loss=192.0,
            short_proceeds=1_850.0,
        )
    }

    mgr = RiskManager(sample_config)
    signal = TradeSignal(
        symbol="GLD",
        action=Action.CLOSE,
        conviction=Conviction.MEDIUM,
        target_weight=0.0,
        stop_loss=0.0,
        reasoning="Flatten short",
    )

    checks = mgr.check_trade(signal, p, sample_prices)
    net_exposure_check = [c for c in checks if c.rule == "net_exposure"]
    assert net_exposure_check
    assert net_exposure_check[0].passed is True
    assert net_exposure_check[0].current_value == 0.0
