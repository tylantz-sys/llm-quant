from datetime import date

from llm_quant.trading.ledger import log_broker_fills


def _snapshot() -> dict:
    return {
        "intraday_position_state": {},
        "order_state": {},
        "lifecycle_state": {},
        "exit_policy_state": {},
    }


def test_log_broker_fills_preserves_short_side_and_semantics(tmp_db) -> None:
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
