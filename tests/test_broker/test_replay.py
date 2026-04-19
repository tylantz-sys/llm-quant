from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from llm_quant.broker.event_ledger import (
    BrokerEventType,
    BrokerLedgerEvent,
    OrderingError,
    append_event,
    ledger_ordering_digest,
)
from llm_quant.broker.exceptions import CausalIntegrityError, OCOConflictError, PositionInvariantError
from llm_quant.broker.reconciliation import BrokerFillEvent, BrokerOrderStatus, _resolve_fill_decisions
from llm_quant.broker.parity import ParityDiffCategory, ParityMode, snapshot_parity_state, validate_parity
from llm_quant.broker.replay import (
    DeterministicBrokerSimulator,
    HistoricalBar,
    ReplayBrokerOrderIntent,
    ReplayEngine,
    ReplayEvent,
    ReplayEventType,
    ReplayFillModelConfig,
    ReplayPositionLimitConfig,
    ReplaySignal,
    ReplayValidationSnapshot,
    SimulatedOrderState,
    signal_to_order_intent,
    validate_replay,
)


def _bar(
    symbol: str,
    minute: int,
    *,
    open: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
) -> HistoricalBar:
    return HistoricalBar(
        symbol=symbol,
        timestamp=datetime(2026, 4, 1, 9, 30, tzinfo=UTC) + timedelta(minutes=minute),
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


@dataclass
class ScriptedStrategy:
    schedule: dict[tuple[str, datetime], list[ReplaySignal]]

    def on_bar(
        self,
        bar: HistoricalBar,
        *,
        portfolio: object,
        open_orders: object,
    ) -> list[ReplaySignal]:
        return list(self.schedule.get((bar.symbol, bar.timestamp), []))


def test_simple_market_entry_exit() -> None:
    bars = [
        _bar("AAPL", 0, open=100, high=101, low=99, close=100),
        _bar("AAPL", 1, open=101, high=102, low=100, close=101),
        _bar("AAPL", 2, open=102, high=103, low=101, close=102),
        _bar("AAPL", 3, open=103, high=104, low=102, close=103),
    ]
    strategy = ScriptedStrategy(
        schedule={
            ("AAPL", bars[0].timestamp): [
                ReplaySignal(
                    symbol="AAPL",
                    action="buy",
                    qty=5,
                    strategy_id="s1",
                    chain_id="chain-1",
                    timestamp=bars[0].timestamp,
                )
            ],
            ("AAPL", bars[2].timestamp): [
                ReplaySignal(
                    symbol="AAPL",
                    action="sell",
                    qty=5,
                    strategy_id="s1",
                    chain_id="chain-1-exit",
                    timestamp=bars[2].timestamp,
                )
            ],
        }
    )
    expected = ReplayValidationSnapshot(
        label="expected",
        cash=1_010.0,
        positions={},
        open_orders={},
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )

    result = ReplayEngine(strategy=strategy, initial_capital=1_000.0).run(
        bars,
        expected=expected,
    )

    assert result.validation.ok
    assert result.final_snapshot.cash == 1_010.0
    assert result.final_snapshot.positions == {}
    assert len(result.fills) == 2


def test_signal_to_order_intent_maps_short_and_cover() -> None:
    ts = datetime(2026, 4, 1, 9, 30, tzinfo=UTC)
    short_intent = signal_to_order_intent(
        ReplaySignal(
            symbol="SPY",
            action="short",
            qty=1,
            strategy_id="s1",
            chain_id="short-1",
            timestamp=ts,
        )
    )
    cover_intent = signal_to_order_intent(
        ReplaySignal(
            symbol="SPY",
            action="cover",
            qty=1,
            strategy_id="s1",
            chain_id="cover-1",
            timestamp=ts,
        )
    )

    assert short_intent.side == "sell"
    assert short_intent.intent_type == "entry_short"
    assert cover_intent.side == "buy"
    assert cover_intent.intent_type == "cover"


def test_limit_order_never_fills() -> None:
    bars = [
        _bar("MSFT", 0, open=100, high=101, low=99.5, close=100),
        _bar("MSFT", 1, open=101, high=102, low=100.5, close=101),
        _bar("MSFT", 2, open=102, high=103, low=101.5, close=102),
    ]
    strategy = ScriptedStrategy(
        schedule={
            ("MSFT", bars[0].timestamp): [
                ReplaySignal(
                    symbol="MSFT",
                    action="buy",
                    qty=3,
                    strategy_id="s1",
                    chain_id="limit-never",
                    timestamp=bars[0].timestamp,
                    order_type="limit",
                    limit_price=95.0,
                )
            ]
        }
    )
    result = ReplayEngine(strategy=strategy, initial_capital=1_000.0).run(bars)

    assert result.final_snapshot.cash == 1_000.0
    assert result.final_snapshot.positions == {}
    assert len(result.fills) == 0
    assert len(result.final_snapshot.open_orders) == 1


def test_partial_fill_across_multiple_bars() -> None:
    broker = DeterministicBrokerSimulator(
        ReplayFillModelConfig(
            market_fill_delay_bars=1,
            volume_participation_rate=0.25,
            min_fill_chunk=1.0,
        )
    )
    created_at = datetime(2026, 4, 1, 9, 30, tzinfo=UTC)
    order = broker.submit_intent(
        ReplayBrokerOrderIntent(
            symbol="NVDA",
            side="buy",
            qty=3,
            order_type="limit",
            strategy_id="s1",
            chain_id="partial",
            timestamp=created_at,
            limit_price=100.0,
        )
    )
    fills = [
        *broker.process_bar(_bar("NVDA", 1, open=100, high=101, low=99, close=100, volume=4)),
        *broker.process_bar(_bar("NVDA", 2, open=100, high=101, low=99, close=100, volume=4)),
        *broker.process_bar(_bar("NVDA", 3, open=100, high=101, low=99, close=100, volume=4)),
    ]

    assert [fill.fill_qty for fill in fills] == [1.0, 1.0, 1.0]
    assert broker.orders[order.order_id].filled_qty == 3.0
    assert broker.orders[order.order_id].remaining_qty == 0.0
    assert broker.orders[order.order_id].status is SimulatedOrderState.FILLED
    assert broker.broker_positions() == [{"symbol": "NVDA", "qty": "3.0"}]


def test_partial_fill_reconciliation_accumulates_across_bars() -> None:
    bars = [
        _bar("NVDA", 0, open=100, high=101, low=99, close=100, volume=100),
        _bar("NVDA", 1, open=100, high=101, low=99, close=100, volume=4),
        _bar("NVDA", 2, open=100, high=101, low=99, close=100, volume=4),
        _bar("NVDA", 3, open=100, high=101, low=99, close=100, volume=4),
    ]
    strategy = ScriptedStrategy(
        schedule={
            ("NVDA", bars[0].timestamp): [
                ReplaySignal(
                    symbol="NVDA",
                    action="buy",
                    qty=3,
                    strategy_id="s1",
                    chain_id="partial-reconcile",
                    timestamp=bars[0].timestamp,
                    order_type="limit",
                    limit_price=100.0,
                )
            ]
        }
    )

    result = ReplayEngine(
        strategy=strategy,
        initial_capital=1_000.0,
        broker=DeterministicBrokerSimulator(
            ReplayFillModelConfig(
                market_fill_delay_bars=1,
                volume_participation_rate=0.25,
                min_fill_chunk=1.0,
            )
        ),
    ).run(bars)

    assert result.validation.ok
    assert [fill.fill_qty for fill in result.fills] == [1.0, 1.0, 1.0]
    assert result.final_snapshot.positions == {"NVDA": 3.0}
    assert result.final_snapshot.cash == 700.0
    assert result.reconciliation[-1].persisted_fill_count == 1


def test_cancel_before_fill() -> None:
    broker = DeterministicBrokerSimulator()
    created_at = datetime(2026, 4, 1, 9, 30, tzinfo=UTC)
    order = broker.submit_intent(
        ReplayBrokerOrderIntent(
            symbol="AMD",
            side="buy",
            qty=2,
            order_type="limit",
            strategy_id="s1",
            chain_id="cancel",
            timestamp=created_at,
            limit_price=90.0,
        )
    )
    broker.cancel_order(order.order_id, timestamp=created_at + timedelta(seconds=30))
    fills = broker.process_bar(_bar("AMD", 1, open=95, high=96, low=89, close=95))

    assert fills == []
    assert broker.orders[order.order_id].status is SimulatedOrderState.CANCELED


def test_replacement_chain_old_to_new_order() -> None:
    broker = DeterministicBrokerSimulator()
    created_at = datetime(2026, 4, 1, 9, 30, tzinfo=UTC)
    original = broker.submit_intent(
        ReplayBrokerOrderIntent(
            symbol="META",
            side="buy",
            qty=2,
            order_type="limit",
            strategy_id="s1",
            chain_id="replace",
            timestamp=created_at,
            limit_price=95.0,
        )
    )
    replacement = broker.replace_order(
        ReplayBrokerOrderIntent(
            symbol="META",
            side="buy",
            qty=2,
            order_type="replace",
            strategy_id="s1",
            chain_id="replace",
            timestamp=created_at + timedelta(minutes=1),
            limit_price=100.0,
            replace_order_id=original.order_id,
        )
    )

    fills = broker.process_bar(_bar("META", 2, open=100, high=101, low=99, close=100))

    assert original.status is SimulatedOrderState.REPLACED
    assert original.replaced_by_order_id == replacement.order_id
    assert replacement.parent_order_id == original.order_id
    assert len(fills) == 1
    assert fills[0].order_id == replacement.order_id


def test_position_limit_rejects_excess_concurrent_entries() -> None:
    broker = DeterministicBrokerSimulator(
        position_limit_config=ReplayPositionLimitConfig(max_positions=2)
    )
    created_at = datetime(2026, 4, 1, 9, 30, tzinfo=UTC)

    order_a = broker.submit_intent(
        ReplayBrokerOrderIntent(
            symbol="AAPL",
            side="buy",
            qty=1,
            order_type="market",
            strategy_id="s1",
            chain_id="limit-a",
            timestamp=created_at,
        )
    )
    order_b = broker.submit_intent(
        ReplayBrokerOrderIntent(
            symbol="MSFT",
            side="buy",
            qty=1,
            order_type="market",
            strategy_id="s1",
            chain_id="limit-b",
            timestamp=created_at,
        )
    )
    order_c = broker.submit_intent(
        ReplayBrokerOrderIntent(
            symbol="NVDA",
            side="buy",
            qty=1,
            order_type="market",
            strategy_id="s1",
            chain_id="limit-c",
            timestamp=created_at,
        )
    )

    assert order_a.status is SimulatedOrderState.ACCEPTED
    assert order_b.status is SimulatedOrderState.ACCEPTED
    assert order_c.status is SimulatedOrderState.REJECTED
    assert order_c.rejection_reason == "POSITION_LIMIT_EXCEEDED"


def test_replacement_still_respects_position_limit_constraints() -> None:
    broker = DeterministicBrokerSimulator(
        position_limit_config=ReplayPositionLimitConfig(max_positions=1)
    )
    created_at = datetime(2026, 4, 1, 9, 30, tzinfo=UTC)

    original = broker.submit_intent(
        ReplayBrokerOrderIntent(
            symbol="AAPL",
            side="buy",
            qty=1,
            order_type="limit",
            strategy_id="s1",
            chain_id="replace-limit",
            timestamp=created_at,
            limit_price=99.0,
        )
    )
    competing = broker.submit_intent(
        ReplayBrokerOrderIntent(
            symbol="MSFT",
            side="buy",
            qty=1,
            order_type="market",
            strategy_id="s1",
            chain_id="competing-limit",
            timestamp=created_at + timedelta(seconds=1),
        )
    )

    replacement = broker.replace_order(
        ReplayBrokerOrderIntent(
            symbol="AAPL",
            side="buy",
            qty=1,
            order_type="replace",
            strategy_id="s1",
            chain_id="replace-limit",
            timestamp=created_at + timedelta(minutes=1),
            limit_price=100.0,
            replace_order_id=original.order_id,
        )
    )

    assert original.status is SimulatedOrderState.REPLACED
    assert competing.status is SimulatedOrderState.REJECTED
    assert replacement.status is SimulatedOrderState.ACCEPTED
    assert replacement.rejection_reason is None


def test_end_of_day_forced_exit_fully_flattens_partial_position() -> None:
    bars = [
        _bar("QQQ", 0, open=100, high=101, low=99, close=100, volume=100),
        _bar("QQQ", 1, open=101, high=102, low=100, close=101, volume=4),
        _bar("QQQ", 2, open=102, high=103, low=101, close=102, volume=4),
    ]
    strategy = ScriptedStrategy(
        schedule={
            ("QQQ", bars[0].timestamp): [
                ReplaySignal(
                    symbol="QQQ",
                    action="buy",
                    qty=3,
                    strategy_id="s1",
                    chain_id="eod",
                    timestamp=bars[0].timestamp,
                    order_type="limit",
                    limit_price=101.0,
                )
            ]
        }
    )

    result = ReplayEngine(
        strategy=strategy,
        initial_capital=1_000.0,
        broker=DeterministicBrokerSimulator(
            ReplayFillModelConfig(
                market_fill_delay_bars=1,
                volume_participation_rate=0.25,
                min_fill_chunk=1.0,
            )
        ),
    ).run(
        bars,
        force_flatten_at_eod=True,
    )

    assert result.validation.ok
    assert result.final_snapshot.positions == {}
    assert any(fill.intent_type == "forced_exit" for fill in result.fills)
    assert any(fill.is_forced_liquidation for fill in result.fills)
    assert result.final_snapshot.cash == 1002.0


def test_event_ledger_identical_timestamps_preserve_sequence_order() -> None:
    conn = duckdb.connect(":memory:")
    timestamp = datetime(2026, 4, 1, 9, 30, tzinfo=UTC)
    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="order-1",
            event_type=BrokerEventType.ORDER_SUBMITTED,
            symbol="AAPL",
            side="buy",
            qty=1.0,
            event_time=timestamp,
            sequence_id=1,
        ),
    )
    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="order-1",
            event_type=BrokerEventType.ORDER_FILLED,
            symbol="AAPL",
            side="buy",
            qty=1.0,
            event_time=timestamp,
            sequence_id=2,
        ),
    )

    digest = ledger_ordering_digest(conn)
    assert [(item.event_time, item.sequence_id, item.event_type) for item in digest] == [
        (timestamp, 1, BrokerEventType.ORDER_SUBMITTED),
        (timestamp, 2, BrokerEventType.ORDER_FILLED),
    ]


