"""Paper-trade executor.

Translates ``TradeSignal`` objects (produced by the LLM brain after risk
filtering) into concrete position changes on the in-memory ``Portfolio``.
No real orders are sent – this is a simulation layer.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from llm_quant.brain.models import Action, TradeSignal
from llm_quant.trading.portfolio import Portfolio

if TYPE_CHECKING:
    from llm_quant.trading.portfolio import Position

logger = logging.getLogger(__name__)


class ExecutionMode(StrEnum):
    """Execution mode for the portfolio mutation layer."""

    PAPER = "paper"
    ALPACA = "alpaca"


class RuntimeExecutionNotAllowedError(RuntimeError):
    """Raised when simulated execution is requested for a broker runtime."""


# ---------------------------------------------------------------------------
# Executed trade record
# ---------------------------------------------------------------------------


@dataclass
class ExecutedTrade:
    """Immutable record of a single trade that was applied to the portfolio."""

    symbol: str
    action: str  # "buy" / "sell" / "close"
    shares: float
    price: float
    notional: float  # abs(shares * price)
    conviction: str
    reasoning: str
    strategy_id: str = ""
    entry_batch: int = 1
    exit_reason: str = ""
    short_proceeds: float = 0.0
    is_short_close: bool = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ensure_runtime_execution_allowed(mode: ExecutionMode | str = ExecutionMode.PAPER) -> None:
    """Guard the paper executor from being used in broker-authoritative runtimes."""
    normalized = ExecutionMode(str(mode).lower())
    if normalized == ExecutionMode.ALPACA:
        raise RuntimeExecutionNotAllowedError(
            "Simulated portfolio execution is forbidden in alpaca mode. "
            "Use broker order submission and broker fill reconciliation only."
        )


def execute_signals(
    portfolio: Portfolio,
    signals: list[TradeSignal],
    prices: dict[str, float],
    nav: float,
    asset_class_map: dict[str, str] | None = None,
    reserve_cash: float = 0.0,
    *,
    mode: ExecutionMode | str = ExecutionMode.PAPER,
) -> list[ExecutedTrade]:
    """Execute a batch of trade signals against *portfolio*.

    The function mutates *portfolio* in place (cash, positions) and returns
    the list of trades that were actually executed. Signals that cannot be
    executed (e.g. missing price, zero shares) are skipped with a warning.

    In ``alpaca`` mode this function is explicitly disabled because the broker
    must remain the only execution truth for live runtime state.

    Parameters
    ----------
    portfolio:
        Live portfolio object to mutate.
    signals:
        Trade signals already approved by the risk manager.
    prices:
        Latest prices keyed by symbol.
    nav:
        Portfolio NAV **before** this batch (used for weight calculations).

    Returns
    -------
    list[ExecutedTrade]
        Records of every trade that was applied.
    """
    ensure_runtime_execution_allowed(mode)
    executed: list[ExecutedTrade] = []

    for signal in signals:
        symbol = signal.symbol
        price = prices.get(symbol)

        if price is None or price <= 0.0:
            logger.warning(
                "Skipping %s %s – no valid price available",
                signal.action.value,
                symbol,
            )
            continue

        trade: ExecutedTrade | None = None

        if signal.action == Action.BUY:
            trade = _execute_buy(
                portfolio,
                signal,
                price,
                nav,
                asset_class_map,
                reserve_cash=reserve_cash,
            )
        elif signal.action == Action.SELL:
            trade = _execute_sell(portfolio, signal, price, nav, asset_class_map)
        elif signal.action == Action.SHORT:
            trade = _execute_short(
                portfolio,
                signal,
                price,
                nav,
                asset_class_map,
            )
        elif signal.action == Action.COVER:
            trade = _execute_cover(portfolio, signal, price, nav, asset_class_map)
        elif signal.action == Action.CLOSE:
            trade = _execute_close(portfolio, signal, price)
        elif signal.action == Action.HOLD:
            logger.debug("HOLD signal for %s – no trade.", symbol)
            continue
        else:
            logger.warning("Unknown action %s for %s", signal.action, symbol)
            continue

        if trade is not None:
            executed.append(trade)
            logger.info(
                "Executed %s %s: %.4f shares @ %.4f (notional=%.2f)",
                trade.action,
                trade.symbol,
                trade.shares,
                trade.price,
                trade.notional,
            )

    return executed


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _execute_buy(
    portfolio: Portfolio,
    signal: TradeSignal,
    price: float,
    nav: float,
    asset_class_map: dict[str, str] | None = None,
    reserve_cash: float = 0.0,
) -> ExecutedTrade | None:
    """Buy (or add to) a position."""
    current_notional = 0.0

    existing = portfolio.positions.get(signal.symbol)
    if existing is not None and existing.shares < 0:
        logger.warning(
            "BUY %s: cannot buy while holding a short position. COVER first.",
            signal.symbol,
        )
        return None

    if existing is not None:
        current_notional = existing.market_value

    target_notional = min(
        signal.target_weight * nav,
        current_notional + max(portfolio.cash - reserve_cash, 0.0),
    )
    additional_notional = target_notional - current_notional
    if additional_notional <= 0.0:
        logger.debug(
            "BUY %s: already at or above target weight – skipping.",
            signal.symbol,
        )
        return None

    asset_class = (asset_class_map or {}).get(signal.symbol, "equity")
    allow_fractional = str(asset_class).lower() == "crypto"
    if allow_fractional:
        shares_to_buy = round(additional_notional / price, 6)
    else:
        shares_to_buy = math.floor(additional_notional / price)
    if shares_to_buy <= 0:
        logger.debug(
            "BUY %s: computed 0 shares (notional=%.2f, price=%.4f) – skipping.",
            signal.symbol,
            additional_notional,
            price,
        )
        return None

    cost = shares_to_buy * price

    max_spend = max(portfolio.cash - reserve_cash, 0.0)

    # Ensure we have enough deployable cash after preserving reserve
    if cost > max_spend:
        # Buy as many shares as deployable cash allows
        if allow_fractional:
            shares_to_buy = round(max_spend / price, 6)
        else:
            shares_to_buy = math.floor(max_spend / price)
        if shares_to_buy <= 0:
            logger.warning(
                "BUY %s: insufficient deployable cash (need=%.2f, have=%.2f, reserve=%.2f).",
                signal.symbol,
                cost,
                max_spend,
                reserve_cash,
            )
            return None
        cost = shares_to_buy * price

    # Update portfolio
    portfolio.cash -= cost

    if existing is not None:
        # Weighted average cost
        total_shares = existing.shares + shares_to_buy
        existing.avg_cost = (
            existing.shares * existing.avg_cost + shares_to_buy * price
        ) / total_shares
        existing.shares = total_shares
        existing.current_price = price
        existing.stop_loss = round(signal.stop_loss, 2)
    else:
        portfolio.positions[signal.symbol] = _make_position(
            signal.symbol, shares_to_buy, price, signal.stop_loss
        )

    return ExecutedTrade(
        symbol=signal.symbol,
        action="buy",
        shares=shares_to_buy,
        price=price,
        notional=cost,
        conviction=signal.conviction.value,
        reasoning=signal.reasoning,
        strategy_id=signal.strategy_id,
        entry_batch=signal.entry_batch,
        exit_reason=signal.exit_reason,
    )


def _execute_sell(
    portfolio: Portfolio,
    signal: TradeSignal,
    price: float,
    nav: float,
    asset_class_map: dict[str, str] | None = None,
) -> ExecutedTrade | None:
    """Reduce a position toward a target weight."""
    existing = portfolio.positions.get(signal.symbol)
    if existing is None or existing.shares <= 0:
        logger.warning("SELL %s: no position to sell.", signal.symbol)
        return None

    target_notional = signal.target_weight * nav
    current_notional = existing.shares * price
    reduce_notional = current_notional - target_notional

    if reduce_notional <= 0.0:
        logger.debug(
            "SELL %s: position already at or below target weight.",
            signal.symbol,
        )
        return None

    asset_class = (asset_class_map or {}).get(signal.symbol, "equity")
    allow_fractional = str(asset_class).lower() == "crypto"
    if allow_fractional:
        shares_to_sell = round(reduce_notional / price, 6)
    else:
        shares_to_sell = math.floor(reduce_notional / price)
    shares_to_sell = min(shares_to_sell, existing.shares)

    if shares_to_sell <= 0:
        return None

    proceeds = shares_to_sell * price
    existing.shares -= shares_to_sell
    existing.current_price = price
    portfolio.cash += proceeds

    # Remove position if fully liquidated
    if existing.shares <= 0:
        del portfolio.positions[signal.symbol]
    # Update stop-loss if signal provides one
    elif signal.stop_loss > 0.0:
        existing.stop_loss = round(signal.stop_loss, 2)

    return ExecutedTrade(
        symbol=signal.symbol,
        action="sell",
        shares=shares_to_sell,
        price=price,
        notional=proceeds,
        conviction=signal.conviction.value,
        reasoning=signal.reasoning,
        strategy_id=signal.strategy_id,
        entry_batch=signal.entry_batch,
        exit_reason=signal.exit_reason,
    )


def _execute_short(
    portfolio: Portfolio,
    signal: TradeSignal,
    price: float,
    nav: float,
    asset_class_map: dict[str, str] | None = None,
) -> ExecutedTrade | None:
    """Open or add to a short position."""
    existing = portfolio.positions.get(signal.symbol)
    if existing is not None and existing.shares > 0:
        logger.warning("SHORT %s: cannot short while holding a long position.", signal.symbol)
        return None

    current_notional = abs(existing.market_value) if existing is not None else 0.0
    target_notional = signal.target_weight * nav
    additional_notional = target_notional - current_notional
    if additional_notional <= 0.0:
        logger.debug(
            "SHORT %s: already at or above target short weight – skipping.",
            signal.symbol,
        )
        return None

    asset_class = (asset_class_map or {}).get(signal.symbol, "equity")
    allow_fractional = str(asset_class).lower() == "crypto"
    if allow_fractional:
        shares_to_short = round(additional_notional / price, 6)
    else:
        shares_to_short = math.floor(additional_notional / price)
    if shares_to_short <= 0:
        logger.debug(
            "SHORT %s: computed 0 shares (notional=%.2f, price=%.4f) – skipping.",
            signal.symbol,
            additional_notional,
            price,
        )
        return None

    proceeds = shares_to_short * price
    portfolio.cash += proceeds

    if existing is not None:
        existing_short_shares = abs(existing.shares)
        total_short_shares = existing_short_shares + shares_to_short
        existing.avg_cost = (
            existing_short_shares * existing.avg_cost + shares_to_short * price
        ) / total_short_shares
        existing.shares = -total_short_shares
        existing.current_price = price
        existing.stop_loss = round(signal.stop_loss, 2)
        existing.short_proceeds += proceeds
    else:
        portfolio.positions[signal.symbol] = _make_position(
            signal.symbol,
            -shares_to_short,
            price,
            signal.stop_loss,
            short_proceeds=proceeds,
        )

    return ExecutedTrade(
        symbol=signal.symbol,
        action="short",
        shares=shares_to_short,
        price=price,
        notional=proceeds,
        conviction=signal.conviction.value,
        reasoning=signal.reasoning,
        strategy_id=signal.strategy_id,
        entry_batch=signal.entry_batch,
        exit_reason=signal.exit_reason,
        short_proceeds=proceeds,
    )


def _execute_cover(
    portfolio: Portfolio,
    signal: TradeSignal,
    price: float,
    nav: float,
    asset_class_map: dict[str, str] | None = None,
) -> ExecutedTrade | None:
    """Reduce or close a short position toward a target weight."""
    existing = portfolio.positions.get(signal.symbol)
    if existing is None or existing.shares >= 0:
        logger.warning("COVER %s: no short position to cover.", signal.symbol)
        return None

    target_notional = signal.target_weight * nav
    current_notional = abs(existing.shares * price)
    reduce_notional = current_notional - target_notional
    if reduce_notional <= 0.0:
        logger.debug(
            "COVER %s: short position already at or below target weight.",
            signal.symbol,
        )
        return None

    asset_class = (asset_class_map or {}).get(signal.symbol, "equity")
    allow_fractional = str(asset_class).lower() == "crypto"
    if allow_fractional:
        shares_to_cover = round(reduce_notional / price, 6)
    else:
        shares_to_cover = math.floor(reduce_notional / price)
    shares_to_cover = min(shares_to_cover, abs(existing.shares))

    if shares_to_cover <= 0:
        return None

    cover_cost = shares_to_cover * price
    portfolio.cash -= cover_cost
    existing.shares += shares_to_cover
    existing.current_price = price

    if existing.shares >= -1e-9:
        del portfolio.positions[signal.symbol]
    elif signal.stop_loss > 0.0:
        existing.stop_loss = round(signal.stop_loss, 2)

    return ExecutedTrade(
        symbol=signal.symbol,
        action="cover",
        shares=shares_to_cover,
        price=price,
        notional=cover_cost,
        conviction=signal.conviction.value,
        reasoning=signal.reasoning,
        strategy_id=signal.strategy_id,
        entry_batch=signal.entry_batch,
        exit_reason=signal.exit_reason,
    )


def _execute_close(
    portfolio: Portfolio,
    signal: TradeSignal,
    price: float,
) -> ExecutedTrade | None:
    """Close an entire position."""
    existing = portfolio.positions.get(signal.symbol)
    if existing is None or existing.shares == 0:
        logger.warning("CLOSE %s: no position to close.", signal.symbol)
        return None

    shares_to_close = abs(existing.shares)
    is_short_close = existing.shares < 0
    action = "cover" if is_short_close else "close"
    if existing.shares > 0:
        proceeds = shares_to_close * price
        portfolio.cash += proceeds
        notional = proceeds
    else:
        cover_cost = shares_to_close * price
        portfolio.cash -= cover_cost
        notional = cover_cost
    del portfolio.positions[signal.symbol]

    return ExecutedTrade(
        symbol=signal.symbol,
        action=action,
        shares=shares_to_close,
        price=price,
        notional=notional,
        conviction=signal.conviction.value,
        reasoning=signal.reasoning,
        strategy_id=signal.strategy_id,
        entry_batch=signal.entry_batch,
        exit_reason=signal.exit_reason,
        is_short_close=is_short_close,
    )


def _make_position(
    symbol: str,
    shares: float,
    price: float,
    stop_loss: float,
    short_proceeds: float = 0.0,
) -> Position:
    """Create a new ``Position`` dataclass.

    Import is deferred to the function body to avoid circular imports
    (executor imports Portfolio at module level via type hints, but
    Position lives in the same module).
    """
    from llm_quant.trading.portfolio import Position  # local import

    return Position(
        symbol=symbol,
        shares=shares,
        avg_cost=price,
        current_price=price,
        stop_loss=stop_loss,
        short_proceeds=short_proceeds,
    )