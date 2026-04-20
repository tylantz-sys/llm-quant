from datetime import date
from typing import Any

from llm_quant.trading.executor import ExecutedTrade
from llm_quant.trading.ledger import log_broker_fills, log_trades


def _snapshot() -> dict[str, dict[str, object]]:
    return {
        "intraday_position_state": {},
        "order_state": {},
        "lifecycle_state": {},
        "exit_policy_state": {},
    }


def test_log_broker_fills_preserves_short_side_and_semantics(tmp_db: Any) -> None:
    fills = [
        {
            "symbol": "SPY",
            "side": "sell_short",
            "fill_qty": 2.0,
            "fill_price": 100.0,
            "order_id": "order-short-1",
            "intent_type": "entry",
            "parent_order_id": None,
            "lifecycle_state": "open",
        },
        {
            "symbol": "SPY",
            "side": "buy_to_cover",
            "fill_qty": 1.0,
            "fill_price": 90.0,
            "order_id": "order-cover-1",
            "intent_type": "cover",
            "parent_order_id": "order-short-1",
            "lifecycle_state": "close",
        },
    ]

    inserted_ids = log_broker_fills(
        tmp_db,
        fills=fills,
        trade_date=date(2026, 4, 19),
        pod_id="default",
        decision_id=1,
        decision_source="broker",
        sleeve="test",
        source_decision_id=1,
        snapshot=_snapshot(),
    )

    assert len(inserted_ids) == 2

    rows = tmp_db.execute(
        """
        SELECT action, semantic_action, broker_side, intent_type, order_id, parent_order_id
        FROM trades
        WHERE trade_id IN (?, ?)
        ORDER BY trade_id ASC
        """,
        [inserted_ids[0], inserted_ids[1]],
    ).fetchall()

    assert rows[0] == (
        "sell",
        "short_entry",
        "sell_short",
        "entry",
        "order-short-1",
        None,
    )
    assert rows[1] == (
        "buy",
        "short_cover",
        "buy_to_cover",
        "cover",
        "order-cover-1",
        "order-short-1",
    )


def test_log_trades_preserves_short_semantics_for_local_paper_execution(tmp_db: Any) -> None:
    trades = [
        ExecutedTrade(
            symbol="SPY",
            action="short",
            shares=2.0,
            price=100.0,
            notional=200.0,
            conviction="medium",
            reasoning="local short",
        ),
        ExecutedTrade(
            symbol="SPY",
            action="cover",
            shares=2.0,
            price=95.0,
            notional=190.0,
            conviction="medium",
            reasoning="local cover",
        ),
    ]

    inserted_ids = log_trades(
        tmp_db,
        trades,
        date(2026, 4, 20),
        decision_id=7,
        pod_id="default",
    )

    rows = tmp_db.execute(
        """
        SELECT action, semantic_action
        FROM trades
        WHERE trade_id IN (?, ?)
        ORDER BY trade_id ASC
        """,
        inserted_ids,
    ).fetchall()

    assert rows == [
        ("short", "short_entry"),
        ("cover", "short_cover"),
    ]