def test_event_ledger_out_of_order_insertion_attempt_fails() -> None:
    conn = duckdb.connect(":memory:")
    timestamp = datetime(2026, 4, 1, 9, 30, tzinfo=UTC)
    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="order-1",
            event_type=BrokerEventType.ORDER_SUBMITTED,
            symbol="AAPL",
            side="buy",
            qty=1.0,
            event_time=timestamp,
            sequence_id=5,
        ),
    )

    with pytest.raises(OrderingError, match="EVENT_SEQUENCE_REGRESSION"):
        append_event(
            conn,
            BrokerLedgerEvent(
                order_id="order-2",
                event_type=BrokerEventType.ORDER_SUBMITTED,
                symbol="MSFT",
                side="buy",
                qty=1.0,
                event_time=timestamp,
                sequence_id=4,
            ),
        )


def test_replay_parity_validator_reports_no_diff_for_identical_states() -> None:
    broker = DeterministicBrokerSimulator()
    created_at = datetime(2026, 4, 1, 9, 30, tzinfo=UTC)
    order = broker.submit_intent(
        ReplayBrokerOrderIntent(
            symbol="AAPL",
            side="buy",
            qty=1,
            order_type="market",
            strategy_id="s1",
            chain_id="parity",
            timestamp=created_at,
        )
    )

    state = snapshot_parity_state(
        positions={"AAPL": 1.0},
        cash=900.0,
        orders={order.order_id: broker.orders[order.order_id]},
        event_keys=("intent|AAPL",),
        exposure_delta=1.0,
    )
    result = validate_parity(expected_states=[state], actual_states=[state])

    assert result.ok
    assert result.diffs == []


