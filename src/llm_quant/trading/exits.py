"""Canonical exit-policy engine for profit-taking, trailing, and EOD flatten."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time as dt_time
import warnings
from typing import Literal

from llm_quant.brain.models import Action, Conviction, TradeSignal
from llm_quant.config import ExecutionConfig, RiskLimits
from llm_quant.trading.intraday import IntradayPositionState
from llm_quant.trading.portfolio import Portfolio, Position

ExitMode = Literal["native", "synthetic"]
BrokerExitKind = Literal["none", "bracket", "oco", "market_only"]
SyntheticExitParityTier = Literal["tier1_close_only", "tier2_ohlc_conservative"]
SYNTHETIC_EXIT_PARITY_MODE = "synthetic/daily-conservative"
SYNTHETIC_EXIT_EXECUTION_ASSUMPTION = "close-only daily bars; not exact intraday parity"
SYNTHETIC_EXIT_EXECUTION_ASSUMPTION_TIER2 = (
    "conservative daily OHLC reachability with pessimistic same-bar ambiguity resolution; "
    "not exact intraday parity"
)


@dataclass(frozen=True)
class ExitPolicy:
    """Normalized exit policy loaded from config.

    Risk config owns the policy itself. Execution config only decides whether
    broker-native order realization is available.
    """

    take_profit_mode: str
    take_profit_pct: float
    take_profit_rr: float
    partial_take_profit_enabled: bool
    partial_take_profit_pct: float
    partial_take_profit_size: float
    remainder_take_profit_mult: float
    trailing_stop_enabled: bool
    trailing_stop_pct: float
    eod_flatten_enabled: bool
    eod_flatten_time: str
    fail_on_unprotected_exits: bool

    @property
    def uses_partial_take_profit(self) -> bool:
        return (
            self.partial_take_profit_enabled
            and self.partial_take_profit_pct > 0
            and self.partial_take_profit_size > 0
        )

    @property
    def uses_trailing_stop(self) -> bool:
        return self.trailing_stop_enabled and self.trailing_stop_pct > 0


@dataclass(frozen=True)
class ExitRuntime:
    """How the current runtime realizes the canonical exit policy."""

    broker: str
    intraday_enabled: bool
    intraday_use_oco: bool
    asset_class_filter: tuple[str, ...] = ()

    @property
    def exit_mode(self) -> ExitMode:
        if self.intraday_enabled and (
            self.broker.lower() == "paper" or not self.intraday_use_oco
        ):
            return "synthetic"
        return "native"

    @property
    def is_crypto(self) -> bool:
        return "crypto" in {
            asset_class.lower() for asset_class in self.asset_class_filter
        }

    @property
    def broker_exit_kind(self) -> BrokerExitKind:
        if self.broker.lower() != "alpaca":
            return "none"
        if self.intraday_enabled and self.intraday_use_oco:
            return "oco"
        if not self.intraday_enabled:
            return "bracket"
        return "market_only"


@dataclass(frozen=True)
class ExitTelemetry:
    symbol: str
    entry_price: float
    current_price: float
    stop_loss: float
    partial_target_price: float | None
    trailing_stop_price: float | None
    peak_price: float
    partial_exit_taken: bool
    exit_mode: ExitMode
    broker_exit_kind: BrokerExitKind
    uses_partial_take_profit: bool
    uses_trailing_stop: bool
    unprotected: bool
    synthetic_exit_parity_tier: SyntheticExitParityTier
    synthetic_exit_execution_assumption: str


@dataclass(frozen=True)
class ExitTriggerStatus:
    symbol: str
    stop_loss: float
    take_profit_1_price: float
    take_profit_1_hit: bool
    take_profit_2_price: float
    take_profit_2_hit: bool
    trailing_stop_pct: float
    trailing_stop_active: bool
    trailing_stop_price: float | None
    stop_loss_hit: bool
    current_price: float
    peak_price: float
    exit_reason: str | None
    broker_exit_kind: BrokerExitKind
    exit_mode: ExitMode


@dataclass(frozen=True)
class BrokerExitStatus:
    symbol: str
    tp1_hit: bool
    tp2_hit: bool
    stop_loss_hit: bool
    trailing_hit: bool
    trailing_active: bool
    current_price: float
    entry_price: float
    peak_price: float
    stop_loss_price: float
    tp1_price: float
    tp2_price: float
    trailing_stop_price: float | None
    remaining_qty: float


@dataclass(frozen=True)
class SyntheticExitContext:
    position: Position
    price: float
    nav: float
    state: IntradayPositionState
    bar_high: float | None = None
    bar_low: float | None = None
    parity_tier: SyntheticExitParityTier = "tier1_close_only"


@dataclass(frozen=True)
class BrokerExitPlan:
    symbol: str
    kind: BrokerExitKind
    stop_loss: float
    take_profit: float
    partial_take_profit_price: float | None = None
    partial_take_profit_size: float | None = None
    remainder_take_profit_price: float | None = None
    trailing_stop_pct: float | None = None
    fail_on_unprotected: bool = False


@dataclass(frozen=True)
class EODFlattenDecision:
    enabled: bool
    target_time: dt_time
    due: bool
    reason: str


def build_exit_policy(limits: RiskLimits, execution: ExecutionConfig) -> ExitPolicy:
    """Create canonical exit policy from config.

    The thresholds and policy flags live on risk config. Execution flags only
    determine runtime mechanics such as OCO availability.
    """
    partial_enabled = bool(
        getattr(limits, "partial_take_profit_enabled", False)
        or getattr(execution, "profit_take_partial_pct", 0.0) > 0
    )
    trailing_enabled = bool(
        getattr(limits, "trailing_stop_enabled", False)
        or getattr(execution, "trailing_stop_pct", 0.0) > 0
    )
    return ExitPolicy(
        take_profit_mode=getattr(limits, "take_profit_mode", "pct"),
        take_profit_pct=float(getattr(limits, "take_profit_pct", 0.03)),
        take_profit_rr=float(getattr(limits, "take_profit_rr", 2.0)),
        partial_take_profit_enabled=partial_enabled,
        partial_take_profit_pct=float(
            getattr(limits, "partial_take_profit_pct", 0.0)
            or getattr(execution, "profit_take_partial_pct", 0.0)
        ),
        partial_take_profit_size=float(
            getattr(limits, "partial_take_profit_size", 0.0)
            or getattr(execution, "profit_take_partial_size", 0.0)
        ),
        remainder_take_profit_mult=float(
            getattr(limits, "remainder_take_profit_mult", 0.0)
            or getattr(execution, "profit_take_remainder_tp_mult", 0.0)
        ),
        trailing_stop_enabled=trailing_enabled,
        trailing_stop_pct=float(
            getattr(limits, "trailing_stop_pct", 0.0)
            or getattr(execution, "trailing_stop_pct", 0.0)
        ),
        eod_flatten_enabled=bool(getattr(limits, "eod_flatten_enabled", False)),
        eod_flatten_time=str(getattr(limits, "eod_flatten_time", "15:55")),
        fail_on_unprotected_exits=bool(
            getattr(limits, "fail_on_unprotected_exits", True)
        ),
    )


def build_exit_runtime(
    broker: str,
    execution: ExecutionConfig,
) -> ExitRuntime:
    return ExitRuntime(
        broker=broker,
        intraday_enabled=bool(execution.intraday_enabled),
        intraday_use_oco=bool(execution.intraday_use_oco),
        asset_class_filter=tuple(getattr(execution, "asset_class_filter", []) or []),
    )


def synthetic_exit_parity_mode() -> str:
    """Describe the backtest/runtime parity contract for synthetic exits."""
    return SYNTHETIC_EXIT_PARITY_MODE


def synthetic_exit_execution_assumption(
    parity_tier: SyntheticExitParityTier = "tier1_close_only",
) -> str:
    """Describe the daily-bar execution assumption used by backtest parity."""
    if parity_tier == "tier2_ohlc_conservative":
        return SYNTHETIC_EXIT_EXECUTION_ASSUMPTION_TIER2
    return SYNTHETIC_EXIT_EXECUTION_ASSUMPTION


def resolve_take_profit_price(
    entry_price: float,
    stop_loss: float,
    policy: ExitPolicy,
) -> float:
    """Resolve full take-profit price from canonical policy."""
    if policy.take_profit_mode == "pct":
        return round(entry_price * (1.0 + policy.take_profit_pct), 2)
    risk = max(entry_price - stop_loss, 0.0)
    return round(entry_price + policy.take_profit_rr * risk, 2)


def build_broker_exit_plan(
    symbol: str,
    entry_price: float,
    stop_loss: float,
    policy: ExitPolicy,
    runtime: ExitRuntime,
) -> BrokerExitPlan:
    full_take_profit = resolve_take_profit_price(entry_price, stop_loss, policy)
    partial_take_profit_price = None
    remainder_take_profit_price = None
    partial_take_profit_size = None
    trailing_stop_pct = None

    if runtime.broker_exit_kind == "oco" and policy.uses_partial_take_profit:
        partial_take_profit_price = round(
            entry_price * (1.0 + policy.partial_take_profit_pct), 2
        )
        partial_take_profit_size = policy.partial_take_profit_size
        remainder_pct = policy.partial_take_profit_pct * max(
            policy.remainder_take_profit_mult, 0.0
        )
        remainder_take_profit_price = round(entry_price * (1.0 + remainder_pct), 2)
        if remainder_take_profit_price <= partial_take_profit_price:
            remainder_take_profit_price = round(partial_take_profit_price + 0.01, 2)
        if policy.uses_trailing_stop:
            trailing_stop_pct = policy.trailing_stop_pct

    return BrokerExitPlan(
        symbol=symbol,
        kind=runtime.broker_exit_kind,
        stop_loss=round(stop_loss, 2),
        take_profit=full_take_profit,
        partial_take_profit_price=partial_take_profit_price,
        partial_take_profit_size=partial_take_profit_size,
        remainder_take_profit_price=remainder_take_profit_price,
        trailing_stop_pct=trailing_stop_pct,
        fail_on_unprotected=policy.fail_on_unprotected_exits,
    )


def evaluate_position_exits(
    portfolio: Portfolio,
    prices: dict[str, float],
    states: dict[str, IntradayPositionState],
    policy: ExitPolicy,
    runtime: ExitRuntime,
    *,
    bar_highs: dict[str, float] | None = None,
    bar_lows: dict[str, float] | None = None,
    parity_tier: SyntheticExitParityTier = "tier1_close_only",
) -> tuple[list[TradeSignal], list[ExitTelemetry]]:
    """Evaluate position exits using one canonical ruleset.

    Signals are generated only for synthetic-monitoring modes. Native broker
    modes still emit telemetry so the runtime can report intended protection.
    """
    signals: list[TradeSignal] = []
    telemetry: list[ExitTelemetry] = []
    nav = portfolio.nav
    execution_assumption = synthetic_exit_execution_assumption(parity_tier)

    for symbol, pos in portfolio.positions.items():
        price = prices.get(symbol, pos.current_price)
        state = states.get(symbol)
        if state is None:
            state = IntradayPositionState(
                symbol=symbol,
                entry_batch=1,
                entry_price=pos.avg_cost,
                peak_price=price,
            )
            states[symbol] = state

        observed_peak = price
        if bar_highs is not None:
            observed_peak = max(observed_peak, bar_highs.get(symbol, observed_peak))
        peak_price = max(state.peak_price, observed_peak)

        partial_target_price = None
        if policy.uses_partial_take_profit:
            partial_target_price = round(
                state.entry_price * (1.0 + policy.partial_take_profit_pct),
                4,
            )

        trailing_stop_price = None
        if policy.uses_trailing_stop and state.partial_exit_taken:
            trailing_stop_price = round(peak_price * (1.0 - policy.trailing_stop_pct), 4)

        unprotected = bool(
            runtime.broker != "paper"
            and runtime.exit_mode == "native"
            and pos.stop_loss <= 0
        )
        telemetry.append(
            ExitTelemetry(
                symbol=symbol,
                entry_price=state.entry_price,
                current_price=price,
                stop_loss=pos.stop_loss,
                partial_target_price=partial_target_price,
                trailing_stop_price=trailing_stop_price,
                peak_price=peak_price,
                partial_exit_taken=state.partial_exit_taken,
                exit_mode=runtime.exit_mode,
                broker_exit_kind=runtime.broker_exit_kind,
                uses_partial_take_profit=policy.uses_partial_take_profit,
                uses_trailing_stop=policy.uses_trailing_stop,
                unprotected=unprotected,
                synthetic_exit_parity_tier=parity_tier,
                synthetic_exit_execution_assumption=execution_assumption,
            )
        )

        if runtime.exit_mode != "synthetic":
            continue

        signal = _evaluate_synthetic_exit(
            pos=pos,
            price=price,
            nav=nav,
            state=state,
            policy=policy,
            bar_high=bar_highs.get(symbol) if bar_highs is not None else None,
            bar_low=bar_lows.get(symbol) if bar_lows is not None else None,
            parity_tier=parity_tier,
        )
        if signal is not None:
            signals.append(signal)

    return signals, telemetry


def evaluate_broker_exit_status(
    portfolio: Portfolio,
    prices: dict[str, float],
    states: dict[str, IntradayPositionState],
    policy: ExitPolicy,
) -> list[BrokerExitStatus]:
    """Evaluate synthetic intraday Alpaca exit status using broker-authoritative state only."""
    statuses: list[BrokerExitStatus] = []
    if not policy.uses_partial_take_profit:
        return statuses

    for symbol, pos in portfolio.positions.items():
        current_price = float(prices.get(symbol, pos.current_price) or pos.current_price)
        state = states.get(symbol)
        if state is None:
            state = IntradayPositionState(
                symbol=symbol,
                entry_batch=1,
                entry_price=pos.avg_cost,
                peak_price=current_price,
            )
            states[symbol] = state

        entry_price = float(state.entry_price or pos.avg_cost)
        if entry_price <= 0:
            raise RuntimeError(f"Missing entry price for synthetic broker exit status: {symbol}")

        peak_price = max(float(state.peak_price or entry_price), current_price)
        stop_loss_price = float(pos.stop_loss or 0.0)
        if stop_loss_price <= 0:
            raise RuntimeError(f"Missing stop loss for synthetic broker exit status: {symbol}")

        tp1_price = round(entry_price * (1.0 + policy.partial_take_profit_pct), 4)
        tp2_pct = policy.partial_take_profit_pct * max(policy.remainder_take_profit_mult, 0.0)
        tp2_price = round(entry_price * (1.0 + tp2_pct), 4)
        trailing_active = bool(state.partial_exit_taken)
        trailing_stop_price = None
        if policy.uses_trailing_stop and trailing_active:
            trailing_stop_price = round(
                peak_price * (1.0 - policy.trailing_stop_pct),
                4,
            )

        tp1_hit = bool(not state.partial_exit_taken and current_price >= tp1_price)
        tp2_hit = bool(state.partial_exit_taken and current_price >= tp2_price)
        stop_loss_hit = bool(current_price <= stop_loss_price)
        trailing_hit = bool(
            policy.uses_trailing_stop
            and trailing_active
            and trailing_stop_price is not None
            and current_price <= trailing_stop_price
        )

        statuses.append(
            BrokerExitStatus(
                symbol=symbol,
                tp1_hit=tp1_hit,
                tp2_hit=tp2_hit,
                stop_loss_hit=stop_loss_hit,
                trailing_hit=trailing_hit,
                trailing_active=trailing_active,
                current_price=current_price,
                entry_price=entry_price,
                peak_price=peak_price,
                stop_loss_price=stop_loss_price,
                tp1_price=tp1_price,
                tp2_price=tp2_price,
                trailing_stop_price=trailing_stop_price,
                remaining_qty=float(pos.shares),
            )
        )

    return statuses


def evaluate_synthetic_exit(
    context: SyntheticExitContext,
    policy: ExitPolicy,
) -> TradeSignal | None:
    pos = context.position
    price = context.price
    nav = context.nav
    state = context.state

    if context.parity_tier == "tier2_ohlc_conservative":
        signal = _evaluate_synthetic_exit_tier2(
            pos=pos,
            price=price,
            nav=nav,
            state=state,
            policy=policy,
            bar_high=context.bar_high,
            bar_low=context.bar_low,
        )
        if signal is not None:
            return signal

    if pos.stop_loss and price <= pos.stop_loss:
        return TradeSignal(
            symbol=pos.symbol,
            action=Action.CLOSE,
            conviction=Conviction.HIGH,
            target_weight=0.0,
            stop_loss=0.0,
            reasoning="Canonical exit engine stop-loss triggered.",
            exit_reason="stop_loss",
            entry_batch=state.entry_batch or 1,
        )

    if policy.uses_partial_take_profit and not state.partial_exit_taken:
        partial_target = state.entry_price * (1.0 + policy.partial_take_profit_pct)
        if price >= partial_target:
            current_weight = pos.market_value / nav if nav else 0.0
            target_weight = max(current_weight * (1.0 - policy.partial_take_profit_size), 0.0)
            return TradeSignal(
                symbol=pos.symbol,
                action=Action.SELL,
                conviction=Conviction.HIGH,
                target_weight=round(target_weight, 4),
                stop_loss=pos.stop_loss,
                reasoning=(
                    f"Canonical exit engine partial TP reached (+{policy.partial_take_profit_pct:.1%})."
                ),
                exit_reason="tp_partial",
                entry_batch=state.entry_batch or 1,
            )

    if policy.uses_trailing_stop and state.partial_exit_taken:
        trail_price = state.peak_price * (1.0 - policy.trailing_stop_pct)
        if price <= trail_price:
            return TradeSignal(
                symbol=pos.symbol,
                action=Action.CLOSE,
                conviction=Conviction.HIGH,
                target_weight=0.0,
                stop_loss=0.0,
                reasoning=(
                    f"Canonical exit engine trailing stop hit ({policy.trailing_stop_pct:.2%})."
                ),
                exit_reason="trailing_stop",
                entry_batch=state.entry_batch or 1,
            )

    return None


def _evaluate_synthetic_exit_tier2(
    pos: Position,
    price: float,
    nav: float,
    state: IntradayPositionState,
    policy: ExitPolicy,
    bar_high: float | None,
    bar_low: float | None,
) -> TradeSignal | None:
    reachable_low = price if bar_low is None else min(bar_low, price)
    reachable_high = price if bar_high is None else max(bar_high, price)

    stop_hit = bool(pos.stop_loss and reachable_low <= pos.stop_loss)
    partial_target = None
    partial_hit = False
    if policy.uses_partial_take_profit and not state.partial_exit_taken:
        partial_target = state.entry_price * (1.0 + policy.partial_take_profit_pct)
        partial_hit = reachable_high >= partial_target

    if stop_hit and partial_hit:
        return TradeSignal(
            symbol=pos.symbol,
            action=Action.CLOSE,
            conviction=Conviction.HIGH,
            target_weight=0.0,
            stop_loss=0.0,
            reasoning=(
                "Canonical exit engine conservative OHLC parity saw stop-loss and "
                "take-profit reachable in the same bar; pessimistically resolving to stop-loss."
            ),
            exit_reason="stop_loss",
            entry_batch=state.entry_batch or 1,
        )

    if stop_hit:
        return TradeSignal(
            symbol=pos.symbol,
            action=Action.CLOSE,
            conviction=Conviction.HIGH,
            target_weight=0.0,
            stop_loss=0.0,
            reasoning="Canonical exit engine stop-loss triggered via conservative OHLC reachability.",
            exit_reason="stop_loss",
            entry_batch=state.entry_batch or 1,
        )

    if partial_hit and partial_target is not None:
        current_weight = pos.market_value / nav if nav else 0.0
        target_weight = max(current_weight * (1.0 - policy.partial_take_profit_size), 0.0)
        return TradeSignal(
            symbol=pos.symbol,
            action=Action.SELL,
            conviction=Conviction.HIGH,
            target_weight=round(target_weight, 4),
            stop_loss=pos.stop_loss,
            reasoning=(
                "Canonical exit engine partial TP reached via conservative OHLC reachability "
                f"(+{policy.partial_take_profit_pct:.1%})."
            ),
            exit_reason="tp_partial",
            entry_batch=state.entry_batch or 1,
        )

    if policy.uses_trailing_stop and state.partial_exit_taken:
        observed_peak = max(state.peak_price, reachable_high)
        trail_price = observed_peak * (1.0 - policy.trailing_stop_pct)
        if reachable_low <= trail_price:
            return TradeSignal(
                symbol=pos.symbol,
                action=Action.CLOSE,
                conviction=Conviction.HIGH,
                target_weight=0.0,
                stop_loss=0.0,
                reasoning=(
                    "Canonical exit engine trailing stop hit via conservative OHLC reachability "
                    f"({policy.trailing_stop_pct:.2%})."
                ),
                exit_reason="trailing_stop",
                entry_batch=state.entry_batch or 1,
            )

    return None


def _evaluate_synthetic_exit(
    pos: Position,
    price: float,
    nav: float,
    state: IntradayPositionState,
    policy: ExitPolicy,
    bar_high: float | None = None,
    bar_low: float | None = None,
    parity_tier: SyntheticExitParityTier = "tier1_close_only",
) -> TradeSignal | None:
    return evaluate_synthetic_exit(
        SyntheticExitContext(
            position=pos,
            price=price,
            nav=nav,
            state=state,
            bar_high=bar_high,
            bar_low=bar_low,
            parity_tier=parity_tier,
        ),
        policy,
    )


def parse_eod_time(value: str) -> dt_time:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError("EOD time must be HH:MM")
    hour, minute = (int(part) for part in parts)
    return dt_time(hour=hour, minute=minute)


def assess_eod_flatten(
    policy: ExitPolicy,
    now_et: datetime,
    market_is_open: bool,
    runtime: ExitRuntime | None = None,
) -> EODFlattenDecision:
    target_time = parse_eod_time(policy.eod_flatten_time)
    if not policy.eod_flatten_enabled:
        return EODFlattenDecision(False, target_time, False, "disabled")
    if runtime is not None and runtime.is_crypto:
        return EODFlattenDecision(False, target_time, False, "disabled_for_crypto")
    if not market_is_open:
        return EODFlattenDecision(True, target_time, False, "market_closed")
    if now_et.time() < target_time:
        return EODFlattenDecision(True, target_time, False, "before_cutoff")
    return EODFlattenDecision(True, target_time, True, "due")


def build_exit_telemetry_payload(
    telemetry: list[ExitTelemetry],
    policy: ExitPolicy,
    runtime: ExitRuntime,
) -> dict[str, object]:
    return {
        "exit_engine": {
            "parity": {
                "synthetic_exit_parity_mode": synthetic_exit_parity_mode(),
                "synthetic_exit_execution_assumption": (
                    synthetic_exit_execution_assumption()
                ),
            },
            "policy": {
                "take_profit_mode": policy.take_profit_mode,
                "take_profit_pct": policy.take_profit_pct,
                "take_profit_rr": policy.take_profit_rr,
                "partial_take_profit_enabled": policy.partial_take_profit_enabled,
                "partial_take_profit_pct": policy.partial_take_profit_pct,
                "partial_take_profit_size": policy.partial_take_profit_size,
                "remainder_take_profit_mult": policy.remainder_take_profit_mult,
                "trailing_stop_enabled": policy.trailing_stop_enabled,
                "trailing_stop_pct": policy.trailing_stop_pct,
                "eod_flatten_enabled": policy.eod_flatten_enabled,
                "eod_flatten_time": policy.eod_flatten_time,
                "fail_on_unprotected_exits": policy.fail_on_unprotected_exits,
            },
            "runtime": {
                "broker": runtime.broker,
                "intraday_enabled": runtime.intraday_enabled,
                "intraday_use_oco": runtime.intraday_use_oco,
                "asset_class_filter": list(runtime.asset_class_filter),
                "is_crypto": runtime.is_crypto,
                "exit_mode": runtime.exit_mode,
                "broker_exit_kind": runtime.broker_exit_kind,
            },
            "positions": [
                {
                    "symbol": item.symbol,
                    "entry_price": item.entry_price,
                    "current_price": item.current_price,
                    "stop_loss": item.stop_loss,
                    "partial_target_price": item.partial_target_price,
                    "trailing_stop_price": item.trailing_stop_price,
                    "peak_price": item.peak_price,
                    "partial_exit_taken": item.partial_exit_taken,
                    "exit_mode": item.exit_mode,
                    "broker_exit_kind": item.broker_exit_kind,
                    "uses_partial_take_profit": item.uses_partial_take_profit,
                    "uses_trailing_stop": item.uses_trailing_stop,
                    "unprotected": item.unprotected,
                    "synthetic_exit_parity_tier": item.synthetic_exit_parity_tier,
                    "synthetic_exit_execution_assumption": (
                        item.synthetic_exit_execution_assumption
                    ),
                }
                for item in telemetry
            ],
        }
    }