"""Pre-trade risk manager.

Orchestrates all individual risk checks from :mod:`llm_quant.risk.limits`
and decides which signals are safe to execute.

Track routing
-------------
Pass ``track="C"`` (or ``"B"``) to ``check_trade`` / ``filter_signals`` to
activate the corresponding limit set.  Default is Track A.
"""

from __future__ import annotations

import logging
from typing import Any

from llm_quant.brain.models import Action, TradeSignal
from llm_quant.config import AppConfig, TrackCLimits
from llm_quant.risk.limits import (
    RiskCheckResult,
    check_atr_stop_loss,
    check_cash_reserve,
    check_drawdown_limit,
    check_gross_exposure,
    check_locate_availability,
    check_margin_buffer,
    check_net_exposure,
    check_position_size,
    check_position_weight,
    check_sector_concentration,
    check_short_exposure,
    check_stop_loss,
    check_volatility_sizing,
)
from llm_quant.trading.portfolio import Portfolio

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Track C kill-switch helpers
# ---------------------------------------------------------------------------


def _check_exchange_outage(db_conn: Any | None, exchange: str) -> RiskCheckResult:
    """Check for active exchange outages recorded in DuckDB.

    Reads ``track_c_exchange_events`` table.  If the table does not exist
    (common in early dev), returns a pass to avoid blocking trading.

    Parameters
    ----------
    db_conn:
        An active DuckDB connection, or None.
    exchange:
        Exchange identifier to check (e.g. ``"NYSE"``, ``"CME"``).
    """
    if db_conn is None:
        return RiskCheckResult(
            passed=True,
            rule="tc_exchange_outage",
            message="No DB connection — exchange outage check skipped.",
        )
    try:
        row = db_conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM track_c_exchange_events
            WHERE exchange = ?
              AND event_type = 'outage'
              AND resolved_at IS NULL
            """,
            [exchange],
        ).fetchone()
        active_outages = row[0] if row else 0
        passed = active_outages == 0
        return RiskCheckResult(
            passed=passed,
            rule="tc_exchange_outage",
            message=(
                f"Exchange '{exchange}': {active_outages} active outage(s)."
                if not passed
                else f"Exchange '{exchange}': no active outages."
            ),
            current_value=float(active_outages),
            limit_value=0.0,
        )
    except Exception:  # noqa: BLE001  # table missing or schema mismatch
        return RiskCheckResult(
            passed=True,
            rule="tc_exchange_outage",
            message="track_c_exchange_events table not found — check skipped.",
        )


def _check_funding_reversal(
    db_conn: Any | None,
    symbol: str,
    max_funding_rate_pct: float,
) -> RiskCheckResult:
    """Check whether current funding rate exceeds the reversal threshold.

    Reads ``track_c_funding_rates`` table.  If absent, returns pass.

    Parameters
    ----------
    db_conn:
        An active DuckDB connection, or None.
    symbol:
        Instrument symbol (e.g. ``"BTC-PERP"``).
    max_funding_rate_pct:
        Maximum acceptable funding rate in bps/day before flagging reversal.
    """
    if db_conn is None:
        return RiskCheckResult(
            passed=True,
            rule="tc_funding_reversal",
            message="No DB connection — funding reversal check skipped.",
        )
    try:
        row = db_conn.execute(
            """
            SELECT funding_rate_bps
            FROM track_c_funding_rates
            WHERE symbol = ?
            ORDER BY recorded_at DESC
            LIMIT 1
            """,
            [symbol],
        ).fetchone()
        if row is None:
            return RiskCheckResult(
                passed=True,
                rule="tc_funding_reversal",
                message=f"{symbol}: no funding rate data — check skipped.",
            )
        rate = float(row[0])
        passed = abs(rate) <= max_funding_rate_pct
        return RiskCheckResult(
            passed=passed,
            rule="tc_funding_reversal",
            message=(
                f"{symbol}: funding rate {rate:.2f} bps/day "
                f"{'<=' if passed else '>'} threshold {max_funding_rate_pct:.2f} bps/day."
            ),
            current_value=abs(rate),
            limit_value=max_funding_rate_pct,
        )
    except Exception:  # noqa: BLE001
        return RiskCheckResult(
            passed=True,
            rule="tc_funding_reversal",
            message="track_c_funding_rates table not found — check skipped.",
        )


def _check_spread_collapse(
    db_conn: Any | None,
    symbol: str,
    min_spread_bps: float,
) -> RiskCheckResult:
    """Check whether the arb spread has collapsed below the minimum threshold.

    Reads ``track_c_arb_spreads`` table.  If absent, returns pass.

    Parameters
    ----------
    db_conn:
        An active DuckDB connection, or None.
    symbol:
        Instrument pair / strategy identifier.
    min_spread_bps:
        Minimum viable spread in basis points; below this the arb is not
        worth the execution risk.
    """
    if db_conn is None:
        return RiskCheckResult(
            passed=True,
            rule="tc_spread_collapse",
            message="No DB connection — spread collapse check skipped.",
        )
    try:
        row = db_conn.execute(
            """
            SELECT spread_bps
            FROM track_c_arb_spreads
            WHERE symbol = ?
            ORDER BY recorded_at DESC
            LIMIT 1
            """,
            [symbol],
        ).fetchone()
        if row is None:
            return RiskCheckResult(
                passed=True,
                rule="tc_spread_collapse",
                message=f"{symbol}: no spread data — check skipped.",
            )
        spread = float(row[0])
        passed = spread >= min_spread_bps
        return RiskCheckResult(
            passed=passed,
            rule="tc_spread_collapse",
            message=(
                f"{symbol}: spread {spread:.2f} bps "
                f"{'>=' if passed else '<'} minimum {min_spread_bps:.2f} bps."
            ),
            current_value=spread,
            limit_value=min_spread_bps,
        )
    except Exception:  # noqa: BLE001
        return RiskCheckResult(
            passed=True,
            rule="tc_spread_collapse",
            message="track_c_arb_spreads table not found — check skipped.",
        )


def _check_beta_breach(
    spy_beta: float | None,
    max_beta: float,
) -> RiskCheckResult:
    """Check whether the rolling 30-day beta to SPY exceeds the limit.

    Parameters
    ----------
    spy_beta:
        Pre-computed rolling-30d beta of the strategy to SPY.  If None,
        the check is skipped (pass).
    max_beta:
        Maximum allowable absolute beta before flagging a breach.
    """
    if spy_beta is None:
        return RiskCheckResult(
            passed=True,
            rule="tc_beta_breach",
            message="SPY beta not provided — beta breach check skipped.",
        )
    passed = abs(spy_beta) <= max_beta
    return RiskCheckResult(
        passed=passed,
        rule="tc_beta_breach",
        message=(
            f"Rolling-30d SPY beta {spy_beta:.3f} "
            f"({'<=' if passed else '>'} abs limit {max_beta:.3f})."
        ),
        current_value=abs(spy_beta),
        limit_value=max_beta,
    )


class RiskManager:
    """Stateless pre-trade risk gate.

    The manager holds a reference to the risk-limit configuration and the
    sector map derived from the investment universe.  For each proposed
    ``TradeSignal`` it runs the full battery of checks and returns
    structured results.

    Track routing
    -------------
    ``check_trade`` and ``filter_signals`` accept an optional ``track``
    parameter (``"A"``, ``"B"``, or ``"C"``).  When ``track="C"`` the
    manager applies :class:`~llm_quant.config.TrackCLimits` and also runs
    four additional structural-arb kill-switch checks.
    """

    def __init__(self, config: AppConfig) -> None:
        self.limits = config.risk
        self.track_b_limits = config.track_b
        self.track_c_limits: TrackCLimits = config.track_c
        self.sector_map: dict[str, str] = {
            e.symbol: e.sector for e in config.universe.assets
        }
        self.asset_class_map: dict[str, str] = {
            e.symbol: e.asset_class for e in config.universe.assets
        }
        logger.info(
            "RiskManager initialised – %d sector, %d asset-class mappings, limits=%s",
            len(self.sector_map),
            len(self.asset_class_map),
            self.limits.model_dump(),
        )

    # ------------------------------------------------------------------
    # Single-signal evaluation
    # ------------------------------------------------------------------

    def check_trade(  # noqa: PLR0912, C901
        self,
        signal: TradeSignal,
        portfolio: Portfolio,
        prices: dict[str, float],
        atrs: dict[str, float] | None = None,
        track: str = "A",
        db_conn: Any | None = None,
        exchange: str = "UNKNOWN",
        spy_beta: float | None = None,
    ) -> list[RiskCheckResult]:
        """Run **all** risk checks on a single proposed trade.

        Parameters
        ----------
        signal:
            The trade signal to evaluate.
        portfolio:
            Current portfolio state (already price-updated).
        prices:
            Latest market prices keyed by symbol.
        atrs:
            Optional mapping of symbol → current ATR value.  When provided,
            ATR-based volatility sizing and ATR-calibrated stop-loss checks
            are activated.  When absent those two checks are skipped.
        track:
            Which risk-limit track to apply: ``"A"`` (default),
            ``"B"``, or ``"C"``.  Track C also runs four additional
            structural-arb kill-switch checks.
        db_conn:
            Active DuckDB connection for Track C kill-switch queries.  Pass
            ``None`` to skip DB-backed checks (they default to pass).
        exchange:
            Exchange identifier used by the Track C exchange-outage check.
        spy_beta:
            Pre-computed rolling-30d beta to SPY for the Track C beta-breach
            check.  Pass ``None`` to skip.

        Returns
        -------
        list[RiskCheckResult]
            One result per check – callers can inspect ``.passed`` on
            each to decide whether the trade should proceed.
        """
        # Select the active limit set based on the requested track.
        if track == "C":
            limits: Any = self.track_c_limits
        elif track == "B":
            limits = self.track_b_limits
        else:
            limits = self.limits

        results: list[RiskCheckResult] = []
        nav = portfolio.nav
        price = prices.get(signal.symbol, 0.0)

        is_buy = signal.action == Action.BUY
        is_short = signal.action == Action.SHORT
        is_cover = signal.action == Action.COVER
        is_open_risk = is_buy or is_short

        # ---- Trade notional estimation --------------------------------
        # For buys and shorts: target_weight * nav is the desired position size;
        # the *incremental* notional is the difference from the current
        # position.
        current_weight = portfolio.get_position_weight(signal.symbol)
        current_long_weight = max(current_weight, 0.0)
        current_short_weight = abs(min(current_weight, 0.0))

        if is_buy:
            additional_weight = max(signal.target_weight - current_long_weight, 0.0)
            trade_notional = additional_weight * nav
        elif is_short:
            additional_weight = max(signal.target_weight - current_short_weight, 0.0)
            trade_notional = additional_weight * nav
        else:
            # Sells / closes free up capital; compute notional for
            # informational checks but don't block on cash/exposure.
            existing = portfolio.positions.get(signal.symbol)
            if existing is not None and price > 0:
                if signal.action == Action.CLOSE:
                    trade_notional = abs(existing.market_value)
                elif is_cover:
                    reduce_weight = max(current_short_weight - signal.target_weight, 0.0)
                    trade_notional = reduce_weight * nav
                else:
                    reduce_weight = max(current_long_weight - signal.target_weight, 0.0)
                    trade_notional = reduce_weight * nav
            else:
                trade_notional = 0.0

        # 1. Position size (single-trade cap)
        results.append(
            check_position_size(trade_notional, nav, limits.max_trade_size)
        )

        # 2. Position weight
        # Determine per-asset-class position weight limit
        asset_class = self.asset_class_map.get(signal.symbol, "equity")
        if asset_class == "crypto":
            max_weight = getattr(limits, "crypto_max_position_weight", limits.max_position_weight)
        elif asset_class == "forex":
            max_weight = getattr(limits, "forex_max_position_weight", limits.max_position_weight)
        else:
            max_weight = limits.max_position_weight

        if is_short:
            max_weight = getattr(limits, "short_max_position_weight", max_weight)

        if is_buy or is_short:
            target_position_weight = signal.target_weight
            current_position_weight = current_long_weight if is_buy else current_short_weight
        elif is_cover:
            current_position_weight = current_short_weight
            target_position_weight = max(
                current_short_weight - (trade_notional / nav if nav else 0.0),
                0.0,
            )
        else:
            current_position_weight = current_long_weight
            target_position_weight = max(
                current_long_weight - (trade_notional / nav if nav else 0.0),
                0.0,
            )

        results.append(
            check_position_weight(
                current_position_weight,
                target_position_weight,
                max_weight,
            )
        )

        # 3. Gross exposure
        if is_open_risk:
            results.append(
                check_gross_exposure(
                    portfolio.gross_exposure,
                    trade_notional,
                    nav,
                    limits.max_gross_exposure,
                )
            )
        else:
            # Sells reduce gross exposure – always pass.
            results.append(
                RiskCheckResult(
                    passed=True,
                    rule="gross_exposure",
                    message="Sell/close/cover reduces gross exposure.",
                )
            )

        # 4. Net exposure
        if is_buy:
            signed_notional = trade_notional
        elif is_short:
            signed_notional = -trade_notional
        elif signal.action in (Action.SELL, Action.CLOSE):
            signed_notional = -trade_notional
        elif is_cover:
            signed_notional = trade_notional
        else:
            signed_notional = 0.0

        results.append(
            check_net_exposure(
                portfolio.net_exposure,
                signed_notional,
                nav,
                limits.max_net_exposure,
            )
        )

        # 5. Sector concentration (buys only)
        sector = self.sector_map.get(signal.symbol, "Unknown")
        sector_exposures = portfolio.get_sector_exposure(self.sector_map)
        sector_weight = sector_exposures.get(sector, 0.0)

        if is_open_risk:
            additional_sector_weight = additional_weight
            results.append(
                check_sector_concentration(
                    abs(sector_weight),
                    additional_sector_weight,
                    limits.max_sector_concentration,
                )
            )
        else:
            results.append(
                RiskCheckResult(
                    passed=True,
                    rule="sector_concentration",
                    message="Sell/close/cover reduces sector concentration.",
                )
            )

        # 6. Cash reserve (buys only)
        if is_buy:
            results.append(
                check_cash_reserve(
                    portfolio.cash,
                    trade_notional,
                    nav,
                    limits.min_cash_reserve,
                )
            )
        else:
            results.append(
                RiskCheckResult(
                    passed=True,
                    rule="cash_reserve",
                    message="Sell/close/cover does not consume deployable cash.",
                )
            )

        current_short_notional = sum(
            abs(position.market_value)
            for position in portfolio.positions.values()
            if position.shares < 0.0
        )

        if is_short:
            results.append(
                check_short_exposure(
                    current_short_notional,
                    trade_notional,
                    nav,
                    getattr(limits, "max_short_exposure", 0.0),
                )
            )
            results.append(
                check_margin_buffer(
                    portfolio.cash,
                    current_short_notional + trade_notional,
                    getattr(limits, "short_margin_rate", 0.50),
                )
            )
            results.append(
                check_locate_availability(
                    getattr(limits, "require_locate", False),
                    locate_available=True,
                )
            )
        else:
            for rule in ("short_exposure", "margin_buffer", "locate_availability"):
                results.append(
                    RiskCheckResult(
                        passed=True,
                        rule=rule,
                        message=f"{rule} not applicable for action {signal.action.value}.",
                    )
                )

        # 7. Stop-loss (buys only — close/sell actions don't need a stop-loss)
        if is_open_risk:
            results.append(
                check_stop_loss(
                    has_stop_loss=(signal.stop_loss > 0.0),
                    require=limits.require_stop_loss,
                )
            )
        else:
            results.append(
                RiskCheckResult(
                    passed=True,
                    rule="stop_loss",
                    message="Sell/close/cover does not require stop-loss.",
                )
            )

        if is_short:
            short_stop_valid = signal.stop_loss > price if price > 0.0 else False
            results.append(
                RiskCheckResult(
                    passed=short_stop_valid,
                    rule="short_stop_direction",
                    message=(
                        "Short stop-loss is above entry price."
                        if short_stop_valid
                        else "Short stop-loss must be above the current price."
                    ),
                    current_value=signal.stop_loss,
                    limit_value=price,
                )
            )
        else:
            results.append(
                RiskCheckResult(
                    passed=True,
                    rule="short_stop_direction",
                    message="short_stop_direction not applicable for non-short actions.",
                )
            )

        # 8. Portfolio drawdown circuit breaker (buys only)
        if is_open_risk:
            peak_nav = getattr(portfolio, "peak_nav", None)
            if peak_nav is None:
                peak_nav = max(nav, portfolio.initial_capital)
            max_drawdown_pct = getattr(limits, "max_drawdown_pct", 0.15)
            results.append(check_drawdown_limit(nav, peak_nav, max_drawdown_pct))
        else:
            results.append(
                RiskCheckResult(
                    passed=True,
                    rule="drawdown_limit",
                    message="Sell/close/cover not blocked by drawdown limit.",
                )
            )

        # 9. ATR-based position sizing (buys only, when ATR data available)
        if is_open_risk and atrs is not None:
            atr = atrs.get(signal.symbol)
            if atr is not None and atr > 0.0 and price > 0.0:
                results.append(
                    check_volatility_sizing(
                        symbol=signal.symbol,
                        atr=atr,
                        price=price,
                        proposed_size=signal.target_weight,
                        nav=nav,
                        target_risk_pct=getattr(limits, "target_risk_pct", 0.01),
                        deviation_buffer=getattr(limits, "deviation_buffer", 0.20),
                    )
                )
            else:
                results.append(
                    RiskCheckResult(
                        passed=True,
                        rule="volatility_sizing",
                        message=f"{signal.symbol}: ATR unavailable — skipping volatility sizing check.",
                    )
                )
        else:
            results.append(
                RiskCheckResult(
                    passed=True,
                    rule="volatility_sizing",
                    message="Volatility sizing check skipped (risk-reducing action or no ATR data).",
                )
            )

        # 10. ATR-calibrated stop-loss validation (buys only, when ATR data available)
        if is_open_risk and atrs is not None:
            atr = atrs.get(signal.symbol)
            if (
                atr is not None
                and atr > 0.0
                and price > 0.0
                and signal.stop_loss > 0.0
            ):
                # Select multiplier based on asset class
                if asset_class == "crypto":
                    multiplier = getattr(limits, "atr_stop_multiplier_crypto", 2.5)
                elif asset_class == "commodity":
                    multiplier = getattr(limits, "atr_stop_multiplier_commodity", 2.5)
                else:
                    multiplier = getattr(limits, "atr_stop_multiplier", 2.0)

                results.append(
                    check_atr_stop_loss(
                        stop_loss_price=signal.stop_loss,
                        entry_price=price,
                        atr=atr,
                        atr_multiplier=multiplier,
                    )
                )
            else:
                results.append(
                    RiskCheckResult(
                        passed=True,
                        rule="atr_stop_loss",
                        message=f"{signal.symbol}: ATR or stop price unavailable — skipping ATR stop check.",
                    )
                )
        else:
            results.append(
                RiskCheckResult(
                    passed=True,
                    rule="atr_stop_loss",
                    message="ATR stop-loss check skipped (risk-reducing action or no ATR data).",
                )
            )

        # 11-14. Track C kill-switch checks (structural arb / event-driven only)
        if track == "C":
            tc = self.track_c_limits

            # 11. Exchange outage check
            results.append(_check_exchange_outage(db_conn, exchange))

            # 12. Funding reversal check
            results.append(
                _check_funding_reversal(
                    db_conn,
                    signal.symbol,
                    tc.max_funding_rate_pct,
                )
            )

            # 13. Spread collapse check
            results.append(
                _check_spread_collapse(
                    db_conn,
                    signal.symbol,
                    tc.min_spread_bps,
                )
            )

            # 14. Beta breach check (rolling 30d beta to SPY)
            results.append(_check_beta_breach(spy_beta, tc.max_beta_to_spy))
        else:
            # Emit pass placeholders so downstream code sees a consistent
            # result count regardless of track.
            for rule in (
                "tc_exchange_outage",
                "tc_funding_reversal",
                "tc_spread_collapse",
                "tc_beta_breach",
            ):
                results.append(
                    RiskCheckResult(
                        passed=True,
                        rule=rule,
                        message=f"Track C check '{rule}' not applicable for track '{track}'.",
                    )
                )

        return results

    # ------------------------------------------------------------------
    # Batch filtering
    # ------------------------------------------------------------------

    def filter_signals(
        self,
        signals: list[TradeSignal],
        portfolio: Portfolio,
        prices: dict[str, float],
        atrs: dict[str, float] | None = None,
        track: str = "A",
        db_conn: Any | None = None,
        exchange: str = "UNKNOWN",
        spy_beta: float | None = None,
    ) -> tuple[list[TradeSignal], list[tuple[TradeSignal, list[RiskCheckResult]]]]:
        """Filter a batch of signals through the risk gate.

        Parameters
        ----------
        signals:
            Raw signals from the LLM brain.
        portfolio:
            Current portfolio state (already price-updated).
        prices:
            Latest market prices keyed by symbol.
        atrs:
            Optional mapping of symbol → current ATR value.  Forwarded to
            ``check_trade`` to enable ATR-based volatility sizing and
            ATR-calibrated stop-loss checks.
        track:
            Which risk-limit track to apply: ``"A"`` (default),
            ``"B"``, or ``"C"``.  Forwarded to ``check_trade``.
        db_conn:
            Active DuckDB connection for Track C kill-switch queries.
        exchange:
            Exchange identifier for Track C exchange-outage check.
        spy_beta:
            Pre-computed rolling-30d beta to SPY for Track C beta-breach check.

        Returns
        -------
        tuple[list[TradeSignal], list[tuple[TradeSignal, list[RiskCheckResult]]]]
            ``(approved, rejected)`` where *rejected* pairs each signal
            with the full list of check results (including passed ones)
            for transparency.
        """
        # Resolve max_trades_per_session from the active track's limits.
        if track == "C":
            active_limits: Any = self.track_c_limits
        elif track == "B":
            active_limits = self.track_b_limits
        else:
            active_limits = self.limits

        approved: list[TradeSignal] = []
        rejected: list[tuple[TradeSignal, list[RiskCheckResult]]] = []

        n_short = sum(1 for signal in signals if signal.action == Action.SHORT)
        n_cover = sum(1 for signal in signals if signal.action == Action.COVER)
        if n_short or n_cover:
            logger.info("Short signals: %d, Cover signals: %d", n_short, n_cover)

        for signal in signals:
            # HOLD signals pass through without checks – they don't
            # result in a trade.
            if signal.action == Action.HOLD:
                approved.append(signal)
                continue

            checks = self.check_trade(
                signal,
                portfolio,
                prices,
                atrs=atrs,
                track=track,
                db_conn=db_conn,
                exchange=exchange,
                spy_beta=spy_beta,
            )
            failures = [c for c in checks if not c.passed]

            if failures:
                rejected.append((signal, checks))
                for fail in failures:
                    logger.warning(
                        "REJECTED %s %s – %s: %s",
                        signal.action.value.upper(),
                        signal.symbol,
                        fail.rule,
                        fail.message,
                    )
            else:
                approved.append(signal)
                logger.info(
                    "APPROVED %s %s (target_weight=%.2f%%, conviction=%s)",
                    signal.action.value.upper(),
                    signal.symbol,
                    signal.target_weight * 100,
                    signal.conviction.value,
                )

        # Enforce max_positions on entry signals that open new positions.
        max_positions = getattr(active_limits, "max_positions", 0)
        if max_positions and max_positions > 0:
            open_symbols = {
                symbol
                for symbol, pos in portfolio.positions.items()
                if getattr(pos, "shares", 0) != 0
            }
            closing_symbols = {
                sig.symbol
                for sig in approved
                if sig.action == Action.CLOSE
                or (sig.action == Action.SELL and sig.target_weight <= 0)
                or (sig.action == Action.COVER and sig.target_weight <= 0)
            }
            projected_open = open_symbols - closing_symbols

            opening_signals = [
                s for s in approved if s.action in {Action.BUY, Action.SHORT}
            ]
            new_buys = [s for s in opening_signals if s.symbol not in projected_open]
            available_slots = max_positions - len(projected_open)

            if available_slots < len(new_buys):
                conviction_rank = {
                    "high": 0,
                    "medium": 1,
                    "low": 2,
                }
                new_buys.sort(key=lambda s: conviction_rank.get(s.conviction.value, 99))
                keep_ids = {id(s) for s in new_buys[: max(available_slots, 0)]}
                dropped = [s for s in new_buys if id(s) not in keep_ids]

                approved = [s for s in approved if id(s) not in {id(d) for d in dropped}]
                for sig in dropped:
                    rejected.append(
                        (
                            sig,
                            [
                                RiskCheckResult(
                                    passed=False,
                                    rule="max_positions",
                                    message=(
                                        f"Open positions cap reached ({max_positions}). "
                                        f"Signal for {sig.symbol} dropped."
                                    ),
                                    current_value=float(len(projected_open) + len(new_buys)),
                                    limit_value=float(max_positions),
                                )
                            ],
                        )
                    )
                    logger.warning(
                        "DROPPED %s %s – max positions (%d) exceeded.",
                        sig.action.value.upper(),
                        sig.symbol,
                        max_positions,
                    )

        # Enforce max_trades_per_session on the approved list.
        # Prioritise by conviction (HIGH > MEDIUM > LOW), preserving
        # original order within the same conviction tier.
        max_trades = active_limits.max_trades_per_session
        tradeable = [s for s in approved if s.action != Action.HOLD]
        holds = [s for s in approved if s.action == Action.HOLD]

        if len(tradeable) > max_trades:
            conviction_rank = {
                "high": 0,
                "medium": 1,
                "low": 2,
            }
            # Stable sort – preserves input order for equal conviction.
            tradeable.sort(key=lambda s: conviction_rank.get(s.conviction.value, 99))
            trimmed = tradeable[max_trades:]
            tradeable = tradeable[:max_trades]

            for sig in trimmed:
                rejected.append(
                    (
                        sig,
                        [
                            RiskCheckResult(
                                passed=False,
                                rule="max_trades_per_session",
                                message=(
                                    f"Trade limit reached ({max_trades}). "
                                    f"Signal for {sig.symbol} dropped."
                                ),
                                current_value=float(len(tradeable) + len(trimmed)),
                                limit_value=float(max_trades),
                            )
                        ],
                    )
                )
                logger.warning(
                    "DROPPED %s %s – max trades per session (%d) exceeded.",
                    sig.action.value.upper(),
                    sig.symbol,
                    max_trades,
                )

        approved = holds + tradeable

        logger.info(
            "Risk filter: %d approved, %d rejected out of %d signal(s).",
            len(approved),
            len(rejected),
            len(signals),
        )

        return approved, rejected