def test_end_of_day_forced_exit_flattens_multiple_symbols() -> None:
    bars = [
        _bar("QQQ", 0, open=100, high=101, low=99, close=100, volume=100),
        _bar("SPY", 0, open=200, high=201, low=199, close=200, volume=100),
        _bar("QQQ", 1, open=101, high=102, low=100, close=101, volume=4),
        _bar("SPY", 1, open=201, high=202, low=200, close=201, volume=4),
    ]
    strategy = ScriptedStrategy(
        schedule={
            ("QQQ", bars[0].timestamp): [
                ReplaySignal(
                    symbol="QQQ",
                    action="buy",
                    qty=1,
                    strategy_id="s1",
                    chain_id="eod-qqq",
                    timestamp=bars[0].timestamp,
                )
            ],
            ("SPY", bars[1].timestamp): [
                ReplaySignal(
                    symbol="SPY",
                    action="buy",
                    qty=1,
                    strategy_id="s1",
                    chain_id="eod-spy",
                    timestamp=bars[1].timestamp,
                )
            ],
        }
    )

    result = ReplayEngine(strategy=strategy, initial_capital=1_000.0).run(
        bars,
        force_flatten_at_eod=True,
    )

    assert result.validation.ok
    assert result.final_snapshot.positions == {}
    forced_exit_fills = [fill for fill in result.fills if fill.is_forced_liquidation]
    assert len(forced_exit_fills) == 2


