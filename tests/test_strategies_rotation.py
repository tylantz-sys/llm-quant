from datetime import date

from llm_quant.strategies.rotation import (
    load_rotation_state,
    select_rotated_specs,
    upsert_rotation_state,
)
from llm_quant.strategies.runtime import StrategySpec


def test_rotation_selects_top_n(tmp_db):
    # Seed snapshots for NAV normalization
    tmp_db.execute(
        """
        INSERT INTO portfolio_snapshots (
            snapshot_id, date, pod_id, nav, cash, gross_exposure, net_exposure, total_pnl
        ) VALUES
            (1, '2026-01-01', 'default', 100000, 100000, 0, 0, 0),
            (2, '2026-01-02', 'default', 100000, 100000, 0, 0, 0)
        """
    )

    # Strategy A: +100 pnl
    tmp_db.execute(
        """
        INSERT INTO trades (trade_id, date, pod_id, symbol, action, shares, price, notional, strategy_id)
        VALUES
            (1, '2026-01-01', 'default', 'SPY', 'buy', 10, 100, 1000, 'strat_a'),
            (2, '2026-01-02', 'default', 'SPY', 'sell', 10, 110, 1100, 'strat_a')
        """
    )

    # Strategy B: -100 pnl
    tmp_db.execute(
        """
        INSERT INTO trades (trade_id, date, pod_id, symbol, action, shares, price, notional, strategy_id)
        VALUES
            (3, '2026-01-01', 'default', 'QQQ', 'buy', 10, 100, 1000, 'strat_b'),
            (4, '2026-01-02', 'default', 'QQQ', 'sell', 10, 90, 900, 'strat_b')
        """
    )

    specs = [
        StrategySpec(slug="strat_a", strategy_name="lead_lag", parameters={}),
        StrategySpec(slug="strat_b", strategy_name="lead_lag", parameters={}),
    ]

    selected, selected_ids = select_rotated_specs(
        tmp_db,
        specs,
        as_of_date=date(2026, 1, 2),
        pod_id="default",
        initial_capital=100000.0,
        enabled=True,
        window_days=10,
        top_n=1,
        min_trades=1,
        cooldown_days=5,
    )

    assert [s.slug for s in selected] == ["strat_a"]
    assert selected_ids == ["strat_a"]


def test_rotation_state_is_pod_scoped(tmp_db):
    upsert_rotation_state(
        tmp_db,
        pod_id="default",
        state={"strat_a": date(2026, 1, 10)},
    )
    upsert_rotation_state(
        tmp_db,
        pod_id="crypto",
        state={"strat_a": date(2026, 1, 20)},
    )

    default_state = load_rotation_state(tmp_db, pod_id="default")
    crypto_state = load_rotation_state(tmp_db, pod_id="crypto")
    assert default_state["strat_a"] == date(2026, 1, 10)
    assert crypto_state["strat_a"] == date(2026, 1, 20)
