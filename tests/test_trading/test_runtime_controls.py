from datetime import UTC, datetime, timedelta

from llm_quant.brain.models import Action, Conviction, TradeSignal
from llm_quant.trading.runtime_controls import (
    apply_expectancy_buy_scale,
    assess_intraday_symbol_freshness,
    compute_peak_nav,
    compute_recent_realized_expectancy,
    filter_signals_by_asset_class,
)


def test_compute_peak_nav_uses_persisted_snapshots(tmp_db):
    conn = tmp_db
    conn.execute("""
        INSERT INTO portfolio_snapshots
        (snapshot_id, date, pod_id, nav, cash, gross_exposure, net_exposure, total_pnl)
        VALUES
        (1, DATE '2026-03-01', 'default', 100000, 90000, 10000, 10000, 0),
        (2, DATE '2026-03-02', 'default', 112500, 98000, 14500, 14500, 12500)
        """)
    peak = compute_peak_nav(conn, pod_id="default", initial_capital=100000.0)
    assert peak == 112500.0


def test_assess_intraday_symbol_freshness_flags_missing_and_stale(tmp_db):
    conn = tmp_db
    base = datetime(2026, 3, 30, 15, 0, tzinfo=UTC)
    conn.execute(
        """
        INSERT INTO market_data_intraday
        (symbol, timestamp, open, high, low, close, volume)
        VALUES
        ('SPY', ?, 100, 101, 99, 100.5, 1000),
        ('QQQ', ?, 200, 201, 199, 200.5, 1000)
        """,
        [base - timedelta(minutes=2), base - timedelta(minutes=20)],
    )
    spy_ts = conn.execute(
        "SELECT MAX(timestamp) FROM market_data_intraday WHERE symbol = 'SPY'"
    ).fetchone()[0]
    now = spy_ts + timedelta(minutes=2)

    missing, stale, latest = assess_intraday_symbol_freshness(
        conn,
        symbols=["SPY", "QQQ", "IWM"],
        now_ts=now,
        max_age_minutes=10,
    )
    assert missing == ["IWM"]
    assert stale == ["QQQ"]
    assert "SPY" in latest


def test_compute_recent_realized_expectancy_negative_window(tmp_db):
    conn = tmp_db
    # 20 closed trades with -$1 realized each (buy 10, sell 9).
    for idx in range(1, 21):
        base_trade_id = idx * 2 - 1
        conn.execute(
            """
            INSERT INTO trades
            (trade_id, date, pod_id, symbol, action, shares, price, notional)
            VALUES (?, DATE '2026-03-30', 'default', 'SPY', 'buy', 1, 10, 10)
            """,
            [base_trade_id],
        )
        conn.execute(
            """
            INSERT INTO trades
            (trade_id, date, pod_id, symbol, action, shares, price, notional)
            VALUES (?, DATE '2026-03-30', 'default', 'SPY', 'sell', 1, 9, 9)
            """,
            [base_trade_id + 1],
        )

    expectancy, sample_size = compute_recent_realized_expectancy(
        conn,
        pod_id="default",
        lookback_closed_trades=20,
    )
    assert sample_size == 20
    assert expectancy == -1.0


def test_apply_expectancy_buy_scale_scales_only_buys():
    signals = [
        TradeSignal(
            symbol="SPY",
            action=Action.BUY,
            conviction=Conviction.MEDIUM,
            target_weight=0.20,
            stop_loss=95.0,
            reasoning="buy",
        ),
        TradeSignal(
            symbol="SPY",
            action=Action.SELL,
            conviction=Conviction.MEDIUM,
            target_weight=0.10,
            stop_loss=95.0,
            reasoning="sell",
        ),
    ]
    scaled = apply_expectancy_buy_scale(signals, 0.5)
    assert scaled == 1
    assert signals[0].target_weight == 0.10
    assert signals[1].target_weight == 0.10


def test_filter_signals_by_asset_class():
    signals = [
        TradeSignal(
            symbol="USO",
            action=Action.BUY,
            conviction=Conviction.MEDIUM,
            target_weight=0.2,
            stop_loss=120.0,
            reasoning="commodity",
        ),
        TradeSignal(
            symbol="SPY",
            action=Action.BUY,
            conviction=Conviction.MEDIUM,
            target_weight=0.2,
            stop_loss=450.0,
            reasoning="equity",
        ),
    ]
    filtered, dropped = filter_signals_by_asset_class(
        signals,
        asset_class_map={"USO": "commodity", "SPY": "equity"},
        allowed_asset_classes=["commodity"],
    )
    assert dropped == 1
    assert len(filtered) == 1
    assert filtered[0].symbol == "USO"