def test_strict_parity_reports_event_level_mismatch() -> None:
    expected = snapshot_parity_state(
        positions={"AAPL": 1.0},
        cash=900.0,
        orders={},
        event_keys=("on_fill_event|2026-04-01T09:31:00+00:00|symbol=AAPL",),
        exposure_delta=1.0,
    )
    actual = snapshot_parity_state(
        positions={"AAPL": 1.0},
        cash=900.0,
        orders={},
        event_keys=("on_fill_event|2026-04-01T09:31:00+00:00|symbol=AAPL,duplicate=true",),
        exposure_delta=1.0,
    )

    result = validate_parity(
        expected_states=[expected],
        actual_states=[actual],
        mode=ParityMode.STRICT,
    )

    assert not result.ok
    assert any(diff.category is ParityDiffCategory.EVENT for diff in result.diffs)


def test_semantic_parity_tolerates_equivalent_event_identity_change() -> None:
    expected = snapshot_parity_state(
        positions={"AAPL": 1.0},
        cash=900.0,
        orders={},
        event_keys=("broker-1|on_fill_event|2026-04-01T09:31:00+00:00|symbol=AAPL",),
        exposure_delta=1.0,
    )
    actual = snapshot_parity_state(
        positions={"AAPL": 1.0},
        cash=900.0,
        orders={},
        event_keys=("broker-2|on_fill_event|2026-04-01T09:31:00+00:00|symbol=AAPL",),
        exposure_delta=1.0,
    )

    result = validate_parity(
        expected_states=[expected],
        actual_states=[actual],
        mode=ParityMode.SEMANTIC,
    )

    assert result.ok
    assert result.diffs == []


