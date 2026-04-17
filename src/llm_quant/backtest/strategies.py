"""Additional strategy implementations for backtesting.

Each strategy follows the Strategy ABC contract:
- generate_signals() receives only causal data (up to as_of_date)
- Returns a list of TradeSignals
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from datetime import date
from typing import cast

import polars as pl

from llm_quant.arb.cef_strategy import CEFDiscountRegistryStrategy
from llm_quant.backtest.nlp_signal_strategy import NlpSignalStrategy
from llm_quant.backtest.strategy import SMACrossoverStrategy, Strategy, StrategyConfig
from llm_quant.brain.models import Action, Conviction, TradeSignal
from llm_quant.trading.portfolio import Portfolio

logger = logging.getLogger(__name__)

RowDict = dict[str, object]
ScoreEntry = dict[str, object]


def _compute_momentum_scores(
    indicators_df: pl.DataFrame,
    symbols: list[str],
    lookback: int,
) -> list[tuple[str, float]]:
    """Compute trailing-return momentum scores for given symbols."""
    scores: list[tuple[str, float]] = []
    for symbol in symbols:
        sym_data = indicators_df.filter(pl.col("symbol") == symbol).sort("date")
        if len(sym_data) < lookback:
            continue
        recent = sym_data.tail(lookback)
        row0 = cast("RowDict", recent.row(0, named=True))
        row_last = cast("RowDict", recent.row(-1, named=True))
        first_close_obj = row0.get("close")
        last_close_obj = row_last.get("close")
        if (
            isinstance(first_close_obj, (int, float))
            and isinstance(last_close_obj, (int, float))
            and first_close_obj > 0
        ):
            scores.append(
                (symbol, float(last_close_obj) / float(first_close_obj) - 1.0)
            )
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores


# ---------------------------------------------------------------------------
# RSI Mean Reversion
# ---------------------------------------------------------------------------


class RSIMeanReversionStrategy(Strategy):
    """Mean-reversion strategy based on RSI extremes.

    Buys when RSI < oversold threshold, sells when RSI > overbought.
    """

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        signals: list[TradeSignal] = []
        params = self.config.parameters
        oversold = params.get("rsi_oversold", 30)
        overbought = params.get("rsi_overbought", 70)

        symbols = indicators_df.select("symbol").unique().to_series().to_list()

        for symbol in symbols:
            sym_data = indicators_df.filter(pl.col("symbol") == symbol).sort("date")
            if len(sym_data) < 2 or "rsi_14" not in sym_data.columns:
                continue

            curr = sym_data.tail(1).row(0, named=True)
            rsi = curr.get("rsi_14")
            close = curr["close"]
            if rsi is None:
                continue

            has_position = symbol in portfolio.positions

            if (
                rsi < oversold
                and not has_position
                and len(portfolio.positions) < self.config.max_positions
            ):
                stop_loss = close * (1.0 - self.config.stop_loss_pct)
                signals.append(
                    TradeSignal(
                        symbol=symbol,
                        action=Action.BUY,
                        conviction=Conviction.MEDIUM,
                        target_weight=self.config.target_position_weight,
                        stop_loss=stop_loss,
                        reasoning=f"RSI oversold ({rsi:.1f} < {oversold})",
                    )
                )
            elif rsi > overbought and has_position:
                signals.append(
                    TradeSignal(
                        symbol=symbol,
                        action=Action.CLOSE,
                        conviction=Conviction.MEDIUM,
                        target_weight=0.0,
                        stop_loss=0.0,
                        reasoning=f"RSI overbought ({rsi:.1f} > {overbought})",
                    )
                )

        return signals


class MomentumStrategy(Strategy):
    """Cross-sectional momentum: buy top-N performers, sell bottom-N.

    Ranks symbols by trailing return over lookback_days, then buys the
    top_n and exits positions that drop below top_n ranking.
    """

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        signals: list[TradeSignal] = []
        params = self.config.parameters
        lookback = params.get("lookback_days", 63)
        top_n = params.get("top_n", 5)

        symbols = indicators_df.select("symbol").unique().to_series().to_list()
        momentum_scores = _compute_momentum_scores(indicators_df, symbols, lookback)
        top_symbols = {s for s, _ in momentum_scores[:top_n]}
        scored_symbols = {s for s, _ in momentum_scores}

        # Buy top-N that we don't hold
        new_positions = 0
        for symbol, score in momentum_scores[:top_n]:
            if (
                symbol in portfolio.positions
                or len(portfolio.positions) + new_positions >= self.config.max_positions
            ):
                continue
            close = prices.get(symbol, 0)
            if close <= 0:
                continue
            stop_loss = close * (1.0 - self.config.stop_loss_pct)
            signals.append(
                TradeSignal(
                    symbol=symbol,
                    action=Action.BUY,
                    conviction=Conviction.MEDIUM,
                    target_weight=self.config.target_position_weight,
                    stop_loss=stop_loss,
                    reasoning=(f"Momentum top-{top_n} ({score:.2%} over {lookback}d)"),
                )
            )
            new_positions += 1

        # Close positions that fell out of top-N (only for symbols this strategy scored)
        signals.extend(
            TradeSignal(
                symbol=symbol,
                action=Action.CLOSE,
                conviction=Conviction.LOW,
                target_weight=0.0,
                stop_loss=0.0,
                reasoning=f"Dropped from momentum top-{top_n}",
            )
            for symbol in portfolio.positions
            if symbol not in top_symbols and symbol in scored_symbols
        )

        return signals


# ---------------------------------------------------------------------------
# MACD Trend Following
# ---------------------------------------------------------------------------


class MACDStrategy(Strategy):
    """MACD-based trend following strategy.

    Buys on bullish MACD histogram crossover, sells on bearish.
    """

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        signals: list[TradeSignal] = []

        symbols = indicators_df.select("symbol").unique().to_series().to_list()

        for symbol in symbols:
            sym_data = indicators_df.filter(pl.col("symbol") == symbol).sort("date")
            if len(sym_data) < 2:
                continue
            if "macd_hist" not in sym_data.columns:
                continue

            last = sym_data.tail(2)
            prev = last.row(0, named=True)
            curr = last.row(1, named=True)

            prev_hist = prev.get("macd_hist")
            curr_hist = curr.get("macd_hist")
            close = curr["close"]

            if prev_hist is None or curr_hist is None:
                continue

            has_position = symbol in portfolio.positions

            # Bullish crossover: histogram turns positive
            if (
                prev_hist <= 0
                and curr_hist > 0
                and not has_position
                and len(portfolio.positions) < self.config.max_positions
            ):
                stop_loss = close * (1.0 - self.config.stop_loss_pct)
                signals.append(
                    TradeSignal(
                        symbol=symbol,
                        action=Action.BUY,
                        conviction=Conviction.MEDIUM,
                        target_weight=self.config.target_position_weight,
                        stop_loss=stop_loss,
                        reasoning=f"MACD histogram bullish crossover ({curr_hist:.4f})",
                    )
                )
            # Bearish crossover: histogram turns negative
            elif prev_hist >= 0 and curr_hist < 0 and has_position:
                signals.append(
                    TradeSignal(
                        symbol=symbol,
                        action=Action.CLOSE,
                        conviction=Conviction.MEDIUM,
                        target_weight=0.0,
                        stop_loss=0.0,
                        reasoning=f"MACD histogram bearish crossover ({curr_hist:.4f})",
                    )
                )

        return signals


# ---------------------------------------------------------------------------
# Regime-Aware Momentum
# ---------------------------------------------------------------------------


class RegimeMomentumStrategy(Strategy):
    """Regime-aware momentum: adjusts exposure based on VIX and SMA200.

    Risk-on regime: full momentum allocation
    Transition: half allocation
    Risk-off: reduce to defensive positions only
    """

    def _detect_regime(
        self,
        indicators_df: pl.DataFrame,
        vix_risk_off: float,
        vix_transition: float,
    ) -> str:
        """Classify market regime from VIX level and SPY vs SMA200."""
        regime = "risk_on"
        vix_data = indicators_df.filter(pl.col("symbol") == "VIX").sort("date")
        if len(vix_data) > 0:
            vix_close = vix_data.tail(1).row(0, named=True)["close"]
            if vix_close >= vix_risk_off:
                regime = "risk_off"
            elif vix_close >= vix_transition:
                regime = "transition"

        spy_data = indicators_df.filter(pl.col("symbol") == "SPY").sort("date")
        if len(spy_data) > 0 and "sma_200" in spy_data.columns:
            spy_row = spy_data.tail(1).row(0, named=True)
            sma200 = spy_row.get("sma_200")
            if sma200 is not None and spy_row["close"] < sma200:
                if regime == "risk_on":
                    regime = "transition"
                elif regime == "transition":
                    regime = "risk_off"
        return regime

    def _compute_regime_momentum_scores(
        self,
        indicators_df: pl.DataFrame,
        lookback: int,
    ) -> list[tuple[str, float]]:
        """Compute trailing-return momentum scores, filtered by SMA200."""
        symbols = [
            s
            for s in indicators_df.select("symbol").unique().to_series().to_list()
            if s != "VIX"
        ]
        scores = _compute_momentum_scores(indicators_df, symbols, lookback)

        # Filter out symbols trading below their 200-day SMA
        if "sma_200" in indicators_df.columns:
            filtered: list[tuple[str, float]] = []
            for symbol, ret in scores:
                sym_data = indicators_df.filter(pl.col("symbol") == symbol).sort("date")
                if len(sym_data) == 0:
                    continue
                row = sym_data.tail(1).row(0, named=True)
                sma200 = row.get("sma_200")
                if sma200 is None or row["close"] >= sma200:
                    filtered.append((symbol, ret))
            scores = filtered

        return scores

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        signals: list[TradeSignal] = []
        params = self.config.parameters
        vix_risk_off = params.get(
            "vix_risk_off_threshold", params.get("vix_risk_off", 25)
        )
        vix_transition = params.get(
            "vix_risk_on_threshold", params.get("vix_transition", 20)
        )
        lookback = params.get("momentum_lookback", params.get("lookback_days", 63))
        top_n = params.get("top_n_momentum", params.get("top_n", 5))
        stop_mult = params.get("stop_atr_multiplier", 2.0)
        defensive_symbols = set(
            params.get(
                "defensive_symbols",
                ["TLT", "IEF", "SHY", "GLD", "TIP", "XLP", "XLU", "XLV"],
            )
        )

        regime = self._detect_regime(indicators_df, vix_risk_off, vix_transition)

        # Adjust allocation based on regime
        if regime == "risk_off":
            weight_mult = 0.5
            max_pos = min(3, self.config.max_positions)
        elif regime == "transition":
            weight_mult = 0.75
            max_pos = self.config.max_positions
        else:
            weight_mult = 1.0
            max_pos = self.config.max_positions

        target_weight = self.config.target_position_weight * weight_mult
        scores = self._compute_regime_momentum_scores(indicators_df, lookback)

        # In risk-off, prefer defensive symbols
        if regime == "risk_off":
            defensive = [s for s in scores if s[0] in defensive_symbols]
            offensive = [s for s in scores if s[0] not in defensive_symbols]
            ranked = defensive + offensive
        else:
            ranked = scores

        top_symbols = {s for s, _ in ranked[:top_n]}
        scored_symbols = {s for s, _ in scores}

        # Generate buy signals
        new_positions = 0
        for symbol, score in ranked[:top_n]:
            if (
                symbol not in portfolio.positions
                and len(portfolio.positions) + new_positions < max_pos
            ):
                close = prices.get(symbol, 0)
                if close <= 0:
                    continue

                # ATR-based stop-loss
                sym_data = indicators_df.filter(pl.col("symbol") == symbol).sort("date")
                atr_col = "atr_14"
                if atr_col in sym_data.columns and len(sym_data) > 0:
                    atr_val = sym_data.tail(1).row(0, named=True).get(atr_col)
                    if atr_val and atr_val > 0:
                        stop_loss = close - (stop_mult * atr_val)
                    else:
                        stop_loss = close * (1.0 - self.config.stop_loss_pct)
                else:
                    stop_loss = close * (1.0 - self.config.stop_loss_pct)

                signals.append(
                    TradeSignal(
                        symbol=symbol,
                        action=Action.BUY,
                        conviction=Conviction.MEDIUM,
                        target_weight=target_weight,
                        stop_loss=stop_loss,
                        reasoning=(
                            f"Regime={regime}, momentum rank top-{top_n} "
                            f"({score:.2%} over {lookback}d)"
                        ),
                    )
                )
                new_positions += 1

        # Close positions not in top-N (only for symbols this strategy scored)
        signals.extend(
            TradeSignal(
                symbol=symbol,
                action=Action.CLOSE,
                conviction=Conviction.LOW,
                target_weight=0.0,
                stop_loss=0.0,
                reasoning=f"Regime={regime}, dropped from top-{top_n}",
            )
            for symbol in portfolio.positions
            if symbol not in top_symbols and symbol in scored_symbols
        )

        return signals


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _detect_regime_from_vix(
    indicators_df: pl.DataFrame,
    vix_threshold: float,
) -> str:
    """Classify regime as risk_on or risk_off based on VIX level."""
    vix_data = indicators_df.filter(pl.col("symbol") == "VIX").sort("date")
    if len(vix_data) > 0:
        vix_row = cast("RowDict", vix_data.tail(1).row(0, named=True))
        vix_close = vix_row.get("close")
        if isinstance(vix_close, (int, float)) and vix_close >= vix_threshold:
            return "risk_off"
    return "risk_on"


def _get_atr_stop(
    sym_data: pl.DataFrame,
    close: float,
    stop_mult: float,
    fallback_pct: float,
) -> float:
    """Compute ATR-based stop-loss, falling back to percentage-based."""
    if "atr_14" in sym_data.columns and len(sym_data) > 0:
        atr_row = cast("RowDict", sym_data.tail(1).row(0, named=True))
        atr_val_obj = atr_row.get("atr_14")
        if isinstance(atr_val_obj, (int, float)) and atr_val_obj > 0:
            atr_val = float(atr_val_obj)
            return close - (stop_mult * atr_val)
    return close * (1.0 - fallback_pct)


def _vol_target_weight(
    sym_data: pl.DataFrame,
    base_weight: float,
    target_vol: float,
) -> float:
    """Compute volatility-targeted weight using ATR as vol proxy.

    realized_vol_proxy = ATR_14 * sqrt(252) / close
    weight = base_weight * (target_vol / realized_vol)
    Clamped to [0.01, base_weight * 2].
    """
    if "atr_14" not in sym_data.columns or len(sym_data) == 0:
        return base_weight
    row = cast("RowDict", sym_data.tail(1).row(0, named=True))
    atr_val_obj = row.get("atr_14")
    close_obj = row.get("close", 0)
    if not isinstance(atr_val_obj, (int, float)) or atr_val_obj <= 0:
        return base_weight
    if not isinstance(close_obj, (int, float)) or close_obj <= 0:
        return base_weight
    atr_val = float(atr_val_obj)
    close = float(close_obj)
    realized_vol = atr_val * math.sqrt(252) / close
    if realized_vol <= 0:
        return base_weight
    weight = base_weight * (target_vol / realized_vol)
    return max(0.01, min(weight, base_weight * 2))


def _trailing_return(sym_data: pl.DataFrame, lookback: int) -> float | None:
    """Compute trailing return over lookback days. Returns None if insufficient data."""
    if len(sym_data) < lookback:
        return None
    recent = sym_data.tail(lookback)
    row0 = cast("RowDict", recent.row(0, named=True))
    row_last = cast("RowDict", recent.row(-1, named=True))
    first_close_obj = row0.get("close")
    last_close_obj = row_last.get("close")
    if not isinstance(first_close_obj, (int, float)) or first_close_obj <= 0:
        return None
    if not isinstance(last_close_obj, (int, float)):
        return None
    first_close = float(first_close_obj)
    last_close = float(last_close_obj)
    return last_close / first_close - 1.0


def _close_above_sma(sym_data: pl.DataFrame, sma_col: str = "sma_200") -> bool:
    """Check if latest close is above the given SMA. True if SMA not available."""
    if sma_col not in sym_data.columns or len(sym_data) == 0:
        return True  # no SMA data = no filter applied
    row = cast("RowDict", sym_data.tail(1).row(0, named=True))
    sma_val_obj = row.get(sma_col)
    close_obj = row.get("close")
    if sma_val_obj is None:
        return True
    if not isinstance(sma_val_obj, (int, float)) or not isinstance(
        close_obj, (int, float)
    ):
        return True
    sma_val = float(sma_val_obj)
    close = float(close_obj)
    return close >= sma_val


# ---------------------------------------------------------------------------
# Trend Following (time-series momentum)
# ---------------------------------------------------------------------------


class TrendFollowingStrategy(Strategy):
    """Time-series momentum: go long each asset with positive trailing return.

    Unlike cross-sectional momentum, each asset is evaluated independently.
    Long if: 126d return > 0 AND close > SMA_200.
    Flat if: 126d return <= 0 OR close < SMA_200.
    """

    def _evaluate_symbols(
        self,
        indicators_df: pl.DataFrame,
        symbols: list[str],
        portfolio: Portfolio,
        prices: dict[str, float],
        params: Mapping[str, object],
    ) -> list[TradeSignal]:
        """Evaluate each symbol independently for trend-following signals."""
        signals: list[TradeSignal] = []
        lookback_obj = params["lookback"]
        sma_col_obj = params["sma_col"]
        target_vol_obj = params["target_vol"]
        weight_mult_risk_off_obj = params["weight_mult_risk_off"]
        stop_mult_obj = params["stop_mult"]
        regime_obj = params["regime"]

        if not isinstance(lookback_obj, int):
            return signals
        if not isinstance(sma_col_obj, str):
            return signals
        if not isinstance(target_vol_obj, (int, float)):
            return signals
        if not isinstance(weight_mult_risk_off_obj, (int, float)):
            return signals
        if not isinstance(stop_mult_obj, (int, float)):
            return signals
        if not isinstance(regime_obj, str):
            return signals

        lookback = lookback_obj
        sma_col = sma_col_obj
        target_vol = float(target_vol_obj)
        weight_mult_risk_off = float(weight_mult_risk_off_obj)
        stop_mult = float(stop_mult_obj)
        regime = regime_obj

        min_positive_obj = params.get("min_tf_positive", 1)
        min_positive = min_positive_obj if isinstance(min_positive_obj, int) else 1

        new_positions = 0
        for symbol in symbols:
            sym_data = indicators_df.filter(pl.col("symbol") == symbol).sort("date")
            close = prices.get(symbol, 0)
            if close <= 0 or len(sym_data) < lookback:
                continue

            # Multi-timeframe momentum consensus
            raw_lookbacks = [
                params.get("lookback_short"),
                params.get("lookback_medium"),
                params.get("lookback_long"),
            ]
            lookbacks = [lb for lb in raw_lookbacks if isinstance(lb, int)]
            if not lookbacks:
                lookbacks = [lookback]  # fallback to single timeframe

            timeframe_returns: list[tuple[int, float]] = []
            for lb in lookbacks:
                if not isinstance(lb, int):
                    continue
                ret = _trailing_return(sym_data, lb)
                if ret is not None:
                    timeframe_returns.append((lb, ret))

            positive_count = sum(1 for _, r in timeframe_returns if r > 0)
            above_sma = _close_above_sma(sym_data, sma_col)
            has_position = symbol in portfolio.positions
            is_bullish = positive_count >= min_positive and above_sma

            # Momentum acceleration: short > medium suggests strengthening trend
            has_acceleration = False
            if len(timeframe_returns) >= 2:
                sorted_tfs = sorted(timeframe_returns, key=lambda x: x[0])
                short_ret = sorted_tfs[0][1]
                med_ret = sorted_tfs[len(sorted_tfs) // 2][1]
                if short_ret > med_ret:
                    has_acceleration = True

            if is_bullish and not has_position:
                if (
                    len(portfolio.positions) + new_positions
                    >= self.config.max_positions
                ):
                    continue
                base_weight = self.config.target_position_weight
                if regime == "risk_off":
                    base_weight *= weight_mult_risk_off
                weight = _vol_target_weight(sym_data, base_weight, target_vol)
                stop_loss = _get_atr_stop(
                    sym_data, close, stop_mult, self.config.stop_loss_pct
                )

                if has_acceleration and positive_count == len(timeframe_returns):
                    conviction = Conviction.HIGH
                else:
                    conviction = Conviction.MEDIUM

                tf_summary = ", ".join(f"{lb}d={r:.2%}" for lb, r in timeframe_returns)
                reasoning = (
                    f"Trend-following: [{tf_summary}], "
                    f"{positive_count}/{len(timeframe_returns)} positive, "
                    f"above SMA200, regime={regime}"
                )
                if has_acceleration:
                    reasoning += ", accelerating"

                signals.append(
                    TradeSignal(
                        symbol=symbol,
                        action=Action.BUY,
                        conviction=conviction,
                        target_weight=weight,
                        stop_loss=stop_loss,
                        reasoning=reasoning,
                    )
                )
                new_positions += 1
            elif not is_bullish and has_position:
                tf_summary = ", ".join(f"{lb}d={r:.2%}" for lb, r in timeframe_returns)
                reason = (
                    f"Trend-following exit: [{tf_summary}], "
                    f"{positive_count}/{len(timeframe_returns)} positive"
                )
                if not above_sma:
                    reason += ", below SMA200"
                signals.append(
                    TradeSignal(
                        symbol=symbol,
                        action=Action.CLOSE,
                        conviction=Conviction.MEDIUM,
                        target_weight=0.0,
                        stop_loss=0.0,
                        reasoning=reason,
                    )
                )

        return signals

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        params = self.config.parameters
        lookback_obj = params.get("lookback_days", 126)
        sma_trend_obj = params.get("sma_trend", 200)
        vix_threshold_obj = params.get("vix_threshold", 22)

        lookback = lookback_obj if isinstance(lookback_obj, int) else 126
        sma_trend = sma_trend_obj if isinstance(sma_trend_obj, int) else 200
        vix_threshold = (
            float(vix_threshold_obj)
            if isinstance(vix_threshold_obj, (int, float))
            else 22.0
        )

        regime = _detect_regime_from_vix(indicators_df, vix_threshold)

        symbol_values = indicators_df.select("symbol").unique().to_series().to_list()
        symbols = [s for s in symbol_values if isinstance(s, str) and s != "VIX"]

        lookback_short = params.get("lookback_short", None)
        lookback_medium_obj = params.get("lookback_medium", lookback)
        lookback_medium = (
            lookback_medium_obj if isinstance(lookback_medium_obj, int) else lookback
        )
        lookback_long = params.get("lookback_long", None)
        min_tf_positive = params.get("min_timeframes_positive", 1)

        eval_params: dict[str, object] = {
            "lookback": lookback,
            "sma_col": f"sma_{sma_trend}",
            "target_vol": params.get("target_vol", 0.12),
            "weight_mult_risk_off": params.get("weight_mult_risk_off", 0.50),
            "stop_mult": params.get("stop_atr_multiplier", 1.5),
            "regime": regime,
            "min_tf_positive": min_tf_positive,
        }
        if lookback_short is not None:
            eval_params["lookback_short"] = lookback_short
        if lookback_medium != lookback:
            eval_params["lookback_medium"] = lookback_medium
        if lookback_long is not None:
            eval_params["lookback_long"] = lookback_long

        return self._evaluate_symbols(
            indicators_df, symbols, portfolio, prices, eval_params
        )


class MultiFactorStrategy(Strategy):
    """Multi-factor strategy: momentum + value + quality composite ranking.

    Combines three uncorrelated signals:
    - Momentum: 126d trailing return (higher = better)
    - Value: RSI_14 inverted (lower RSI = more value/oversold)
    - Quality: inverse realized volatility (lower vol = higher quality)
    """

    def _score_universe(
        self,
        indicators_df: pl.DataFrame,
        symbols: list[str],
        momentum_lookback: int,
        sma_col: str,
    ) -> list[ScoreEntry]:
        """Score each symbol on momentum, value, and quality factors."""
        scored: list[ScoreEntry] = []
        for symbol in symbols:
            sym_data = indicators_df.filter(pl.col("symbol") == symbol).sort("date")
            if len(sym_data) < momentum_lookback:
                continue
            row = cast("RowDict", sym_data.tail(1).row(0, named=True))
            close_obj = row.get("close")
            if not isinstance(close_obj, (int, float)):
                continue
            close = float(close_obj)
            if close <= 0:
                continue

            mom = _trailing_return(sym_data, momentum_lookback)
            if mom is None:
                continue

            rsi_obj = row.get("rsi_14") if "rsi_14" in sym_data.columns else None
            if not isinstance(rsi_obj, (int, float)):
                continue
            rsi = float(rsi_obj)
            value = 100.0 - rsi

            atr_obj = row.get("atr_14") if "atr_14" in sym_data.columns else None
            if isinstance(atr_obj, (int, float)) and atr_obj > 0:
                atr_val = float(atr_obj)
                vol_proxy = atr_val * math.sqrt(252) / close
                quality = 1.0 / vol_proxy if vol_proxy > 0 else 0.0
            else:
                quality = 0.0

            above_sma = _close_above_sma(sym_data, sma_col)

            scored.append(
                {
                    "symbol": symbol,
                    "momentum": mom,
                    "value": value,
                    "quality": quality,
                    "above_sma": above_sma,
                    "close": close,
                    "sym_data": sym_data,
                }
            )
        return scored

    def _normalize_and_rank(
        self,
        scored: list[ScoreEntry],
        mom_w: float,
        val_w: float,
        qual_w: float,
    ) -> list[ScoreEntry]:
        """Z-score normalize factors and compute composite score."""
        if len(scored) < 2:
            return scored

        for factor in ("momentum", "value", "quality"):
            vals = [
                float(factor_value)
                for s in scored
                for factor_value in [s.get(factor)]
                if isinstance(factor_value, (int, float))
            ]
            if len(vals) != len(scored):
                continue
            mean = sum(vals) / len(vals)
            std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
            for s in scored:
                factor_value = s.get(factor)
                numeric_factor = (
                    float(factor_value)
                    if isinstance(factor_value, (int, float))
                    else 0.0
                )
                if std > 0:
                    s[f"{factor}_z"] = (numeric_factor - mean) / std
                else:
                    s[f"{factor}_z"] = 0.0

        for s in scored:
            momentum_z_obj = s.get("momentum_z", 0.0)
            value_z_obj = s.get("value_z", 0.0)
            quality_z_obj = s.get("quality_z", 0.0)
            momentum_z = (
                float(momentum_z_obj)
                if isinstance(momentum_z_obj, (int, float))
                else 0.0
            )
            value_z = (
                float(value_z_obj) if isinstance(value_z_obj, (int, float)) else 0.0
            )
            quality_z = (
                float(quality_z_obj) if isinstance(quality_z_obj, (int, float)) else 0.0
            )
            s["composite"] = mom_w * momentum_z + val_w * value_z + qual_w * quality_z

        scored.sort(
            key=lambda x: (
                float(composite)
                if isinstance((composite := x.get("composite", 0.0)), (int, float))
                else 0.0
            ),
            reverse=True,
        )
        return scored

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        signals: list[TradeSignal] = []
        params = self.config.parameters
        momentum_lookback = params.get(
            "momentum_lookback", params.get("lookback_days", 126)
        )
        top_n = params.get("top_n", 7)
        mom_w = params.get("momentum_weight", 0.40)
        val_w = params.get("value_weight", 0.30)
        qual_w = params.get("quality_weight", 0.30)
        target_vol = params.get("target_vol", 0.12)
        stop_mult = params.get("stop_atr_multiplier", 1.5)
        vix_risk_off = params.get("vix_risk_off", 25)
        sma_trend = params.get("sma_trend", 200)

        regime = _detect_regime_from_vix(indicators_df, vix_risk_off)
        sma_col = f"sma_{sma_trend}"

        symbols = [
            s
            for s in indicators_df.select("symbol").unique().to_series().to_list()
            if s != "VIX"
        ]

        scored = self._score_universe(
            indicators_df, symbols, momentum_lookback, sma_col
        )
        scored = self._normalize_and_rank(scored, mom_w, val_w, qual_w)

        # Filter: composite > 0 AND above SMA trend filter
        eligible = [
            s
            for s in scored
            if isinstance(s.get("composite"), (int, float))
            and float(cast("int | float", s.get("composite"))) > 0
            and bool(s.get("above_sma", False))
        ]
        top_symbols = {
            str(s["symbol"])
            for s in eligible[:top_n]
            if isinstance(s.get("symbol"), str)
        }
        all_scored_symbols = {
            str(s["symbol"]) for s in scored if isinstance(s.get("symbol"), str)
        }

        # Generate buy signals for top-N
        new_positions = 0
        for entry in eligible[:top_n]:
            symbol = str(entry["symbol"])
            if symbol in portfolio.positions:
                continue
            if len(portfolio.positions) + new_positions >= self.config.max_positions:
                continue
            close = prices.get(symbol, 0)
            if close <= 0:
                continue

            base_weight = self.config.target_position_weight
            if regime == "risk_off":
                base_weight *= 0.5
            sym_data = entry.get("sym_data")
            if not isinstance(sym_data, pl.DataFrame):
                continue
            weight = _vol_target_weight(sym_data, base_weight, target_vol)
            stop_loss = _get_atr_stop(
                sym_data, close, stop_mult, self.config.stop_loss_pct
            )

            signals.append(
                TradeSignal(
                    symbol=symbol,
                    action=Action.BUY,
                    conviction=Conviction.MEDIUM,
                    target_weight=weight,
                    stop_loss=stop_loss,
                    reasoning=(
                        f"Multi-factor top-{top_n}: composite={entry['composite']:.2f} "
                        f"(mom={entry['momentum_z']:.2f}, val={entry['value_z']:.2f}, "
                        f"qual={entry['quality_z']:.2f}), regime={regime}"
                    ),
                )
            )
            new_positions += 1

        # Close positions not in top-N (only for symbols we scored)
        signals.extend(
            TradeSignal(
                symbol=symbol,
                action=Action.CLOSE,
                conviction=Conviction.LOW,
                target_weight=0.0,
                stop_loss=0.0,
                reasoning=f"Multi-factor: dropped from top-{top_n}",
            )
            for symbol in portfolio.positions
            if symbol not in top_symbols and symbol in all_scored_symbols
        )

        return signals


# ---------------------------------------------------------------------------
# Correlation Regime Strategy (A8: SPY-TLT correlation flip signal)
# ---------------------------------------------------------------------------


class CorrelationRegimeStrategy(Strategy):
    """Rolling correlation regime strategy (stateless).

    Holds equity_symbol when the N-day rolling correlation between equity_symbol
    and hedge_symbol daily returns is below exit_threshold (normal regime).
    Exits to cash when correlation rises above exit_threshold (stress regime).
    Defaults to SPY (equity) / TLT (hedge) for backward compatibility.

    Parameters (via StrategyConfig.parameters):
      equity_symbol (str, default "SPY"): The equity/risk asset to hold.
      hedge_symbol (str, default "TLT"): The hedge/safe-haven asset for correlation.
      corr_window (int, default 10): Rolling window for correlation.
      corr_exit_threshold (float, default 0.0): Exit equity when corr > this.
      corr_entry_threshold (float, default 0.0): Enter equity when corr <= this.
      spy_weight_risk_on (float, default 0.95): Target weight in normal regime.
    """

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        params = self.config.parameters or {}
        equity_sym: str = str(params.get("equity_symbol", "SPY"))
        hedge_sym: str = str(params.get("hedge_symbol", "TLT"))
        corr_window: int = int(params.get("corr_window", 10))
        exit_thresh: float = float(params.get("corr_exit_threshold", 0.0))
        entry_thresh: float = float(params.get("corr_entry_threshold", 0.0))
        risk_on_weight: float = float(params.get("spy_weight_risk_on", 0.95))

        # ── Compute rolling correlation ───────────────────────────────────────
        eq_data = (
            indicators_df.filter(pl.col("symbol") == equity_sym)
            .sort("date")
            .tail(corr_window + 2)
        )
        hedge_data = (
            indicators_df.filter(pl.col("symbol") == hedge_sym)
            .sort("date")
            .tail(corr_window + 2)
        )

        if len(eq_data) < corr_window + 1 or len(hedge_data) < corr_window + 1:
            return []

        eq_prices = eq_data["close"].to_list()
        hedge_prices = hedge_data["close"].to_list()
        min_len = min(len(eq_prices), len(hedge_prices))

        # Compute daily returns
        eq_rets = [eq_prices[i] / eq_prices[i - 1] - 1.0 for i in range(1, min_len)]
        hedge_rets = [
            hedge_prices[i] / hedge_prices[i - 1] - 1.0 for i in range(1, min_len)
        ]

        if len(eq_rets) < corr_window:
            return []

        def _corr(xs: list[float], ys: list[float]) -> float:
            n = len(xs)
            if n < 2:
                return 0.0
            mx = sum(xs) / n
            my = sum(ys) / n
            cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
            sx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
            sy = (sum((y - my) ** 2 for y in ys)) ** 0.5
            if sx == 0 or sy == 0:
                return 0.0
            return float(cov / (sx * sy))

        corr_now = _corr(eq_rets[-corr_window:], hedge_rets[-corr_window:])

        eq_price = prices.get(equity_sym)
        if eq_price is None or eq_price <= 0:
            return []

        has_eq = equity_sym in portfolio.positions

        # ── Stress regime: correlation positive → exit equity ─────────────────
        if corr_now > exit_thresh and has_eq:
            logger.info(
                "CorrelationRegime: EXIT on %s (corr=%.3f > threshold=%.3f)",
                as_of_date,
                corr_now,
                exit_thresh,
            )
            return [
                TradeSignal(
                    symbol=equity_sym,
                    action=Action.CLOSE,
                    conviction=Conviction.HIGH,
                    target_weight=0.0,
                    stop_loss=0.0,
                    reasoning=f"Correlation stress: {corr_now:.3f} > {exit_thresh}",
                )
            ]

        # ── Normal regime: correlation low → hold/enter equity ────────────────
        if corr_now <= entry_thresh and not has_eq:
            logger.info(
                "CorrelationRegime: ENTER on %s (corr=%.3f <= threshold=%.3f)",
                as_of_date,
                corr_now,
                entry_thresh,
            )
            return [
                TradeSignal(
                    symbol=equity_sym,
                    action=Action.BUY,
                    conviction=Conviction.MEDIUM,
                    target_weight=risk_on_weight,
                    stop_loss=eq_price * 0.95,
                    reasoning=f"Correlation normal: {corr_now:.3f} <= {entry_thresh}",
                )
            ]

        return []


# ---------------------------------------------------------------------------
# Correlation Surprise Strategy
# ---------------------------------------------------------------------------


class CorrelationSurpriseStrategy(Strategy):
    """SPY-TLT delta-correlation regime strategy (M7).

    Defensive when the N-day rolling correlation between SPY and TLT *changes*
    by more than delta_threshold in delta_window days. Rapid correlation shifts
    signal regime change / elevated systemic risk.

    Parameters (via StrategyConfig.parameters):
      corr_window (int, default 10): Rolling window for correlation.
      delta_window (int, default 5): Days over which to measure delta.
      delta_threshold (float, default 0.3): Exit when delta > this.
      spy_weight_risk_on (float, default 0.95): Target SPY weight in normal regime.
    """

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        params = self.config.parameters or {}
        corr_window: int = int(params.get("corr_window", 10))
        delta_window: int = int(params.get("delta_window", 5))
        delta_threshold: float = float(params.get("delta_threshold", 0.3))
        risk_on_weight: float = float(params.get("spy_weight_risk_on", 0.95))

        lookback = corr_window + delta_window + 2
        spy_data = (
            indicators_df.filter(pl.col("symbol") == "SPY").sort("date").tail(lookback)
        )
        tlt_data = (
            indicators_df.filter(pl.col("symbol") == "TLT").sort("date").tail(lookback)
        )

        if len(spy_data) < lookback - 1 or len(tlt_data) < lookback - 1:
            return []

        spy_prices = spy_data["close"].to_list()
        tlt_prices = tlt_data["close"].to_list()
        min_len = min(len(spy_prices), len(tlt_prices))

        spy_rets = [spy_prices[i] / spy_prices[i - 1] - 1.0 for i in range(1, min_len)]
        tlt_rets = [tlt_prices[i] / tlt_prices[i - 1] - 1.0 for i in range(1, min_len)]

        if len(spy_rets) < corr_window + delta_window:
            return []

        def _corr(xs: list[float], ys: list[float]) -> float:
            n = len(xs)
            if n < 2:
                return 0.0
            mx = sum(xs) / n
            my = sum(ys) / n
            cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
            sx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
            sy = (sum((y - my) ** 2 for y in ys)) ** 0.5
            if sx == 0 or sy == 0:
                return 0.0
            return cov / (sx * sy)

        corr_now = _corr(spy_rets[-corr_window:], tlt_rets[-corr_window:])
        corr_past = _corr(
            spy_rets[-(corr_window + delta_window) : -delta_window],
            tlt_rets[-(corr_window + delta_window) : -delta_window],
        )
        corr_delta = corr_now - corr_past

        spy_price = prices.get("SPY")
        if spy_price is None or spy_price <= 0:
            return []

        has_spy = "SPY" in portfolio.positions

        if corr_delta > delta_threshold and has_spy:
            logger.info(
                "CorrelationSurprise: EXIT on %s (delta=%.3f > %.3f)",
                as_of_date,
                corr_delta,
                delta_threshold,
            )
            return [
                TradeSignal(
                    symbol="SPY",
                    action=Action.CLOSE,
                    conviction=Conviction.HIGH,
                    target_weight=0.0,
                    stop_loss=0.0,
                    reasoning=(
                        f"Correlation surprise: delta={corr_delta:.3f}"
                        f" > {delta_threshold}"
                    ),
                )
            ]

        if corr_delta <= 0.0 and not has_spy:
            logger.info(
                "CorrelationSurprise: ENTER on %s (delta=%.3f <= 0)",
                as_of_date,
                corr_delta,
            )
            return [
                TradeSignal(
                    symbol="SPY",
                    action=Action.BUY,
                    conviction=Conviction.MEDIUM,
                    target_weight=risk_on_weight,
                    stop_loss=spy_price * 0.95,
                    reasoning=f"Correlation stable: delta={corr_delta:.3f} <= 0",
                )
            ]

        return []


# ---------------------------------------------------------------------------
# Calendar Event Strategy
# ---------------------------------------------------------------------------

# FOMC meeting dates 2021-2026 (day of meeting; entry = 1-3 days before)
_FOMC_DATES: frozenset[date] = frozenset(
    date(int(y), int(m), int(d))
    for y, m, d in [
        (2021, 1, 27),
        (2021, 3, 17),
        (2021, 4, 28),
        (2021, 6, 16),
        (2021, 7, 28),
        (2021, 9, 22),
        (2021, 11, 3),
        (2021, 12, 15),
        (2022, 1, 26),
        (2022, 3, 16),
        (2022, 5, 4),
        (2022, 6, 15),
        (2022, 7, 27),
        (2022, 9, 21),
        (2022, 11, 2),
        (2022, 12, 14),
        (2023, 2, 1),
        (2023, 3, 22),
        (2023, 5, 3),
        (2023, 6, 14),
        (2023, 7, 26),
        (2023, 9, 20),
        (2023, 11, 1),
        (2023, 12, 13),
        (2024, 1, 31),
        (2024, 3, 20),
        (2024, 5, 1),
        (2024, 6, 12),
        (2024, 7, 31),
        (2024, 9, 18),
        (2024, 11, 7),
        (2024, 12, 18),
        (2025, 1, 29),
        (2025, 3, 19),
        (2025, 5, 7),
        (2025, 6, 18),
        (2025, 7, 30),
        (2025, 9, 17),
        (2025, 11, 5),
        (2025, 12, 17),
        (2026, 1, 28),
        (2026, 3, 18),
        (2026, 4, 29),
    ]
)


def _is_pre_fomc(d: date, pre_days: int = 3) -> bool:
    """True if d falls within pre_days before an FOMC meeting."""
    from datetime import timedelta

    for offset in range(1, pre_days + 1):
        if d + timedelta(days=offset) in _FOMC_DATES:
            return True
    return False


def _is_month_end_window(d: date, end_days: int = 1, start_days: int = 1) -> bool:
    """True if d is within end_days of month end or start_days of month start."""
    import calendar

    last_day = calendar.monthrange(d.year, d.month)[1]
    if d.day >= last_day - end_days:
        return True
    return d.day <= start_days


class CalendarEventStrategy(Strategy):
    """Entry around predictable calendar events (month-end / pre-FOMC).

    Modes:
      - "month_end": Hold SPY during last N days of month + first N days of next.
      - "pre_fomc": Hold TLT in the 3 days before each FOMC meeting date.

    Parameters (via StrategyConfig.parameters):
      mode (str, default "month_end"): "month_end" or "pre_fomc".
      target_symbol (str): Asset to trade ("SPY" for month_end, "TLT" for pre_fomc).
      pre_days (int, default 3): Days before event to enter.
      target_weight (float, default 0.95): Position weight during event window.
    """

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        params = self.config.parameters or {}
        mode = str(params.get("mode", "month_end"))
        pre_days_obj = params.get("pre_days", 3)
        target_weight_obj = params.get("target_weight", 0.95)
        pre_days = (
            int(pre_days_obj) if isinstance(pre_days_obj, (int, float, str)) else 3
        )
        tgt_weight = (
            float(target_weight_obj)
            if isinstance(target_weight_obj, (int, float, str))
            else 0.95
        )

        if mode == "pre_fomc":
            symbol = str(params.get("target_symbol", "TLT"))
            in_window = _is_pre_fomc(as_of_date, pre_days=pre_days)
        else:  # month_end
            symbol = str(params.get("target_symbol", "SPY"))
            end_days = int(params.get("end_days", 1))
            start_days = int(params.get("start_days", 1))
            in_window = _is_month_end_window(as_of_date, end_days, start_days)

        price = prices.get(symbol)
        if price is None or price <= 0:
            return []

        has_pos = symbol in portfolio.positions

        if in_window and not has_pos:
            logger.info("CalendarEvent[%s]: ENTER %s on %s", mode, symbol, as_of_date)
            return [
                TradeSignal(
                    symbol=symbol,
                    action=Action.BUY,
                    conviction=Conviction.MEDIUM,
                    target_weight=tgt_weight,
                    stop_loss=price * 0.97,
                    reasoning=f"Calendar event window ({mode}): enter {symbol}",
                )
            ]

        if not in_window and has_pos:
            logger.info("CalendarEvent[%s]: EXIT %s on %s", mode, symbol, as_of_date)
            return [
                TradeSignal(
                    symbol=symbol,
                    action=Action.CLOSE,
                    conviction=Conviction.HIGH,
                    target_weight=0.0,
                    stop_loss=0.0,
                    reasoning=f"Calendar event window closed ({mode}): exit {symbol}",
                )
            ]

        return []


# ---------------------------------------------------------------------------
# Pairs Ratio Mean-Reversion Strategy
# ---------------------------------------------------------------------------


class PairsRatioStrategy(Strategy):
    """Bollinger Band mean-reversion on the ratio of two assets (D7 / O-series).

    Computes ratio = price_a / price_b and fits Bollinger Bands. Trades mean
    reversion: when ratio is stretched above upper band, buy the underperformer
    (symbol_b); when below lower band, buy the outperformer (symbol_a).
    Exits when ratio returns within exit_z sigma of the mean.

    Parameters (via StrategyConfig.parameters):
      symbol_a (str): Numerator asset (default "ETH-USD").
      symbol_b (str): Denominator asset (default "BTC-USD").
      bb_window (int, default 20): Bollinger Band lookback (single-window mode).
      bb_std (float, default 2.0): Band width in standard deviations.
      exit_z (float, default 0.5): Exit when |z| < this threshold.
      target_weight (float, default 0.90): Position weight when in trade.
      consensus_windows (list[int], optional): When set, compute BB z-scores for
        each window and require a majority (>= ceil(N/2)) to agree on direction
        before entering or exiting. Overrides bb_window for multi-window mode.
        Example: [60, 90, 120] — at least 2 of 3 windows must agree.
    """

    @staticmethod
    def _bb_z(ratios: list[float], window: int) -> float | None:
        """Compute z-score of current ratio vs BB for the given window."""
        if len(ratios) < window:
            return None
        w_ratios = ratios[-window:]
        mean_r = sum(w_ratios) / window
        std_r = (sum((r - mean_r) ** 2 for r in w_ratios) / window) ** 0.5
        if std_r == 0:
            return None
        return (ratios[-1] - mean_r) / std_r

    def generate_signals(  # noqa: PLR0911
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        params = self.config.parameters or {}
        symbol_a: str = str(params.get("symbol_a", "ETH-USD"))
        symbol_b: str = str(params.get("symbol_b", "BTC-USD"))
        bb_window: int = int(params.get("bb_window", 20))
        bb_std: float = float(params.get("bb_std", 2.0))
        tgt_weight: float = float(params.get("target_weight", 0.90))
        exit_z: float = float(params.get("exit_z", 0.5))

        # Multi-window consensus mode: list of window sizes.
        raw_cw = params.get("consensus_windows", None)
        if raw_cw is not None:
            windows: list[int] = [int(w) for w in raw_cw]
        else:
            windows = [bb_window]
        max_window = max(windows)

        a_data = (
            indicators_df.filter(pl.col("symbol") == symbol_a)
            .sort("date")
            .tail(max_window + 2)
        )
        b_data = (
            indicators_df.filter(pl.col("symbol") == symbol_b)
            .sort("date")
            .tail(max_window + 2)
        )

        min_len = min(len(a_data), len(b_data))
        if min_len < max_window:
            return []

        a_prices = a_data["close"].to_list()[-min_len:]
        b_prices = b_data["close"].to_list()[-min_len:]

        ratios = [a_prices[i] / b_prices[i] for i in range(min_len) if b_prices[i] > 0]
        if len(ratios) < max_window:
            return []

        # Compute z-scores and tally votes across all windows.
        z_scores: list[float] = []
        votes_buy_b = 0  # ratio stretched up → buy symbol_b
        votes_buy_a = 0  # ratio stretched down → buy symbol_a
        votes_exit = 0  # ratio reverted → exit

        for w in windows:
            z = self._bb_z(ratios, w)
            if z is None:
                continue
            z_scores.append(z)
            if z > bb_std:
                votes_buy_b += 1
            elif z < -bb_std:
                votes_buy_a += 1
            if abs(z) < exit_z:
                votes_exit += 1

        if not z_scores:
            return []

        n_valid = len(z_scores)
        majority = (n_valid + 1) // 2  # ceil(N/2) — majority threshold

        has_a = symbol_a in portfolio.positions
        has_b = symbol_b in portfolio.positions
        price_a = prices.get(symbol_a, 0)
        price_b = prices.get(symbol_b, 0)
        z_now = z_scores[-1]

        # Ratio stretched up: symbol_a expensive → buy symbol_b
        if votes_buy_b >= majority and not has_b and not has_a:
            if price_b <= 0:
                return []
            logger.info(
                "PairsRatio: BUY %s on %s (z=%.2f, %d/%d windows agree)",
                symbol_b,
                as_of_date,
                z_now,
                votes_buy_b,
                n_valid,
            )
            return [
                TradeSignal(
                    symbol=symbol_b,
                    action=Action.BUY,
                    conviction=Conviction.MEDIUM,
                    target_weight=tgt_weight,
                    stop_loss=price_b * 0.90,
                    reasoning=(
                        f"Ratio stretched up: z={z_now:.2f}"
                        f" ({votes_buy_b}/{n_valid} windows), buy {symbol_b}"
                    ),
                )
            ]

        # Ratio stretched down: symbol_b expensive → buy symbol_a
        if votes_buy_a >= majority and not has_a and not has_b:
            if price_a <= 0:
                return []
            logger.info(
                "PairsRatio: BUY %s on %s (z=%.2f, %d/%d windows agree)",
                symbol_a,
                as_of_date,
                z_now,
                votes_buy_a,
                n_valid,
            )
            return [
                TradeSignal(
                    symbol=symbol_a,
                    action=Action.BUY,
                    conviction=Conviction.MEDIUM,
                    target_weight=tgt_weight,
                    stop_loss=price_a * 0.90,
                    reasoning=(
                        f"Ratio stretched down: z={z_now:.2f}"
                        f" ({votes_buy_a}/{n_valid} windows), buy {symbol_a}"
                    ),
                )
            ]

        # Mean-reversion exit: majority windows within exit_z of mean
        if votes_exit >= majority and (has_a or has_b):
            return [
                TradeSignal(
                    symbol=sym,
                    action=Action.CLOSE,
                    conviction=Conviction.HIGH,
                    target_weight=0.0,
                    stop_loss=0.0,
                    reasoning=(
                        f"Pairs ratio reverted: z={z_now:.2f}"
                        f" ({votes_exit}/{n_valid} windows)"
                    ),
                )
                for sym in [symbol_a, symbol_b]
                if sym in portfolio.positions
            ]

        return []


# ---------------------------------------------------------------------------
# Lead-Lag Strategy
# ---------------------------------------------------------------------------


class LeadLagStrategy(Strategy):
    """Directional signal from a leading asset's lagged return (H-series).

    Observes the N-day return of a *leader* asset (e.g. XLF, HYG, BTC-USD)
    and takes a position in a *follower* asset (e.g. SPY, EEM) when the
    lagged signal is strong enough.

    Parameters:
      leader_symbol (str): Leading asset ticker (e.g. "XLF").
      follower_symbol (str): Follower asset ticker (e.g. "SPY").
      lag_days (int, default 2): How many days ago the leader signal is read.
      signal_window (int, default 3): Return window on the leader.
      entry_threshold (float, default 0.01): Min leader return to go long follower.
      exit_threshold (float, default -0.005): Leader return below which to exit.
      target_weight (float, default 0.90): Weight for follower when in trade.
      inverse (bool-like, default False): If True, leader up → short follower.

    Extended structural controls used by governed successor variants such as
    `soxx-qqq-lead-lag-v3`:
      entry_threshold_lower / entry_threshold_upper: Tiered entry bands.
      target_weight_lower / target_weight_upper: Tiered follower sizing.
      confirmation_window / confirmation_threshold: Follower confirmation filter.
      max_holding_days: Time stop for stale trades.
      cooldown_days_after_exit: Re-entry cooldown after a close.
    """

    @staticmethod
    def _extract_position_days_held(position: object) -> int:
        days_held = getattr(position, "days_held", None)
        if isinstance(position, dict):
            days_held = position.get("days_held", days_held)
        if days_held is None:
            return 0
        try:
            return int(days_held)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _extract_position_exit_cooldown(position: object) -> int:
        cooldown = getattr(position, "exit_cooldown_days", None)
        if isinstance(position, dict):
            cooldown = position.get("exit_cooldown_days", cooldown)
        if cooldown is None:
            return 0
        try:
            return int(cooldown)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _trailing_return_from_prices(
        prices_list: list[float],
        window: int,
        lag_days: int = 0,
    ) -> float | None:
        end_idx = len(prices_list) - lag_days
        start_idx = end_idx - window
        if start_idx < 0 or end_idx <= 0:
            return None
        start_price = prices_list[start_idx]
        end_price = prices_list[end_idx - 1]
        if start_price <= 0:
            return None
        return float(end_price / start_price - 1.0)

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        params = self.config.parameters or {}
        leader: str = str(params.get("leader_symbol", "XLF"))
        follower: str = str(params.get("follower_symbol", "SPY"))
        lag_days: int = int(params.get("lag_days", 2))
        sig_window: int = int(params.get("signal_window", 3))
        exit_thresh: float = float(params.get("exit_threshold", -0.005))
        inverse_raw = params.get("inverse", False)
        if isinstance(inverse_raw, bool):
            inverse = inverse_raw
        elif isinstance(inverse_raw, str):
            inverse = inverse_raw.strip().lower() in {"1", "true", "yes", "on"}
        else:
            inverse = bool(inverse_raw)

        entry_thresh = params.get("entry_threshold")
        entry_thresh_lower = float(
            params.get(
                "entry_threshold_lower",
                entry_thresh if entry_thresh is not None else 0.01,
            )
        )
        entry_thresh_upper = params.get("entry_threshold_upper")
        if entry_thresh_upper is not None:
            entry_thresh_upper = float(entry_thresh_upper)

        target_weight_raw = params.get("target_weight", 0.90)
        target_weight_base = (
            float(target_weight_raw)
            if isinstance(target_weight_raw, (int, float, str))
            else 0.90
        )
        target_weight_lower_raw = params.get("target_weight_lower", target_weight_base)
        target_weight_lower = (
            float(target_weight_lower_raw)
            if isinstance(target_weight_lower_raw, (int, float, str))
            else target_weight_base
        )
        target_weight_upper_raw = params.get(
            "target_weight_upper",
            (
                target_weight_base
                if entry_thresh_upper is not None
                else target_weight_lower
            ),
        )
        target_weight_upper = (
            float(target_weight_upper_raw)
            if isinstance(target_weight_upper_raw, (int, float, str))
            else target_weight_lower
        )

        confirmation_window = params.get("confirmation_window")
        confirmation_threshold_raw = params.get("confirmation_threshold", 0.0)
        confirmation_threshold = (
            float(confirmation_threshold_raw)
            if isinstance(confirmation_threshold_raw, (int, float, str))
            else 0.0
        )
        max_holding_days = params.get("max_holding_days")
        cooldown_days_after_exit = int(params.get("cooldown_days_after_exit", 0))

        lookback = sig_window + lag_days + 2
        if confirmation_window is not None:
            lookback = max(lookback, int(confirmation_window) + 2)

        leader_data = (
            indicators_df.filter(pl.col("symbol") == leader).sort("date").tail(lookback)
        )
        if len(leader_data) < sig_window + lag_days:
            return []

        leader_prices = leader_data["close"].to_list()
        leader_ret = self._trailing_return_from_prices(
            leader_prices,
            sig_window,
            lag_days=lag_days,
        )
        if leader_ret is None:
            return []

        follower_price = prices.get(follower, 0)
        if follower_price <= 0:
            return []

        follower_confirmation_ret: float | None = None
        if confirmation_window is not None:
            follower_window = int(confirmation_window)
            follower_data = (
                indicators_df.filter(pl.col("symbol") == follower)
                .sort("date")
                .tail(follower_window + 2)
            )
            if len(follower_data) < follower_window:
                return []
            follower_prices = follower_data["close"].to_list()
            follower_confirmation_ret = self._trailing_return_from_prices(
                follower_prices,
                follower_window,
                lag_days=0,
            )
            if follower_confirmation_ret is None:
                return []

        position = portfolio.positions.get(follower)
        has_pos = follower in portfolio.positions
        days_held = self._extract_position_days_held(position) if has_pos else 0
        cooldown_remaining = (
            self._extract_position_exit_cooldown(position) if has_pos else 0
        )

        confirmation_pass = (
            True
            if follower_confirmation_ret is None
            else follower_confirmation_ret >= confirmation_threshold
        )

        if inverse:
            signal_long_lower = leader_ret <= -entry_thresh_lower
            signal_long_upper = (
                leader_ret <= -entry_thresh_upper
                if entry_thresh_upper is not None
                else False
            )
            signal_exit = leader_ret >= -exit_thresh
        else:
            signal_long_lower = leader_ret >= entry_thresh_lower
            signal_long_upper = (
                leader_ret >= entry_thresh_upper
                if entry_thresh_upper is not None
                else False
            )
            signal_exit = leader_ret <= exit_thresh

        target_weight_for_signal = target_weight_lower
        if signal_long_upper and entry_thresh_upper is not None:
            target_weight_for_signal = target_weight_upper

        if (
            signal_exit
            or (
                max_holding_days is not None
                and has_pos
                and days_held >= int(max_holding_days)
            )
        ) and has_pos:
            reason = f"Lead-lag: {leader} {leader_ret:.3f}<={exit_thresh}"
            if max_holding_days is not None and days_held >= int(max_holding_days):
                reason = (
                    f"Lead-lag time stop: held {days_held}d >= {int(max_holding_days)}d"
                )
            logger.info(
                "LeadLag: EXIT %s on %s (leader=%s ret=%.3f)",
                follower,
                as_of_date,
                leader,
                leader_ret,
            )
            return [
                TradeSignal(
                    symbol=follower,
                    action=Action.CLOSE,
                    conviction=Conviction.HIGH,
                    target_weight=0.0,
                    stop_loss=0.0,
                    reasoning=reason,
                    metadata={
                        "exit_cooldown_days": cooldown_days_after_exit,
                    },
                )
            ]

        if (
            signal_long_lower
            and confirmation_pass
            and not has_pos
            and cooldown_remaining <= 0
        ):
            logger.info(
                "LeadLag: ENTER %s on %s (leader=%s ret=%.3f)",
                follower,
                as_of_date,
                leader,
                leader_ret,
            )
            reasoning = (
                f"Lead-lag: {leader} {leader_ret:.3f}>={entry_thresh_lower} "
                f"target_weight={target_weight_for_signal:.2f}"
            )
            if follower_confirmation_ret is not None:
                reasoning += (
                    f", follower_confirm={follower_confirmation_ret:.3f}"
                    f">={confirmation_threshold:.3f}"
                )
            return [
                TradeSignal(
                    symbol=follower,
                    action=Action.BUY,
                    conviction=Conviction.MEDIUM,
                    target_weight=target_weight_for_signal,
                    stop_loss=follower_price * 0.95,
                    reasoning=reasoning,
                    metadata={
                        "days_held": 0,
                    },
                )
            ]
        return []


# ---------------------------------------------------------------------------
# Asset Rotation Strategy
# ---------------------------------------------------------------------------


class AssetRotationStrategy(Strategy):
    """Rank assets by recent Sharpe or return; hold top-K (A7, O3, K-series).

    Parameters:
      symbols_list (str): Comma-separated list of symbols to rotate among.
      lookback_days (int, default 60): Return window for ranking.
      top_k (int, default 1): How many assets to hold simultaneously.
      rerank_days (int, default 20): Minimum days between rebalances.
      target_weight (float, default 0.90): Weight per held asset.
      rank_by (str, default "return"): "return" or "sharpe".
      absolute_momentum_threshold (float|None): If set, only include assets
        whose trailing return exceeds this value. Assets below threshold are
        excluded before top-K ranking. Enables dual momentum (Antonacci).
    """

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        params = self.config.parameters or {}
        syms_str: str = str(params.get("symbols_list", "SPY,TLT,GLD"))
        symbols = [s.strip() for s in syms_str.split(",") if s.strip()]
        lookback: int = int(params.get("lookback_days", 60))
        top_k: int = int(params.get("top_k", 1))
        tgt_weight: float = float(params.get("target_weight", 0.90))
        rank_by: str = str(params.get("rank_by", "return"))
        abs_thresh_raw = params.get("absolute_momentum_threshold")
        abs_thresh: float | None = (
            float(abs_thresh_raw) if abs_thresh_raw is not None else None
        )

        # Compute scores
        scores: list[tuple[str, float]] = []
        returns: dict[str, float] = {}
        for sym in symbols:
            sym_data = (
                indicators_df.filter(pl.col("symbol") == sym)
                .sort("date")
                .tail(lookback + 2)
            )
            if len(sym_data) < lookback:
                continue
            p = sym_data["close"].to_list()[-lookback - 1 :]
            rets = [p[i] / p[i - 1] - 1.0 for i in range(1, len(p))]
            if not rets:
                continue
            total_ret = p[-1] / p[0] - 1.0 if p[0] > 0 else 0.0
            returns[sym] = total_ret
            if rank_by == "sharpe":
                mu = sum(rets) / len(rets)
                std = (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5
                score = (mu / std * (252**0.5)) if std > 0 else 0.0
            else:
                score = total_ret
            scores.append((sym, score))

        if not scores:
            return []

        # Absolute momentum filter: exclude assets with return below threshold
        if abs_thresh is not None:
            scores = [(s, sc) for s, sc in scores if returns.get(s, 0.0) > abs_thresh]
        if not scores:
            return []

        scores.sort(key=lambda x: x[1], reverse=True)
        target_set = {s for s, _ in scores[:top_k] if s in prices and prices[s] > 0}
        current_set = set(portfolio.positions.keys()) & set(symbols)

        # Exit positions no longer in top-K
        signals: list[TradeSignal] = [
            TradeSignal(
                symbol=sym,
                action=Action.CLOSE,
                conviction=Conviction.MEDIUM,
                target_weight=0.0,
                stop_loss=0.0,
                reasoning=f"Rotation: {sym} dropped from top-{top_k}",
            )
            for sym in current_set - target_set
        ]
        # Enter new top-K positions
        weight_per = tgt_weight / max(len(target_set), 1)
        for sym in target_set - current_set:
            entry_price = prices.get(sym, 0.0)
            if entry_price <= 0:
                continue
            signals.append(
                TradeSignal(
                    symbol=sym,
                    action=Action.BUY,
                    conviction=Conviction.MEDIUM,
                    target_weight=weight_per,
                    stop_loss=entry_price * 0.93,
                    reasoning=f"Rotation: {sym} entered top-{top_k} by {rank_by}",
                )
            )
        return signals


# ---------------------------------------------------------------------------
# VIX Regime Strategy
# ---------------------------------------------------------------------------


class VixRegimeStrategy(Strategy):
    """Defensive equity positioning based on VIX level or volatility-of-vol (C-series).

    Modes:
      - "vov": VIX 30-day rolling std dev above percentile_threshold → exit.
      - "level": VIX level above vix_threshold → defensive.
      - "vix_spike": Single-day VIX % change above spike_threshold → contrarian entry.
      - "term_structure": Allocate between equity and defensive assets using the
        research-spec ratio medium/near = VIX / VIX9D. Ratio > backwardation_threshold
        is risk-off, ratio < contango_threshold is risk-on, and values in between are
        neutral/hold-current.

    Parameters:
      mode (str, default "level"): "vov", "level", "vix_spike", or "term_structure".
      vix_symbol (str, default "VIX"): Near-term VIX ticker for signal calculation.
      vix3m_symbol (str, default "VIX3M"): Medium-term VIX ticker for term_structure mode.
      equity_symbol (str, default "SPY"): Risk-on asset to trade.
      risk_off_symbol (str, default "SHY"): Defensive asset for term_structure mode.
      vix_threshold (float, default 25.0): VIX level for "level" mode; in term_structure
        mode this is the backwardation threshold on medium/near ratio.
      contango_threshold (float, default 0.95): Risk-on threshold on medium/near ratio.
      target_weight (float, default 0.90): Equity target weight in favourable regime.
      risk_off_weight (float, optional): Explicit defensive weight in backwardation.
        When omitted, defaults to 1 - weight_spy_risk_off from the mapped spec.
    """

    def generate_signals(  # noqa: PLR0911
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        params = self.config.parameters or {}
        mode: str = str(params.get("mode", "level"))
        vix_sym: str = str(params.get("vix_symbol", "VIX"))
        equity_sym: str = str(params.get("equity_symbol", "SPY"))
        vix_thresh: float = float(params.get("vix_threshold", 25.0))
        vov_window: int = int(params.get("vov_window", 30))
        vov_pct: float = float(params.get("vov_percentile", 0.80))
        spike_thresh: float = float(params.get("spike_threshold", 0.20))
        spike_exit_vix: float = float(params.get("spike_exit_vix", 20.0))
        tgt_weight: float = float(params.get("target_weight", 0.90))

        vix_data = indicators_df.filter(pl.col("symbol") == vix_sym).sort("date")
        if len(vix_data) < 5:
            return []

        vix_prices = vix_data["close"].to_list()
        vix_now = vix_prices[-1]

        equity_price = prices.get(equity_sym, 0)
        has_pos = equity_sym in portfolio.positions

        if mode == "level":
            if equity_price <= 0:
                return []
            defensive = vix_now > vix_thresh
        elif mode == "vov":
            if equity_price <= 0:
                return []
            if len(vix_prices) < vov_window + 1:
                return []

            def _rolling_std(series: list[float], w: int) -> float:
                if len(series) < w:
                    return 0.0
                vals = series[-w:]
                mu = sum(vals) / w
                return (sum((v - mu) ** 2 for v in vals) / w) ** 0.5

            vov_now = _rolling_std(vix_prices, vov_window)
            hist_vov = [
                _rolling_std(vix_prices[: i + 1], vov_window)
                for i in range(vov_window - 1, len(vix_prices))
            ]
            if not hist_vov:
                return []
            n_below = sum(1 for v in hist_vov if v <= vov_now)
            pct = n_below / len(hist_vov)
            defensive = pct >= vov_pct
        elif mode == "vix_spike":
            if equity_price <= 0:
                return []
            if len(vix_prices) < 2 or vix_prices[-2] <= 0:
                return []
            vix_change = vix_prices[-1] / vix_prices[-2] - 1.0
            if vix_change >= spike_thresh and not has_pos:
                return [
                    TradeSignal(
                        symbol=equity_sym,
                        action=Action.BUY,
                        conviction=Conviction.MEDIUM,
                        target_weight=tgt_weight,
                        stop_loss=equity_price * 0.95,
                        reasoning=f"VIX spike {vix_change:.1%}>={spike_thresh:.1%}",
                    )
                ]
            if has_pos and vix_now < spike_exit_vix:
                return [
                    TradeSignal(
                        symbol=equity_sym,
                        action=Action.CLOSE,
                        conviction=Conviction.HIGH,
                        target_weight=0.0,
                        stop_loss=0.0,
                        reasoning=f"VIX spike exit: VIX={vix_now:.1f}",
                    )
                ]
            return []
        elif mode == "term_structure":
            medium_sym: str = str(params.get("vix3m_symbol", "VIX3M"))
            contango_thresh: float = float(params.get("contango_threshold", 0.95))
            risk_off_symbol: str = str(params.get("risk_off_symbol", "SHY"))
            risk_off_price = prices.get(risk_off_symbol, 0.0)
            medium_data = indicators_df.filter(pl.col("symbol") == medium_sym).sort(
                "date"
            )
            if len(medium_data) < 5 or vix_now <= 0:
                return []

            medium_now = medium_data["close"].to_list()[-1]
            if medium_now <= 0:
                return []

            ratio = medium_now / vix_now
            has_risk_off = risk_off_symbol in portfolio.positions
            risk_off_weight_raw = params.get("risk_off_symbol_weight")
            if risk_off_weight_raw is None:
                risk_off_weight_raw = params.get("risk_off_weight")
            if risk_off_weight_raw is None:
                spy_risk_off_weight = float(params.get("weight_spy_risk_off", 0.0))
                risk_off_weight = max(0.0, 1.0 - spy_risk_off_weight)
            else:
                risk_off_weight = float(risk_off_weight_raw)

            regime = "neutral"
            if ratio > vix_thresh:
                regime = "backwardation"
            elif ratio < contango_thresh:
                regime = "contango"

            logger.debug(
                "VIX term structure: near=%s %.2f medium=%s %.2f ratio=%.3f backwardation_thresh=%.3f contango_thresh=%.3f regime=%s",
                vix_sym,
                vix_now,
                medium_sym,
                medium_now,
                ratio,
                vix_thresh,
                contango_thresh,
                regime,
            )

            signals: list[TradeSignal] = []
            if regime == "backwardation":
                if has_pos and risk_off_weight >= 1.0:
                    signals.append(
                        TradeSignal(
                            symbol=equity_sym,
                            action=Action.CLOSE,
                            conviction=Conviction.HIGH,
                            target_weight=0.0,
                            stop_loss=0.0,
                            reasoning=(
                                f"VIX[{mode}]: backwardation ratio={ratio:.3f}"
                                f" > {vix_thresh:.3f}, exit {equity_sym}"
                            ),
                        )
                    )
                elif equity_price > 0:
                    signals.append(
                        TradeSignal(
                            symbol=equity_sym,
                            action=Action.BUY,
                            conviction=Conviction.MEDIUM,
                            target_weight=max(0.0, 1.0 - risk_off_weight),
                            stop_loss=equity_price * 0.95,
                            reasoning=(
                                f"VIX[{mode}]: backwardation ratio={ratio:.3f}"
                                f" > {vix_thresh:.3f}, set {equity_sym} defensive weight"
                            ),
                        )
                    )
                if risk_off_weight > 0 and risk_off_price > 0:
                    signals.append(
                        TradeSignal(
                            symbol=risk_off_symbol,
                            action=Action.BUY,
                            conviction=Conviction.MEDIUM,
                            target_weight=risk_off_weight,
                            stop_loss=risk_off_price * 0.99,
                            reasoning=(
                                f"VIX[{mode}]: backwardation ratio={ratio:.3f}"
                                f" > {vix_thresh:.3f}, set {risk_off_symbol} defensive weight"
                            ),
                        )
                    )
                return signals

            if regime == "contango":
                if has_risk_off:
                    signals.append(
                        TradeSignal(
                            symbol=risk_off_symbol,
                            action=Action.CLOSE,
                            conviction=Conviction.MEDIUM,
                            target_weight=0.0,
                            stop_loss=0.0,
                            reasoning=(
                                f"VIX[{mode}]: contango ratio={ratio:.3f}"
                                f" < {contango_thresh:.3f}, exit defensive"
                            ),
                        )
                    )
                if equity_price > 0:
                    signals.append(
                        TradeSignal(
                            symbol=equity_sym,
                            action=Action.BUY,
                            conviction=Conviction.MEDIUM,
                            target_weight=tgt_weight,
                            stop_loss=equity_price * 0.95,
                            reasoning=(
                                f"VIX[{mode}]: contango ratio={ratio:.3f}"
                                f" < {contango_thresh:.3f}, set {equity_sym} risk-on weight"
                            ),
                        )
                    )
                return signals

            logger.debug(
                "VIX[%s]: neutral term structure on %s (ratio=%.3f within [%.3f, %.3f]); holding current allocation",
                mode,
                as_of_date,
                ratio,
                contango_thresh,
                vix_thresh,
            )
            return []

        else:
            return []

        if defensive and has_pos:
            return [
                TradeSignal(
                    symbol=equity_sym,
                    action=Action.CLOSE,
                    conviction=Conviction.HIGH,
                    target_weight=0.0,
                    stop_loss=0.0,
                    reasoning=f"VIX[{mode}]: defensive exit VIX={vix_now:.1f}",
                )
            ]
        if not defensive and not has_pos:
            return [
                TradeSignal(
                    symbol=equity_sym,
                    action=Action.BUY,
                    conviction=Conviction.MEDIUM,
                    target_weight=tgt_weight,
                    stop_loss=equity_price * 0.95,
                    reasoning=f"VIX regime [{mode}]: normal, enter (VIX={vix_now:.1f})",
                )
            ]
        return []


# ---------------------------------------------------------------------------
# Yield Curve Regime Strategy
# ---------------------------------------------------------------------------


class YieldCurveRegimeStrategy(Strategy):
    """Equity/bond positioning based on yield curve slope (N/E-series).

    Uses freely available Yahoo Finance yield tickers (^TNX=10y, ^IRX=3m,
    ^FVX=5y) to compute yield spread as regime signal.

    Modes:
      - "inversion": Hold TLT when 2s10s inverted (^IRX > ^TNX); exit when normal.
      - "momentum": Hold TLT when 63d TNX momentum negative (rates falling).
      - "steepener": Hold SPY when curve steepening (spread rising).

    Parameters:
      mode (str, default "inversion"): Strategy mode.
      short_yield_symbol (str, default "^IRX"): Short-end yield ticker.
      long_yield_symbol (str, default "^TNX"): Long-end yield ticker.
      equity_symbol (str, default "SPY"): Equity asset.
      bond_symbol (str, default "TLT"): Bond asset.
      momentum_window (int, default 63): Days for yield momentum.
      target_weight (float, default 0.90): Position weight.
    """

    def generate_signals(  # noqa: PLR0911
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        params = self.config.parameters or {}
        mode: str = str(params.get("mode", "momentum"))
        short_sym: str = str(params.get("short_yield_symbol", "^IRX"))
        long_sym: str = str(params.get("long_yield_symbol", "^TNX"))
        equity_sym: str = str(params.get("equity_symbol", "SPY"))
        bond_sym: str = str(params.get("bond_symbol", "TLT"))
        mom_window: int = int(params.get("momentum_window", 63))
        tgt_weight: float = float(params.get("target_weight", 0.90))

        long_data = (
            indicators_df.filter(pl.col("symbol") == long_sym)
            .sort("date")
            .tail(mom_window + 5)
        )
        if len(long_data) < 5:
            return []
        tnx_prices = long_data["close"].to_list()
        tnx_now = tnx_prices[-1]

        if mode == "momentum":
            # Hold TLT when 10y yield trending down (rates falling = bonds rising)
            if len(tnx_prices) < mom_window:
                return []
            tnx_past = tnx_prices[-mom_window]
            rates_falling = tnx_now < tnx_past
            trade_sym = bond_sym
        elif mode == "inversion":
            short_data = (
                indicators_df.filter(pl.col("symbol") == short_sym).sort("date").tail(5)
            )
            if len(short_data) < 2:
                return []
            irx_now = short_data["close"].to_list()[-1]
            inverted = irx_now > tnx_now  # short > long = inverted curve
            rates_falling = inverted  # inverted curve → hold TLT (defensive)
            trade_sym = bond_sym
        elif mode == "steepener":
            short_data = (
                indicators_df.filter(pl.col("symbol") == short_sym)
                .sort("date")
                .tail(mom_window + 5)
            )
            if len(short_data) < mom_window:
                return []
            irx = short_data["close"].to_list()
            spread_now = tnx_now - irx[-1]
            spread_past = tnx_prices[-mom_window] - irx[-mom_window]
            rates_falling = spread_now > spread_past  # steepening → hold equities
            trade_sym = equity_sym
        else:
            return []

        asset_price = prices.get(trade_sym, 0)
        if asset_price <= 0:
            return []
        has_pos = trade_sym in portfolio.positions

        if rates_falling and not has_pos:
            return [
                TradeSignal(
                    symbol=trade_sym,
                    action=Action.BUY,
                    conviction=Conviction.MEDIUM,
                    target_weight=tgt_weight,
                    stop_loss=asset_price * 0.95,
                    reasoning=f"YieldCurve [{mode}]: signal active (TNX={tnx_now:.2f})",
                )
            ]
        if not rates_falling and has_pos:
            return [
                TradeSignal(
                    symbol=trade_sym,
                    action=Action.CLOSE,
                    conviction=Conviction.HIGH,
                    target_weight=0.0,
                    stop_loss=0.0,
                    reasoning=f"YieldCurve [{mode}]: signal off (TNX={tnx_now:.2f})",
                )
            ]
        return []


# ---------------------------------------------------------------------------
# OHLCV Momentum Strategy (L2 / L4 series)
# ---------------------------------------------------------------------------


class OHLCVMomentumStrategy(Strategy):
    """OHLCV-based momentum on high-volume conviction candles (L2/L4 series).

    Modes:
      - "conviction_candle" (L2): Enter when any of the last ``signal_lookback``
        days had intraday_return > conviction_pct AND volume > vol_multiplier *
        vol_sma_20. Maintains position as long as a recent signal exists; exits
        when no conviction candle in the lookback window.
      - "atr_breakout" (L4): Enter when close > high_20 + atr_mult * atr_14
        AND volume > vol_multiplier * vol_sma_20.

    Parameters (via StrategyConfig.parameters):
      symbol (str, default "SPY"): Asset to trade.
      mode (str, default "conviction_candle"): "conviction_candle" or "atr_breakout".
      conviction_pct (float, default 0.01): Min intraday return for candle signal.
      vol_multiplier (float, default 1.5): Volume must exceed this × vol_sma_20.
      signal_lookback (int, default 5): Days to look back for conviction candle.
      atr_mult (float, default 0.5): ATR multiplier for breakout threshold (L4 only).
      target_weight (float, default 0.90): Position weight when in trade.
    """

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        params = self.config.parameters or {}
        symbol: str = str(params.get("symbol", "SPY"))
        mode: str = str(params.get("mode", "conviction_candle"))
        conviction_pct: float = float(params.get("conviction_pct", 0.01))
        vol_multiplier: float = float(params.get("vol_multiplier", 1.5))
        signal_lookback: int = int(params.get("signal_lookback", 5))
        atr_mult: float = float(params.get("atr_mult", 0.5))
        tgt_weight: float = float(params.get("target_weight", 0.90))

        lookback = signal_lookback + 5
        ind = (
            indicators_df.filter(pl.col("symbol") == symbol).sort("date").tail(lookback)
        )
        price = prices.get(symbol, 0)
        if len(ind) < signal_lookback or price <= 0:
            return []

        has_pos = symbol in portfolio.positions
        in_signal = False

        if mode == "conviction_candle":
            # Any of the last signal_lookback days had a high-volume conviction candle
            if "intraday_return" not in ind.columns or "vol_sma_20" not in ind.columns:
                return []
            recent = ind.tail(signal_lookback)
            for row in recent.iter_rows(named=True):
                ir = row.get("intraday_return")
                vol = row.get("volume")
                vsma = row.get("vol_sma_20")
                if (
                    ir is not None
                    and vol is not None
                    and vsma is not None
                    and vsma > 0
                    and ir > conviction_pct
                    and vol > vol_multiplier * vsma
                ):
                    in_signal = True
                    break

        elif mode == "atr_breakout":
            # Current day: close > high_20 + atr_mult * atr_14 AND high volume
            if (
                "high_20" not in ind.columns
                or "atr_14" not in ind.columns
                or "vol_sma_20" not in ind.columns
            ):
                return []
            row = ind.tail(1).row(0, named=True)
            h20 = row.get("high_20")
            atr = row.get("atr_14")
            vol = row.get("volume")
            vsma = row.get("vol_sma_20")
            close = row.get("close")
            if (
                isinstance(h20, (int, float))
                and isinstance(atr, (int, float))
                and isinstance(vol, (int, float))
                and isinstance(vsma, (int, float))
                and isinstance(close, (int, float))
                and vsma > 0
            ):
                h20_f = float(h20)
                atr_f = float(atr)
                vol_f = float(vol)
                vsma_f = float(vsma)
                close_f = float(close)
                in_signal = (
                    close_f > h20_f + atr_mult * atr_f
                    and vol_f > vol_multiplier * vsma_f
                )

        if in_signal and not has_pos:
            logger.info("OHLCVMomentum[%s]: ENTER %s on %s", mode, symbol, as_of_date)
            return [
                TradeSignal(
                    symbol=symbol,
                    action=Action.BUY,
                    conviction=Conviction.MEDIUM,
                    target_weight=tgt_weight,
                    stop_loss=price * 0.95,
                    reasoning=f"OHLCV {mode}: entry signal triggered",
                )
            ]
        if not in_signal and has_pos:
            logger.info("OHLCVMomentum[%s]: EXIT %s on %s", mode, symbol, as_of_date)
            return [
                TradeSignal(
                    symbol=symbol,
                    action=Action.CLOSE,
                    conviction=Conviction.HIGH,
                    target_weight=0.0,
                    stop_loss=0.0,
                    reasoning=f"OHLCV {mode}: signal window expired",
                )
            ]
        return []


# ---------------------------------------------------------------------------
# Overnight Momentum Strategy (C7 series)
# ---------------------------------------------------------------------------


class OvernightMomentumStrategy(Strategy):
    """Overnight return decomposition momentum (C7 series).

    Computes the N-day rolling average of overnight returns
    (overnight_return = open_t / close_{t-1} - 1).  When the average overnight
    return exceeds ``entry_thresh``, institutional accumulation is assumed and
    the strategy enters long.  When it drops below ``exit_thresh``, it exits.

    Overnight returns capture institutional order flow executing at market open
    after after-hours research.  Sustained positive overnight gaps signal demand
    from large institutions rebalancing on a bi-weekly (10-day) cycle.

    Parameters (via StrategyConfig.parameters):
      symbol (str, default "SPY"): Asset to trade.
      window (int, default 10): Rolling window for overnight return average.
      entry_thresh (float, default 0.002): Enter when avg_overnight > this.
      exit_thresh (float, default -0.0005): Exit when avg_overnight < this.
      target_weight (float, default 0.90): Position weight when in trade.
    """

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        params = self.config.parameters or {}
        symbol: str = str(params.get("symbol", "SPY"))
        window: int = int(params.get("window", 10))
        entry_thresh: float = float(params.get("entry_thresh", 0.002))
        exit_thresh: float = float(params.get("exit_thresh", -0.0005))
        tgt_weight: float = float(params.get("target_weight", 0.90))

        ind = (
            indicators_df.filter(pl.col("symbol") == symbol)
            .sort("date")
            .tail(window + 3)
        )
        price = prices.get(symbol, 0)
        if len(ind) < window + 1 or price <= 0:
            return []
        if "open" not in ind.columns:
            return []

        closes = ind["close"].to_list()
        opens = ind["open"].to_list()
        n = len(closes)

        overnight_rets = [
            float(opens[i]) / float(closes[i - 1]) - 1.0
            for i in range(1, n)
            if isinstance(opens[i], (int, float))
            and isinstance(closes[i - 1], (int, float))
            and float(closes[i - 1]) > 0
        ]
        if len(overnight_rets) < window:
            return []

        avg_overnight = sum(overnight_rets[-window:]) / window
        has_pos = symbol in portfolio.positions

        if avg_overnight > entry_thresh and not has_pos:
            logger.info(
                "OvernightMomentum: ENTER %s on %s (avg_overnight=%.4f)",
                symbol,
                as_of_date,
                avg_overnight,
            )
            return [
                TradeSignal(
                    symbol=symbol,
                    action=Action.BUY,
                    conviction=Conviction.MEDIUM,
                    target_weight=tgt_weight,
                    stop_loss=price * 0.95,
                    reasoning=f"C7: avg_overnight={avg_overnight:.4f} > {entry_thresh}",
                )
            ]
        if avg_overnight < exit_thresh and has_pos:
            logger.info(
                "OvernightMomentum: EXIT %s on %s (avg_overnight=%.4f)",
                symbol,
                as_of_date,
                avg_overnight,
            )
            return [
                TradeSignal(
                    symbol=symbol,
                    action=Action.CLOSE,
                    conviction=Conviction.HIGH,
                    target_weight=0.0,
                    stop_loss=0.0,
                    reasoning=f"C7: avg_overnight={avg_overnight:.4f} < {exit_thresh}",
                )
            ]
        return []


class SpyRegimeStarterStrategy(Strategy):
    """Deterministic SPY starter strategy using SPY indicators plus VIX regime checks."""

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        self._runtime_state: dict[str, dict[str, object]] = {}

    def _latest_row(
        self,
        indicators_df: pl.DataFrame,
        symbol: str,
    ) -> dict[str, object] | None:
        sym_data = indicators_df.filter(pl.col("symbol") == symbol).sort("date")
        if len(sym_data) == 0:
            return None
        row = sym_data.tail(1).row(0, named=True)
        return dict(row)

    @staticmethod
    def _position_weight(position: object) -> float:
        weight = getattr(position, "weight", None)
        if weight is None and isinstance(position, dict):
            weight = position.get("weight")
        if isinstance(weight, (int, float, str)):
            try:
                return float(weight)
            except ValueError:
                return 0.0
        return 0.0

    @staticmethod
    def _position_entry_price(position: object) -> float | None:
        entry_price = getattr(position, "entry_price", None)
        if entry_price is None:
            entry_price = getattr(position, "avg_cost", None)
        if entry_price is None and isinstance(position, dict):
            entry_price = position.get("entry_price", position.get("avg_cost"))
        if entry_price is None:
            return None
        if isinstance(entry_price, (int, float, str)):
            try:
                return float(entry_price)
            except ValueError:
                return None
        return None

    @staticmethod
    def _position_metadata(position: object) -> dict[str, object]:
        metadata = getattr(position, "metadata", None)
        if isinstance(metadata, dict):
            return dict(metadata)
        if isinstance(position, dict):
            raw = position.get("metadata")
            if isinstance(raw, dict):
                return dict(raw)
        return {}

    def _get_state(self, symbol: str) -> dict[str, object]:
        state = self._runtime_state.get(symbol)
        if state is None:
            state = {
                "entry_atr_14": None,
                "adds_completed": 0,
                "cooldown_until": None,
            }
            self._runtime_state[symbol] = state
        return state

    @staticmethod
    def _extract_float(value: object) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float, str)):
            try:
                return float(value)
            except ValueError:
                return None
        return None

    @staticmethod
    def _extract_int(value: object) -> int:
        if value is None:
            return 0
        if isinstance(value, (int, float, str)):
            try:
                return int(value)
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _extract_date(value: object) -> date | None:
        if isinstance(value, date):
            return value
        return None

    def _vix_value(self, indicators_df: pl.DataFrame, vix_symbol: str) -> float | None:
        vix_row = self._latest_row(indicators_df, vix_symbol)
        if vix_row is None:
            return None
        return self._extract_float(vix_row.get("close"))

    def _hydrate_position_state(
        self,
        symbol: str,
        position: object,
        atr_14: float,
    ) -> dict[str, object]:
        state = self._get_state(symbol)
        metadata = self._position_metadata(position)

        entry_atr = self._extract_float(
            metadata.get("entry_atr_14", state.get("entry_atr_14"))
        )
        state["entry_atr_14"] = atr_14 if entry_atr is None else entry_atr

        adds_completed = self._extract_int(
            metadata.get("adds_completed", state.get("adds_completed", 0))
        )
        state["adds_completed"] = adds_completed
        return state

    @staticmethod
    def _cooldown_active(
        cooldown_until: date | None,
        as_of_date: date,
    ) -> bool:
        return cooldown_until is not None and as_of_date <= cooldown_until

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        params = self.config.parameters or {}
        trade_symbol = str(params.get("trade_symbol", "SPY"))
        vix_symbol = str(params.get("vix_symbol", "VIX"))
        starter_weight = float(params.get("starter_weight", 0.02))
        max_weight = float(params.get("max_weight", 0.05))
        rsi_entry_threshold = float(params.get("rsi_entry_threshold", 55.0))
        rsi_exit_threshold = float(params.get("rsi_exit_threshold", 40.0))
        vix_entry_max = float(params.get("vix_entry_max", 19.2))
        vix_add_max = float(params.get("vix_add_max", 16.4))
        vix_exit_min = float(params.get("vix_exit_min", 25.0))
        macd_add_min = float(params.get("macd_add_min", 0.0))
        macd_exit_max = float(params.get("macd_exit_max", -0.20))
        atr_stop_multiple = float(params.get("atr_stop_multiple", 1.75))
        max_adds = int(params.get("max_adds", 1))
        cooldown_days_after_exit = int(params.get("cooldown_days_after_exit", 2))
        missing_vix_policy = str(
            params.get("missing_vix_policy", "block_new_entries_allow_risk_exits")
        )

        spy_row = self._latest_row(indicators_df, trade_symbol)
        if spy_row is None:
            return []

        required_columns = ["close", "sma_20", "sma_50", "rsi_14", "macd", "atr_14"]
        values: dict[str, float] = {}
        for column in required_columns:
            value = self._extract_float(spy_row.get(column))
            if value is None:
                return []
            values[column] = value

        close = values["close"]
        sma_20 = values["sma_20"]
        sma_50 = values["sma_50"]
        rsi_14 = values["rsi_14"]
        macd = values["macd"]
        atr_14 = values["atr_14"]

        state = self._get_state(trade_symbol)
        position = portfolio.positions.get(trade_symbol)
        has_position = position is not None
        current_weight = self._position_weight(position) if has_position else 0.0

        vix_close = self._vix_value(indicators_df, vix_symbol)
        vix_available = vix_close is not None
        block_new_risk = (
            not vix_available
            and missing_vix_policy == "block_new_entries_allow_risk_exits"
        )

        if has_position:
            state = self._hydrate_position_state(trade_symbol, position, atr_14)
            entry_price = self._position_entry_price(position)
            entry_atr_14 = self._extract_float(state.get("entry_atr_14"))
            adds_completed = self._extract_int(state.get("adds_completed"))
            atr_stop_hit = False
            if entry_price is not None and entry_atr_14 is not None:
                atr_stop_hit = close < (entry_price - atr_stop_multiple * entry_atr_14)

            if (
                atr_stop_hit
                or (vix_close is not None and vix_close > vix_exit_min)
                or rsi_14 < rsi_exit_threshold
                or macd < macd_exit_max
            ):
                exit_cooldown_until: date = as_of_date.fromordinal(
                    as_of_date.toordinal() + cooldown_days_after_exit
                )
                state["cooldown_until"] = exit_cooldown_until
                state["adds_completed"] = 0
                state["entry_atr_14"] = None
                return [
                    TradeSignal(
                        symbol=trade_symbol,
                        action=Action.CLOSE,
                        conviction=Conviction.HIGH,
                        target_weight=0.0,
                        stop_loss=0.0,
                        reasoning=(
                            "SPY regime exit: fixed ATR stop, VIX, RSI, or MACD threshold hit"
                        ),
                        metadata={
                            "cooldown_until": exit_cooldown_until.isoformat(),
                            "adds_completed": 0,
                        },
                    )
                ]

            if block_new_risk or vix_close is None:
                return []

            if current_weight >= max_weight:
                return []

            if adds_completed >= max_adds:
                return []

            if (
                vix_close is not None
                and macd > macd_add_min
                and vix_close < vix_add_max
            ):
                state["adds_completed"] = adds_completed + 1
                stop_anchor = entry_atr_14 if entry_atr_14 is not None else atr_14
                return [
                    TradeSignal(
                        symbol=trade_symbol,
                        action=Action.BUY,
                        conviction=Conviction.MEDIUM,
                        target_weight=max_weight,
                        stop_loss=max(close - atr_stop_multiple * stop_anchor, 0.0),
                        reasoning=(
                            "SPY add: existing long below max weight, MACD positive, "
                            "and VIX favorable"
                        ),
                        metadata={
                            "entry_atr_14": stop_anchor,
                            "adds_completed": self._extract_int(
                                state.get("adds_completed")
                            ),
                        },
                    )
                ]

            return []

        cooldown_until_value: date | None = self._extract_date(
            state.get("cooldown_until")
        )
        if self._cooldown_active(cooldown_until_value, as_of_date):
            return []

        if block_new_risk or vix_close is None:
            return []

        if (
            close > sma_20
            and close > sma_50
            and rsi_14 >= rsi_entry_threshold
            and vix_close < vix_entry_max
        ):
            state["entry_atr_14"] = atr_14
            state["adds_completed"] = 0
            state["cooldown_until"] = None
            return [
                TradeSignal(
                    symbol=trade_symbol,
                    action=Action.BUY,
                    conviction=Conviction.MEDIUM,
                    target_weight=starter_weight,
                    stop_loss=max(close - atr_stop_multiple * atr_14, 0.0),
                    reasoning=(
                        "SPY starter: price above SMA20/SMA50, RSI strong, VIX calm"
                    ),
                    metadata={
                        "entry_atr_14": atr_14,
                        "adds_completed": 0,
                        "signal_timing": "close_signal_next_open_fill",
                    },
                )
            ]

        return []


class GldBreakoutConfirmedStrategy(Strategy):
    """Deterministic GLD breakout strategy with metals and duration confirmation."""

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        self._runtime_state: dict[str, dict[str, object]] = {}

    def _latest_row(
        self,
        indicators_df: pl.DataFrame,
        symbol: str,
    ) -> dict[str, object] | None:
        sym_data = indicators_df.filter(pl.col("symbol") == symbol).sort("date")
        if len(sym_data) == 0:
            return None
        return dict(sym_data.tail(1).row(0, named=True))

    @staticmethod
    def _extract_float(value: object) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float, str)):
            try:
                return float(value)
            except ValueError:
                return None
        return None

    @staticmethod
    def _extract_date(value: object) -> date | None:
        if isinstance(value, date):
            return value
        return None

    @staticmethod
    def _position_entry_price(position: object) -> float | None:
        entry_price = getattr(position, "entry_price", None)
        if entry_price is None:
            entry_price = getattr(position, "avg_cost", None)
        if entry_price is None and isinstance(position, dict):
            entry_price = position.get("entry_price", position.get("avg_cost"))
        if entry_price is None:
            return None
        if isinstance(entry_price, (int, float, str)):
            try:
                return float(entry_price)
            except ValueError:
                return None
        return None

    @staticmethod
    def _position_metadata(position: object) -> dict[str, object]:
        metadata = getattr(position, "metadata", None)
        if isinstance(metadata, dict):
            return dict(metadata)
        if isinstance(position, dict):
            raw = position.get("metadata")
            if isinstance(raw, dict):
                return dict(raw)
        return {}

    def _get_state(self, symbol: str) -> dict[str, object]:
        state = self._runtime_state.get(symbol)
        if state is None:
            state = {
                "entry_atr_14": cast("object", None),
                "cooldown_until": cast("object", None),
            }
            self._runtime_state[symbol] = state
        return state

    @staticmethod
    def _cooldown_active(
        cooldown_until: date | None,
        as_of_date: date,
    ) -> bool:
        return cooldown_until is not None and as_of_date <= cooldown_until

    def _hydrate_position_state(
        self,
        symbol: str,
        position: object,
        atr_14: float,
    ) -> dict[str, object]:
        state = self._get_state(symbol)
        metadata = self._position_metadata(position)
        entry_atr = self._extract_float(
            metadata.get("entry_atr_14", state.get("entry_atr_14"))
        )
        state["entry_atr_14"] = atr_14 if entry_atr is None else entry_atr
        return state

    def _confirmation_close_above_sma_20(
        self,
        indicators_df: pl.DataFrame,
        symbol: str,
    ) -> bool | None:
        row = self._latest_row(indicators_df, symbol)
        if row is None:
            return None
        close = self._extract_float(row.get("close"))
        sma_20 = self._extract_float(row.get("sma_20"))
        if close is None or sma_20 is None:
            return None
        return close > sma_20

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        params = self.config.parameters or {}
        trade_symbol = str(params.get("trade_symbol", "GLD"))
        metals_confirm_symbol = str(params.get("metals_confirm_symbol", "SLV"))
        raw_macro_confirm_symbols = params.get("macro_confirm_symbols", ["TLT", "IEF"])
        macro_confirm_symbols = [
            str(symbol) for symbol in raw_macro_confirm_symbols if symbol is not None
        ]
        starter_weight = float(params.get("starter_weight", 0.08))
        breakout_lookback_days = int(params.get("breakout_lookback_days", 20))
        require_close_above_sma_50_raw = params.get("require_close_above_sma_50", True)
        if isinstance(require_close_above_sma_50_raw, bool):
            require_close_above_sma_50 = require_close_above_sma_50_raw
        elif isinstance(require_close_above_sma_50_raw, str):
            require_close_above_sma_50 = (
                require_close_above_sma_50_raw.strip().lower()
                in {"1", "true", "yes", "on"}
            )
        else:
            require_close_above_sma_50 = bool(require_close_above_sma_50_raw)
        slv_confirmation_mode = str(
            params.get("slv_confirmation_mode", "close_above_sma_20")
        )
        macro_confirmation_mode = str(
            params.get("macro_confirmation_mode", "any_close_above_sma_20")
        )
        rsi_exit_threshold = float(params.get("rsi_exit_threshold", 48.0))
        macd_exit_max = float(params.get("macd_exit_max", 0.0))
        atr_stop_multiple = float(params.get("atr_stop_multiple", 1.2))
        cooldown_days_after_exit = int(params.get("cooldown_days_after_exit", 3))
        missing_confirmation_policy = str(
            params.get(
                "missing_confirmation_policy",
                "block_new_entries_allow_risk_exits",
            )
        )

        trade_row = self._latest_row(indicators_df, trade_symbol)
        if trade_row is None:
            return []

        required_columns = ["close", "high_20", "sma_20", "rsi_14", "macd", "atr_14"]
        if require_close_above_sma_50:
            required_columns.append("sma_50")

        values: dict[str, float] = {}
        for column in required_columns:
            value = self._extract_float(trade_row.get(column))
            if value is None:
                return []
            values[column] = value

        close = values["close"]
        high_20 = values["high_20"]
        sma_20 = values["sma_20"]
        sma_50 = values.get("sma_50")
        rsi_14 = values["rsi_14"]
        macd = values["macd"]
        atr_14 = values["atr_14"]

        state = self._get_state(trade_symbol)
        position = portfolio.positions.get(trade_symbol)
        has_position = position is not None

        if has_position:
            state = self._hydrate_position_state(trade_symbol, position, atr_14)
            entry_price = self._position_entry_price(position)
            entry_atr_14 = self._extract_float(state.get("entry_atr_14"))
            atr_stop_hit = False
            if entry_price is not None and entry_atr_14 is not None:
                atr_stop_hit = close < (entry_price - atr_stop_multiple * entry_atr_14)

            breakout_failed = close < sma_20
            if (
                breakout_failed
                or atr_stop_hit
                or rsi_14 < rsi_exit_threshold
                or macd < macd_exit_max
            ):
                exit_cooldown_until: date = as_of_date.fromordinal(
                    as_of_date.toordinal() + cooldown_days_after_exit
                )
                state["cooldown_until"] = exit_cooldown_until
                state["entry_atr_14"] = None
                return [
                    TradeSignal(
                        symbol=trade_symbol,
                        action=Action.CLOSE,
                        conviction=Conviction.HIGH,
                        target_weight=0.0,
                        stop_loss=0.0,
                        reasoning=(
                            "GLD breakout exit: breakout failure, fixed ATR stop, RSI, "
                            "or MACD threshold hit"
                        ),
                        metadata={
                            "cooldown_until": exit_cooldown_until.isoformat(),
                        },
                    )
                ]
            return []

        cooldown_until_value: date | None = self._extract_date(
            state.get("cooldown_until")
        )
        if self._cooldown_active(cooldown_until_value, as_of_date):
            return []

        metals_confirmed: bool | None
        if slv_confirmation_mode == "close_above_sma_20":
            metals_confirmed = self._confirmation_close_above_sma_20(
                indicators_df,
                metals_confirm_symbol,
            )
        else:
            metals_confirmed = None

        macro_results: list[bool | None] = [
            self._confirmation_close_above_sma_20(indicators_df, symbol)
            for symbol in macro_confirm_symbols
        ]
        macro_confirmed: bool | None
        if macro_confirmation_mode == "any_close_above_sma_20":
            available_results = [
                result for result in macro_results if result is not None
            ]
            macro_confirmed = None if not available_results else any(available_results)
        else:
            macro_confirmed = None

        block_new_entries = (
            missing_confirmation_policy == "block_new_entries_allow_risk_exits"
            and (metals_confirmed is None or macro_confirmed is None)
        )
        if block_new_entries:
            return []

        breakout_confirmed = close > high_20
        trend_confirmed = (
            True
            if (not require_close_above_sma_50 or sma_50 is None)
            else close > sma_50
        )

        if (
            breakout_confirmed
            and trend_confirmed
            and metals_confirmed is True
            and macro_confirmed is True
        ):
            state["entry_atr_14"] = atr_14
            state["cooldown_until"] = None
            breakout_level = f"{breakout_lookback_days}d_high"
            return [
                TradeSignal(
                    symbol=trade_symbol,
                    action=Action.BUY,
                    conviction=Conviction.MEDIUM,
                    target_weight=starter_weight,
                    stop_loss=max(close - atr_stop_multiple * atr_14, 0.0),
                    reasoning=(
                        "GLD breakout confirmed: close above prior "
                        f"{breakout_level}, above SMA50, SLV confirmed, and "
                        "at least one duration proxy confirmed"
                    ),
                    metadata={
                        "entry_atr_14": atr_14,
                        "signal_timing": "close_signal_next_open_fill",
                    },
                )
            ]

        return []


# ---------------------------------------------------------------------------
# BTC Regime + Alt Momentum (btc-regime-alt-momentum-v1)
# ---------------------------------------------------------------------------


class BtcRegimeAltMomentumStrategy(Strategy):
    """BTC trend regime gate drives alt momentum selection.

    Regime: bull when BTC price > SMA50 AND SMA20 > SMA50.
    Signal: hold top-1 alt (by momentum_lookback-day return) in bull regime; cash otherwise.
    Exit: regime flip OR position stop (stop_loss_pct from entry).
    Guard: portfolio NAV circuit breaker — if drawdown from rolling peak exceeds
           nav_drawdown_halt, exit all and stay flat for nav_cooloff_days days
           (peak resets on re-entry to prevent immediate re-fire).
    """

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        # Persistent state across generate_signals() calls
        self._nav_peak: float | None = None
        self._cooloff_remaining: int = 0
        self._entry_prices: dict[str, float] = {}  # symbol → entry close price

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        params = self.config.parameters
        momentum_lb: int = int(params.get("momentum_lookback", 10))
        n_top: int = int(params.get("n_top_alts", 1))
        stop_pct: float = float(params.get("stop_loss_pct", self.config.stop_loss_pct))
        nav_halt: float = float(params.get("nav_drawdown_halt", 0.18))
        cooloff_days: int = int(params.get("nav_cooloff_days", 10))
        regime_asset: str = str(params.get("regime_asset", "BTC-USD"))
        alt_universe: list[str] = list(
            params.get("tradeable_alts", ["ETH-USD", "ADA-USD", "SOL-USD", "XRP-USD"])
        )

        current_nav = portfolio.nav

        # Initialise peak on first call
        if self._nav_peak is None:
            self._nav_peak = current_nav

        # ── Portfolio-level NAV circuit breaker ─────────────────────────────
        # Reset watermark when cooloff expires so we don't immediately re-fire
        if self._cooloff_remaining == 1:
            self._nav_peak = current_nav

        if self._cooloff_remaining > 0:
            self._cooloff_remaining -= 1
            self._entry_prices.clear()
            # Close any open positions
            close_signals = [
                TradeSignal(
                    symbol=sym,
                    action=Action.CLOSE,
                    conviction=Conviction.LOW,
                    target_weight=0.0,
                    stop_loss=0.0,
                    reasoning="NAV circuit breaker: cooling off",
                )
                for sym in portfolio.positions
            ]
            self._nav_peak = max(self._nav_peak, current_nav)
            return close_signals

        # Update rolling peak
        self._nav_peak = max(self._nav_peak, current_nav)
        nav_dd = (current_nav - self._nav_peak) / self._nav_peak
        if nav_dd < -nav_halt:
            self._cooloff_remaining = cooloff_days
            self._entry_prices.clear()
            return [
                TradeSignal(
                    symbol=sym,
                    action=Action.CLOSE,
                    conviction=Conviction.LOW,
                    target_weight=0.0,
                    stop_loss=0.0,
                    reasoning=f"NAV circuit breaker triggered: dd={nav_dd:.2%}",
                )
                for sym in portfolio.positions
            ]

        # ── BTC regime detection ─────────────────────────────────────────────
        btc_data = indicators_df.filter(pl.col("symbol") == regime_asset).sort("date")
        if len(btc_data) < 50:
            return []  # insufficient warmup

        btc_row = cast("RowDict", btc_data.tail(1).row(0, named=True))
        btc_close = btc_row.get("close")
        btc_sma20 = btc_row.get("sma_20")
        btc_sma50 = btc_row.get("sma_50")

        if not all(
            isinstance(v, (int, float)) for v in [btc_close, btc_sma20, btc_sma50]
        ):
            return []

        bull_regime = float(btc_close) > float(
            btc_sma50
        ) and float(  # type: ignore[arg-type]
            btc_sma20
        ) > float(
            btc_sma50
        )  # type: ignore[arg-type]

        signals: list[TradeSignal] = []

        # ── Non-bull: exit all positions ────────────────────────────────────
        if not bull_regime:
            for sym in list(portfolio.positions):
                signals.append(
                    TradeSignal(
                        symbol=sym,
                        action=Action.CLOSE,
                        conviction=Conviction.LOW,
                        target_weight=0.0,
                        stop_loss=0.0,
                        reasoning="BTC regime: non-bull — exit to cash",
                    )
                )
                self._entry_prices.pop(sym, None)
            return signals

        # ── Bull regime: rank alts by momentum ──────────────────────────────
        alt_returns: list[tuple[str, float]] = []
        for sym in alt_universe:
            sym_data = indicators_df.filter(pl.col("symbol") == sym).sort("date")
            ret = _trailing_return(sym_data, momentum_lb)
            if ret is not None:
                alt_returns.append((sym, ret))

        if not alt_returns:
            return []

        alt_returns.sort(key=lambda x: x[1], reverse=True)
        top_alts = {sym for sym, _ in alt_returns[:n_top]}

        # ── Exit positions that rotated out or hit stop ──────────────────────
        for sym in list(portfolio.positions):
            current_price = prices.get(sym, 0.0)
            entry_price = self._entry_prices.get(sym, current_price)

            # Position stop-loss check
            if (
                current_price > 0
                and entry_price > 0
                and current_price < entry_price * (1.0 - stop_pct)
            ):
                signals.append(
                    TradeSignal(
                        symbol=sym,
                        action=Action.CLOSE,
                        conviction=Conviction.LOW,
                        target_weight=0.0,
                        stop_loss=0.0,
                        reasoning=f"Stop-loss hit: {current_price:.2f} < {entry_price*(1-stop_pct):.2f}",
                    )
                )
                self._entry_prices.pop(sym, None)
            elif sym not in top_alts:
                signals.append(
                    TradeSignal(
                        symbol=sym,
                        action=Action.CLOSE,
                        conviction=Conviction.LOW,
                        target_weight=0.0,
                        stop_loss=0.0,
                        reasoning=f"Rotated out: {sym} no longer top-{n_top} by momentum",
                    )
                )
                self._entry_prices.pop(sym, None)

        # ── Enter new top-alt positions ──────────────────────────────────────
        for sym in top_alts:
            if sym in portfolio.positions:
                continue
            current_price = prices.get(sym, 0.0)
            if current_price <= 0:
                continue
            stop_level = round(current_price * (1.0 - stop_pct), 2)
            self._entry_prices[sym] = current_price
            signals.append(
                TradeSignal(
                    symbol=sym,
                    action=Action.BUY,
                    conviction=Conviction.HIGH,
                    target_weight=self.config.target_position_weight,
                    stop_loss=stop_level,
                    reasoning=(
                        f"BTC bull regime + top-{n_top} alt momentum "
                        f"({alt_returns[0][1]:.2%} over {momentum_lb}d)"
                    ),
                )
            )

        return signals


class BtcRegimeAltMomentumV2Strategy(Strategy):
    """BTC trend regime gate (SMA5/SMA20) drives alt momentum selection — v2.

    Changes vs v1:
    - Faster regime filter: SMA5 > SMA20 AND price > SMA20 (vs SMA20/SMA50 in v1)
    - ADA removed from universe (illiquid in stress events)
    - target_position_weight=0.40 (partial allocation to reduce MaxDD)
    - Research simulation explicitly uses t+1 returns to match engine fill_delay=1
    """

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        self._nav_peak: float | None = None
        self._cooloff_remaining: int = 0
        self._entry_prices: dict[str, float] = {}

    def generate_signals(  # noqa: PLR0911, PLR0915  # guard-clause pattern; refactoring would obscure intent
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        params = self.config.parameters
        regime_fast: int = int(params.get("regime_sma_fast", 5))
        regime_slow: int = int(params.get("regime_sma_slow", 20))
        momentum_lb: int = int(params.get("momentum_lookback", 10))
        n_top: int = int(params.get("n_top_alts", 1))
        stop_pct: float = float(params.get("stop_loss_pct", self.config.stop_loss_pct))
        nav_halt: float = float(params.get("nav_drawdown_halt", 0.18))
        cooloff_days: int = int(params.get("nav_cooloff_days", 10))
        regime_asset: str = str(params.get("regime_asset", "BTC-USD"))
        alt_universe: list[str] = list(
            params.get("tradeable_alts", ["ETH-USD", "SOL-USD", "XRP-USD"])
        )

        current_nav = portfolio.nav

        if self._nav_peak is None:
            self._nav_peak = current_nav

        # Reset watermark when cooloff expires to prevent immediate re-fire
        if self._cooloff_remaining == 1:
            self._nav_peak = current_nav

        if self._cooloff_remaining > 0:
            self._cooloff_remaining -= 1
            self._entry_prices.clear()
            close_signals = [
                TradeSignal(
                    symbol=sym,
                    action=Action.CLOSE,
                    conviction=Conviction.LOW,
                    target_weight=0.0,
                    stop_loss=0.0,
                    reasoning="NAV circuit breaker: cooling off",
                )
                for sym in portfolio.positions
            ]
            self._nav_peak = max(self._nav_peak, current_nav)
            return close_signals

        self._nav_peak = max(self._nav_peak, current_nav)
        nav_dd = (current_nav - self._nav_peak) / self._nav_peak
        if nav_dd < -nav_halt:
            self._cooloff_remaining = cooloff_days
            self._entry_prices.clear()
            return [
                TradeSignal(
                    symbol=sym,
                    action=Action.CLOSE,
                    conviction=Conviction.LOW,
                    target_weight=0.0,
                    stop_loss=0.0,
                    reasoning=f"NAV circuit breaker triggered: dd={nav_dd:.2%}",
                )
                for sym in portfolio.positions
            ]

        # ── BTC regime detection (SMA_fast/SMA_slow) ────────────────────────
        sma_fast_col = f"sma_{regime_fast}"
        sma_slow_col = f"sma_{regime_slow}"

        btc_data = indicators_df.filter(pl.col("symbol") == regime_asset).sort("date")
        if len(btc_data) < regime_slow:
            return []

        btc_row = cast("RowDict", btc_data.tail(1).row(0, named=True))
        btc_close = btc_row.get("close")

        # Compute SMAs inline if not pre-computed by indicators pipeline
        btc_sma_fast = btc_row.get(sma_fast_col)
        if btc_sma_fast is None:
            close_series = btc_data.tail(regime_fast)["close"].to_list()
            if len(close_series) >= regime_fast:
                btc_sma_fast = sum(close_series) / len(close_series)

        btc_sma_slow = btc_row.get(sma_slow_col)
        if btc_sma_slow is None:
            close_series = btc_data.tail(regime_slow)["close"].to_list()
            if len(close_series) >= regime_slow:
                btc_sma_slow = sum(close_series) / len(close_series)

        if not all(
            isinstance(v, (int, float)) for v in [btc_close, btc_sma_fast, btc_sma_slow]
        ):
            return []

        bull_regime = float(btc_sma_fast) > float(
            btc_sma_slow
        ) and float(  # type: ignore[arg-type]
            btc_close
        ) > float(
            btc_sma_slow
        )  # type: ignore[arg-type]

        signals: list[TradeSignal] = []

        if not bull_regime:
            for sym in list(portfolio.positions):
                signals.append(
                    TradeSignal(
                        symbol=sym,
                        action=Action.CLOSE,
                        conviction=Conviction.LOW,
                        target_weight=0.0,
                        stop_loss=0.0,
                        reasoning="BTC regime: non-bull — exit to cash",
                    )
                )
                self._entry_prices.pop(sym, None)
            return signals

        # ── Bull regime: rank alts by momentum ──────────────────────────────
        alt_returns: list[tuple[str, float]] = []
        for sym in alt_universe:
            sym_data = indicators_df.filter(pl.col("symbol") == sym).sort("date")
            ret = _trailing_return(sym_data, momentum_lb)
            if ret is not None:
                alt_returns.append((sym, ret))

        if not alt_returns:
            return []

        alt_returns.sort(key=lambda x: x[1], reverse=True)
        top_alts = {sym for sym, _ in alt_returns[:n_top]}

        for sym in list(portfolio.positions):
            current_price = prices.get(sym, 0.0)
            entry_price = self._entry_prices.get(sym, current_price)

            if (
                current_price > 0
                and entry_price > 0
                and current_price < entry_price * (1.0 - stop_pct)
            ):
                signals.append(
                    TradeSignal(
                        symbol=sym,
                        action=Action.CLOSE,
                        conviction=Conviction.LOW,
                        target_weight=0.0,
                        stop_loss=0.0,
                        reasoning=f"Stop-loss hit: {current_price:.2f} < {entry_price*(1-stop_pct):.2f}",
                    )
                )
                self._entry_prices.pop(sym, None)
            elif sym not in top_alts:
                signals.append(
                    TradeSignal(
                        symbol=sym,
                        action=Action.CLOSE,
                        conviction=Conviction.LOW,
                        target_weight=0.0,
                        stop_loss=0.0,
                        reasoning=f"Rotated out: {sym} no longer top-{n_top} by momentum",
                    )
                )
                self._entry_prices.pop(sym, None)

        for sym in top_alts:
            if sym in portfolio.positions:
                continue
            current_price = prices.get(sym, 0.0)
            if current_price <= 0:
                continue
            stop_level = round(current_price * (1.0 - stop_pct), 2)
            self._entry_prices[sym] = current_price
            signals.append(
                TradeSignal(
                    symbol=sym,
                    action=Action.BUY,
                    conviction=Conviction.HIGH,
                    target_weight=self.config.target_position_weight,
                    stop_loss=stop_level,
                    reasoning=(
                        f"BTC bull regime (SMA{regime_fast}/SMA{regime_slow}) + "
                        f"top-{n_top} alt momentum ({alt_returns[0][1]:.2%} over {momentum_lb}d)"
                    ),
                )
            )

        return signals


# ---------------------------------------------------------------------------
# Multi-Asset Time-Series Momentum with Crash-Aware Overlay
# ---------------------------------------------------------------------------


class MultiAssetTsmomStrategy(Strategy):
    """Multi-asset TSMOM with crash detection and regime filtering.

    Implements the research spec ``multi-asset-tsmom-crash-aware``:
    - Cross-sectional ranking by composite TSMOM across 4 lookbacks
    - Crash index from VIX backwardation, volume spike, and dispersion widening
    - Three-state regime filter (risk_on / transition / risk_off)
    - ATR-based initial stop + 8% trailing stop
    - Rebalances every ``rebalance_frequency_days`` trading days
    """

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        self._state: dict[str, object] = {
            "last_rebalance_date": None,
            "entry_prices": {},      # symbol -> fill price
            "atr_at_entry": {},      # symbol -> ATR_14 at entry
            "initial_stops": {},     # symbol -> initial ATR stop price
            "trailing_stops": {},    # symbol -> current trailing stop price
            "highest_since_entry": {},  # symbol -> highest close since entry
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_price_col(self, sym_df: pl.DataFrame) -> str:
        """Return 'adj_close' if present and non-null, else 'close'."""
        if "adj_close" in sym_df.columns:
            last = sym_df.tail(1).row(0, named=True)
            if last.get("adj_close") is not None:
                return "adj_close"
        return "close"

    def _compute_tsmom_composite(
        self,
        sym_df: pl.DataFrame,
        params: dict,
        price_col: str,
    ) -> float | None:
        """Compute equal-weight composite TSMOM score for one symbol."""
        lb1 = params.get("lookback_1m", 21)
        lb3 = params.get("lookback_3m", 63)
        lb6 = params.get("lookback_6m", 126)
        lb12 = params.get("lookback_12m", 252)
        skip = params.get("lookback_skip", 21)

        needed = lb12 + skip + 5
        if len(sym_df) < needed:
            return None

        prices_arr = sym_df[price_col].to_list()
        atrs_arr = sym_df["atr_14"].to_list()

        last_price = prices_arr[-1]
        last_atr = atrs_arr[-1]

        if last_price is None or last_price <= 0 or last_atr is None or last_atr <= 0:
            return None

        vol_ann = last_atr * math.sqrt(252) / last_price
        if vol_ann <= 0:
            return None

        scores: list[float] = []
        for lb in (lb1, lb3, lb6):
            if len(prices_arr) < lb + 1:
                break
            start_price = prices_arr[-(lb + 1)]
            if start_price is None or start_price <= 0:
                break
            ret = (last_price - start_price) / start_price
            scores.append(ret / vol_ann)
        else:
            # 12M with 1M skip: price at -(lb12+skip) relative to -(skip)
            if len(prices_arr) >= lb12 + skip + 1:
                end_idx = -(skip + 1)          # price at T-skip
                start_idx = -(lb12 + skip + 1)  # price at T-lb12-skip
                end_price = prices_arr[end_idx]
                start_price = prices_arr[start_idx]
                if (
                    end_price is not None
                    and start_price is not None
                    and start_price > 0
                    and end_price > 0
                ):
                    ret12 = (end_price - start_price) / start_price
                    scores.append(ret12 / vol_ann)

        if len(scores) < 4:
            return None
        return sum(scores) / len(scores)

    def _compute_crash_index(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        held_symbols: list[str],
        params: dict,
        disable_crash: bool = False,
    ) -> float:
        """Return crash index in {0.0, 0.33, 0.67, 1.0}."""
        if disable_crash:
            return 0.0

        vix_ratio_thresh = params.get("crash_vix_ratio_threshold", 0.90)
        vol_z_thresh = params.get("crash_vol_zscore_threshold", 2.0)
        disp_window = params.get("crash_dispersion_window", 20)
        disp_baseline = params.get("crash_dispersion_baseline", 90)
        disp_mult = params.get("crash_dispersion_multiplier", 1.50)

        components: list[float] = []

        # Component 1: VIX/VIX3M < threshold
        vix_df = indicators_df.filter(pl.col("symbol") == "VIX").sort("date")
        vix3m_df = indicators_df.filter(pl.col("symbol") == "VIX3M").sort("date")
        if len(vix_df) > 0 and len(vix3m_df) > 0:
            vix_close = vix_df.tail(1).row(0, named=True).get("close")
            vix3m_close = vix3m_df.tail(1).row(0, named=True).get("close")
            if (
                vix_close is not None
                and vix3m_close is not None
                and vix3m_close > 0
            ):
                ratio = vix_close / vix3m_close
                components.append(1.0 if ratio < vix_ratio_thresh else 0.0)
            else:
                components.append(0.0)
        else:
            components.append(0.0)

        # Component 2: max volume z-score across held assets > threshold
        if held_symbols:
            max_z = 0.0
            for sym in held_symbols:
                sym_df = indicators_df.filter(pl.col("symbol") == sym).sort("date")
                if len(sym_df) < 22:
                    continue
                vols = sym_df["volume"].tail(21).to_list()
                vols_clean = [v for v in vols if v is not None and v > 0]
                if len(vols_clean) < 5:
                    continue
                recent_vol = vols_clean[-1]
                window_vols = vols_clean[:-1]
                if len(window_vols) < 2:
                    continue
                mean_v = sum(window_vols) / len(window_vols)
                std_v = math.sqrt(
                    sum((x - mean_v) ** 2 for x in window_vols) / (len(window_vols) - 1)
                )
                if std_v > 0:
                    z = (recent_vol - mean_v) / std_v
                    max_z = max(max_z, z)
            components.append(1.0 if max_z > vol_z_thresh else 0.0)
        else:
            components.append(0.0)

        # Component 3: cross-asset dispersion > multiplier × baseline
        tradeable = params.get("tradeable_symbols", [])
        all_daily_rets: list[list[float]] = []
        needed_bars = disp_baseline + disp_window + 5
        for sym in tradeable:
            sym_df = indicators_df.filter(pl.col("symbol") == sym).sort("date")
            if len(sym_df) < needed_bars:
                continue
            price_col = "adj_close" if "adj_close" in sym_df.columns else "close"
            prices = sym_df[price_col].tail(needed_bars).to_list()
            rets = [
                (prices[i] - prices[i - 1]) / prices[i - 1]
                for i in range(1, len(prices))
                if prices[i] is not None
                and prices[i - 1] is not None
                and prices[i - 1] > 0
            ]
            all_daily_rets.append(rets)

        if len(all_daily_rets) >= 5:
            # Current 20d cross-asset std of returns
            n_bars_recent = min(disp_window, min(len(r) for r in all_daily_rets))
            cross_std_recent: list[float] = []
            for t in range(n_bars_recent):
                day_rets = [r[-(n_bars_recent - t)] for r in all_daily_rets]
                mn = sum(day_rets) / len(day_rets)
                std_ = math.sqrt(
                    sum((x - mn) ** 2 for x in day_rets) / len(day_rets)
                )
                cross_std_recent.append(std_)
            current_disp = sum(cross_std_recent) / len(cross_std_recent) if cross_std_recent else 0.0

            # Baseline 90d mean
            n_bars_base = min(disp_baseline, min(len(r) for r in all_daily_rets))
            cross_std_base: list[float] = []
            for t in range(n_bars_base):
                day_rets = [r[-(n_bars_base - t)] for r in all_daily_rets]
                mn = sum(day_rets) / len(day_rets)
                std_ = math.sqrt(
                    sum((x - mn) ** 2 for x in day_rets) / len(day_rets)
                )
                cross_std_base.append(std_)
            baseline_disp = sum(cross_std_base) / len(cross_std_base) if cross_std_base else 0.0

            if baseline_disp > 0:
                components.append(1.0 if current_disp > disp_mult * baseline_disp else 0.0)
            else:
                components.append(0.0)
        else:
            components.append(0.0)

        return sum(components) / 3.0 if components else 0.0

    def _compute_regime(
        self,
        indicators_df: pl.DataFrame,
        params: dict,
        disable_regime: bool = False,
    ) -> str:
        """Return 'risk_on', 'transition', or 'risk_off'."""
        if disable_regime:
            return "risk_on"

        vix_on = params.get("regime_vix_risk_on", 20.0)
        vix_off = params.get("regime_vix_risk_off", 30.0)
        slope_days = params.get("regime_slope_days", 10)
        slope_risk_off = params.get("regime_slope_risk_off", -0.02)

        vix_df = indicators_df.filter(pl.col("symbol") == "VIX").sort("date")
        vix_level = None
        if len(vix_df) > 0:
            vix_level = vix_df.tail(1).row(0, named=True).get("close")

        hyg_df = indicators_df.filter(pl.col("symbol") == "HYG").sort("date")
        tlt_df = indicators_df.filter(pl.col("symbol") == "TLT").sort("date")

        hyg_tlt_slope = None
        if len(hyg_df) >= slope_days + 1 and len(tlt_df) >= slope_days + 1:
            hyg_prices = hyg_df["adj_close"].tail(slope_days + 1).to_list()
            tlt_prices = tlt_df["adj_close"].tail(slope_days + 1).to_list()
            if (
                all(x is not None and x > 0 for x in hyg_prices)
                and all(x is not None and x > 0 for x in tlt_prices)
            ):
                ratio_now = hyg_prices[-1] / tlt_prices[-1]
                ratio_then = hyg_prices[0] / tlt_prices[0]
                if ratio_then > 0:
                    hyg_tlt_slope = (ratio_now - ratio_then) / ratio_then

        if vix_level is None:
            return "transition"

        risk_off = vix_level > vix_off or (
            hyg_tlt_slope is not None and hyg_tlt_slope < slope_risk_off
        )
        risk_on = vix_level < vix_on and (
            hyg_tlt_slope is None or hyg_tlt_slope >= 0
        )

        if risk_off:
            return "risk_off"
        if risk_on:
            return "risk_on"
        return "transition"

    # ------------------------------------------------------------------
    # Main signal generator
    # ------------------------------------------------------------------

    def generate_signals(
        self,
        as_of_date: date,
        indicators_df: pl.DataFrame,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> list[TradeSignal]:
        params = self.config.parameters
        top_n = params.get("top_n", 6)
        rebal_freq = params.get("rebalance_frequency_days", 5)
        target_risk_pct = params.get("target_risk_pct", 0.01)
        max_weight = params.get("max_position_weight", 0.08)
        atr_stop_mult = params.get("atr_stop_multiple", 2.5)
        trailing_stop_pct = params.get("trailing_stop_pct", 0.08)
        disable_crash = params.get("disable_crash", False)
        disable_regime = params.get("disable_regime", False)
        tradeable_symbols: list[str] = params.get(
            "tradeable_symbols",
            [
                "SPY","QQQ","IWM","DIA","XLK","XLF","XLE","XLV","XLI","XLY",
                "XLP","XLU","XLRE","XLB","XLC","EEM","EFA","VGK","EWJ",
                "GLD","SLV","USO","DBA",
            ],
        )

        # Retrieve persistent state
        last_rebal: date | None = cast(
            "date | None", self._state.get("last_rebalance_date")
        )
        entry_prices: dict[str, float] = cast(
            "dict[str, float]", self._state["entry_prices"]
        )
        atr_at_entry: dict[str, float] = cast(
            "dict[str, float]", self._state["atr_at_entry"]
        )
        initial_stops: dict[str, float] = cast(
            "dict[str, float]", self._state["initial_stops"]
        )
        trailing_stops: dict[str, float] = cast(
            "dict[str, float]", self._state["trailing_stops"]
        )
        highest_since_entry: dict[str, float] = cast(
            "dict[str, float]", self._state["highest_since_entry"]
        )

        signals: list[TradeSignal] = []

        # --- Update trailing stops for all held positions ---
        for sym in list(portfolio.positions):
            curr_price = prices.get(sym)
            if curr_price is None or curr_price <= 0:
                continue
            prev_high = highest_since_entry.get(sym, curr_price)
            new_high = max(prev_high, curr_price)
            highest_since_entry[sym] = new_high
            new_trail = new_high * (1.0 - trailing_stop_pct)
            trailing_stops[sym] = max(trailing_stops.get(sym, 0.0), new_trail)
            # Check stop triggers
            stop = max(initial_stops.get(sym, 0.0), trailing_stops.get(sym, 0.0))
            if stop > 0 and curr_price <= stop:
                signals.append(
                    TradeSignal(
                        symbol=sym,
                        action=Action.CLOSE,
                        conviction=Conviction.HIGH,
                        target_weight=0.0,
                        stop_loss=0.0,
                        reasoning=f"Stop triggered: {curr_price:.2f} <= {stop:.2f}",
                    )
                )
                for d in (entry_prices, atr_at_entry, initial_stops, trailing_stops, highest_since_entry):
                    d.pop(sym, None)

        # Decide if this is a rebalance day
        days_since = 999 if last_rebal is None else (as_of_date - last_rebal).days
        is_rebalance = days_since >= rebal_freq

        if not is_rebalance:
            return signals

        self._state["last_rebalance_date"] = as_of_date

        # --- Compute TSMOM composite for all tradeable symbols ---
        params_with_tradeable = dict(params)
        params_with_tradeable["tradeable_symbols"] = tradeable_symbols

        tsmom_scores: dict[str, float] = {}
        sym_atr: dict[str, float] = {}
        for sym in tradeable_symbols:
            sym_df = indicators_df.filter(pl.col("symbol") == sym).sort("date")
            if len(sym_df) == 0:
                continue
            price_col = self._get_price_col(sym_df)
            score = self._compute_tsmom_composite(sym_df, params, price_col)
            if score is None:
                continue
            tsmom_scores[sym] = score
            last_row = sym_df.tail(1).row(0, named=True)
            atr_val = last_row.get("atr_14")
            if atr_val is not None and atr_val > 0:
                sym_atr[sym] = float(atr_val)

        if not tsmom_scores:
            return signals

        # --- Cross-sectional rank ---
        ranked = sorted(tsmom_scores.items(), key=lambda x: x[1], reverse=True)
        rank_map = {sym: i + 1 for i, (sym, _) in enumerate(ranked)}

        # --- Crash index and regime ---
        held = list(portfolio.positions.keys())
        crash_idx = self._compute_crash_index(
            as_of_date, indicators_df, held, params_with_tradeable, disable_crash
        )
        regime = self._compute_regime(indicators_df, params, disable_regime)

        regime_scale = {"risk_on": 1.0, "transition": 0.75, "risk_off": 0.50}.get(
            regime, 0.75
        )
        crash_scale = max(0.0, 1.0 - crash_idx * 0.50)

        # --- Full flatten if crash_index == 1.0 ---
        if crash_idx >= 1.0:
            for sym in list(portfolio.positions):
                already_signaled = any(s.symbol == sym for s in signals)
                if not already_signaled:
                    signals.append(
                        TradeSignal(
                            symbol=sym,
                            action=Action.CLOSE,
                            conviction=Conviction.HIGH,
                            target_weight=0.0,
                            stop_loss=0.0,
                            reasoning="Crash index == 1.0 — full flatten",
                        )
                    )
            return signals

        # --- Exit: rank fell to bottom quartile ---
        n_total = len(tsmom_scores)
        exit_rank_thresh = max(int(n_total * 0.75) + 1, top_n + 1)
        for sym in list(portfolio.positions):
            r = rank_map.get(sym, n_total + 1)
            if r >= exit_rank_thresh:
                already = any(s.symbol == sym for s in signals)
                if not already:
                    signals.append(
                        TradeSignal(
                            symbol=sym,
                            action=Action.CLOSE,
                            conviction=Conviction.MEDIUM,
                            target_weight=0.0,
                            stop_loss=0.0,
                            reasoning=f"Rank {r} >= {exit_rank_thresh} (bottom quartile)",
                        )
                    )
                    for d in (entry_prices, atr_at_entry, initial_stops, trailing_stops, highest_since_entry):
                        d.pop(sym, None)

        # --- Entry: top_n ranked, not already held ---
        exiting = {s.symbol for s in signals if s.action == Action.CLOSE}
        current_held = set(portfolio.positions.keys()) - exiting
        n_slots = top_n - len(current_held)

        nav = portfolio.nav if hasattr(portfolio, "nav") else 100_000.0  # noqa: F841

        added = 0
        for sym, _ in ranked:
            if added >= n_slots:
                break
            if sym in current_held:
                continue
            if sym in exiting:
                continue

            curr_price = prices.get(sym)
            if curr_price is None or curr_price <= 0:
                continue

            atr_val = sym_atr.get(sym, 0.0)
            vol_ann = atr_val * math.sqrt(252) / curr_price if atr_val > 0 else 0.02
            base_weight = min(target_risk_pct / max(vol_ann, 1e-6), max_weight)
            final_weight = base_weight * regime_scale * crash_scale
            final_weight = min(final_weight, max_weight)

            stop_price = (
                round(curr_price - atr_stop_mult * atr_val, 4)
                if atr_val > 0
                else round(curr_price * 0.95, 4)
            )

            entry_prices[sym] = curr_price
            atr_at_entry[sym] = atr_val
            initial_stops[sym] = stop_price
            trailing_stops[sym] = curr_price * (1.0 - trailing_stop_pct)
            highest_since_entry[sym] = curr_price

            signals.append(
                TradeSignal(
                    symbol=sym,
                    action=Action.BUY,
                    conviction=Conviction.HIGH,
                    target_weight=final_weight,
                    stop_loss=stop_price,
                    reasoning=(
                            f"Rank {rank_map[sym]}/{n_total} | "
                            f"TSMOM={tsmom_scores[sym]:.3f} | "
                            f"regime={regime} | crash={crash_idx:.2f} | "
                            f"w={final_weight:.3f}"
                        ),
                )
            )
            added += 1

        return signals


# ---------------------------------------------------------------------------
# Strategy factory
# ---------------------------------------------------------------------------

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "sma_crossover": SMACrossoverStrategy,
    "rsi_mean_reversion": RSIMeanReversionStrategy,
    "momentum": MomentumStrategy,
    "macd": MACDStrategy,
    "regime_momentum": RegimeMomentumStrategy,
    "trend_following": TrendFollowingStrategy,
    "multi_factor": MultiFactorStrategy,
    "correlation_regime": CorrelationRegimeStrategy,
    "correlation_surprise": CorrelationSurpriseStrategy,
    "calendar_event": CalendarEventStrategy,
    "pairs_ratio": PairsRatioStrategy,
    "lead_lag": LeadLagStrategy,
    "asset_rotation": AssetRotationStrategy,
    "vix_regime": VixRegimeStrategy,
    "yield_curve_regime": YieldCurveRegimeStrategy,
    "ohlcv_momentum": OHLCVMomentumStrategy,
    "overnight_momentum": OvernightMomentumStrategy,
    "spy_regime_starter": SpyRegimeStarterStrategy,
    "gld_breakout_confirmed": GldBreakoutConfirmedStrategy,
    "cef_discount": CEFDiscountRegistryStrategy,
    "nlp_signal": NlpSignalStrategy,
    "btc_regime_alt_momentum": BtcRegimeAltMomentumStrategy,
    "btc_regime_alt_momentum_v2": BtcRegimeAltMomentumV2Strategy,
    "btc_regime_alt_momentum_v3": BtcRegimeAltMomentumV2Strategy,
    "multi_asset_tsmom": MultiAssetTsmomStrategy,
}


def create_strategy(name: str, config: StrategyConfig) -> Strategy:
    """Create a strategy instance by name."""
    cls = STRATEGY_REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown strategy '{name}'. Available: {list(STRATEGY_REGISTRY.keys())}"
        )
    return cls(config)