def test_adversarial_event_ordering_with_identical_timestamps_is_auditable() -> None:
    bars = [
        _bar("AAPL", 0, open=100, high=101, low=99, close=100),
        _bar("MSFT", 0, open=200, high=201, low=199, close=200),
        _bar("AAPL", 1, open=101, high=102, low=100, close=101),
        _bar("MSFT", 1, open=201, high=202, low=200, close=201),
    ]
    shared_ts = bars[0].timestamp
    strategy = ScriptedStrategy(
        schedule={
            ("AAPL", shared_ts): [
                ReplaySignal(
                    symbol="AAPL",
                    action="buy",
                    qty=1,
                    strategy_id="s1",
                    chain_id="same-ts-aapl",
                    timestamp=shared_ts,
                )
            ],
            ("MSFT", shared_ts): [
                ReplaySignal(
                    symbol="MSFT",
                    action="buy",
                    qty=1,
                    strategy_id="s1",
                    chain_id="same-ts-msft",
                    timestamp=shared_ts,
                )
            ],
        }
    )

    result = ReplayEngine(strategy=strategy, initial_capital=1_000.0).run(bars)

    intent_events = [
        event.payload["symbol"]
        for event in result.events
        if event.event_type is ReplayEventType.ON_ORDER_INTENT
    ]
    assert intent_events == ["AAPL", "MSFT"]
    assert result.validation.ok


def test_duplicate_and_corrected_fill_injection_surfaces_divergence() -> None:
    expected = ReplayValidationSnapshot(
        label="expected",
        cash=900.0,
        positions={"AAPL": 1.0},
        open_orders={},
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    actual = ReplayValidationSnapshot(
        label="actual",
        cash=800.0,
        positions={"AAPL": 2.0},
        open_orders={},
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )

    expected_events = [
        ReplayEvent(
            event_type=ReplayEventType.ON_FILL_EVENT,
            timestamp=datetime(2026, 4, 1, 9, 31, tzinfo=UTC),
            payload={"order_id": "ord-1", "symbol": "AAPL", "fill_qty": 1.0},
        )
    ]
    actual_events = expected_events + [
        ReplayEvent(
            event_type=ReplayEventType.ON_FILL_EVENT,
            timestamp=datetime(2026, 4, 1, 9, 31, tzinfo=UTC),
            payload={"order_id": "ord-1-correction", "symbol": "AAPL", "fill_qty": 1.0},
        )
    ]

    from llm_quant.trading.portfolio import Portfolio

    portfolio = Portfolio(initial_capital=1_000.0, pod_id="test")
    portfolio.cash = 800.0
    portfolio.positions["AAPL"] = type("PositionLike", (), {"shares": 2.0})()

    result = validate_replay(
        expected=expected,
        actual=actual,
        fills=[],
        portfolio=portfolio,
        expected_events=expected_events,
        actual_events=actual_events,
        parity_mode=ParityMode.STRICT,
    )

    assert not result.ok
    assert result.parity is not None
    assert any(diff.category is ParityDiffCategory.EVENT for diff in result.parity.diffs)


def test_oco_race_with_delayed_fill_arrival_is_detected_in_strict_mode() -> None:
    expected = snapshot_parity_state(
        positions={},
        cash=1_005.0,
        orders={},
        event_keys=(
            "leg-a|on_order_intent|2026-04-01T09:30:00+00:00|symbol=AAPL",
            "leg-a|on_fill_event|2026-04-01T09:31:00+00:00|symbol=AAPL",
        ),
        exposure_delta=0.0,
    )
    actual = snapshot_parity_state(
        positions={},
        cash=1_005.0,
        orders={},
        event_keys=(
            "leg-b|on_order_intent|2026-04-01T09:30:00+00:00|symbol=AAPL",
            "leg-b|on_fill_event|2026-04-01T09:35:00+00:00|symbol=AAPL",
        ),
        exposure_delta=0.0,
    )

    strict = validate_parity(
        expected_states=[expected],
        actual_states=[actual],
        mode=ParityMode.STRICT,
    )
    semantic = validate_parity(
        expected_states=[expected],
        actual_states=[actual],
        mode=ParityMode.SEMANTIC,
    )

    assert not strict.ok
    assert not semantic.ok
    assert any(diff.category is ParityDiffCategory.EVENT for diff in strict.diffs)


def test_replay_divergence_injection_reports_state_and_exposure_mismatch() -> None:
    expected = snapshot_parity_state(
        positions={"QQQ": 1.0},
        cash=900.0,
        orders={},
        event_keys=("x|on_fill_event|2026-04-01T09:31:00+00:00|symbol=QQQ",),
        exposure_delta=1.0,
    )
    actual = snapshot_parity_state(
        positions={"QQQ": 2.0},
        cash=800.0,
        orders={},
        event_keys=("x|on_fill_event|2026-04-01T09:31:00+00:00|symbol=QQQ",),
        exposure_delta=2.0,
    )

    result = validate_parity(
        expected_states=[expected],
        actual_states=[actual],
        mode=ParityMode.STRICT,
    )

    assert not result.ok
    categories = {diff.category for diff in result.diffs}
    assert ParityDiffCategory.STATE_TRANSITION in categories
    assert ParityDiffCategory.EXPOSURE in categories


def test_event_ledger_rejects_fill_before_order_submission_timestamp() -> None:
    conn = duckdb.connect(":memory:")
    submitted_at = datetime(2026, 4, 1, 9, 31, tzinfo=UTC)
    append_event(
        conn,
        BrokerLedgerEvent(
            order_id="order-1",
            event_type=BrokerEventType.ORDER_SUBMITTED,
            symbol="AAPL",
            side="buy",
            qty=1.0,
            event_time=submitted_at,
            sequence_id=1,
        ),
    )

    with pytest.raises(OrderingError, match="EVENT_CAUSAL_TIME_REGRESSION"):
        append_event(
            conn,
            BrokerLedgerEvent(
                order_id="order-1",
                event_type=BrokerEventType.ORDER_FILLED,
                symbol="AAPL",
                side="buy",
                qty=1.0,
                event_time=submitted_at - timedelta(minutes=1),
                sequence_id=2,
            ),
        )


def test_event_ledger_rejects_missing_intermediate_chain_event() -> None:
    conn = duckdb.connect(":memory:")
    timestamp = datetime(2026, 4, 1, 9, 30, tzinfo=UTC)

    with pytest.raises(CausalIntegrityError, match="EVENT_CAUSAL_CHAIN_GAP"):
        append_event(
            conn,
            BrokerLedgerEvent(
                order_id="child-order",
                event_type=BrokerEventType.ORDER_FILLED,
                symbol="AAPL",
                side="sell",
                qty=1.0,
                event_time=timestamp,
                sequence_id=1,
                parent_order_id="parent-order",
                event_chain_id="parent-order",
                parent_event_order_id="parent-order",
            ),
        )


def test_reconciliation_deduplicates_duplicate_fill_metadata_with_same_economic_effect() -> None:
    timestamp = datetime(2026, 4, 1, 9, 31, tzinfo=UTC)
    status = BrokerOrderStatus(
        order_id="order-1",
        symbol="AAPL",
        side="buy",
        status="filled",
        qty=1.0,
        filled_qty=1.0,
        remaining_qty=0.0,
        filled_avg_price=100.0,
        submitted_at=timestamp - timedelta(minutes=1),
        updated_at=timestamp,
        intent_type="entry",
        fill_events=[
            BrokerFillEvent(
                order_id="order-1",
                symbol="AAPL",
                side="buy",
                fill_qty=1.0,
                fill_price=100.0,
                fill_time=timestamp,
                intent_type="entry",
                execution_id="exec-1",
            ),
            BrokerFillEvent(
                order_id="order-1",
                symbol="AAPL",
                side="buy",
                fill_qty=1.0,
                fill_price=100.0,
                fill_time=timestamp,
                intent_type="entry",
                execution_id="exec-2",
            ),
        ],
    )

    decisions = _resolve_fill_decisions(status)

    assert len(decisions) == 1
    assert decisions[0].resolution == "applied"


def test_semantic_parity_reports_permutation_equivalent_but_path_divergent_sequence() -> None:
    expected = snapshot_parity_state(
        positions={"AAPL": 1.0},
        cash=900.0,
        orders={},
        event_keys=(
            "broker-1|on_order_intent|2026-04-01T09:30:00+00:00|symbol=AAPL",
            "broker-1|on_fill_event|2026-04-01T09:31:00+00:00|symbol=AAPL",
        ),
        exposure_delta=1.0,
    )
    actual = snapshot_parity_state(
        positions={"AAPL": 1.0},
        cash=900.0,
        orders={},
        event_keys=(
            "broker-2|on_fill_event|2026-04-01T09:31:00+00:00|symbol=AAPL",
            "broker-2|on_order_intent|2026-04-01T09:30:00+00:00|symbol=AAPL",
        ),
        exposure_delta=1.0,
    )

    result = validate_parity(
        expected_states=[expected],
        actual_states=[actual],
        mode=ParityMode.SEMANTIC,
    )

    assert not result.ok
    assert any(
        diff.category is ParityDiffCategory.EVENT
        and "Permutation-equivalent" in diff.detail
        for diff in result.diffs
    )


def test_semantic_parity_detects_cumulative_exposure_path_drift() -> None:
    expected = snapshot_parity_state(
        positions={"AAPL": 1.0},
        cash=900.0,
        orders={},
        event_keys=("broker|on_fill_event|2026-04-01T09:31:00+00:00|symbol=AAPL",),
        exposure_delta=1.0,
    )
    actual = snapshot_parity_state(
        positions={"AAPL": 1.0},
        cash=900.0,
        orders={},
        event_keys=("broker|on_fill_event|2026-04-01T09:31:00+00:00|symbol=AAPL",),
        exposure_delta=1.0,
    )
    actual = type(actual)(
        positions=actual.positions,
        cash=actual.cash,
        order_states=actual.order_states,
        event_keys=actual.event_keys,
        exposure_delta=actual.exposure_delta,
        cumulative_exposure=2.0,
        state_digest=actual.state_digest,
    )

    result = validate_parity(
        expected_states=[expected],
        actual_states=[actual],
        mode=ParityMode.SEMANTIC,
    )

    assert not result.ok
    assert any(diff.key == "cumulative_exposure" for diff in result.diffs)


def test_validate_replay_surfaces_event_ledger_state_divergence_from_parity() -> None:
    expected = ReplayValidationSnapshot(
        label="expected",
        cash=900.0,
        positions={"AAPL": 1.0},
        open_orders={},
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        cumulative_exposure=1.0,
    )
    actual = ReplayValidationSnapshot(
        label="actual",
        cash=900.0,
        positions={"AAPL": 1.0},
        open_orders={},
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        cumulative_exposure=2.0,
    )
    expected_events = [
        ReplayEvent(
            event_type=ReplayEventType.ON_FILL_EVENT,
            timestamp=datetime(2026, 4, 1, 9, 31, tzinfo=UTC),
            payload={"order_id": "ord-1", "symbol": "AAPL", "fill_qty": 1.0},
        )
    ]
    actual_events = list(expected_events)

    from llm_quant.trading.portfolio import Portfolio

    portfolio = Portfolio(initial_capital=1_000.0, pod_id="test")
    portfolio.cash = 900.0
    portfolio.positions["AAPL"] = type("PositionLike", (), {"shares": 1.0, "avg_cost": 100.0, "current_price": 100.0})()

    result = validate_replay(
        expected=expected,
        actual=actual,
        fills=[],
        portfolio=portfolio,
        expected_events=expected_events,
        actual_events=actual_events,
        parity_mode=ParityMode.SEMANTIC,
    )

    assert not result.ok
    assert "EVENT_LEDGER_STATE_DIVERGENCE" in result.invariant_failures
